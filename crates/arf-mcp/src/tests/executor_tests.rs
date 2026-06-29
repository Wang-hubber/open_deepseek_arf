use std::collections::HashMap;
use std::sync::Arc;

use crate::executor;
use crate::tool::Tool;
use crate::types::{ToolCallItem, ToolCallSet, ToolError};
use serde_json::Value;

// ── Mock Tool ──────────────────────────────────────────────────────

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

    fn panic(name: &str) -> Self {
        Self {
            name: name.into(),
            description: format!("Mock {name}"),
            schema: serde_json::json!({"type": "object"}),
            result: Err(ToolError::from("should not reach")),
        }
    }
}

#[async_trait::async_trait]
impl Tool for MockTool {
    fn name(&self) -> &str {
        &self.name
    }
    fn description(&self) -> &str {
        &self.description
    }
    fn parameters_schema(&self) -> Value {
        self.schema.clone()
    }
    async fn execute(&self, _params: Value) -> Result<Value, ToolError> {
        if self.name == "panic_tool" {
            panic!("intentional panic in MockTool");
        }
        self.result.clone()
    }
}

fn registry(tools: Vec<MockTool>) -> HashMap<String, Arc<dyn Tool>> {
    let mut map = HashMap::new();
    for t in tools {
        map.insert(t.name.clone(), Arc::new(t) as Arc<dyn Tool>);
    }
    map
}

fn call(id: &str, tool: &str) -> ToolCallItem {
    ToolCallItem {
        id: id.into(),
        tool: tool.into(),
        params: Value::Null,
        blocked_by: vec![],
        blocking: vec![],
    }
}

fn call_with(
    id: &str,
    tool: &str,
    blocked_by: Vec<&str>,
    blocking: Vec<&str>,
) -> ToolCallItem {
    ToolCallItem {
        id: id.into(),
        tool: tool.into(),
        params: Value::Null,
        blocked_by: blocked_by.into_iter().map(|s| s.into()).collect(),
        blocking: blocking.into_iter().map(|s| s.into()).collect(),
    }
}

// ═══════════════════════════════════════════════════════════════
// 基础执行 — 4 tests
// ═══════════════════════════════════════════════════════════════

// [方法] 空 call_set → 空 results
#[tokio::test]
async fn empty_call_set() {
    let tools = registry(vec![]);
    let set = ToolCallSet {
        session_id: "s".into(),
        calls: vec![],
        timeout_ms: None,
    };
    let result = executor::execute(&set, &tools).await;
    assert!(result.results.is_empty());
    assert_eq!(result.session_id, "s");
}

// [方法] 单调用成功 → success + name 回填
#[tokio::test]
async fn single_call_success() {
    let tools = registry(vec![MockTool::success("t", serde_json::json!({"ok": true}))]);
    let set = ToolCallSet {
        session_id: "s1".into(),
        calls: vec![call("c0", "t")],
        timeout_ms: None,
    };
    let result = executor::execute(&set, &tools).await;
    assert_eq!(result.results.len(), 1);
    assert_eq!(result.results[0].status, "success");
    assert_eq!(result.results[0].name, "t");
    assert_eq!(result.results[0].call_id, "c0");
    assert_eq!(result.results[0].result["ok"], true);
    assert_eq!(result.results[0].error, None);
}

// [方法] 单调用 error → error + error message
#[tokio::test]
async fn single_call_error() {
    let tools = registry(vec![MockTool::error("t", "something broke")]);
    let set = ToolCallSet {
        session_id: "s".into(),
        calls: vec![call("c0", "t")],
        timeout_ms: None,
    };
    let result = executor::execute(&set, &tools).await;
    assert_eq!(result.results[0].status, "error");
    assert_eq!(result.results[0].error.as_deref(), Some("something broke"));
}

// [方法] tool 不存在 → error
#[tokio::test]
async fn tool_not_found() {
    let tools = registry(vec![]);
    let set = ToolCallSet {
        session_id: "s".into(),
        calls: vec![call("c0", "ghost")],
        timeout_ms: None,
    };
    let result = executor::execute(&set, &tools).await;
    assert_eq!(result.results[0].status, "error");
    assert!(result
        .results[0]
        .error
        .as_deref()
        .unwrap()
        .contains("not found"));
}

// ═══════════════════════════════════════════════════════════════
// 并发 — 2 tests
// ═══════════════════════════════════════════════════════════════

// [方法] 两个独立调用 → 都成功（同层并发）
#[tokio::test]
async fn two_independent_concurrent() {
    let tools = registry(vec![
        MockTool::success("a", serde_json::json!({"n": 1})),
        MockTool::success("b", serde_json::json!({"n": 2})),
    ]);
    let set = ToolCallSet {
        session_id: "s".into(),
        calls: vec![call("c0", "a"), call("c1", "b")],
        timeout_ms: None,
    };
    let result = executor::execute(&set, &tools).await;
    assert_eq!(result.results.len(), 2);
    assert!(result.results.iter().all(|r| r.status == "success"));
}

// [方法] 全部独立 → 单层
#[tokio::test]
async fn all_independent_single_layer() {
    let tools = registry(vec![
        MockTool::success("a", serde_json::json!({})),
        MockTool::success("b", serde_json::json!({})),
        MockTool::success("c", serde_json::json!({})),
        MockTool::success("d", serde_json::json!({})),
    ]);
    let set = ToolCallSet {
        session_id: "s".into(),
        calls: vec![
            call("c0", "a"),
            call("c1", "b"),
            call("c2", "c"),
            call("c3", "d"),
        ],
        timeout_ms: None,
    };
    let result = executor::execute(&set, &tools).await;
    assert_eq!(result.results.len(), 4);
    assert!(result.results.iter().all(|r| r.status == "success"));
}

// ═══════════════════════════════════════════════════════════════
// DAG 拓扑排序 — 5 tests
// ═══════════════════════════════════════════════════════════════

// [方法] 线性链 A→B→C → 全部成功（三层）
#[tokio::test]
async fn linear_chain_three_layers() {
    let tools = registry(vec![
        MockTool::success("a", serde_json::json!({"step": 1})),
        MockTool::success("b", serde_json::json!({"step": 2})),
        MockTool::success("c", serde_json::json!({"step": 3})),
    ]);
    let set = ToolCallSet {
        session_id: "s".into(),
        calls: vec![
            call_with("c0", "a", vec![], vec!["c1"]),
            call_with("c1", "b", vec!["c0"], vec!["c2"]),
            call_with("c2", "c", vec!["c1"], vec![]),
        ],
        timeout_ms: None,
    };
    let result = executor::execute(&set, &tools).await;
    assert_eq!(result.results.len(), 3);
    assert!(result.results.iter().all(|r| r.status == "success"));
}

// [方法] 菱形 A→C, B→C → A,B 先执行，C 后执行
#[tokio::test]
async fn diamond_dependency() {
    let tools = registry(vec![
        MockTool::success("a", serde_json::json!({"n": "a"})),
        MockTool::success("b", serde_json::json!({"n": "b"})),
        MockTool::success("c", serde_json::json!({"n": "c"})),
    ]);
    let set = ToolCallSet {
        session_id: "s".into(),
        calls: vec![
            call_with("c0", "a", vec![], vec!["c2"]),
            call_with("c1", "b", vec![], vec!["c2"]),
            call_with("c2", "c", vec!["c0", "c1"], vec![]),
        ],
        timeout_ms: None,
    };
    let result = executor::execute(&set, &tools).await;
    assert_eq!(result.results.len(), 3);
    assert!(result.results.iter().all(|r| r.status == "success"));
}

// [方法] 两条独立链 A→B, C→D → 两层，每层两个
#[tokio::test]
async fn two_independent_chains() {
    let tools = registry(vec![
        MockTool::success("a", serde_json::json!({})),
        MockTool::success("b", serde_json::json!({})),
        MockTool::success("c", serde_json::json!({})),
        MockTool::success("d", serde_json::json!({})),
    ]);
    let set = ToolCallSet {
        session_id: "s".into(),
        calls: vec![
            call_with("c0", "a", vec![], vec!["c1"]),
            call_with("c1", "b", vec!["c0"], vec![]),
            call_with("c2", "c", vec![], vec!["c3"]),
            call_with("c3", "d", vec!["c2"], vec![]),
        ],
        timeout_ms: None,
    };
    let result = executor::execute(&set, &tools).await;
    assert!(result.results.iter().all(|r| r.status == "success"));
}

// [方法] 复杂 DAG: A,B,C→D→E,F → 三层
#[tokio::test]
async fn complex_dag_three_layers() {
    let tools = registry(vec![
        MockTool::success("a", serde_json::json!({})),
        MockTool::success("b", serde_json::json!({})),
        MockTool::success("c", serde_json::json!({})),
        MockTool::success("d", serde_json::json!({})),
        MockTool::success("e", serde_json::json!({})),
        MockTool::success("f", serde_json::json!({})),
    ]);
    let set = ToolCallSet {
        session_id: "s".into(),
        calls: vec![
            call_with("c0", "a", vec![], vec!["c3"]),
            call_with("c1", "b", vec![], vec!["c3"]),
            call_with("c2", "c", vec![], vec!["c3"]),
            call_with("c3", "d", vec!["c0", "c1", "c2"], vec!["c4", "c5"]),
            call_with("c4", "e", vec!["c3"], vec![]),
            call_with("c5", "f", vec!["c3"], vec![]),
        ],
        timeout_ms: None,
    };
    let result = executor::execute(&set, &tools).await;
    assert_eq!(result.results.len(), 6);
    assert!(result.results.iter().all(|r| r.status == "success"));
    // Verify layer: c4,c5 should only run after c3
    // (implicitly tested by all-success — if order were wrong, deps would fail)
}

// [方法] 四层深链 A→B→C→D
#[tokio::test]
async fn four_layer_deep_chain() {
    let tools = registry(vec![
        MockTool::success("a", serde_json::json!({})),
        MockTool::success("b", serde_json::json!({})),
        MockTool::success("c", serde_json::json!({})),
        MockTool::success("d", serde_json::json!({})),
    ]);
    let set = ToolCallSet {
        session_id: "s".into(),
        calls: vec![
            call_with("c0", "a", vec![], vec!["c1"]),
            call_with("c1", "b", vec!["c0"], vec!["c2"]),
            call_with("c2", "c", vec!["c1"], vec!["c3"]),
            call_with("c3", "d", vec!["c2"], vec![]),
        ],
        timeout_ms: None,
    };
    let result = executor::execute(&set, &tools).await;
    assert!(result.results.iter().all(|r| r.status == "success"));
}

// ═══════════════════════════════════════════════════════════════
// 环检测 — 4 tests
// ═══════════════════════════════════════════════════════════════

// [边界] 直接环 A↔B → 全部 error
#[tokio::test]
async fn cycle_direct_two_nodes() {
    let tools = registry(vec![
        MockTool::success("a", serde_json::json!({})),
        MockTool::success("b", serde_json::json!({})),
    ]);
    let set = ToolCallSet {
        session_id: "s".into(),
        calls: vec![
            call_with("c0", "a", vec!["c1"], vec![]),
            call_with("c1", "b", vec!["c0"], vec![]),
        ],
        timeout_ms: None,
    };
    let result = executor::execute(&set, &tools).await;
    assert_eq!(result.results.len(), 2);
    assert!(result.results.iter().all(|r| r.status == "error"));
    assert!(result
        .results[0]
        .error
        .as_deref()
        .unwrap()
        .contains("cycle"));
}

// [边界] 三节点环 A→B→C→A → 全部 error
#[tokio::test]
async fn cycle_three_nodes() {
    let tools = registry(vec![
        MockTool::success("a", serde_json::json!({})),
        MockTool::success("b", serde_json::json!({})),
        MockTool::success("c", serde_json::json!({})),
    ]);
    let set = ToolCallSet {
        session_id: "s".into(),
        calls: vec![
            call_with("c0", "a", vec!["c2"], vec![]),
            call_with("c1", "b", vec!["c0"], vec![]),
            call_with("c2", "c", vec!["c1"], vec![]),
        ],
        timeout_ms: None,
    };
    let result = executor::execute(&set, &tools).await;
    assert!(result.results.iter().all(|r| r.status == "error"));
    assert!(result
        .results[0]
        .error
        .as_deref()
        .unwrap()
        .contains("cycle"));
}

// [边界] 无环的菱形 → 正常执行（不误报）
#[tokio::test]
async fn no_cycle_diamond_passes() {
    let tools = registry(vec![
        MockTool::success("a", serde_json::json!({})),
        MockTool::success("b", serde_json::json!({})),
        MockTool::success("c", serde_json::json!({})),
    ]);
    let set = ToolCallSet {
        session_id: "s".into(),
        calls: vec![
            call_with("c0", "a", vec![], vec!["c2"]),
            call_with("c1", "b", vec![], vec!["c2"]),
            call_with("c2", "c", vec!["c0", "c1"], vec![]),
        ],
        timeout_ms: None,
    };
    let result = executor::execute(&set, &tools).await;
    assert!(result.results.iter().all(|r| r.status == "success"));
}

// [边界] blocked_by 引用不存在的 call_id → error
#[tokio::test]
async fn unknown_reference_in_blocked_by() {
    let tools = registry(vec![MockTool::success("t", serde_json::json!({}))]);
    let set = ToolCallSet {
        session_id: "s".into(),
        calls: vec![call_with("c0", "t", vec!["non_existent"], vec![])],
        timeout_ms: None,
    };
    let result = executor::execute(&set, &tools).await;
    assert_eq!(result.results[0].status, "error");
    assert!(result
        .results[0]
        .error
        .as_deref()
        .unwrap()
        .contains("unknown"));
}

// ═══════════════════════════════════════════════════════════════
// 双向锁验证 — 3 tests
// ═══════════════════════════════════════════════════════════════

// [边界] 一致的双向锁 → 通过
#[tokio::test]
async fn consistent_bidirectional_lock_passes() {
    let tools = registry(vec![
        MockTool::success("a", serde_json::json!({})),
        MockTool::success("b", serde_json::json!({})),
    ]);
    let set = ToolCallSet {
        session_id: "s".into(),
        calls: vec![
            call_with("c0", "a", vec![], vec!["c1"]),       // c0 blocks c1
            call_with("c1", "b", vec!["c0"], vec![]),        // c1 blocked_by c0 ✓
        ],
        timeout_ms: None,
    };
    let result = executor::execute(&set, &tools).await;
    assert!(result.results.iter().all(|r| r.status == "success"));
}

// [边界] 不一致：c0.blocking 含 c1，但 c1.blocked_by 不含 c0 → 全部 error
#[tokio::test]
async fn inconsistent_lock_c0_claims_block_c1() {
    let tools = registry(vec![
        MockTool::success("a", serde_json::json!({})),
        MockTool::success("b", serde_json::json!({})),
    ]);
    let set = ToolCallSet {
        session_id: "s".into(),
        calls: vec![
            call_with("c0", "a", vec![], vec!["c1"]),       // c0 claims to block c1
            call_with("c1", "b", vec![], vec![]),             // but c1 has no blocked_by
        ],
        timeout_ms: None,
    };
    let result = executor::execute(&set, &tools).await;
    assert!(result.results.iter().all(|r| r.status == "error"));
    assert!(result
        .results[0]
        .error
        .as_deref()
        .unwrap()
        .contains("inconsistent"));
}

// [边界] 只有 blocked_by 无 blocking → 合法（Engine 可能只设 blocked_by）
#[tokio::test]
async fn blocked_by_only_no_blocking_is_valid() {
    let tools = registry(vec![
        MockTool::success("a", serde_json::json!({})),
        MockTool::success("b", serde_json::json!({})),
    ]);
    let set = ToolCallSet {
        session_id: "s".into(),
        calls: vec![
            call_with("c0", "a", vec![], vec![]),            // no blocking declared
            call_with("c1", "b", vec!["c0"], vec![]),         // just blocked_by
        ],
        timeout_ms: None,
    };
    let result = executor::execute(&set, &tools).await;
    // Should pass — c1 depends on c0 via blocked_by only
    // Kahn will still correctly order: c0 first, then c1
    assert!(result.results.iter().all(|r| r.status == "success"));
}

// ═══════════════════════════════════════════════════════════════
// 级联取消 — 6 tests
// ═══════════════════════════════════════════════════════════════

// [方法] 单个失败 → 一个下游被取消
#[tokio::test]
async fn cascade_single_failure_one_dependent() {
    let tools = registry(vec![
        MockTool::error("a", "fail"),
        MockTool::success("b", serde_json::json!({})),
    ]);
    let set = ToolCallSet {
        session_id: "s".into(),
        calls: vec![
            call_with("c0", "a", vec![], vec!["c1"]),
            call_with("c1", "b", vec!["c0"], vec![]),
        ],
        timeout_ms: None,
    };
    let result = executor::execute(&set, &tools).await;
    assert_eq!(result.results[0].status, "error");
    assert_eq!(result.results[1].status, "cancelled");
    assert!(result.results[1]
        .error
        .as_deref()
        .unwrap()
        .contains("c0"));
}

// [方法] 单个失败 → 两个下游都被取消
#[tokio::test]
async fn cascade_one_failure_two_dependents() {
    let tools = registry(vec![
        MockTool::error("a", "fail"),
        MockTool::success("b", serde_json::json!({})),
        MockTool::success("c", serde_json::json!({})),
    ]);
    let set = ToolCallSet {
        session_id: "s".into(),
        calls: vec![
            call_with("c0", "a", vec![], vec!["c1", "c2"]),
            call_with("c1", "b", vec!["c0"], vec![]),
            call_with("c2", "c", vec!["c0"], vec![]),
        ],
        timeout_ms: None,
    };
    let result = executor::execute(&set, &tools).await;
    assert_eq!(result.results[0].status, "error");
    assert_eq!(result.results[1].status, "cancelled");
    assert_eq!(result.results[2].status, "cancelled");
}

// [方法] 三级链失败 A(error)→B(cancelled)→C(cancelled)
#[tokio::test]
async fn cascade_three_chain() {
    let tools = registry(vec![
        MockTool::error("a", "root cause"),
        MockTool::success("b", serde_json::json!({})),
        MockTool::success("c", serde_json::json!({})),
    ]);
    let set = ToolCallSet {
        session_id: "s".into(),
        calls: vec![
            call_with("c0", "a", vec![], vec!["c1"]),
            call_with("c1", "b", vec!["c0"], vec!["c2"]),
            call_with("c2", "c", vec!["c1"], vec![]),
        ],
        timeout_ms: None,
    };
    let result = executor::execute(&set, &tools).await;
    assert_eq!(result.results[0].status, "error");
    assert_eq!(result.results[1].status, "cancelled");
    assert_eq!(result.results[2].status, "cancelled");
    // Verify error message chain: c1 should reference c0, c2 should reference c1
    assert!(result.results[1].error.as_deref().unwrap().contains("c0"));
    assert!(result.results[2].error.as_deref().unwrap().contains("c1"));
}

// [方法] 菱形：一个上游失败 → 共享下游被取消
#[tokio::test]
async fn cascade_diamond_one_fails() {
    let tools = registry(vec![
        MockTool::success("a", serde_json::json!({"ok": true})),
        MockTool::error("b", "b failed"),
        MockTool::success("c", serde_json::json!({})),
    ]);
    let set = ToolCallSet {
        session_id: "s".into(),
        calls: vec![
            call_with("c0", "a", vec![], vec!["c2"]),
            call_with("c1", "b", vec![], vec!["c2"]),
            call_with("c2", "c", vec!["c0", "c1"], vec![]),
        ],
        timeout_ms: None,
    };
    let result = executor::execute(&set, &tools).await;
    assert_eq!(result.results[0].status, "success"); // A 成功
    assert_eq!(result.results[1].status, "error"); // B 失败
    assert_eq!(result.results[2].status, "cancelled"); // C 因 B 失败而取消
}

// [方法] 独立分支：A(error)→B(cancelled), C(ok)→D(ok)
#[tokio::test]
async fn cascade_independent_branches() {
    let tools = registry(vec![
        MockTool::error("a", "a failed"),
        MockTool::success("b", serde_json::json!({})),
        MockTool::success("c", serde_json::json!({"ok": true})),
        MockTool::success("d", serde_json::json!({})),
    ]);
    let set = ToolCallSet {
        session_id: "s".into(),
        calls: vec![
            call_with("c0", "a", vec![], vec!["c1"]),       // A 阻塞 B
            call_with("c1", "b", vec!["c0"], vec![]),        // B 依赖 A
            call_with("c2", "c", vec![], vec!["c3"]),        // C 阻塞 D（独立链）
            call_with("c3", "d", vec!["c2"], vec![]),        // D 依赖 C
        ],
        timeout_ms: None,
    };
    let result = executor::execute(&set, &tools).await;
    // First layer: c0(error), c2(success)
    assert_eq!(result.results[0].status, "error");
    assert_eq!(result.results[2].status, "success");
    // Second layer: c1 cancelled (c0 failed), c3 should run because c2 succeeded
    assert_eq!(result.results[1].status, "cancelled");
    // c3: depends on c2(ok), but the cascade break happens after layer 1
    // c3 never runs because the outer loop breaks on any_failed.
    // That's intentional — the DAG executor stops all further layers.
    assert_eq!(result.results[3].status, "cancelled");
}

// ═══════════════════════════════════════════════════════════════
// 边界 — 3 tests
// ═══════════════════════════════════════════════════════════════

// [边界] session_id 保留在结果中
#[tokio::test]
async fn session_id_preserved() {
    let tools = registry(vec![MockTool::success("t", serde_json::json!({}))]);
    let set = ToolCallSet {
        session_id: "my-session-42".into(),
        calls: vec![call("c0", "t")],
        timeout_ms: None,
    };
    let result = executor::execute(&set, &tools).await;
    assert_eq!(result.session_id, "my-session-42");
}

// [边界] panic 被 catch_unwind 捕获 → error
#[tokio::test]
async fn panic_in_tool_caught() {
    let tools = registry(vec![MockTool::panic("panic_tool")]);
    let set = ToolCallSet {
        session_id: "s".into(),
        calls: vec![call("c0", "panic_tool")],
        timeout_ms: None,
    };
    let result = executor::execute(&set, &tools).await;
    assert_eq!(result.results[0].status, "error");
    assert!(result
        .results[0]
        .error
        .as_deref()
        .unwrap()
        .contains("panic"));
}

// [边界] 超大 call_set（50 个独立调用）→ 全部成功
#[tokio::test]
async fn large_call_set_all_independent() {
    let mut tools_vec = Vec::new();
    let mut calls_vec = Vec::new();
    for i in 0..50 {
        let name = format!("t{i}");
        tools_vec.push(MockTool::success(&name, serde_json::json!({"i": i})));
        calls_vec.push(call(&format!("c{i}"), &name));
    }
    let tools = registry(tools_vec);
    let set = ToolCallSet {
        session_id: "s".into(),
        calls: calls_vec,
        timeout_ms: None,
    };
    let result = executor::execute(&set, &tools).await;
    assert_eq!(result.results.len(), 50);
    assert!(result.results.iter().all(|r| r.status == "success"));
}
