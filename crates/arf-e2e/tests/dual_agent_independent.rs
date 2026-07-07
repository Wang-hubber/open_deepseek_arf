//! dual_agent_independent.rs — Phase 9 task 9.9.1
//!
//! 探查：1 个 Bus 上跑 2 个 Engine（每 engine = 1 个 agent），无任何跨 agent 通信。
//!
//! 4 test cases：
//! 1. `two_engines_coexist_on_bus`             — 2 EngineBuilder.build() 同 bus，
//!                                                 graph() 看到 2 engine node
//! 2. `two_engines_run_parallel_independent`    — 各跑各的 user_input，session 独立
//! 3. `two_engines_no_cross_talk`               — engine A 的 response 不会被 B 误收
//! 4. `same_provider_engines_node_id_collision` — 同 provider 撞 agent_id 暴露 lesion
//!
//! 输出物：`docs/v1.x/phase9/audit-probe-9.9.1.md`（独立文件，独立 commit）。

use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration;

use arf_bus::Bus;
use arf_core::{ModelMessage, NodeId, Route, State};
use arf_engine::{AgentConfig, Engine, EngineBuilder, EngineConfig, ModelDecl};
use arf_model_adapter::{ModelAdapterNode, Provider};
use arf_model_adapter::types::{ModelResponsePayload, ToolDef, Usage};
use arf_model_adapter::types::ModelParams;
use arf_model_adapter::ProviderError;
use async_trait::async_trait;
use tokio_util::sync::CancellationToken;

/// A scripted Provider that returns a single text response.
/// Each instance has its own name + model so we can have 2 distinct nodes
/// resolve to different `provider` values when the engine looks up by provider.
struct TaggedMock {
    name: String,
    model: String,
    text: String,
}

#[async_trait]
impl Provider for TaggedMock {
    fn name(&self) -> &str { &self.name }
    fn supported_models(&self) -> &[String] { std::slice::from_ref(&self.model) }

    async fn chat(
        &self,
        _model_name: &str,
        _messages: Vec<ModelMessage>,
        _tools: Vec<ToolDef>,
        _params: ModelParams,
    ) -> Result<ModelResponsePayload, ProviderError> {
        Ok(ModelResponsePayload {
            message: ModelMessage::new("assistant", &self.text),
            tool_calls: None,
            finish_reason: "stop".into(),
            usage: Some(Usage { input_tokens: 5, output_tokens: 5, total_tokens: 10 }),
            id: format!("tagged-{}-{}", self.name, uuid::Uuid::new_v4()),
            model: self.model.clone(),
        })
    }
}

/// Build 2 engines on the same bus, each with its own provider.
async fn build_dual_engines(
    bus: Arc<Bus>,
    a_text: &str,
    b_text: &str,
) -> anyhow::Result<(Engine, Engine, Arc<ModelAdapterNode>, Arc<ModelAdapterNode>)> {
    let prov_a: Arc<dyn Provider> = Arc::new(TaggedMock {
        name: "alpha".into(),
        model: "alpha-v1".into(),
        text: a_text.into(),
    });
    let prov_b: Arc<dyn Provider> = Arc::new(TaggedMock {
        name: "beta".into(),
        model: "beta-v1".into(),
        text: b_text.into(),
    });

    let model_a = ModelAdapterNode::new(prov_a, &bus, NodeId::new("model/alpha")).await?;
    let model_b = ModelAdapterNode::new(prov_b, &bus, NodeId::new("model/beta")).await?;

    let cfg_a = AgentConfig {
        model: ModelDecl { provider: "alpha".into(), model_name: "alpha-v1".into(), ..Default::default() },
        resources: vec![],
        system_prompt_template: "agent A".into(),
        initial_memory: vec![],
        allowed_paths: vec![],
tools: vec![],
        engine: EngineConfig {
            routes: HashMap::<String, Route>::new(),
            checkpoint_rules: vec![],
            processors: HashMap::new(),
            on_member_failed: None,
                middlewares: vec![],
            max_turns: 5,
            tool_timeout_ms: Some(5_000),
        inbound_dedup_capacity: 1024,
        },
    };
    let cfg_b = AgentConfig {
        model: ModelDecl { provider: "beta".into(), model_name: "beta-v1".into(), ..Default::default() },
        resources: vec![],
        system_prompt_template: "agent B".into(),
        initial_memory: vec![],
        allowed_paths: vec![],
tools: vec![],
        engine: EngineConfig {
            routes: HashMap::<String, Route>::new(),
            checkpoint_rules: vec![],
            processors: HashMap::new(),
            on_member_failed: None,
                middlewares: vec![],
            max_turns: 5,
            tool_timeout_ms: Some(5_000),
        inbound_dedup_capacity: 1024,
        },
    };

    let engine_a = EngineBuilder::new(vec![bus.clone()]).build(cfg_a).await?;
    let engine_b = EngineBuilder::new(vec![bus.clone()]).build(cfg_b).await?;
    Ok((engine_a, engine_b, model_a, model_b))
}

#[tokio::test]
async fn two_engines_coexist_on_bus() {
    let bus = Arc::new(Bus::new(Duration::from_millis(500), Duration::from_secs(2), 32));
    let (engine_a, engine_b, _ma, _mb) =
        build_dual_engines(bus.clone(), "from A", "from B").await.expect("build dual");

    // 等 heartbeat tick 让所有 node 全部 online
    tokio::time::sleep(Duration::from_millis(700)).await;

    let g = bus.graph();
    let engine_node_ids: Vec<&str> = g
        .nodes
        .iter()
        .filter(|n| n.node_type == "engine")
        .map(|n| n.node_id.as_str())
        .collect();
    // 两个 engine 各有独立 node_id（"engine/alpha" + "engine/beta"）
    assert!(
        engine_node_ids.contains(&"engine/alpha"),
        "missing engine/alpha, got: {engine_node_ids:?}"
    );
    assert!(
        engine_node_ids.contains(&"engine/beta"),
        "missing engine/beta, got: {engine_node_ids:?}"
    );
    assert_eq!(engine_node_ids.len(), 2, "expected 2 engine nodes, got: {engine_node_ids:?}");

    // agent_id 各自独立
    assert_eq!(engine_a.agent_id().as_str(), "engine/alpha");
    assert_eq!(engine_b.agent_id().as_str(), "engine/beta");
    assert_ne!(engine_a.session_id(), engine_b.session_id(), "session_ids must differ");

    // 各 engine 在 bus 上的订阅（filter 只看 response types）
    assert_eq!(engine_a.handle().subscriptions().len(), 1);
    assert_eq!(engine_b.handle().subscriptions().len(), 1);

    println!("[test1] engine_node_ids={engine_node_ids:?}");
    println!("[test1] agent_a_id={} session_a_id={}",
             engine_a.agent_id().as_str(), engine_a.session_id());
    println!("[test1] agent_b_id={} session_b_id={}",
             engine_b.agent_id().as_str(), engine_b.session_id());
}

#[tokio::test]
async fn two_engines_run_parallel_independent() {
    let bus = Arc::new(Bus::new(Duration::from_millis(500), Duration::from_secs(2), 32));
    let (mut engine_a, mut engine_b, _ma, _mb) =
        build_dual_engines(bus.clone(), "alpha-reply", "beta-reply").await.expect("build dual");

    let mut state_a = State::new();
    let mut state_b = State::new();
    let cancel = CancellationToken::new();

    // 跑两个 engine 的 chat（顺序执行，避免 race）
    let out_a = tokio::time::timeout(
        Duration::from_secs(5),
        engine_a.run(&mut state_a, "q from A".into(), cancel.clone()),
    )
    .await
    .expect("A timeout")
    .expect("A run");

    let out_b = tokio::time::timeout(
        Duration::from_secs(5),
        engine_b.run(&mut state_b, "q from B".into(), cancel),
    )
    .await
    .expect("B timeout")
    .expect("B run");

    assert_eq!(out_a, "alpha-reply", "A output mismatch");
    assert_eq!(out_b, "beta-reply", "B output mismatch");

    // 各自 state 独立：都应是 user + assistant = 2 messages
    assert_eq!(state_a.messages.len(), 2, "A state has wrong msg count: {}", state_a.messages.len());
    assert_eq!(state_b.messages.len(), 2, "B state has wrong msg count: {}", state_b.messages.len());
    assert_eq!(state_a.messages[0].content, "q from A");
    assert_eq!(state_b.messages[0].content, "q from B");
    assert_eq!(state_a.messages[1].content, "alpha-reply");
    assert_eq!(state_b.messages[1].content, "beta-reply");

    println!("[test2] out_a={out_a:?} out_b={out_b:?}");
}

#[tokio::test]
async fn two_engines_no_cross_talk() {
    // 探查点：engine A 触发 model_call，response 应只被 A 收，B 不收
    let bus = Arc::new(Bus::new(Duration::from_millis(500), Duration::from_secs(2), 32));
    let (mut engine_a, mut engine_b, _ma, _mb) =
        build_dual_engines(bus.clone(), "alpha-iso", "beta-iso").await.expect("build dual");

    let mut state_a = State::new();
    let cancel = CancellationToken::new();

    // 只跑 A，bus 流量仅 A ↔ model/alpha
    let out_a = tokio::time::timeout(
        Duration::from_secs(5),
        engine_a.run(&mut state_a, "isolate-A".into(), cancel),
    )
    .await
    .expect("A timeout")
    .expect("A run");
    assert_eq!(out_a, "alpha-iso");

    // B 的 state 不应被 A 污染
    let mut state_b = State::new();
    assert_eq!(state_b.messages.len(), 0, "B state polluted: {} msgs", state_b.messages.len());
    // B 的 round_count 仍 0
    assert_eq!(state_b.over_view.round_count, 0);

    // 现在 B 跑一次，验证 A 留下的 bus 状态不影响 B 的 chat
    let cancel2 = CancellationToken::new();
    let out_b = tokio::time::timeout(
        Duration::from_secs(5),
        engine_b.run(&mut state_b, "isolate-B".into(), cancel2),
    )
    .await
    .expect("B timeout")
    .expect("B run");
    assert_eq!(out_b, "beta-iso");

    // A 的 round_count 应是 1，B 也是 1
    assert_eq!(state_a.over_view.round_count, 1);
    assert_eq!(state_b.over_view.round_count, 1);

    // bus graph 4 节点（2 engine + 2 model）
    let g = bus.graph();
    let n_engine = g.nodes.iter().filter(|n| n.node_type == "engine").count();
    let n_model = g.nodes.iter().filter(|n| n.node_type == "model").count();
    assert_eq!(n_engine, 2, "expected 2 engines online, got {n_engine}");
    assert_eq!(n_model, 2, "expected 2 models online, got {n_model}");

    println!("[test3] A round_count={} B round_count={}", state_a.over_view.round_count, state_b.over_view.round_count);
    println!("[test3] bus graph: {} engine, {} model nodes", n_engine, n_model);
}

#[tokio::test]
async fn same_provider_engines_node_id_collision() {
    // 探查点：同 provider 名两次 build → agent_id = "engine/{provider}" 撞 → 暴露 lesion
    let bus = Arc::new(Bus::new(Duration::from_millis(500), Duration::from_secs(2), 32));

    let prov_a: Arc<dyn Provider> = Arc::new(TaggedMock {
        name: "alpha".into(),
        model: "alpha-v1".into(),
        text: "a1".into(),
    });
    let prov_b: Arc<dyn Provider> = Arc::new(TaggedMock {
        name: "alpha".into(),  // same name as prov_a
        model: "alpha-v1".into(),
        text: "a2".into(),
    });

    let _model_a = ModelAdapterNode::new(prov_a, &bus, NodeId::new("model/alpha")).await.expect("model_a");
    // Second ModelAdapterNode with same NodeId "model/alpha" will collide at connect.
    // We use a different node_id to avoid blocking the test, then create a SECOND
    // engine that also wants provider="alpha" — the engine's agent_id will collide.
    let _model_b = ModelAdapterNode::new(prov_b, &bus, NodeId::new("model/alpha2")).await.expect("model_b");

    let make_cfg = || AgentConfig {
        model: ModelDecl { provider: "alpha".into(), model_name: "alpha-v1".into(), ..Default::default() },
        resources: vec![],
        system_prompt_template: "A".into(),
        initial_memory: vec![],
        allowed_paths: vec![],
tools: vec![],
        engine: EngineConfig {
            routes: HashMap::<String, Route>::new(),
            checkpoint_rules: vec![],
            processors: HashMap::new(),
            on_member_failed: None,
                middlewares: vec![],
            max_turns: 5,
            tool_timeout_ms: Some(5_000),
        inbound_dedup_capacity: 1024,
        },
    };

    // First engine build succeeds.
    let mut engine_a = EngineBuilder::new(vec![bus.clone()]).build(make_cfg()).await.expect("first build");
    // Second engine build tries to connect to bus with same agent_id "engine/alpha" → AlreadyConnected.
    let result_b = EngineBuilder::new(vec![bus.clone()]).build(make_cfg()).await;
    assert!(
        result_b.is_err(),
        "expected second build to fail with same provider collision, got Ok"
    );
    let err_str = format!("{:?}", result_b.err().unwrap());
    println!("[test4] second-build error: {err_str}");
    // 验证：碰撞经 PrimaryBusConnect (which wraps AlreadyConnected) 上报
    assert!(
        err_str.contains("AlreadyConnected") || err_str.contains("PrimaryBusConnect"),
        "expected collision error, got: {err_str}"
    );

    // 存活的 1 个 engine 仍能跑
    let mut state = State::new();
    let cancel = CancellationToken::new();
    let out = tokio::time::timeout(
        Duration::from_secs(5),
        engine_a.run(&mut state, "ping".into(), cancel),
    )
    .await
    .expect("run timeout")
    .expect("run failed");
    assert_eq!(out, "a1");

    println!("[test4] confirmed F-010: same-provider engine second build fails: {err_str}");
}
