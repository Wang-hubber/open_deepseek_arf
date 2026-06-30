//! Tests for arf-engine. Module structure uses `crate::tests` rather than
//! a `#[cfg(test)]` `mod tests` inline so unit tests in this crate can be
//! file-organized.

use std::collections::HashMap;
use std::sync::Arc;

use arf_bus::Bus;
use arf_core::{NodeId, Route, State};
use tokio_util::sync::CancellationToken;

use crate::config::{AgentConfig, ModelConfig};
use crate::error::{BuildError, RunError};
use crate::EngineBuilder;

// ── EngineBuilder validation tests ───────────────────────────

fn test_bus() -> Bus {
    Bus::new(
        std::time::Duration::from_secs(1),
        std::time::Duration::from_secs(3),
        16,
    )
}

// 注册一个 NodeEntry（无 forwarding task 干扰；直接手持 bus.subscribe 即可）。
// 在 async test 里调用：handler 立即 drop，让 entry 残留在 BusGraph 中。
fn test_bus_with_node(node_id: &str, _kind: &str) -> (Arc<Bus>, NodeId) {
    let bus = Arc::new(test_bus());
    let id = NodeId::new(node_id);
    (bus, id)
}

fn minimal_config(agent_id: &str) -> AgentConfig {
    AgentConfig {
        agent_id: agent_id.into(),
        model_config: ModelConfig {
            provider: "deepseek".into(),
            model: "deepseek-v4-flash".into(),
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

// [构造] EngineBuilder.new 接受 Vec<Arc<Bus>>（空 vec 也是合法但 build 时缺 primary）
#[tokio::test]
async fn builder_new_constructs() {
    let _b = EngineBuilder::new(vec![]); // 不会 panic；空 bus 会在 build().await 报错
}

// [构造] 无 bus 提供 → BuildError::MissingNodes
#[tokio::test]
async fn build_fails_with_no_buses() {
    let res = EngineBuilder::new(vec![]).build(minimal_config("a")).await;
    assert!(matches!(res, Err(BuildError::MissingNodes { .. })));
}

// [构造] Strict route 指向不在线节点 → MissingNodes
#[tokio::test]
async fn build_fails_when_strict_route_target_offline() {
    let bus = Arc::new(test_bus());
    let mut cfg = minimal_config("a");
    cfg.routes.insert(
        "model_call".into(),
        Route::strict(vec![NodeId::new("ghost")]),
    );
    let res = EngineBuilder::new(vec![bus]).build(cfg).await;
    assert!(matches!(res, Err(BuildError::MissingNodes { .. })));
}

// [构造] Strict route 指向在线节点 → success
#[tokio::test]
async fn build_succeeds_with_online_strict_routes() {
    let (bus, model_id) = test_bus_with_node("model/a", "model");
    // 手动注册 NodeEntry 让 BusGraph 包含 model_id
    {
        let info = arf_core::NodeInfo {
            node_id: model_id.clone(),
            node_type: "test".into(),
            capabilities: serde_json::json!({"kind": "model"}),
            online_since: 0,
        };
        let h = bus
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
    let mut cfg = minimal_config("a");
    cfg.routes.insert(
        "model_call".into(),
        Route::strict(vec![model_id]),
    );
    let res = EngineBuilder::new(vec![bus]).build(cfg).await;
    assert!(res.is_ok(), "build should succeed");
}

// [构造] Discovery route capability 无任何匹配 → MissingCapabilities
#[tokio::test]
async fn build_fails_when_discovery_capability_no_match() {
    let bus = Arc::new(test_bus());
    let mut cfg = minimal_config("a");
    cfg.routes.insert(
        "tool_exec".into(),
        Route::discovery(vec![("kind".into(), "mcp".into())]),
    );
    let res = EngineBuilder::new(vec![bus]).build(cfg).await;
    assert!(matches!(res, Err(BuildError::MissingCapabilities { .. })));
}

// [构造] Discovery route 有至少一个匹配 → success
#[tokio::test]
async fn build_succeeds_with_discovery_match() {
    let (bus, _id) = test_bus_with_node("mcp/filesystem", "mcp");
    {
        let info = arf_core::NodeInfo {
            node_id: _id.clone(),
            node_type: "test".into(),
            capabilities: serde_json::json!({"kind": "mcp"}),
            online_since: 0,
        };
        let h = bus
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

    let mut cfg = minimal_config("a");
    cfg.routes.insert(
        "tool_exec".into(),
        Route::discovery(vec![("kind".into(), "mcp".into())]),
    );
    let res = EngineBuilder::new(vec![bus]).build(cfg).await;
    if let Err(e) = &res {
        eprintln!("build failed: {e:?}");
    }
    assert!(res.is_ok());
}

// [构造] CheckpointRule name 重复 → DuplicateRuleName
#[tokio::test]
async fn build_fails_on_duplicate_rule_name() {
    use arf_core::{Checkpoint, CheckpointRule, ModelCall};
    let bus = Arc::new(test_bus());

    fn mk_rule(name: &str) -> CheckpointRule {
        CheckpointRule::new(
            name,
            Checkpoint::RoundEnd,
            |_s| false,
            |_s| Box::new(ModelCall::new(vec![])),
        )
    }
    let mut cfg = minimal_config("a");
    cfg.checkpoint_rules.push(mk_rule("dup"));
    cfg.checkpoint_rules.push(mk_rule("dup"));
    let res = EngineBuilder::new(vec![bus]).build(cfg).await;
    assert!(matches!(res, Err(BuildError::DuplicateRuleName { .. })));
}

// [构造] {{skills}} 但 BusGraph 无 skill 节点 → InvalidTemplate
#[tokio::test]
async fn build_fails_when_skills_placeholder_but_no_skill_node() {
    let bus = Arc::new(test_bus());
    let mut cfg = minimal_config("a");
    cfg.system_prompt_template = "Skills: {{skills}}".into();
    let res = EngineBuilder::new(vec![bus]).build(cfg).await;
    assert!(matches!(res, Err(BuildError::InvalidTemplate { .. })));
}

// [构造] {{skills}} 且 BusGraph 有 skill 节点 → success 且 system_prompt 替换
#[tokio::test]
async fn build_replaces_skills_placeholder() {
    let bus = Arc::new(test_bus());
    // 手动在线注册一个 skill 节点
    let skill_info = arf_core::NodeInfo {
        node_id: NodeId::new("skill/greet"),
        node_type: "skill".into(),
        capabilities: serde_json::json!({"kind": "skill"}),
        online_since: 0,
    };
    let filter = arf_core::MessageFilter {
        types: None,
        to_match: arf_core::ToMatch::All,
    };
    let h = bus.connect(skill_info, filter).await.unwrap();
    drop(h); // keep NodeEntry in BusGraph; drop handle lets forwarding task exit

    let mut cfg = minimal_config("a");
    cfg.system_prompt_template = "Skills available:\n{{skills}}".into();
    let engine = EngineBuilder::new(vec![bus]).build(cfg).await.unwrap();
    let prompt = engine.system_prompt();
    assert!(prompt.contains("skill/greet"), "got: {prompt}");
    assert!(!prompt.contains("{{skills}}"), "should be replaced, got: {prompt}");
}

// [构造] EngineBuilder build 后通过 engine.handle() 拿到 NodeHandle
#[tokio::test]
async fn engine_provides_handle_after_build() {
    let bus = Arc::new(test_bus());
    let engine = EngineBuilder::new(vec![bus])
        .build(minimal_config("a"))
        .await
        .unwrap();
    assert_eq!(engine.agent_id().as_str(), "engine/a");
    assert_eq!(engine.handle().subscriptions().len(), 1);
}

// [e2e] Engine.run 1 round：发布 model_call → 模拟 receiver 响应 model_response → 返回 content
#[tokio::test]
async fn engine_run_one_round_completes() {
    use arf_core::Message;
    let bus = Arc::new(test_bus());

    let mut engine = EngineBuilder::new(vec![bus.clone()])
        .build(minimal_config("a"))
        .await
        .unwrap();

    // 同步：确保 receiver 任务在 Engine.run 之前已订阅到 bus。
    // race 表现：tokio::spawn 后调度顺序不定，receiver.subscribe 可能慢于
    //   Engine.run 发送 model_call，导致 model_call 发出时无 receiver 监听，
    //   Engine.wait_for_response 永远收不到 response。
    let (ready_tx, ready_rx) = tokio::sync::oneshot::channel();
    let bus_for_receiver = bus.clone();
    let receiver_handle = tokio::spawn(async move {
        let mut rx = bus_for_receiver.subscribe();
        ready_tx.send(()).expect("ready_tx send");
        while let Ok(m) = rx.recv().await {
            if m.msg_type == "model_call" {
                let cid = m
                    .payload
                    .get("correlation_id")
                    .and_then(|v| v.as_str())
                    .and_then(|s| uuid::Uuid::parse_str(s).ok())
                    .expect("model_call missing correlation_id");
                let response = Message::with_from_bus(
                    "model_response",
                    NodeId::new("model/mock"),
                    vec![],
                    serde_json::json!({
                        "correlation_id": cid.to_string(),
                        "content": "echo from mock model",
                        "usage": { "prompt_tokens": 42 },
                    }),
                    bus_for_receiver.id,
                );
                let _ = bus_for_receiver.send(response).await;
                break;
            }
        }
    });
    ready_rx.await.expect("receiver should subscribe before run");
    // 短暂让出，确保 receiver 任务的 .recv() poll 已开始
    tokio::task::yield_now().await;

    let mut state = State::new();
    let cancel = CancellationToken::new();
    let output = tokio::time::timeout(
        std::time::Duration::from_secs(3),
        engine.run(&mut state, "hello world".into(), cancel),
    )
    .await
    .expect("engine.run timed out")
    .expect("engine.run should succeed");
    assert_eq!(output, "echo from mock model");
    assert_eq!(state.messages.len(), 3); // system + user + assistant
    assert!(state.messages[0].role == "system");
    assert_eq!(state.messages[1].role, "user");
    assert_eq!(state.messages[2].role, "assistant");
    assert_eq!(state.messages[2].content, "echo from mock model");
    assert_eq!(state.over_view.context_tokens, 42);
    assert_eq!(state.over_view.round_count, 1);
    assert_eq!(state.over_view.turn_count, 1); // 1 model_call = 1 turn

    receiver_handle.abort();
}

// [e2e] Engine.run 取消 → RunError::Stopped
#[tokio::test]
async fn engine_run_returns_stopped_on_cancel() {
    let bus = Arc::new(test_bus());
    let mut engine = EngineBuilder::new(vec![bus.clone()])
        .build(minimal_config("a"))
        .await
        .unwrap();

    let cancel = CancellationToken::new();
    cancel.cancel();

    let mut state = State::new();
    let res = engine.run(&mut state, "hi".into(), cancel).await;
    match res {
        Err(RunError::Stopped) => {} // OK
        Ok(_) => {}                  // also OK — send may complete before cancel observed
        Err(e) => panic!("unexpected: {e:?}"),
    }
}

// ── Phase 6 task 6.4 — ReAct loop tests (4 tests) ──

// 简易 model responder：每收到 model_call，按 query 顺序回应 sequence 中的内容。
// 必须从 incoming model_call 提取 correlation_id 并原样回传给 engine（否则 engine 匹配不上）。
async fn run_model_responder(
    mut rx: tokio::sync::broadcast::Receiver<arf_core::Message>,
    bus: Arc<arf_bus::Bus>,
    responses: Vec<serde_json::Value>,
) {
    let mut idx = 0;
    while let Ok(m) = rx.recv().await {
        // 提取 incoming correlation_id 并 reuse 在 response 里
        let cid = m
            .payload
            .get("correlation_id")
            .and_then(|v| v.as_str())
            .and_then(|s| uuid::Uuid::parse_str(s).ok());

        if m.msg_type == "model_call" {
            if let Some(cid) = cid {
                if idx < responses.len() {
                    let mut payload = responses[idx].clone();
                    idx += 1;
                    // Inject actual cid into payload
                    if let Some(obj) = payload.as_object_mut() {
                        obj.insert(
                            "correlation_id".to_string(),
                            serde_json::Value::String(cid.to_string()),
                        );
                    }
                    let response = arf_core::Message::with_from_bus(
                        "model_response",
                        arf_core::NodeId::new("model/mock"),
                        vec![],
                        payload,
                        bus.id,
                    );
                    let _ = bus.send(response).await;
                }
            }
        } else if m.msg_type == "tool_exec" {
            if let Some(cid) = cid {
                let payload = serde_json::json!({
                    "correlation_id": cid.to_string(),
                    "content": "tool success",
                    "status": "ok",
                });
                let response = arf_core::Message::with_from_bus(
                    "tool_result",
                    arf_core::NodeId::new("tool/mock"),
                    vec![],
                    payload,
                    bus.id,
                );
                let _ = bus.send(response).await;
            }
        }
    }
}

// [reAct] model_response 无 tool_calls → 1 round 即返（纯文本）
#[tokio::test]
async fn run_returns_immediately_when_no_tool_calls() {
    let bus = Arc::new(test_bus());
    let mut engine = EngineBuilder::new(vec![bus.clone()])
        .build(minimal_config("a"))
        .await
        .unwrap();

    let (ready_tx, ready_rx) = tokio::sync::oneshot::channel();
    let bus_for_receiver = bus.clone();
    let receiver = tokio::spawn(async move {
        let rx = bus_for_receiver.subscribe();
        ready_tx.send(()).unwrap();
        run_model_responder(rx, bus_for_receiver.clone(), vec![
            serde_json::json!({
                "correlation_id": "00000000-0000-0000-0000-000000000001",
                "content": "hello back",
                "usage": {"prompt_tokens": 10},
            })
        ]).await;
    });
    ready_rx.await.unwrap();
    tokio::task::yield_now().await;

    let mut state = State::new();
    let cancel = CancellationToken::new();
    let output = tokio::time::timeout(
        std::time::Duration::from_secs(3),
        engine.run(&mut state, "hi".into(), cancel),
    )
    .await
    .expect("engine.run timed out")
    .expect("engine.run should succeed");
    assert_eq!(output, "hello back");
    // messages: system + user + assistant
    assert_eq!(state.messages.len(), 3);
    assert_eq!(state.messages[2].role, "assistant");
    assert_eq!(state.over_view.round_count, 1);

    receiver.abort();
}

// [reAct] model_response 有 1 tool_call → tool_exec 完成后 assistant 无 tool_calls → 终止
#[tokio::test]
async fn run_continues_after_tool_result() {
    let bus = Arc::new(test_bus());
    let mut engine = EngineBuilder::new(vec![bus.clone()])
        .build(minimal_config("a"))
        .await
        .unwrap();

    let (ready_tx, ready_rx) = tokio::sync::oneshot::channel();
    let bus_for_receiver = bus.clone();
    let receiver = tokio::spawn(async move {
        let rx = bus_for_receiver.subscribe();
        ready_tx.send(()).unwrap();
        // 第 1 次 model_call：返回 1 个 tool_call
        // 第 2 次 model_call：返回纯文本
        // 共 2 轮
        run_model_responder(rx, bus_for_receiver.clone(), vec![
            serde_json::json!({
                "correlation_id": "00000000-0000-0000-0000-000000000001",
                "content": "",
                "tool_calls": [{
                    "id": "call_0",
                    "name": "bash",
                    "arguments": {"cmd": "ls"},
                }],
            }),
            serde_json::json!({
                "correlation_id": "00000000-0000-0000-0000-000000000002",
                "content": "done",
            }),
        ]).await;
    });
    ready_rx.await.unwrap();
    tokio::task::yield_now().await;

    let mut state = State::new();
    let cancel = CancellationToken::new();
    let output = tokio::time::timeout(
        std::time::Duration::from_secs(5),
        engine.run(&mut state, "run a tool".into(), cancel),
    )
    .await
    .expect("engine.run timed out")
    .expect("engine.run should succeed");
    assert_eq!(output, "done");
    // messages: system + user + assistant(tool_calls) + tool + assistant(text)
    assert_eq!(state.messages.len(), 5);
    assert_eq!(state.messages[2].role, "assistant");
    assert_eq!(state.messages[2].tool_calls.len(), 1);
    assert_eq!(state.messages[2].tool_calls[0].name, "bash");
    assert_eq!(state.messages[3].role, "tool");
    assert_eq!(state.messages[3].content, "tool success");
    assert_eq!(state.messages[4].role, "assistant");
    assert_eq!(state.messages[4].content, "done");

    receiver.abort();
}

// [reAct] max_turns=1 + receiver 在第 1 次响应含 tool_calls → engine 发 tool_exec →
// 期望：max_turns 触发（turn_count=2 > 1）
#[tokio::test]
async fn run_returns_max_turns_exceeded() {
    let bus = Arc::new(test_bus());
    let mut cfg = minimal_config("a");
    cfg.max_turns = 1;
    let mut engine = EngineBuilder::new(vec![bus.clone()])
        .build(cfg)
        .await
        .unwrap();

    let (ready_tx, ready_rx) = tokio::sync::oneshot::channel();
    let bus_for_receiver = bus.clone();
    let receiver = tokio::spawn(async move {
        let rx = bus_for_receiver.subscribe();
        ready_tx.send(()).unwrap();
        // 永远响应含 tool_call → engine 不停地发 tool_exec，再 model_call，
        // 直到 turn_count >= max_turns=1。
        run_model_responder(rx, bus_for_receiver, vec![
            serde_json::json!({
                "correlation_id": "00000000-0000-0000-0000-000000000001",
                "content": "",
                "tool_calls": [{"id": "call_0", "name": "echo", "arguments": {}}],
            }),
        ]).await;
    });
    ready_rx.await.unwrap();
    tokio::task::yield_now().await;

    let mut state = State::new();
    let cancel = CancellationToken::new();
    let res = tokio::time::timeout(
        std::time::Duration::from_secs(5),
        engine.run(&mut state, "test".into(), cancel),
    )
    .await
    .expect("run timed out")
    .expect_err("should error with MaxTurnsExceeded");
    assert!(matches!(res, RunError::MaxTurnsExceeded { .. }));
}

// [reAct] cancel 在 send 之前触发 → Stopped
#[tokio::test]
async fn run_returns_stopped_on_cancel_immediate() {
    let bus = Arc::new(test_bus());
    let mut engine = EngineBuilder::new(vec![bus.clone()])
        .build(minimal_config("a"))
        .await
        .unwrap();

    let cancel = CancellationToken::new();
    cancel.cancel(); // cancel before run

    let mut state = State::new();
    let res = engine.run(&mut state, "x".into(), cancel).await;
    assert!(matches!(res, Err(RunError::Stopped)));
}
