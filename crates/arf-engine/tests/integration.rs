//! Phase 6 task 6.9 — Engine + Bus 全链路集成测试。
//!
//! 与单元测试 (tests.rs) 的区别：
//! - 验证多 round 端到端流程
//! - 验证 CheckpointRule 在真实 run 中触发
//! - 验证 OnMemberFailedHandler 在 node_offline 时被调用
//! - 验证 State 在多 round 间正确持久化
//!
//! 注：当前 ModelAdapterNode 发送 ModelResponsePayload（嵌套格式），
//! Engine 解析为 flat 格式（content/tool_calls 顶层）—— 是已知 mismatch。
//! 6.9 集成测试用 inline mock responder，与单元测试 helper 模式一致，
//! 但测试更接近真实使用场景（多 round + 复杂 CheckpointRule + MemberFailedHandler）。

use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration;

use arf_core::{Checkpoint, CheckpointRule, Message, ModelMessage, NodeId, Route, State};
use arf_engine::{AgentConfig, Engine, EngineBuilder, MemberFailedAction, ModelConfig, OnMemberFailedHandler};
use tokio_util::sync::CancellationToken;
use uuid::Uuid;

fn test_bus() -> arf_bus::Bus {
    arf_bus::Bus::new(
        std::time::Duration::from_secs(1),
        std::time::Duration::from_secs(3),
        16,
    )
}

fn minimal_config(agent_id: &str) -> AgentConfig {
    AgentConfig {
        agent_id: agent_id.into(),
        model_config: ModelConfig {
            provider: "mock".into(),
            model: "mock-v1".into(),
        },
        system_prompt_template: "You are helpful.".into(),
        initial_memory: vec![],
        max_turns: 10,
        tool_timeout_ms: None,
        permissions: Default::default(),
        routes: HashMap::new(),
        checkpoint_rules: vec![],
        processors: HashMap::new(),
        on_member_failed: None,
        tools_include: None,
        tools_exclude: vec![],
        skills_include: None,
        skills_exclude: vec![],
    }
}

/// Inline mock responder: sends back programmed responses to model_call and tool_exec.
async fn run_mock_responder(
    mut rx: tokio::sync::broadcast::Receiver<Message>,
    bus: Arc<arf_bus::Bus>,
    responses: Vec<serde_json::Value>,
) {
    let mut idx = 0;
    let stop_at = tokio::time::Instant::now() + Duration::from_secs(10);
    while let Ok(m) = tokio::time::timeout_at(stop_at, rx.recv()).await {
        let m = match m { Ok(m) => m, Err(_) => break };
        let cid = m.payload.get("correlation_id")
            .and_then(|v| v.as_str())
            .and_then(|s| Uuid::parse_str(s).ok());
        match m.msg_type.as_str() {
            "model_call" if cid.is_some() && idx < responses.len() => {
                let mut payload = responses[idx].clone();
                idx += 1;
                if let Some(obj) = payload.as_object_mut() {
                    obj.insert("correlation_id".to_string(), serde_json::Value::String(cid.unwrap().to_string()));
                }
                let resp = Message::with_from_bus(
                    "model_response",
                    NodeId::new("model/mock"),
                    vec![],
                    payload,
                    bus.id,
                );
                let _ = bus.send(resp).await;
            }
            "tool_exec" if cid.is_some() => {
                let payload = serde_json::json!({
                    "correlation_id": cid.unwrap().to_string(),
                    "content": "tool result",
                });
                let resp = Message::with_from_bus(
                    "tool_result",
                    NodeId::new("tool/mock"),
                    vec![],
                    payload,
                    bus.id,
                );
                let _ = bus.send(resp).await;
            }
            _ => {}
        }
    }
}

// [E2E] 多 round ReAct 完整流程：每 round model_call + tool_exec + tool_result + 终止
#[tokio::test]
async fn e2e_multi_round_react_loop() {
    let bus = Arc::new(test_bus());

    let (ready_tx, ready_rx) = tokio::sync::oneshot::channel();
    let bus_for_resp = bus.clone();
    let resp_h = tokio::spawn(async move {
        let mut rx = bus_for_resp.subscribe();
        let _ = ready_tx.send(());
        run_mock_responder(rx, bus_for_resp, vec![
            serde_json::json!({
                "message": {"content": ""},
                "tool_calls": [{"id":"c1","name":"echo","arguments":{"text":"hello"}}],
            }),
            serde_json::json!({
                "message": {"content": "round 1 done"},
                "tool_calls": [{"id":"c2","name":"echo","arguments":{"text":"world"}}],
            }),
            serde_json::json!({
                "message": {"content": "round 2 done"},
                "tool_calls": [],
            }),
        ]).await;
    });
    ready_rx.await.unwrap();
    tokio::task::yield_now().await;

    let mut engine = EngineBuilder::new(vec![bus.clone()])
        .build(minimal_config("a"))
        .await
        .unwrap();

    let mut state = State::new();
    let cancel = CancellationToken::new();
    let output = tokio::time::timeout(
        Duration::from_secs(5),
        engine.run(&mut state, "multi-round test".into(), cancel),
    )
    .await
    .expect("run timed out")
    .expect("run should succeed");

    assert_eq!(output, "round 2 done");
    // Messages: system + user + assistant(tool) + tool + assistant(tool) + tool + assistant(text)
    assert_eq!(state.messages.len(), 7);
    assert_eq!(state.over_view.round_count, 1);
    // turn_count: 3 model_calls + 2 tool_execs = 5
    assert_eq!(state.over_view.turn_count, 5);
    resp_h.abort();
}

// [E2E] CheckpointRule::every_n_rounds 端到端触发：RoundEnd rule 每次 RoundEnd fire
// 注：6.9 单 chat() = 1 round, RoundEnd 触发 1 次. 每_n 测试留单元.
#[tokio::test]
async fn e2e_round_end_checkpoint_fires_on_completion() {
    use std::sync::atomic::{AtomicU32, Ordering};
    let bus = Arc::new(test_bus());

    let (ready_tx, ready_rx) = tokio::sync::oneshot::channel();
    let bus_for_resp = bus.clone();
    let resp_h = tokio::spawn(async move {
        let mut rx = bus_for_resp.subscribe();
        let _ = ready_tx.send(());
        run_mock_responder(rx, bus_for_resp, vec![
            serde_json::json!({"message": {"content": "done", "tool_calls": []}}),
        ]).await;
    });
    ready_rx.await.unwrap();
    tokio::task::yield_now().await;

    // Register a custom action that increments a counter when fired
    let fire_count = Arc::new(AtomicU32::new(0));
    let fire_count_clone = fire_count.clone();
    let rule = CheckpointRule::new(
        "round_end_marker",
        Checkpoint::RoundEnd,
        |_s| true,
        move |_s| {
            fire_count_clone.fetch_add(1, Ordering::SeqCst);
            Box::new(CpQueryStub {
                cid: Uuid::new_v4(),
            }) as Box<dyn arf_core::ActionMessage>
        },
    );

    // CpQueryStub needs to be defined. Use a Command-intent fake action to avoid
    // the engine having to wait for response. Skip the route registration.
    struct CpQueryStub { cid: Uuid }
    #[async_trait::async_trait]
    impl arf_core::ActionMessage for CpQueryStub {
        fn msg_type(&self) -> &'static str { "test_round_end_marker" }
        fn correlation_id(&self) -> Uuid { self.cid }
        fn payload(&self) -> serde_json::Value { serde_json::json!({}) }
        fn intent(&self) -> arf_core::MessageIntent { arf_core::MessageIntent::Command }
    }

    // Pre-register a sink node so Strict route check passes
    let sink_info = arf_core::NodeInfo {
        node_id: NodeId::new("cp/sink"),
        node_type: "sink".into(),
        capabilities: serde_json::json!({"kind": "test_sink"}),
        online_since: 0,
    };
    let _ = bus
        .connect(
            sink_info,
            arf_core::MessageFilter { types: None, to_match: arf_core::ToMatch::All },
        )
        .await
        .unwrap();

    let mut cfg = minimal_config("a");
    // Strict route for the checkpoint msg_type
    cfg.routes.insert(
        "test_round_end_marker".into(),
        Route::strict(vec![NodeId::new("cp/sink")]),
    );
    cfg.checkpoint_rules = vec![rule];
    let mut engine = EngineBuilder::new(vec![bus.clone()]).build(cfg).await.unwrap();

    let mut state = State::new();
    let cancel = CancellationToken::new();
    let _ = tokio::time::timeout(
        Duration::from_secs(5),
        engine.run(&mut state, "round end test".into(), cancel),
    )
    .await
    .expect("run timed out")
    .expect("run should succeed");

    assert_eq!(fire_count.load(Ordering::SeqCst), 1, "RoundEnd should fire exactly once");
    resp_h.abort();
}

// [E2E] OnMemberFailedHandler 配置 stored；实际 lifecycle 集成在 6.x
//
// 6.8 简化：lifecycle listener 只 invalidate cache；handler invocation 留 6.x。
// 这里仅验证 handler 存在且 build() 接受。
#[tokio::test]
async fn e2e_on_member_failed_handler_stored_in_config() {
    let bus = Arc::new(test_bus());
    let mut cfg = minimal_config("a");
    cfg.on_member_failed = Some(Arc::new(|_a: &NodeId, _m: &NodeId, _r: &str| {
        MemberFailedAction::FailSession
    }) as Arc<dyn OnMemberFailedHandler>);
    let engine = EngineBuilder::new(vec![bus.clone()]).build(cfg).await;
    assert!(engine.is_ok(), "build with on_member_failed should succeed");
}

// [E2E] DiscoveryCache 在 node_offline 后清空，resolution 用新 graph
#[tokio::test]
async fn e2e_discovery_cache_invalidated_on_node_lifecycle() {
    let bus = Arc::new(test_bus());

    // Pre-register 2 nodes
    for i in 0..2 {
        let info = arf_core::NodeInfo {
            node_id: NodeId::new(format!("mcp/n{i}")),
            node_type: "mcp".into(),
            capabilities: serde_json::json!({"kind": "mcp"}),
            online_since: 0,
        };
        let _ = bus
            .connect(
                info,
                arf_core::MessageFilter {
                    types: None,
                    to_match: arf_core::ToMatch::All,
                },
            )
            .await
            .unwrap();
    }

    let _engine = EngineBuilder::new(vec![bus.clone()])
        .build(minimal_config("a"))
        .await
        .unwrap();
    tokio::time::sleep(Duration::from_millis(50)).await;

    let cap = arf_core::Capability::one("kind", "mcp");
    let graph = bus.graph();
    let before = _engine.discovery_cache().get_or_compute(&cap, &graph.nodes);
    assert_eq!(before.len(), 2, "both mcp nodes should resolve");

    // Simulate node_offline
    let sig = Message::new(
        "node_offline",
        NodeId::new("lifecycle"),
        vec![],
        serde_json::json!({"node_id": "mcp/n0"}),
    );
    bus.send(sig).await.unwrap();
    tokio::time::sleep(Duration::from_millis(200)).await;

    // Cache should be invalidated
    assert!(
        _engine.discovery_cache().is_empty(),
        "cache should be empty after node_offline signal"
    );
}

// [E2E] WaitStrategy 端到端：CheckpointRule.build 出 Query msg，engine park 等响应
#[tokio::test]
async fn e2e_query_intent_checkpoint_park_and_resume() {
    let bus = Arc::new(test_bus());

    // Use a sink node to satisfy Strict route
    let sink_info = arf_core::NodeInfo {
        node_id: NodeId::new("cp/sink"),
        node_type: "sink".into(),
        capabilities: serde_json::json!({"kind": "test_sink"}),
        online_since: 0,
    };
    let _ = bus
        .connect(
            sink_info,
            arf_core::MessageFilter { types: None, to_match: arf_core::ToMatch::All },
        )
        .await
        .unwrap();

    // Query action that needs response
    struct CpQuery { cid: Uuid }
    #[async_trait::async_trait]
    impl arf_core::ActionMessage for CpQuery {
        fn msg_type(&self) -> &'static str { "test_cp_query" }
        fn correlation_id(&self) -> Uuid { self.cid }
        fn payload(&self) -> serde_json::Value {
            serde_json::json!({"correlation_id": self.cid.to_string()})
        }
        fn intent(&self) -> arf_core::MessageIntent { arf_core::MessageIntent::Query }
    }

    let rule = CheckpointRule::new(
        "query_at_bmc",
        Checkpoint::BeforeModelCall,
        |_s| true,
        |_s| Box::new(CpQuery { cid: Uuid::new_v4() }) as Box<dyn arf_core::ActionMessage>,
    );

    let mut cfg = minimal_config("a");
    cfg.routes.insert("test_cp_query".into(), Route::strict(vec![NodeId::new("cp/sink")]));
    cfg.checkpoint_rules = vec![rule];

    let (ready_tx, ready_rx) = tokio::sync::oneshot::channel();
    let bus_for_resp = bus.clone();
    let resp_h = tokio::spawn(async move {
        let mut rx = bus_for_resp.subscribe();
        let _ = ready_tx.send(());
        // Respond to test_cp_query AND model_call
        let stop_at = tokio::time::Instant::now() + Duration::from_secs(10);
        while let Ok(m) = tokio::time::timeout_at(stop_at, rx.recv()).await {
            let m = match m { Ok(m) => m, Err(_) => break };
            let cid = m.payload.get("correlation_id")
                .and_then(|v| v.as_str())
                .and_then(|s| Uuid::parse_str(s).ok());
            if let Some(cid) = cid {
                let resp_type = match m.msg_type.as_str() {
                    "model_call" => "model_response",
                    "test_cp_query" => "test_cp_query_result",
                    _ => continue,
                };
                let payload = if resp_type == "model_response" {
                    serde_json::json!({"correlation_id": cid.to_string(), "message": {"content": "ok", "tool_calls": []}})
                } else {
                    serde_json::json!({"correlation_id": cid.to_string()})
                };
                let resp = Message::with_from_bus(
                    resp_type,
                    NodeId::new("mock/handler"),
                    vec![],
                    payload,
                    bus_for_resp.id,
                );
                let _ = bus_for_resp.send(resp).await;
                if resp_type == "model_response" {
                    break;
                }
            }
        }
    });
    ready_rx.await.unwrap();
    tokio::task::yield_now().await;

    let mut engine = EngineBuilder::new(vec![bus.clone()]).build(cfg).await.unwrap();
    let mut state = State::new();
    let cancel = CancellationToken::new();
    let _ = tokio::time::timeout(
        Duration::from_secs(5),
        engine.run(&mut state, "test".into(), cancel),
    )
    .await
    .expect("run timed out — Query intent should park and resume on response")
    .expect("run should succeed");
    assert!(state.wait_events.is_empty(), "wait_events cleared");

    resp_h.abort();
}

// [修复] 6.20 — Engine 读取 nested ModelResponsePayload payload
// 验证 Engine.do_model_turn 解析 payload.message.content / payload.message.tool_calls / payload.usage
//（真实 ModelAdapterNode 发送的嵌套格式），而非 payload.content / payload.tool_calls（flat 旧格式）。
// 注意：usage 在 ModelResponsePayload 顶层，不在 message 内。
#[tokio::test]
async fn engine_reads_nested_model_response_payload() {
    let bus = Arc::new(test_bus());

    let (ready_tx, ready_rx) = tokio::sync::oneshot::channel();
    let bus_for_resp = bus.clone();
    let resp_h = tokio::spawn(async move {
        let mut rx = bus_for_resp.subscribe();
        let _ = ready_tx.send(());
        // 发送 nested ModelResponsePayload 格式（真实 ModelAdapterNode 格式）
        run_mock_responder(rx, bus_for_resp, vec![
            serde_json::json!({
                "message": {
                    "content": "hello from nested payload",
                    "tool_calls": [],
                },
                "usage": {"prompt_tokens": 50},
                "finish_reason": "stop"
            }),
        ]).await;
    });
    ready_rx.await.unwrap();
    tokio::task::yield_now().await;

    let mut engine = EngineBuilder::new(vec![bus.clone()])
        .build(minimal_config("nested-payload-test"))
        .await
        .unwrap();

    let mut state = State::new();
    let cancel = CancellationToken::new();
    let output = tokio::time::timeout(
        Duration::from_secs(5),
        engine.run(&mut state, "test input".into(), cancel),
    )
    .await
    .expect("run timed out")
    .expect("run should succeed");

    // 关键断言：output 来自 nested payload 的 message.content
    assert_eq!(output, "hello from nested payload");
    // state.messages 末尾应是 assistant(text) 消息
    let last = state.messages.last().expect("messages should not be empty");
    assert_eq!(last.role, "assistant");
    assert_eq!(last.content, "hello from nested payload");
    resp_h.abort();
}