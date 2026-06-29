use std::collections::{HashMap, HashSet, VecDeque};
use std::panic::AssertUnwindSafe;
use std::sync::Arc;
use std::time::Duration;

use futures::future::{join_all, FutureExt};
use serde_json::Value;

use crate::tool::Tool;
use crate::types::{ToolCallItem, ToolCallSet, ToolResultItem, ToolResultSet};

/// Execute a ToolCallSet with the given tool registry.
///
/// Pure function — no Bus, no MCP node. Called by RuntimeModule.
pub async fn execute(
    call_set: &ToolCallSet,
    tools: &HashMap<String, Arc<dyn Tool>>,
) -> ToolResultSet {
    let calls = &call_set.calls;

    // Edge case: empty call set
    if calls.is_empty() {
        return ToolResultSet {
            session_id: call_set.session_id.clone(),
            results: vec![],
        };
    }

    // 1. Validate bidirectional lock consistency
    if let Some(error_results) = validate_bidirectional_lock(calls) {
        return ToolResultSet {
            session_id: call_set.session_id.clone(),
            results: error_results,
        };
    }

    // 2. Build in-degree map from blocked_by
    let in_degree = build_in_degree(calls);

    // 3. Detect cycles (DFS) and unknown references
    if let Some(error_msg) = detect_cycle(calls, &in_degree) {
        return ToolResultSet {
            session_id: call_set.session_id.clone(),
            results: error_all(calls, &error_msg),
        };
    }

    // 4. Kahn topological sort → layers
    let layers = kahn_sort(calls, &in_degree);

    // 5. Execute layer by layer
    let mut completed: HashMap<String, ToolResultItem> = HashMap::new();

    for layer in &layers {
        let futures: Vec<_> = layer
            .iter()
            .map(|call_id| {
                let call = find_call(calls, call_id).unwrap();
                let tool = tools.get(&call.tool).cloned();
                let timeout_ms = call_set.timeout_ms;
                let params = call.params.clone();
                let call_id = call_id.clone();
                let tool_name = call.tool.clone();

                tokio::spawn(async move {
                    execute_one(call_id, tool_name, tool, params, timeout_ms).await
                })
            })
            .collect();

        let results = join_all(futures).await;

        let mut any_failed = false;
        for result in results {
            let item = result.unwrap_or_else(|e| {
                // tokio::spawn JoinError (shouldn't happen with catch_unwind)
                panic!("executor task panicked: {e}")
            });
            let call_id = item.call_id.clone();
            let is_error = item.status != "success";
            if is_error {
                any_failed = true;
            }
            completed.insert(call_id, item);
        }

        // 6. Cascade cancel along blocking chain
        if any_failed {
            cascade_cancel(calls, &mut completed);
            break;
        }
    }

    // 7. Assemble final results — fill missing calls as cancelled
    let results: Vec<ToolResultItem> = calls
        .iter()
        .map(|call| {
            completed.get(&call.id).cloned().unwrap_or_else(|| {
                ToolResultItem {
                    call_id: call.id.clone(),
                    name: call.tool.clone(),
                    status: "cancelled".into(),
                    result: Value::Null,
                    error: Some("cancelled: upstream dependency failed".into()),
                }
            })
        })
        .collect();

    ToolResultSet {
        session_id: call_set.session_id.clone(),
        results,
    }
}

// ── Single call execution ───────────────────────────────────────────

async fn execute_one(
    call_id: String,
    tool_name: String,
    tool: Option<Arc<dyn Tool>>,
    params: Value,
    timeout_ms: Option<u64>,
) -> ToolResultItem {
    // Tool not found
    let tool = match tool {
        Some(t) => t,
        None => {
            return ToolResultItem {
                call_id: call_id.clone(),
                name: tool_name.clone(),
                status: "error".into(),
                result: Value::Null,
                error: Some(format!("tool not found: {tool_name}")),
            };
        }
    };

    // Clone for use in both exec_fut and timeout branch
    let cid = call_id.clone();
    let cid2 = call_id;
    let tname = tool_name.clone();
    let tname2 = tool_name;

    // Execution future: catch_unwind + execute
    let exec_fut = async {
        let result = AssertUnwindSafe(tool.execute(params))
            .catch_unwind()
            .await;
        match result {
            Ok(Ok(val)) => ToolResultItem {
                call_id: cid.clone(),
                name: tname.clone(),
                status: "success".into(),
                result: val,
                error: None,
            },
            Ok(Err(tool_err)) => ToolResultItem {
                call_id: cid.clone(),
                name: tname.clone(),
                status: "error".into(),
                result: Value::Null,
                error: Some(tool_err.message),
            },
            Err(panic_payload) => ToolResultItem {
                call_id: cid.clone(),
                name: tname,
                status: "error".into(),
                result: Value::Null,
                error: Some(format!(
                    "panic: {}",
                    panic_payload
                        .downcast_ref::<&str>()
                        .copied()
                        .unwrap_or("<unknown>")
                )),
            },
        }
    };

    // Wrap with optional timeout
    if let Some(ms) = timeout_ms {
        match tokio::time::timeout(Duration::from_millis(ms), exec_fut).await {
            Ok(result) => result,
            Err(_elapsed) => {
                tool.cancel().await;
                ToolResultItem {
                    call_id: cid2,
                    name: tname2,
                    status: "cancelled".into(),
                    result: Value::Null,
                    error: Some("timeout".into()),
                }
            }
        }
    } else {
        exec_fut.await
    }
}

// ── Bidirectional lock validation ───────────────────────────────────

/// Validate A.blocking ⊇ B ⇒ B.blocked_by ⊇ A for all call pairs.
/// Returns Some(errors) if inconsistent; None if valid.
fn validate_bidirectional_lock(calls: &[ToolCallItem]) -> Option<Vec<ToolResultItem>> {
    for a in calls {
        for b_id in &a.blocking {
            if let Some(b) = calls.iter().find(|c| &c.id == b_id) {
                if !b.blocked_by.contains(&a.id) {
                    return Some(error_all(
                        calls,
                        &format!(
                            "inconsistent lock: '{}' .blocking includes '{}' but '{}' .blocked_by does not include '{}'",
                            a.id, b_id, b_id, a.id
                        ),
                    ));
                }
            }
        }
    }
    None
}

// ── In-degree ───────────────────────────────────────────────────────

fn build_in_degree(calls: &[ToolCallItem]) -> HashMap<String, usize> {
    let mut in_degree: HashMap<String, usize> = HashMap::new();
    for call in calls {
        let entry = in_degree.entry(call.id.clone()).or_insert(0);
        *entry += call.blocked_by.len();
    }
    in_degree
}

// ── Cycle detection ─────────────────────────────────────────────────

/// DFS-based cycle detection + unknown reference check.
/// Returns Some(error_message) if a problem is found; None if the graph is valid.
fn detect_cycle(
    calls: &[ToolCallItem],
    _in_degree: &HashMap<String, usize>,
) -> Option<String> {
    let call_ids: HashSet<&str> = calls.iter().map(|c| c.id.as_str()).collect();

    // Check for references to non-existent call IDs in blocked_by
    for call in calls {
        for dep in &call.blocked_by {
            if !call_ids.contains(dep.as_str()) {
                return Some(format!(
                    "call '{}' depends on unknown call '{}'",
                    call.id, dep
                ));
            }
        }
    }

    // DFS with recursion stack for cycle detection
    let mut visited = HashSet::new();
    let mut rec_stack = HashSet::new();

    for call in calls {
        if !visited.contains(&call.id) {
            if dfs_cycle(&call.id, calls, &mut visited, &mut rec_stack) {
                return Some("dependency cycle detected".into());
            }
        }
    }
    None
}

fn dfs_cycle(
    node: &str,
    calls: &[ToolCallItem],
    visited: &mut HashSet<String>,
    rec_stack: &mut HashSet<String>,
) -> bool {
    if rec_stack.contains(node) {
        return true; // back edge → cycle
    }
    if visited.contains(node) {
        return false;
    }
    visited.insert(node.to_string());
    rec_stack.insert(node.to_string());

    if let Some(call) = calls.iter().find(|c| c.id == node) {
        for dep in &call.blocked_by {
            if dfs_cycle(dep, calls, visited, rec_stack) {
                return true;
            }
        }
    }

    rec_stack.remove(node);
    false
}

// ── Kahn topological sort ───────────────────────────────────────────

/// Kahn algorithm: topological sort → Vec<Vec<String>> (layers).
///
/// Layer 0: calls with in_degree == 0 (no dependencies)
/// Layer N: calls whose blockers are all in layers 0..N-1
fn kahn_sort(calls: &[ToolCallItem], in_degree: &HashMap<String, usize>) -> Vec<Vec<String>> {
    let mut deg = in_degree.clone();
    let mut queue: VecDeque<String> = calls
        .iter()
        .filter(|c| deg.get(&c.id).copied().unwrap_or(0) == 0)
        .map(|c| c.id.clone())
        .collect();

    // Build reverse adjacency: who depends on each node
    // (= who needs me to complete before they can run)
    let mut dependents: HashMap<String, Vec<String>> = HashMap::new();
    for call in calls {
        for blocker_id in &call.blocked_by {
            dependents
                .entry(blocker_id.clone())
                .or_default()
                .push(call.id.clone());
        }
    }

    let mut layers: Vec<Vec<String>> = Vec::new();

    while !queue.is_empty() {
        // Snapshot current queue as a layer
        let current_layer: Vec<String> = queue.iter().cloned().collect();
        layers.push(current_layer);

        // Process the entire current layer
        let layer_size = queue.len();
        let mut next_queue = VecDeque::new();

        for _ in 0..layer_size {
            let node = queue.pop_front().unwrap();
            if let Some(deps) = dependents.get(&node) {
                for dep in deps {
                    if let Some(count) = deg.get_mut(dep) {
                        *count -= 1;
                        if *count == 0 {
                            next_queue.push_back(dep.clone());
                        }
                    }
                }
            }
        }

        queue = next_queue;
    }

    layers
}

// ── Cascade cancel ──────────────────────────────────────────────────

/// BFS along blocking chain: mark all downstream dependents as cancelled.
fn cascade_cancel(calls: &[ToolCallItem], completed: &mut HashMap<String, ToolResultItem>) {
    let mut queue: VecDeque<String> = VecDeque::new();

    // Seed: all calls that have error/cancelled status
    for call in calls {
        if let Some(item) = completed.get(&call.id) {
            if item.status != "success" {
                queue.push_back(call.id.clone());
            }
        }
    }

    while let Some(failed_id) = queue.pop_front() {
        if let Some(failed_call) = calls.iter().find(|c| c.id == failed_id) {
            for blocked_id in &failed_call.blocking {
                if !completed.contains_key(blocked_id) {
                    completed.insert(
                        blocked_id.clone(),
                        ToolResultItem {
                            call_id: blocked_id.clone(),
                            name: calls
                                .iter()
                                .find(|c| &c.id == blocked_id)
                                .map(|c| c.tool.clone())
                                .unwrap_or_default(),
                            status: "cancelled".into(),
                            result: Value::Null,
                            error: Some(format!(
                                "cancelled: upstream dependency '{}' failed",
                                failed_id
                            )),
                        },
                    );
                    queue.push_back(blocked_id.clone());
                }
            }
        }
    }
}

// ── Helpers ─────────────────────────────────────────────────────────

fn find_call<'a>(calls: &'a [ToolCallItem], id: &str) -> Option<&'a ToolCallItem> {
    calls.iter().find(|c| c.id == id)
}

fn error_all(calls: &[ToolCallItem], message: &str) -> Vec<ToolResultItem> {
    calls
        .iter()
        .map(|call| ToolResultItem {
            call_id: call.id.clone(),
            name: call.tool.clone(),
            status: "error".into(),
            result: Value::Null,
            error: Some(message.into()),
        })
        .collect()
}
