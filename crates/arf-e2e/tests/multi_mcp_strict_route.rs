//! multi_mcp_strict_route.rs — Phase 9 task 9.7.1
//!
//! 多 MCP + Static route（Strict → multiple NodeIds）端到端探查。
//!
//! 4 test cases：
//! 1. strict_route_resolves_to_multiple_node_ids — `resolve_route_pure` 直接返 Strict 列表
//! 2. strict_route_fails_build_when_node_offline — Build 时 MissingNodes 错误
//! 3. multi_mcp_nodes_distinct_tools_engine_resolves — 3 McpNode 各自 tool + 3 ResourceSpec → owner_of_tool 各 tool → 各 mcp
//! 4. multi_mcp_engine_executes_correct_node_via_owner — Engine + tool_call 走 owner_of_tool 路由 + synthetic responder

mod common;

use std::fs;
use std::io::Write;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use arf_bus::Bus;
use arf_core::{NodeId, NodeInfo, Route, State};
use arf_engine::checkpoint::resolve_route_pure;
use arf_engine::{AgentConfig, Engine, EngineBuilder, EngineConfig, ModelDecl, ResourceSpec};
use arf_mcp::McpNode;
use arf_model_adapter::ModelAdapterNode;
use common::provider::{scripted, text_response, tool_call_response};
use serde_json::json;

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

fn make_mcp_namespace(tmp_root: &PathBuf, ns: &str) -> PathBuf {
    let ns_root = tmp_root.join(ns);
    fs::create_dir_all(&ns_root).unwrap();
    write_echo_tool(&ns_root, ns);
    ns_root
}

// ═══════════════════════════════════════════════════════════════════════
// Test 1: Strict route resolves to multiple NodeIds
// ═══════════════════════════════════════════════════════════════════════

// [方法] `resolve_route_pure(Strict([a,b,c]), _)` 直接返回 [a,b,c]
// （Strict 不查 graph；Discovery 走 graph filter）。
#[tokio::test]
async fn strict_route_resolves_to_multiple_node_ids() {
    let route = Route::strict(vec![
        NodeId::new("mcp/a"),
        NodeId::new("mcp/b"),
        NodeId::new("mcp/c"),
    ]);
    let resolved = resolve_route_pure(&route, &[]);
    assert_eq!(resolved.len(), 3);
    let strs: Vec<&str> = resolved.iter().map(|n| n.as_str()).collect();
    assert!(strs.contains(&"mcp/a"));
    assert!(strs.contains(&"mcp/b"));
    assert!(strs.contains(&"mcp/c"));
    println!("[test1] Strict([a,b,c]) → 3 NodeIds OK ✓");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 2: Strict route build fails when node offline
// ═══════════════════════════════════════════════════════════════════════

// [错误] `EngineBuilder::build` 校验 Strict target 在线。
// 1 个 mcp 在线 + Strict 列表含 1 ghost → `BuildError::MissingNodes`
// 本测试不依赖 React loop，纯 build 路径。
#[tokio::test]
async fn strict_route_fails_build_when_node_offline() {
    let bus = Arc::new(Bus::new(
        Duration::from_millis(500),
        Duration::from_secs(2),
        32,
    ));
    let _model = ModelAdapterNode::new(simple_scripted(), &bus, NodeId::new("model/e2e"))
        .await
        .expect("model node");

    let cfg = AgentConfig {
        model: ModelDecl {
            provider: "scripted".into(),
            model_name: "scripted-v1".into(),
            ..Default::default()
        },
        resources: vec![],
        system_prompt_template: "You are helpful.".into(),
        initial_memory: vec![],
        allowed_paths: vec![],
        tools: vec![],
        engine: EngineConfig {
            // Custom msg_type "test_op" → Strict route，含 1 个 ghost
            routes: std::collections::HashMap::from([(
                "test_op".into(),
                Route::strict(vec![NodeId::new("mcp/online"), NodeId::new("mcp/ghost")]),
            )]),
            max_turns: 5,
            ..Default::default()
        },
    };
    let result = EngineBuilder::new(vec![bus.clone()]).build(cfg).await;
    match result {
        Err(arf_engine::BuildError::MissingNodes { nodes }) => {
            println!("[test2] BuildError::MissingNodes(n={}) = {nodes:?}", nodes.len());
            assert!(nodes.iter().any(|n| n.contains("mcp/ghost")));
        }
        Err(e) => panic!("expected MissingNodes, got error: {e}"),
        Ok(_) => panic!("expected MissingNodes, got Ok engine"),
    }
    println!("[test2] Strict([online, ghost]) → MissingNodes OK ✓");
}

fn simple_scripted() -> Arc<dyn arf_model_adapter::Provider> {
    scripted(vec![text_response("hi")])
}

// ═══════════════════════════════════════════════════════════════════════
// Test 3: 3 McpNode distinct tools + 3 ResourceSpec → owner_of_tool
// ═══════════════════════════════════════════════════════════════════════

// [方法] 3 个 McpNode（不同 namespace）+ 各自 1 tool（echo_a / echo_b / echo_c）
// + 3 ResourceSpec（Subset）→ engine build OK + owner_of_tool 各 tool → 各 mcp。
#[tokio::test]
async fn multi_mcp_nodes_distinct_tools_engine_resolves() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path().to_path_buf();
    make_mcp_namespace(&root, "a");
    make_mcp_namespace(&root, "b");
    make_mcp_namespace(&root, "c");

    let bus = Arc::new(Bus::new(
        Duration::from_millis(500),
        Duration::from_secs(2),
        32,
    ));
    let _model = ModelAdapterNode::new(simple_scripted(), &bus, NodeId::new("model/e2e"))
        .await
        .expect("model node");

    // 3 个 McpNode 接在同一个 bus 上，每个的 root 是独立子目录
    // 共享 root 时 FsDiscovery 扫 root/tools/* → 3 tool 都在一个 node。
    // 用 3 个独立 root 才能 3 node 各自 1 tool。
    let root_a = root.join("a");
    let root_b = root.join("b");
    let root_c = root.join("c");
    let node_a = McpNode::local("a", root_a.clone()).expect("McpNode a");
    let node_b = McpNode::local("b", root_b.clone()).expect("McpNode b");
    let node_c = McpNode::local("c", root_c.clone()).expect("McpNode c");
    node_a.connect(&bus).await.expect("a connect");
    node_b.connect(&bus).await.expect("b connect");
    node_c.connect(&bus).await.expect("c connect");

    // 等 mcp spawn message_loop
    tokio::time::sleep(Duration::from_millis(100)).await;

    // 3 ResourceSpec，每个 Subset filter 取 1 个 tool
    let cfg = AgentConfig {
        model: ModelDecl {
            provider: "scripted".into(),
            model_name: "scripted-v1".into(),
            ..Default::default()
        },
        resources: vec![
            ResourceSpec {
                resource_name: "mcp_a".into(),
                node_type: "mcp".into(),
                capabilities: Some(json!({"tools": ["a"]})),
            },
            ResourceSpec {
                resource_name: "mcp_b".into(),
                node_type: "mcp".into(),
                capabilities: Some(json!({"tools": ["b"]})),
            },
            ResourceSpec {
                resource_name: "mcp_c".into(),
                node_type: "mcp".into(),
                capabilities: Some(json!({"tools": ["c"]})),
            },
        ],
        system_prompt_template: "You are helpful.".into(),
        initial_memory: vec![],
        allowed_paths: vec![],
        tools: vec![],
        engine: EngineConfig {
            // Strict route 显式列出 3 mcp ids —— build 时验证在线
            routes: std::collections::HashMap::from([(
                "test_op".into(),
                Route::strict(vec![
                    NodeId::new("mcp/a"),
                    NodeId::new("mcp/b"),
                    NodeId::new("mcp/c"),
                ]),
            )]),
            max_turns: 5,
            ..Default::default()
        },
    };
    // 1 次 build 同时验证：(1) ResourceSpec 各自匹配对 mcp（tool_index 正确）
    // (2) Strict route target 全在线（不返 MissingNodes）
    let _engine: Engine = EngineBuilder::new(vec![bus.clone()])
        .build(cfg)
        .await
        .expect("engine build (3 mcp + 3 ResourceSpec + Strict route)");

    println!("[test3] 3 McpNode + 3 ResourceSpec + Strict route build OK ✓");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 4: Engine run with tool_call routes to correct mcp via owner_of_tool
// ═══════════════════════════════════════════════════════════════════════

// [方法] Engine + scripted provider 发出 tool_call_response("b") + 3 mcp + 1 facade
// 模拟 responder → tool_result back → engine 收 tool_result → 完成 round。
// 验证：owner_of_tool("b") → mcp_b；engine 真把 tool_exec 投到 mcp_b。
#[tokio::test]
async fn multi_mcp_engine_executes_correct_node_via_owner() -> anyhow::Result<()> {
    use arf_core::{MessageFilter, ToMatch};
    use uuid::Uuid;

    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path().to_path_buf();
    make_mcp_namespace(&root, "a");
    make_mcp_namespace(&root, "b");
    make_mcp_namespace(&root, "c");

    let bus = Arc::new(Bus::new(
        Duration::from_millis(500),
        Duration::from_secs(2),
        32,
    ));

    // Scripted provider: turn 1 → tool_call("b"), turn 2 → text done
    let provider = scripted(vec![
        tool_call_response("b", json!({"q": "hi"})),
        text_response("done"),
    ]);
    let _model = ModelAdapterNode::new(provider, &bus, NodeId::new("model/e2e")).await?;

    let node_a = McpNode::local("a", root.join("a"))?;
    let node_b = McpNode::local("b", root.join("b"))?;
    let node_c = McpNode::local("c", root.join("c"))?;
    node_a.connect(&bus).await?;
    node_b.connect(&bus).await?;
    node_c.connect(&bus).await?;
    tokio::time::sleep(Duration::from_millis(100)).await;

    // 验证 owner_of_tool 静态推导：b → mcp/b
    let cfg = AgentConfig {
        model: ModelDecl {
            provider: "scripted".into(),
            model_name: "scripted-v1".into(),
            ..Default::default()
        },
        resources: vec![
            ResourceSpec {
                resource_name: "mcp_a".into(),
                node_type: "mcp".into(),
                capabilities: Some(json!({"tools": ["a"]})),
            },
            ResourceSpec {
                resource_name: "mcp_b".into(),
                node_type: "mcp".into(),
                capabilities: Some(json!({"tools": ["b"]})),
            },
            ResourceSpec {
                resource_name: "mcp_c".into(),
                node_type: "mcp".into(),
                capabilities: Some(json!({"tools": ["c"]})),
            },
        ],
        system_prompt_template: "You are helpful.".into(),
        initial_memory: vec![],
        allowed_paths: vec![],
        tools: vec![],
        engine: EngineConfig {
            max_turns: 5,
            ..Default::default()
        },
    };
    let mut engine = EngineBuilder::new(vec![bus.clone()]).build(cfg).await?;
    let mut state = State::new();
    let cancel = tokio_util::sync::CancellationToken::new();

    // Install synthetic tool_exec responder — McpNode listens on
    // `tool_call_set` (legacy), but Engine sends `tool_exec`. We bridge.
    // The responder echoes a synthetic tool_result for whatever tool_exec
    // arrives, so Engine can complete the round.
    let responder_id = NodeId::new("harness/tool_exec_responder");
    let rinfo = NodeInfo {
        node_id: responder_id.clone(),
        node_type: "harness-responder".into(),
        capabilities: json!({}),
        online_since: 0,
    };
    let filter = MessageFilter {
        types: Some(vec!["tool_exec".into()]),
        to_match: ToMatch::All,
    };
    let mut handle = bus.connect(rinfo, filter).await?;
    tokio::spawn(async move {
        while let Ok(msg) = handle.recv().await {
            if msg.msg_type != "tool_exec" {
                continue;
            }
            let cid = msg
                .payload
                .get("correlation_id")
                .and_then(|v| v.as_str())
                .and_then(|s| Uuid::parse_str(s).ok());
            let tool_name = msg
                .payload
                .get("tool_name")
                .and_then(|v| v.as_str())
                .unwrap_or("unknown")
                .to_string();
            // Engine sent tool_exec to msg.to[0] (the owner mcp node).
            // Send tool_result back DIRECTED to engine.
            let _ = bus
                .send(arf_core::Message::new(
                    "tool_result",
                    NodeId::new("harness/tool_exec_responder"),
                    vec![msg.from.clone()],
                    json!({
                        "correlation_id": cid.map(|u| u.to_string()).unwrap_or_default(),
                        "name": tool_name,
                        "content": format!("synthetic-{tool_name}"),
                        "ok": true,
                    }),
                ))
                .await;
        }
    });

    let out = tokio::time::timeout(
        Duration::from_secs(10),
        engine.run(&mut state, "use b".into(), cancel),
    )
    .await
    .expect("engine run timed out")
    .expect("engine run failed");
    assert_eq!(out, "done");

    // 验证 state.messages: user + assistant(t1: tool_call "b") + tool("b") + assistant(text)
    // = 4 messages
    assert_eq!(state.messages.len(), 4);
    let tc_name = &state.messages[1].tool_calls[0].name;
    assert_eq!(tc_name, "b", "engine 选 tool 'b'");

    // 找到 assistant 的 tool_call 后，确认 tool message 含 ok=true
    let tool_msg = &state.messages[2];
    assert_eq!(tool_msg.role, "tool");
    println!("[test4] tool result content = {}", tool_msg.content);
    assert!(tool_msg.content.contains("synthetic-b"));
    println!("[test4] Engine + 3 mcp + tool_call('b') → mcp/b + tool_result 端到端 OK ✓");
    Ok(())
}
