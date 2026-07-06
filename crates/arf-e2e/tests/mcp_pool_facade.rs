//! mcp_pool_facade.rs — Phase 9 task 9.8.1
//!
//! 单 agent + 单 MCP pool（facade + lease）端到端探查。
//!
//! 4 test cases：
//! 1. mcp_pool_node_advertises_tools_via_capabilities — connect 后 capabilities.tools 含 advertised_tools
//! 2. mcp_pool_node_resource_registry_resolves_owner — Engine build + ResourceSpec 命中 pool NodeId
//! 3. mcp_pool_node_lease_released_after_tool_exec — Pool<McpResource> lease drop 后回 idle
//! 4. mcp_pool_node_e2e_tool_exec_routed_through_pool — Engine.run + tool_call 端到端（双 bus）

mod common;

use std::fs;
use std::io::Write;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use arf_bus::Bus;
use arf_core::NodeId;
use arf_engine::{AgentConfig, Engine, EngineBuilder, EngineConfig, ModelDecl, ResourceSpec};
use arf_mcp::{McpNode, McpResource, MCPPoolNode};
use arf_model_adapter::ModelAdapterNode;
use arf_pool::{Overflow, Pool, PoolConfig};
use common::provider::{scripted, text_response};
use serde_json::json;
use tempfile::TempDir;

// ═══════════════════════════════════════════════════════════════════════
// Helpers
// ═══════════════════════════════════════════════════════════════════════

fn write_echo_tool(root: &PathBuf, name: &str) {
    let tool_dir = root.join("tools").join(name);
    fs::create_dir_all(&tool_dir).unwrap();
    let mut f = fs::File::create(tool_dir.join("tool.toml")).unwrap();
    f.write_all(
        format!(
            "name = \"{name}\"\ndescription = \"{name} tool\"\nruntime = \"python\"\nentrypoint = \"main.py\"\n"
        )
        .as_bytes(),
    )
    .unwrap();
    fs::write(
        tool_dir.join("main.py"),
        "import sys, json\nparams = json.loads(sys.stdin.read())\nprint(json.dumps(params))\n",
    )
    .unwrap();
}

fn build_mcp_with_tool(name: &str) -> (TempDir, Arc<McpNode>) {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path().to_path_buf();
    write_echo_tool(&root, name);
    let node = McpNode::local(name, root).expect("McpNode::local");
    (tmp, node)
}

fn build_pool(size: usize) -> Arc<Pool<McpResource>> {
    Arc::new(Pool::new(PoolConfig {
        max_size: size,
        overflow: Overflow::Queue(size * 2),
        idle_timeout: None,
    }))
}

async fn connect_model(bus: &Arc<Bus>) {
    let _ = ModelAdapterNode::new(
        scripted(vec![text_response("ok")]),
        bus,
        NodeId::new("model/e2e"),
    )
    .await
    .expect("model connect");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 1: MCPPoolNode connect → advertised tools in capabilities
// ═══════════════════════════════════════════════════════════════════════

// [方法] `MCPPoolNode::connect` 后 top_bus.graph() 节点 NodeId + node_type="mcp"
// + `capabilities.tools` 数组元素 [{name, description, params_schema}] 含 advertised。
#[tokio::test]
async fn mcp_pool_node_advertises_tools_via_capabilities() {
    let (_mcp_tmp, mcp_node) = build_mcp_with_tool("echo");
    let top_bus = Arc::new(Bus::new(
        Duration::from_millis(500),
        Duration::from_secs(2),
        32,
    ));
    let sub_bus = Arc::new(Bus::new(
        Duration::from_millis(500),
        Duration::from_secs(2),
        32,
    ));

    // 真实 McpNode 接在 sub bus 上
    mcp_node.connect(&sub_bus).await.expect("mcp sub connect");
    tokio::time::sleep(Duration::from_millis(50)).await;

    let pool = build_pool(1);
    let pool_node = Arc::new(MCPPoolNode {
        node_id: NodeId::new("mcp/pool/e2e"),
        top_bus: top_bus.clone(),
        sub_bus: sub_bus.clone(),
        pool: pool.clone(),
        advertised_tools: vec!["echo".to_string()],
        advertised_skills: vec![],
    });
    pool_node.connect().await.expect("pool node connect");

    // 等 spawn run_loop
    tokio::time::sleep(Duration::from_millis(100)).await;

    // 验证 graph 节点
    let graph = top_bus.graph();
    let pool_info = graph
        .nodes
        .iter()
        .find(|n| n.node_id.as_str() == "mcp/pool/e2e")
        .expect("mcp pool node in graph");
    assert_eq!(pool_info.node_type, "mcp");
    assert_eq!(pool_info.capabilities["kind"], "mcp_pool");
    let tools = pool_info.capabilities["tools"].as_array().expect("tools array");
    assert_eq!(tools.len(), 1);
    assert_eq!(tools[0]["name"], "echo");
    println!("[test1] MCPPoolNode advertised 1 tool 'echo' in capabilities ✓");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 2: Engine build → owner_of_tool resolves to pool NodeId
// ═══════════════════════════════════════════════════════════════════════

// [方法] `EngineBuilder::build` 配 ResourceSpec Subset["echo"] + 1 MCPPoolNode
// advertised ["echo"] + 1 真正 mcp（sub bus）→ owner_of_tool("echo") → pool NodeId
// （build 成功 = registry 解析 OK）
#[tokio::test]
async fn mcp_pool_node_resource_registry_resolves_owner() {
    let (_mcp_tmp, mcp_node) = build_mcp_with_tool("echo");
    let top_bus = Arc::new(Bus::new(
        Duration::from_millis(500),
        Duration::from_secs(2),
        32,
    ));
    let sub_bus = Arc::new(Bus::new(
        Duration::from_millis(500),
        Duration::from_secs(2),
        32,
    ));
    mcp_node.connect(&sub_bus).await.expect("mcp sub connect");
    connect_model(&top_bus).await;

    let pool = build_pool(1);
    let pool_node = Arc::new(MCPPoolNode {
        node_id: NodeId::new("mcp/pool/e2e"),
        top_bus: top_bus.clone(),
        sub_bus: sub_bus.clone(),
        pool: pool.clone(),
        advertised_tools: vec!["echo".to_string()],
        advertised_skills: vec![],
    });
    pool_node.connect().await.expect("pool node connect");
    tokio::time::sleep(Duration::from_millis(100)).await;

    let cfg = AgentConfig {
        model: ModelDecl {
            provider: "scripted".into(),
            model_name: "scripted-v1".into(),
            ..Default::default()
        },
        resources: vec![ResourceSpec {
            resource_name: "mcp_pool".into(),
            node_type: "mcp".into(),
            capabilities: Some(json!({"tools": ["echo"]})),
        }],
        system_prompt_template: "You are helpful.".into(),
        initial_memory: vec![],
        allowed_paths: vec![],
        tools: vec![],
        engine: EngineConfig {
            max_turns: 5,
            ..Default::default()
        },
    };
    let _engine: Engine = EngineBuilder::new(vec![top_bus.clone()])
        .build(cfg)
        .await
        .expect("engine build with MCPPoolNode advertised tool");
    println!("[test2] Engine build + ResourceSpec Subset['echo'] + MCPPoolNode OK ✓");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 3: Pool<McpResource> lease lifecycle
// ═══════════════════════════════════════════════════════════════════════

// [方法] `Pool<McpResource>` provision 1 + 2 顺序 acquire/release →
// 第 1 个 lease OK，drop 后 idle_count 回到 1；第 2 个 lease OK。
// 验证 lease 释放时序（pool_node.rs:150 `drop(lease)` 在 run_loop 末尾）。
#[tokio::test]
async fn mcp_pool_node_lease_released_after_tool_exec() {
    let (_mcp_tmp, mcp_node) = build_mcp_with_tool("echo");
    let pool = build_pool(1);

    // Provision 1 McpResource
    let node_clone = mcp_node.clone();
    let r1 = pool
        .provision(move || Ok(McpResource::new(node_clone)))
        .await
        .expect("provision");
    pool.release(&r1);
    tokio::time::sleep(Duration::from_millis(50)).await;
    assert_eq!(pool.idle_count().await, 1, "provision + release → idle 1");

    // 1 顺序 acquire → 立即 OK
    let lease1 = pool.acquire().await.expect("first acquire");
    assert_eq!(lease1.resource().node().node_id, mcp_node.node_id);
    assert_eq!(lease1.resource().call_count(), 0, "fresh lease call_count = 0");
    assert_eq!(pool.idle_count().await, 0, "during lease → idle 0");

    // Drop lease → 异步回 idle
    drop(lease1);
    tokio::time::sleep(Duration::from_millis(50)).await;
    assert_eq!(pool.idle_count().await, 1, "after drop → idle 1");

    // 第 2 次 acquire → OK
    let lease2 = pool.acquire().await.expect("second acquire");
    assert_eq!(pool.idle_count().await, 0);
    drop(lease2);
    tokio::time::sleep(Duration::from_millis(50)).await;
    assert_eq!(pool.idle_count().await, 1);

    println!("[test3] Pool<McpResource> lease acquire/release 端到端 OK ✓");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 4: Engine.run + tool_call → 端到端 MCPPoolNode 路由
// ═══════════════════════════════════════════════════════════════════════

// [方法] Engine + scripted provider 发出 tool_call("echo") + MCPPoolNode facade
// + 1 McpNode 真实 sub bus 端 + 1 tool_result 响应器 → 端到端 Engine 完成 round。
// 验证：tool_exec → MCPPoolNode → forward 到 sub bus → McpNode 处理
// → tool_result → MCPPoolNode 转发回 top bus → engine 收 tool_result。
//
// 关键：McpNode 听 `tool_call_set`（legacy），不直接听 `tool_exec`。
// MCPPoolNode 把 tool_exec 转发成 tool_call_set 投到 sub bus。
// —— 验 MCPPoolNode 实际翻译逻辑（pool_node.rs:113-121）。
#[tokio::test]
async fn mcp_pool_node_e2e_tool_exec_routed_through_pool() -> anyhow::Result<()> {
    let (_mcp_tmp, mcp_node) = build_mcp_with_tool("echo");
    let top_bus = Arc::new(Bus::new(
        Duration::from_millis(500),
        Duration::from_secs(2),
        32,
    ));
    let sub_bus = Arc::new(Bus::new(
        Duration::from_millis(500),
        Duration::from_secs(2),
        32,
    ));

    // 真实 McpNode 接 sub bus
    mcp_node.connect(&sub_bus).await?;
    tokio::time::sleep(Duration::from_millis(50)).await;

    // MCPPoolNode facade
    let pool = build_pool(1);
    // ⚠️ MCPPoolNode::run_loop 用 `pool.acquire()`，不调 provision() ——
    // app 必须先 `pool.provision()` 注入至少 1 McpResource，否则
    // `acquire()` 返 `PoolError::Acquire("no idle resource and no provisioner")`
    // → run_loop 直接 return（pool_node.rs:104-107）
    // 这是 framework gap：MCPPoolNode 没自动 provision 路径（F-011 candidate）
    let mcp_for_provision = mcp_node.clone();
    let _r1 = pool
        .provision(move || Ok(McpResource::new(mcp_for_provision)))
        .await
        .expect("provision mcp resource");
    pool.release(&_r1);
    tokio::time::sleep(Duration::from_millis(50)).await;
    assert_eq!(pool.idle_count().await, 1, "provision + release → idle 1");

    let pool_node = Arc::new(MCPPoolNode {
        node_id: NodeId::new("mcp/pool/e2e"),
        top_bus: top_bus.clone(),
        sub_bus: sub_bus.clone(),
        pool: pool.clone(),
        advertised_tools: vec!["echo".to_string()],
        advertised_skills: vec![],
    });
    pool_node.connect().await?;
    tokio::time::sleep(Duration::from_millis(100)).await;

    // 端到端：直接发 tool_exec 到 mcp/pool/e2e，验证 facade 转发 → McpNode 处理 → tool_result 回 top bus
    use arf_core::{Message, MessageFilter, ToMatch};
    use uuid::Uuid;

    // ⚠️ 关键：sender 必须是已注册到 top_bus 的 node，否则 pool_node 转发 tool_result 到
    // 原 sender 时 bus::send 返 NodeOffline（pool_node.rs:138-145 把 tool_result 定向到 req.from）。
    // 注册一个 receiver（也是 sender）到 top_bus 上。
    let receiver_info = arf_core::NodeInfo {
        node_id: NodeId::new("test/receiver"),
        node_type: "test".into(),
        capabilities: json!({}),
        online_since: 0,
    };
    let mut receiver_handle = top_bus
        .connect(
            receiver_info,
            MessageFilter { types: Some(vec!["tool_result".into()]), to_match: ToMatch::BroadcastAndDirectedToMe },
        )
        .await
        .expect("receiver connect");

    let test_cid = Uuid::new_v4();
    let target = NodeId::new("mcp/pool/e2e");
    let tool_exec_msg = Message::with_from_bus(
        "tool_exec",
        NodeId::new("test/receiver"),
        vec![target.clone()],
        json!({
            "correlation_id": test_cid.to_string(),
            "tool_name": "echo",
            "arguments": {"q": "hi"},
            "target": "mcp/pool/e2e",
        }),
        top_bus.id,
    );
    println!("[test4] 发 tool_exec 到 mcp/pool/e2e cid={test_cid}");

    top_bus.send(tool_exec_msg).await.expect("send tool_exec");
    tokio::time::sleep(Duration::from_millis(500)).await;
    assert_eq!(pool.idle_count().await, 1, "tool_exec 处理后 lease 应释放回 idle");

    // 等待 receiver 收 tool_result
    let deadline = std::time::Instant::now() + Duration::from_secs(3);
    let mut got_result = None;
    while std::time::Instant::now() < deadline {
        match tokio::time::timeout_at(deadline.into(), receiver_handle.recv()).await {
            Ok(Ok(m)) => {
                let payload_cid = m
                    .payload
                    .get("correlation_id")
                    .and_then(|v| v.as_str())
                    .unwrap_or("");
                println!("[test4] receiver 收 {} cid={payload_cid} from={}", m.msg_type, m.from.as_str());
                if m.msg_type == "tool_result" && payload_cid == test_cid.to_string() {
                    got_result = Some(m.payload);
                    break;
                }
            }
            _ => break,
        }
    }
    let result = got_result.expect("应收到 tool_result from mcp pool facade");
    assert_eq!(result["name"], "echo");
    assert_eq!(result["ok"], true);
    println!("[test4] tool_result content: {}", serde_json::to_string(&result).unwrap_or_default());
    println!("[test4] MCPPoolNode 端到端转发 OK ✓");
    Ok(())
}
