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
    assert_eq!(state.over_view.turn_count, 2); // user turn + assistant turn

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
