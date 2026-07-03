//! custom_runtime_module.rs — Phase 9 task 9.12.2
//!
//! 探查 app 实现自定义 `RuntimeModule` trait 的端到端能力。
//!
//! 4 test cases：
//! 1. custom_runtime_override_execute_strategy — CountingRuntime 完全 override execute()（不调 executor::execute）
//! 2. custom_runtime_override_run_single — LoggingRuntime override run_single()（pre/post 钩子）
//! 3. custom_runtime_capabilities_only — CustomCapRuntime 只 override capabilities()，execute/run_single 留默认
//! 4. custom_runtime_in_mcp_node_end_to_end — CountingRuntime 经 local_with_runtime 注入 McpNode + bus + tool_exec → 端到端 work

mod common;

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use std::time::Duration;

use arf_mcp::McpNode;
use arf_mcp::discovery::{DiscoveryBackend, FsDiscovery, ToolInfo};
use arf_mcp::runtime::RuntimeModule;
use arf_mcp::tool::Tool;
use arf_mcp::types::{ToolCallItem, ToolCallSet, ToolError, ToolResultItem, ToolResultSet};
use arf_bus::Bus;
use arf_core::{MessageFilter, NodeId, NodeInfo, ToMatch};
use async_trait::async_trait;
use serde_json::{json, Value};

// ═══════════════════════════════════════════════════════════════════════
// TestTool — minimal Tool impl
// ═══════════════════════════════════════════════════════════════════════

struct TestTool {
    name: String,
}

#[async_trait]
impl Tool for TestTool {
    fn name(&self) -> &str { &self.name }
    fn description(&self) -> &str { "Test tool" }
    fn parameters_schema(&self) -> Value { json!({"type": "object"}) }
    async fn execute(&self, params: Value) -> Result<Value, ToolError> {
        Ok(json!({"tool": self.name, "params": params}))
    }
}

// Helper — build tool map
fn build_tool_map(tools: Vec<Arc<dyn Tool>>) -> (Vec<ToolInfo>, HashMap<String, Arc<dyn Tool>>) {
    let mut info = Vec::new();
    let mut map = HashMap::new();
    for t in &tools {
        info.push(ToolInfo {
            name: t.name().to_string(),
            description: t.description().to_string(),
            parameters_schema: t.parameters_schema(),
        });
        map.insert(t.name().to_string(), t.clone());
    }
    (info, map)
}

// ═══════════════════════════════════════════════════════════════════════
// CountingRuntime — override execute() 串行执行（不调 executor::execute）
// ═══════════════════════════════════════════════════════════════════════

struct CountingRuntime {
    call_count: AtomicUsize,
}

impl CountingRuntime {
    fn new() -> Self { Self { call_count: AtomicUsize::new(0) } }
    fn count(&self) -> usize { self.call_count.load(Ordering::SeqCst) }
}

#[async_trait]
impl RuntimeModule for CountingRuntime {
    fn capabilities(&self) -> Value {
        json!({"runtime": "counting", "mode": "sequential-override"})
    }

    async fn execute(
        &self,
        call_set: &ToolCallSet,
        tools: &HashMap<String, Arc<dyn Tool>>,
    ) -> ToolResultSet {
        self.call_count.fetch_add(1, Ordering::SeqCst);
        let mut results = Vec::new();
        for call in &call_set.calls {
            let result = match tools.get(&call.tool) {
                Some(tool) => match tool.execute(call.params.clone()).await {
                    Ok(val) => ToolResultItem {
                        call_id: call.id.clone(),
                        name: call.tool.clone(),
                        status: "success".into(),
                        result: val,
                        error: None,
                    },
                    Err(e) => ToolResultItem {
                        call_id: call.id.clone(),
                        name: call.tool.clone(),
                        status: "error".into(),
                        result: Value::Null,
                        error: Some(e.message),
                    },
                },
                None => ToolResultItem {
                    call_id: call.id.clone(),
                    name: call.tool.clone(),
                    status: "error".into(),
                    result: Value::Null,
                    error: Some(format!("tool not found: {}", call.tool)),
                },
            };
            results.push(result);
        }
        ToolResultSet {
            session_id: call_set.session_id.clone(),
            results,
        }
    }
}

// ═══════════════════════════════════════════════════════════════════════
// LoggingRuntime — override run_single() 加 pre/post 钩子
// ═══════════════════════════════════════════════════════════════════════

struct LoggingRuntime {
    pre_count: AtomicUsize,
    post_count: AtomicUsize,
}

impl LoggingRuntime {
    fn new() -> Self {
        Self { pre_count: AtomicUsize::new(0), post_count: AtomicUsize::new(0) }
    }
}

#[async_trait]
impl RuntimeModule for LoggingRuntime {
    fn capabilities(&self) -> Value {
        json!({"runtime": "logging"})
    }

    async fn run_single(
        &self,
        call_id: &str,
        tool: &dyn Tool,
        params: Value,
    ) -> (String, Value, Option<String>) {
        self.pre_count.fetch_add(1, Ordering::SeqCst);
        let (status, result, error) = match tool.execute(params).await {
            Ok(val) => ("success".into(), val, None),
            Err(e) => ("error".into(), Value::Null, Some(e.message)),
        };
        self.post_count.fetch_add(1, Ordering::SeqCst);
        println!("[logging_runtime] call_id={call_id} status={status}");
        (status, result, error)
    }
}

// ═══════════════════════════════════════════════════════════════════════
// CustomCapRuntime — 只 override capabilities()
// ═══════════════════════════════════════════════════════════════════════

struct CustomCapRuntime;

#[async_trait]
impl RuntimeModule for CustomCapRuntime {
    fn capabilities(&self) -> Value {
        json!({"runtime": "custom-cap", "extra": "field"})
    }
}

// ═══════════════════════════════════════════════════════════════════════
// Test 1 — custom_runtime_override_execute_strategy
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn custom_runtime_override_execute_strategy() {
    let runtime = Arc::new(CountingRuntime::new());
    let (_info, tool_map) = build_tool_map(vec![
        Arc::new(TestTool { name: "a".into() }) as Arc<dyn Tool>,
        Arc::new(TestTool { name: "b".into() }) as Arc<dyn Tool>,
    ]);

    // Build call set with 2 sequential calls (no blocked_by)
    let call_set = ToolCallSet {
        session_id: "s1".into(),
        calls: vec![
            ToolCallItem { id: "c1".into(), tool: "a".into(), params: json!({"x": 1}), blocked_by: vec![], blocking: vec![] },
            ToolCallItem { id: "c2".into(), tool: "b".into(), params: json!({"y": 2}), blocked_by: vec![], blocking: vec![] },
        ],
        timeout_ms: None,
    };

    let result = runtime.execute(&call_set, &tool_map).await;
    assert_eq!(result.results.len(), 2);
    assert_eq!(result.results[0].name, "a");
    assert_eq!(result.results[0].status, "success");
    assert_eq!(result.results[0].result["params"]["x"], json!(1));
    assert_eq!(result.results[1].name, "b");
    assert_eq!(result.results[1].status, "success");
    assert_eq!(result.results[1].result["params"]["y"], json!(2));
    assert_eq!(runtime.count(), 1, "execute() 调 1 次");

    // 调第 2 次
    let result2 = runtime.execute(&call_set, &tool_map).await;
    assert_eq!(result2.results.len(), 2);
    assert_eq!(runtime.count(), 2);

    println!("[test1] CountingRuntime override execute() 串行端到端 OK ✓");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 2 — custom_runtime_override_run_single
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn custom_runtime_override_run_single() {
    let runtime = LoggingRuntime::new();
    let tool = TestTool { name: "x".into() };

    let (status, result, error) = runtime.run_single("call-x", &tool, json!({"p": 1})).await;
    assert_eq!(status, "success");
    assert!(error.is_none());
    assert_eq!(result["tool"], json!("x"));
    assert_eq!(result["params"]["p"], json!(1));

    assert_eq!(runtime.pre_count.load(Ordering::SeqCst), 1);
    assert_eq!(runtime.post_count.load(Ordering::SeqCst), 1);

    println!("[test2] LoggingRuntime override run_single() pre/post 钩子 OK ✓");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 3 — custom_runtime_capabilities_only (走 default execute)
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn custom_runtime_capabilities_only() {
    let runtime = CustomCapRuntime;
    let caps = runtime.capabilities();
    assert_eq!(caps["runtime"], json!("custom-cap"));
    assert_eq!(caps["extra"], json!("field"));
    println!("[test3] CustomCapRuntime override capabilities() OK ✓");

    // execute() / run_single() 走默认（trait default impl）—— type-level test
    // 即 runtime 能调 execute 不会 panic
    let (_info, tool_map) = build_tool_map(vec![
        Arc::new(TestTool { name: "a".into() }) as Arc<dyn Tool>,
    ]);
    let call_set = ToolCallSet {
        session_id: "s".into(),
        calls: vec![ToolCallItem { id: "c".into(), tool: "a".into(), params: json!({}), blocked_by: vec![], blocking: vec![] }],
        timeout_ms: None,
    };
    let result = runtime.execute(&call_set, &tool_map).await;
    assert_eq!(result.results.len(), 1);
    assert_eq!(result.results[0].status, "success");
    println!("[test3] default execute() 调通 OK ✓");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 4 — CountingRuntime 注入 McpNode 端到端
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn custom_runtime_in_mcp_node_end_to_end() {
    // 写 1 个 tool.toml (走 FsDiscovery)
    let id = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let root = std::env::temp_dir().join(format!("arf_custom_rt_{id}"));
    let _ = std::fs::remove_dir_all(&root);
    std::fs::create_dir_all(&root).unwrap();
    let tool_dir = root.join("tools").join("echo");
    std::fs::create_dir_all(&tool_dir).unwrap();
    std::fs::write(
        tool_dir.join("tool.toml"),
        "name = \"echo\"\ndescription = \"Echo tool\"\nruntime = \"python\"\nentrypoint = \"main.py\"\n",
    ).unwrap();
    std::fs::write(
        tool_dir.join("main.py"),
        "import sys, json\nparams = json.loads(sys.stdin.read())\nprint(json.dumps(params))\n",
    ).unwrap();

    let runtime = Arc::new(CountingRuntime::new());
    let node = McpNode::local_with_runtime(
        "custom-rt",
        root.clone(),
        Box::new(CountingRuntime::new()),
    ).expect("McpNode::local_with_runtime");
    println!("[test4] McpNode::local_with_runtime 注入 CountingRuntime OK ✓");

    let bus = Arc::new(Bus::new(
        Duration::from_secs(30),
        Duration::from_secs(60),
        1024,
    ));

    // Subscribe BEFORE connect to ensure we see node_online
    let mut sub = bus.subscribe();

    // Connect tester for response routing
    let tester_info = NodeInfo {
        node_id: NodeId::new("tester"),
        node_type: "tester".into(),
        capabilities: json!({}),
        online_since: 0,
    };
    let _tester = bus.connect(tester_info, MessageFilter { types: None, to_match: ToMatch::All }).await.expect("tester connect");

    node.connect(&bus).await.expect("connect");
    tokio::time::sleep(Duration::from_millis(100)).await;
    println!("[test4] McpNode + bus connect OK ✓");

    // Verify capabilities include custom runtime field
    let mut found_online = false;
    let deadline = tokio::time::Instant::now() + Duration::from_secs(2);
    while tokio::time::Instant::now() < deadline {
        if let Ok(Ok(m)) = tokio::time::timeout(Duration::from_millis(200), sub.recv()).await {
            if m.msg_type == "node_online" && m.from.as_str() == "mcp/custom-rt" {
                // node_online structure: capabilities.runtime = self.runtime.capabilities()
                // CountingRuntime::capabilities() returns {"runtime":"counting","mode":"sequential-override"}
                println!("[test4] mcp node_online payload: {}", m.payload);
                let caps = &m.payload["capabilities"]["runtime"];
                assert_eq!(caps["runtime"], json!("counting"), "capabilities 应含自定义 runtime");
                assert_eq!(caps["mode"], json!("sequential-override"));
                println!("[test4] node_online capabilities.runtime = {caps} ✓");
                found_online = true;
                break;
            }
        }
    }
    assert!(found_online, "应收到 mcp node_online");

    // Send tool_exec
    let tool_exec = arf_core::Message::with_from_bus(
        "tool_exec",
        NodeId::new("tester"),
        vec![NodeId::new("mcp/custom-rt")],
        json!({
            "tool_name": "echo",
            "arguments": {"hello": "world"},
            "correlation_id": uuid::Uuid::new_v4().to_string(),
        }),
        bus.id,
    );
    bus.send(tool_exec).await.expect("send");

    let result = tokio::time::timeout(Duration::from_secs(5), async {
        loop {
            let m = sub.recv().await.expect("recv");
            if m.msg_type == "tool_result" && m.is_for(&NodeId::new("tester")) {
                return m;
            }
        }
    })
    .await
    .expect("timeout");

    println!("[test4] tool_result payload: {}", result.payload);
    assert_eq!(result.payload["ok"], json!(true));
    assert_eq!(result.payload["content"]["hello"], json!("world"));
    println!("[test4] 端到端 tool_exec → CountingRuntime::execute → ScriptTool OK ✓");

    let _ = std::fs::remove_dir_all(&root);
    let _ = node;
    let _ = runtime;
}
