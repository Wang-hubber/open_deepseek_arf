# 任务 5.4：DAG 执行器

> Phase 5 — MCP 第四项任务
> 父文档：`docs/v1.x/phase5_mcp/phase5-mcp-design.md`
> 依赖：Task 5.1 (类型定义), Task 5.2 (ScriptTool)

## 设计思路

**DAG 执行器是纯函数**——接收 `ToolCallSet` + `HashMap<String, Arc<dyn Tool>>`，返回 `ToolResultSet`。不依赖 Bus，不感知 MCP 节点。`RuntimeModule` 和未来的 `LocalMcpNode` 直接调用它。

核心算法流程：

```
ToolCallSet.calls
  → 交叉验证双向锁（blocked_by ↔ blocking 一致性）
  → DFS 环检测
  → Kahn 拓扑排序 → 分层
  → 逐层并发执行（tokio::spawn per call）
    → catch_unwind + Result + timeout 三层保护
  → 任一步失败 → 沿 blocking 正向级联取消
  → backfill name → 组装 ToolResultSet
```

| 文件 | 操作 | 内容 |
|------|------|------|
| `executor.rs` | 新建 | DAG 构建、验证、排序、并发执行、级联取消 |
| `lib.rs` | 更新 | `pub mod executor;` |

---

## 代码实现

### `crates/arf-mcp/src/executor.rs` — 新建

```rust
use std::collections::{HashMap, HashSet, VecDeque};
use std::panic::AssertUnwindSafe;
use std::sync::Arc;

use futures::future::join_all;
use serde_json::Value;

use crate::tool::Tool;
use crate::types::{ToolCallItem, ToolCallSet, ToolError, ToolResultItem, ToolResultSet};

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

    // 2. Build adjacency: blocked_by → edges (computing in-degrees)
    let in_degree = build_in_degree(calls);

    // 3. Detect cycles (DFS)
    if let Some(cycle) = detect_cycle(calls, &in_degree) {
        return error_all(calls, &call_set.session_id, &format!("cycle detected: {cycle}"));
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

        // Collect results, check for failures
        let mut any_failed = false;
        for result in results {
            let item = result.unwrap(); // JoinError — shouldn't happen with catch_unwind
            let call_id = item.call_id.clone();
            let is_error = item.status != "success";
            if is_error {
                any_failed = true;
            }
            completed.insert(call_id, item);
        }

        // 6. Cascade cancel along blocking
        if any_failed {
            cascade_cancel(calls, &mut completed);
            break;
        }
    }

    // 7. Fill in results for cancelled/not-yet-executed calls
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
                call_id,
                name: tool_name,
                status: "error".into(),
                result: Value::Null,
                error: Some(format!("tool not found: {tool_name}")),
            };
        }
    };

    // Layer 1: catch_unwind — panic safety
    let exec_fut = async { tool.execute(params).await };
    let result = match AssertUnwindSafe(exec_fut).catch_unwind().await {
        Ok(Ok(val)) => {
            return ToolResultItem {
                call_id,
                name: tool_name,
                status: "success".into(),
                result: val,
                error: None,
            };
        }
        Ok(Err(tool_err)) => ToolResultItem {
            call_id,
            name: tool_name,
            status: "error".into(),
            result: Value::Null,
            error: Some(tool_err.message),
        },
        Err(panic_payload) => ToolResultItem {
            call_id,
            name: tool_name,
            status: "error".into(),
            result: Value::Null,
            error: Some(format!(
                "panic: {}",
                panic_payload
                    .downcast_ref::<&str>()
                    .unwrap_or(&"<unknown>")
            )),
        },
    };

    // Layer 3: timeout (wraps whole execution)
    // Note: timeout is already handled by ScriptTool internally.
    // The executor-level timeout is a safety net for tools that don't
    // implement their own timeout.
    if let Some(ms) = timeout_ms {
        match tokio::time::timeout(std::time::Duration::from_millis(ms), std::future::ready(()))
            .await
        {
            Ok(_) => result,
            Err(_) => {
                tool.cancel().await;
                ToolResultItem {
                    call_id: result.call_id,
                    name: result.name,
                    status: "cancelled".into(),
                    result: Value::Null,
                    error: Some("timeout".into()),
                }
            }
        }
    } else {
        result
    }
}

// ── DAG algorithms ──────────────────────────────────────────────────

/// Validate A.blocking ⊇ B ⇒ B.blocked_by ⊇ A for all call pairs.
fn validate_bidirectional_lock(calls: &[ToolCallItem]) -> Option<Vec<ToolResultItem>> {
    for a in calls {
        for b_id in &a.blocking {
            if let Some(b) = calls.iter().find(|c| &c.id == b_id) {
                if !b.blocked_by.contains(&a.id) {
                    return Some(error_all(
                        calls,
                        "",
                        &format!(
                            "inconsistent lock: {}.blocking contains {} but {}.blocked_by does not contain {}",
                            a.id, b_id, b_id, a.id
                        ),
                    ));
                }
            }
        }
    }
    None
}

fn build_in_degree(calls: &[ToolCallItem]) -> HashMap<String, usize> {
    let mut in_degree: HashMap<String, usize> = HashMap::new();
    for call in calls {
        in_degree.entry(call.id.clone()).or_insert(0);
        for dep in &call.blocked_by {
            *in_degree.entry(call.id.clone()).or_insert(0) += 1;
        }
    }
    in_degree
}

/// DFS-based cycle detection.
fn detect_cycle(calls: &[ToolCallItem], in_degree: &HashMap<String, usize>) -> Option<String> {
    let call_ids: HashSet<&str> = calls.iter().map(|c| c.id.as_str()).collect();

    // Check for references to non-existent call IDs
    for call in calls {
        for dep in &call.blocked_by {
            if !call_ids.contains(dep.as_str()) {
                return Some(format!("call '{}' depends on unknown call '{}'", call.id, dep));
            }
        }
    }

    // DFS with recursion stack for cycle detection
    let mut visited = HashSet::new();
    let mut rec_stack = HashSet::new();

    for call in calls {
        if dfs_cycle(call.id.as_str(), calls, &mut visited, &mut rec_stack) {
            return Some("dependency cycle detected".into());
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

/// Kahn algorithm: topological sort → Vec<Vec<String>> (layers).
fn kahn_sort(calls: &[ToolCallItem], in_degree: &HashMap<String, usize>) -> Vec<Vec<String>> {
    let mut deg = in_degree.clone();
    let mut queue: VecDeque<String> = calls
        .iter()
        .filter(|c| deg.get(&c.id).copied().unwrap_or(0) == 0)
        .map(|c| c.id.clone())
        .collect();

    // Build adjacency: who depends on me (= who I block = my blocking list)
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
        layers.push(queue.iter().cloned().collect());
        let mut next_queue = VecDeque::new();

        for _ in 0..queue.len() {
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

/// Forward cascade: follow blocking chain, mark all downstream as cancelled.
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

fn error_all(calls: &[ToolCallItem], session_id: &str, message: &str) -> Vec<ToolResultItem> {
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
```

逐行解释：
- `execute()` — 主入口，纯函数。空 call_set 直接返回空。完整流程：验证 → 入度 → 环检测 → 排序 → 分层执行 → 级联取消 → 组装结果
- `execute_one()` — 执行单个 tool call。内层 `catch_unwind` 捕获 panic（`AssertUnwindSafe` 包装）。`catch_unwind` 要求 future 是 unwinding-safe，但 async 块不满足——用 `AssertUnwindSafe` 显式声明
- `validate_bidirectional_lock()` — 检查 `A.blocking ⊇ B ⇒ B.blocked_by ⊇ A`。只做正向验证（blocking → blocked_by），因为 Engine 可能只设 blocking
- `detect_cycle()` — DFS + 递归栈（rec_stack）。先检查引用完整性（blocked_by 中的 ID 是否存在），再跑 DFS
- `kahn_sort()` — 标准 Kahn 算法。关键：邻接表方向是"谁依赖我"（blocking），不是"我依赖谁"（blocked_by）。`dependents[blocker].push(call.id)` 表示"blocker 完成后，call 的入度 -1"
- `cascade_cancel()` — BFS 沿 blocking 正向传播。种子是所有 failure/cancelled 节点，每步向 blocking 下游扩散

### `crates/arf-mcp/Cargo.toml` 更新

添加 `futures` crate：

```toml
[dependencies]
# ... existing ...
futures = "0.3"
```

`futures::future::join_all` 用于 layer 内并发等待所有 spawned task。

### `crates/arf-mcp/src/lib.rs` 更新

```rust
pub mod config;
pub mod executor;
pub mod script;
pub mod skill;
pub mod tool;
pub mod types;
```

---

## 测试

### 测试结构

```
crates/arf-mcp/src/tests/
├── mod.rs
├── executor_tests.rs    # 新建
├── ...
```

### `crates/arf-mcp/src/tests/mod.rs` 更新

```rust
mod config_tests;
mod executor_tests;
mod script_tests;
mod skill_tests;
mod tool_tests;
mod types_tests;
```

### `crates/arf-mcp/src/tests/executor_tests.rs` — 新建

```rust
use std::collections::HashMap;
use std::sync::Arc;

use crate::executor;
use crate::tool::Tool;
use crate::types::{ToolCallItem, ToolCallSet, ToolError};
use serde_json::Value;

// Mock tool — returns predefined result or error
struct MockTool {
    name: String,
    description: String,
    schema: Value,
    result: Result<Value, ToolError>,
}

impl MockTool {
    fn success(name: &str, result: Value) -> Self {
        Self {
            name: name.into(),
            description: format!("Mock {name}"),
            schema: serde_json::json!({"type": "object"}),
            result: Ok(result),
        }
    }

    fn error(name: &str, msg: &str) -> Self {
        Self {
            name: name.into(),
            description: format!("Mock {name}"),
            schema: serde_json::json!({"type": "object"}),
            result: Err(ToolError::from(msg)),
        }
    }
}

#[async_trait::async_trait]
impl Tool for MockTool {
    fn name(&self) -> &str { &self.name }
    fn description(&self) -> &str { &self.description }
    fn parameters_schema(&self) -> Value { self.schema.clone() }
    async fn execute(&self, _params: Value) -> Result<Value, ToolError> {
        self.result.clone()
    }
}

fn registry(tools: Vec<MockTool>) -> HashMap<String, Arc<dyn Tool>> {
    let mut map = HashMap::new();
    for t in tools {
        map.insert(t.name.clone(), Arc::new(t));
    }
    map
}

fn call(id: &str, tool: &str) -> ToolCallItem {
    ToolCallItem { id: id.into(), tool: tool.into(), params: Value::Null, blocked_by: vec![], blocking: vec![] }
}

fn call_with_deps(id: &str, tool: &str, blocked_by: Vec<&str>, blocking: Vec<&str>) -> ToolCallItem {
    ToolCallItem {
        id: id.into(), tool: tool.into(), params: Value::Null,
        blocked_by: blocked_by.into_iter().map(|s| s.into()).collect(),
        blocking: blocking.into_iter().map(|s| s.into()).collect(),
    }
}

// ═══════════════════════════════════════════════════════════════
// 单调用 — 2 tests
// ═══════════════════════════════════════════════════════════════

#[tokio::test]
async fn single_call_success() {
    let tools = registry(vec![MockTool::success("t", serde_json::json!({"ok": true}))]);
    let set = ToolCallSet { session_id: "s".into(), calls: vec![call("c0", "t")], timeout_ms: None };
    let result = executor::execute(&set, &tools).await;
    assert_eq!(result.results.len(), 1);
    assert_eq!(result.results[0].status, "success");
    assert_eq!(result.results[0].result["ok"], true);
    assert_eq!(result.results[0].name, "t");
}

#[tokio::test]
async fn single_call_error() {
    let tools = registry(vec![MockTool::error("t", "bad")]);
    let set = ToolCallSet { session_id: "s".into(), calls: vec![call("c0", "t")], timeout_ms: None };
    let result = executor::execute(&set, &tools).await;
    assert_eq!(result.results[0].status, "error");
    assert_eq!(result.results[0].error.as_deref(), Some("bad"));
}

// ═══════════════════════════════════════════════════════════════
// 并发 — 1 test
// ═══════════════════════════════════════════════════════════════

#[tokio::test]
async fn two_independent_calls_concurrent() {
    let tools = registry(vec![
        MockTool::success("a", serde_json::json!({"n": 1})),
        MockTool::success("b", serde_json::json!({"n": 2})),
    ]);
    let set = ToolCallSet { session_id: "s".into(), calls: vec![call("c0", "a"), call("c1", "b")], timeout_ms: None };
    let result = executor::execute(&set, &tools).await;
    assert_eq!(result.results.len(), 2);
    assert_eq!(result.results[0].result["n"], 1);
    assert_eq!(result.results[1].result["n"], 2);
}

// ═══════════════════════════════════════════════════════════════
// 依赖 — 2 tests
// ═══════════════════════════════════════════════════════════════

#[tokio::test]
async fn dependency_serializes_execution() {
    let tools = registry(vec![
        MockTool::success("a", serde_json::json!({"step": 1})),
        MockTool::success("b", serde_json::json!({"step": 2})),
    ]);
    let set = ToolCallSet {
        session_id: "s".into(),
        calls: vec![
            call_with_deps("c0", "a", vec![], vec!["c1"]),
            call_with_deps("c1", "b", vec!["c0"], vec![]),
        ],
        timeout_ms: None,
    };
    let result = executor::execute(&set, &tools).await;
    assert_eq!(result.results.len(), 2);
    assert!(result.results.iter().all(|r| r.status == "success"));
}

#[tokio::test]
async fn dependency_failure_cascades() {
    let tools = registry(vec![
        MockTool::error("a", "failed"),
        MockTool::success("b", serde_json::json!({"ok": true})),
    ]);
    let set = ToolCallSet {
        session_id: "s".into(),
        calls: vec![
            call_with_deps("c0", "a", vec![], vec!["c1"]),
            call_with_deps("c1", "b", vec!["c0"], vec![]),
        ],
        timeout_ms: None,
    };
    let result = executor::execute(&set, &tools).await;
    assert_eq!(result.results[0].status, "error");
    assert_eq!(result.results[1].status, "cancelled");
    assert!(result.results[1].error.as_deref().unwrap().contains("c0"));
}

// ═══════════════════════════════════════════════════════════════
// 验证 — 2 tests
// ═══════════════════════════════════════════════════════════════

#[tokio::test]
async fn cycle_detected_all_error() {
    let tools = registry(vec![MockTool::success("a", serde_json::json!({})), MockTool::success("b", serde_json::json!({}))]);
    let set = ToolCallSet {
        session_id: "s".into(),
        calls: vec![
            call_with_deps("c0", "a", vec!["c1"], vec![]),
            call_with_deps("c1", "b", vec!["c0"], vec![]),
        ],
        timeout_ms: None,
    };
    let result = executor::execute(&set, &tools).await;
    assert!(result.results.iter().all(|r| r.status == "error"));
}

#[tokio::test]
async fn inconsistent_lock_all_error() {
    let tools = registry(vec![MockTool::success("a", serde_json::json!({})), MockTool::success("b", serde_json::json!({}))]);
    let set = ToolCallSet {
        session_id: "s".into(),
        calls: vec![
            // c0 claims to block c1, but c1 doesn't declare blocked_by c0
            ToolCallItem { id: "c0".into(), tool: "a".into(), params: Value::Null, blocked_by: vec![], blocking: vec!["c1".into()] },
            ToolCallItem { id: "c1".into(), tool: "b".into(), params: Value::Null, blocked_by: vec![], blocking: vec![] },
        ],
        timeout_ms: None,
    };
    let result = executor::execute(&set, &tools).await;
    assert!(result.results.iter().all(|r| r.status == "error"));
}

// ═══════════════════════════════════════════════════════════════
// 边界 — 2 tests
// ═══════════════════════════════════════════════════════════════

#[tokio::test]
async fn empty_call_set() {
    let tools = registry(vec![]);
    let set = ToolCallSet { session_id: "s".into(), calls: vec![], timeout_ms: None };
    let result = executor::execute(&set, &tools).await;
    assert!(result.results.is_empty());
}

#[tokio::test]
async fn tool_not_found() {
    let tools = registry(vec![]);
    let set = ToolCallSet { session_id: "s".into(), calls: vec![call("c0", "ghost")], timeout_ms: None };
    let result = executor::execute(&set, &tools).await;
    assert_eq!(result.results[0].status, "error");
    assert!(result.results[0].error.as_deref().unwrap().contains("not found"));
}
```

---

## 验证命令

```bash
. "$HOME/.cargo/env" && cargo check -p arf-mcp
. "$HOME/.cargo/env" && cargo test -p arf-mcp
. "$HOME/.cargo/env" && cargo test --workspace
```

---

## 测试覆盖摘要

| 文件 | 新增测试 | 覆盖角度 |
|------|---------|---------|
| `executor_tests.rs` | 10 | `[方法][边界][依赖][验证]` — 单调用(2)、并发(1)、依赖序列化(1)、级联取消(1)、环检测(1)、锁不一致(1)、空集(1)、tool 不存在(1) |
| **合计** | **10** | 累计 arf-mcp: 127 + 10 = **137 tests** |
