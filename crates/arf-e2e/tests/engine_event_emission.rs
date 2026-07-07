//! Task 18b — Engine emits round/model/tool events to configured SessionStore.
//!
//! Verifies the Engine-side hooks (spec §2.5): run() entry/exit fires
//! round_start/round_end; do_model_turn success fires model_call_end;
//! do_tool_turn success + failure fire tool_call_end.

use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration;

use arf_bus::Bus;
use arf_core::{ModelMessage, NodeId, Route};
use arf_engine::{
    AgentConfig, EngineBuilder, EngineConfig, ModelDecl,
};
use arf_model_adapter::types::{ModelResponsePayload, ToolDef, Usage};
use arf_model_adapter::{ModelAdapterNode, Provider, ProviderError};
use arf_model_adapter::types::ModelParams;
use arf_session::{JsonlSessionStore, SessionData, SessionMeta, SessionStatus, SessionStore};
use async_trait::async_trait;
use chrono::Utc;
use arf_core::State;

struct TextProvider {
    name: &'static str,
    model: String,
    text: String,
    usage: Usage,
}
#[async_trait]
impl Provider for TextProvider {
    fn name(&self) -> &str { self.name }
    fn supported_models(&self) -> &[String] { std::slice::from_ref(&self.model) }
    async fn chat(
        &self,
        _m: &str,
        _msgs: Vec<ModelMessage>,
        _t: Vec<ToolDef>,
        _p: ModelParams,
    ) -> Result<ModelResponsePayload, ProviderError> {
        Ok(ModelResponsePayload {
            message: ModelMessage::new("assistant", &self.text),
            tool_calls: None,
            finish_reason: "stop".into(),
            usage: Some(self.usage.clone()),
            id: format!("id-{}", self.name),
            model: self.model.clone(),
        })
    }
}

fn text_provider(name: &'static str, model: &str, text: &str, in_t: u32, out_t: u32) -> Arc<TextProvider> {
    Arc::new(TextProvider {
        name,
        model: model.to_string(),
        text: text.to_string(),
        usage: Usage {
            input_tokens: in_t,
            output_tokens: out_t,
            total_tokens: in_t + out_t,
        },
    })
}

fn make_cfg(provider_name: &str, model: &str) -> AgentConfig {
    let routes = HashMap::<String, Route>::new();
    AgentConfig {
        model: ModelDecl {
            provider: provider_name.into(),
            model_name: model.into(),
            ..Default::default()
        },
        resources: vec![],
        system_prompt_template: "x".into(),
        initial_memory: vec![],
        allowed_paths: vec![],
        engine: EngineConfig {
            routes,
            checkpoint_rules: vec![],
            processors: HashMap::new(),
            on_member_failed: None,
                middlewares: vec![],
            max_turns: 5,
            tool_timeout_ms: Some(3_000),
        inbound_dedup_capacity: 1024,
        },
        tools: vec![],
    }
}

async fn register_target(bus: &Bus, id: &str) -> NodeId {
    let nid = NodeId::new(id);
    let info = arf_core::NodeInfo {
        node_id: nid.clone(),
        node_type: "test_target".into(),
        capabilities: serde_json::json!({"kind": "test_target"}),
        online_since: 0,
    };
    let filter = arf_core::MessageFilter {
        types: None,
        to_match: arf_core::ToMatch::BroadcastAndDirectedToMe,
    };
    let _handle = bus.connect(info, filter).await.expect("connect dummy");
    nid
}

fn count_events(jsonl_path: &std::path::Path, event_type: &str) -> usize {
    let Ok(content) = std::fs::read_to_string(jsonl_path) else { return 0; };
    content.lines().filter(|l| l.contains(&format!("\"event_type\":\"{}\"", event_type))).count()
}

/// Pre-save a session so `Engine::run()` accepts it (Task 9 F-012 fail-fast).
async fn pre_save(store: &dyn SessionStore, session_id: &str) {
    let data = SessionData {
        meta: SessionMeta {
            session_id: session_id.into(),
            title: "test".into(),
            created_at: Utc::now(),
            updated_at: Utc::now(),
            round_count: 0,
            turn_count: 0,
            status: SessionStatus::Active,
            current_round: None,
        },
        state: State::new(),
        last_checkpoint: None,
        config_snapshot: serde_json::json!({}),
        model_params: arf_core::CoreModelParams::default(),
    };
    store.save(&data).await.unwrap();
}

// [方法] Engine run() → round_start + model_call_end + round_end 三事件
#[tokio::test(flavor = "multi_thread")]
async fn engine_run_emits_round_and_model_events() {
    let tmp = tempfile::tempdir().unwrap();
    let store = Arc::new(JsonlSessionStore::new(tmp.path()));
    pre_save(&*store, "E1-sess").await;

    let bus = Arc::new(Bus::new(Duration::from_millis(500), Duration::from_secs(2), 32));
    let _ma = ModelAdapterNode::new(
        text_provider("ep1", "m1", "hi", 100, 50) as Arc<dyn Provider>,
        &bus,
        NodeId::new("model/m1"),
    ).await.unwrap();
    let _t = register_target(&bus, "T").await;

    let mut engine = EngineBuilder::new(vec![bus.clone()])
        .with_session_store(store.clone() as Arc<dyn SessionStore>)
        .with_agent_id(NodeId::new("E1"))
        .with_session_id("E1-sess")
        .build(make_cfg("ep1", "m1"))
        .await.unwrap();

    let result = engine.run(
        &mut State::new(),
        "hello".to_string(),
        tokio_util::sync::CancellationToken::new(),
    ).await;
    assert!(result.is_ok(), "run() failed: {:?}", result.err());

    let jsonl = tmp.path().join("events.E1-sess.jsonl");
    assert_eq!(count_events(&jsonl, "round_start"), 1, "应有 1 个 round_start");
    assert_eq!(count_events(&jsonl, "round_end"), 1, "应有 1 个 round_end");
    assert_eq!(count_events(&jsonl, "model_call_end"), 1, "应有 1 个 model_call_end");

    // 验证 round_end 带 duration_ms > 0
    let content = std::fs::read_to_string(&jsonl).unwrap();
    assert!(content.contains("\"duration_ms\":"), "round_end 应含 duration_ms");
    // model_call_end 含 token 数
    assert!(content.contains("\"input_tokens\":100"));
    assert!(content.contains("\"output_tokens\":50"));
}

// [方法] Engine run() cancel → round_end 仍写（含 duration）
#[tokio::test(flavor = "multi_thread")]
async fn engine_run_cancel_still_writes_round_end() {
    let tmp = tempfile::tempdir().unwrap();
    let store = Arc::new(JsonlSessionStore::new(tmp.path()));
    pre_save(&*store, "E2-sess").await;

    let bus = Arc::new(Bus::new(Duration::from_millis(500), Duration::from_secs(2), 32));
    let _ma = ModelAdapterNode::new(
        text_provider("ep2", "m2", "ok", 1, 1) as Arc<dyn Provider>,
        &bus,
        NodeId::new("model/m2"),
    ).await.unwrap();

    let mut engine = EngineBuilder::new(vec![bus.clone()])
        .with_session_store(store.clone() as Arc<dyn SessionStore>)
        .with_agent_id(NodeId::new("E2"))
        .with_session_id("E2-sess")
        .build(make_cfg("ep2", "m2"))
        .await.unwrap();

    let cancel = tokio_util::sync::CancellationToken::new();
    cancel.cancel();  // 立刻 cancel
    let result = engine.run(
        &mut State::new(),
        "hi".to_string(),
        cancel,
    ).await;
    // Cancel 应让 run 返回 Stopped 错误
    assert!(result.is_err());

    let jsonl = tmp.path().join("events.E2-sess.jsonl");
    assert_eq!(count_events(&jsonl, "round_start"), 1, "round_start 应写");
    assert_eq!(count_events(&jsonl, "round_end"), 1, "cancel 后 round_end 也应写");
}

// [方法] Engine 无 session_store → 仍正常跑（向后兼容）
#[tokio::test(flavor = "multi_thread")]
async fn engine_run_without_session_store_succeeds() {
    let bus = Arc::new(Bus::new(Duration::from_millis(500), Duration::from_secs(2), 32));
    let _ma = ModelAdapterNode::new(
        text_provider("ep3", "m3", "ok", 1, 1) as Arc<dyn Provider>,
        &bus,
        NodeId::new("model/m3"),
    ).await.unwrap();

    let mut engine = EngineBuilder::new(vec![bus.clone()])
        .with_agent_id(NodeId::new("E3"))
        .with_session_id("E3-sess")
        .build(make_cfg("ep3", "m3"))
        .await.unwrap();

    let result = engine.run(
        &mut State::new(),
        "hi".to_string(),
        tokio_util::sync::CancellationToken::new(),
    ).await;
    assert!(result.is_ok(), "无 store 也应成功：{:?}", result.err());
}