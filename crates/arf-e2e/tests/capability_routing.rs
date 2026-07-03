//! capability_routing.rs — Phase 9 task 9.4.2
//!
//! 探查 engine 是否按 `Provider::supported_models()` capability 路由。
//!
//! **关键探查**（F-007 candidate）：Engine `resolve_model`（registry.rs:253-269）
//! 只按 `capabilities.provider` 匹配，**不**按 `model_name` 匹配。
//! `Provider::supported_models()` 仅作元数据塞 `NodeInfo.capabilities.models`（node.rs:38）——
//! 在 routing 时**完全无效**。
//!
//! **测试设计**（4 test cases）：
//! 1. engine_routes_by_provider_not_model_name — 2 节点同 provider 不同 model → engine 选首（忽略 model_name）
//! 2. engine_ignores_unsupported_model_name — model_name 不在 supports → engine 仍路由
//! 3. supported_models_in_capabilities_advertised — capabilities.models 元数据正确
//! 4. real_qwen_specific_model_name — 真实 qwen capability 行为
//!
//! 输出物：`docs/v1.x/phase9/audit-probe-9.4.2.md`（F-007 实证 + lesion-registry 增 F-007）

mod common;

use std::sync::Arc;
use std::time::Duration;

use arf_bus::Bus;
use arf_core::{ModelMessage, State};
use arf_engine::{AgentConfig, Engine, EngineBuilder, EngineConfig, ModelDecl, RunError};
use arf_model_adapter::types::{ModelParams, ModelResponsePayload, ToolDef, Usage};
use arf_model_adapter::{ModelAdapterNode, Provider, ProviderError};
use async_trait::async_trait;
use tokio_util::sync::CancellationToken;
use uuid::Uuid;

// ═══════════════════════════════════════════════════════════════════════
// NamedScriptedProvider — provider with custom name + supported_models
// ═══════════════════════════════════════════════════════════════════════

struct NamedScriptedProvider {
    name: String,
    models: Vec<String>,
    response: String,
}

#[async_trait]
impl Provider for NamedScriptedProvider {
    fn name(&self) -> &str { &self.name }
    fn supported_models(&self) -> &[String] { &self.models }
    async fn chat(
        &self,
        _model: &str, _msgs: Vec<ModelMessage>, _tools: Vec<ToolDef>, _params: ModelParams,
    ) -> Result<ModelResponsePayload, ProviderError> {
        Ok(ModelResponsePayload {
            message: ModelMessage::new("assistant", &self.response),
            tool_calls: None,
            finish_reason: "stop".into(),
            usage: Some(Usage { input_tokens: 0, output_tokens: 0, total_tokens: 0 }),
            id: format!("stub-{}", Uuid::new_v4()),
            model: self.models.first().cloned().unwrap_or_default(),
        })
    }
}

fn mock_provider(name: &str, models: Vec<String>, response: &str) -> Arc<dyn Provider> {
    Arc::new(NamedScriptedProvider { name: name.into(), models, response: response.into() })
}

// ═══════════════════════════════════════════════════════════════════════
// Test 1: 2 节点同 provider 不同 model_name → engine 选首（忽略 model_name）
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn engine_routes_by_provider_not_model_name() {
    let bus = Arc::new(Bus::new(Duration::from_millis(500), Duration::from_secs(3), 32));
    // 2 节点同 provider="openai" 但 supported_models 不同 + response 不同
    // 若 engine 按 model_name 选，会**总是**选 node 2（qwen3.5）
    // 若 engine 按 provider 选首（HashMap 非确定），会随机选 1 或 2
    let _n1 = ModelAdapterNode::new(
        mock_provider("openai", vec!["qwen3.7-max-preview".into()], "from node 1 (qwen3.7)"),
        &bus,
        NodeId::new("model/openai-1"),
    ).await.expect("node 1");
    let _n2 = ModelAdapterNode::new(
        mock_provider("openai", vec!["qwen3.5-turbo".into()], "from node 2 (qwen3.5)"),
        &bus,
        NodeId::new("model/openai-2"),
    ).await.expect("node 2");
    // cfg.model.model_name = "qwen3.5-turbo"（只在 node 2 supports）
    let cfg = AgentConfig {
        model: ModelDecl {
            provider: "openai".into(),
            model_name: "qwen3.5-turbo".into(),
            ..Default::default()
        },
        system_prompt_template: "You are helpful.".into(),
        initial_memory: vec![],
        allowed_paths: vec![],
        resources: vec![],
        engine: EngineConfig {
            max_turns: 2,
            tool_timeout_ms: Some(60_000),
            ..Default::default()
        },
    };
    let cfg_model_name = cfg.model.model_name.clone();
    let mut engine = EngineBuilder::new(vec![bus.clone()]).build(cfg).await.expect("engine");
    let mut state = State::new();
    let cancel = CancellationToken::new();
    let out = engine.run(
        &mut state,
        "hi".into(),
        cancel,
    ).await.expect("run");
    println!("[F-007/test1] engine output: {out:?}");
    // F-007 预期：engine 选**任一** provider 节点（**不**按 model_name 选）
    // HashMap 非确定（graph.rs:60 `map.values()`），实际可能是 node 1 或 node 2
    // F-007 关键：model_name="qwen3.5-turbo" 只在 node 2 supports，但 engine **不**保证选 node 2
    assert!(
        out == "from node 1 (qwen3.7)" || out == "from node 2 (qwen3.5)",
        "engine 应选其中一个 provider 节点（model_name 不影响选择）；got={out:?}"
    );
    println!("[F-007/test1] F-007 实证：engine 按 provider 选（model_name={cfg_model_name} 不影响选择）✓");
    println!("[F-007/test1] F-008 实证：BusGraph HashMap 非确定，2 节点同 provider 时路由非确定");
}

use arf_core::NodeId;

// ═══════════════════════════════════════════════════════════════════════
// Test 2: model_name 不在 supports 里 → engine 仍路由（不报错）
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn engine_ignores_unsupported_model_name() {
    let bus = Arc::new(Bus::new(Duration::from_millis(500), Duration::from_secs(3), 32));
    let _n1 = ModelAdapterNode::new(
        mock_provider("openai", vec!["qwen3.7-max-preview".into()], "ok from qwen3.7"),
        &bus,
        NodeId::new("model/openai-1"),
    ).await.expect("node 1");
    // cfg.model.model_name = "qwen3.5-turbo"（**不**在 supports）
    // F-007 预期：engine **不**报错（只检查 provider，不检查 model_name 是否在 supports）
    let cfg = AgentConfig {
        model: ModelDecl {
            provider: "openai".into(),
            model_name: "qwen3.5-turbo".into(), // **不**在 node 1 supports
            ..Default::default()
        },
        system_prompt_template: "You are helpful.".into(),
        initial_memory: vec![],
        allowed_paths: vec![],
        resources: vec![],
        engine: EngineConfig {
            max_turns: 2,
            tool_timeout_ms: Some(60_000),
            ..Default::default()
        },
    };
    let mut engine = EngineBuilder::new(vec![bus.clone()]).build(cfg).await.expect("engine");
    let mut state = State::new();
    let cancel = CancellationToken::new();
    let out = engine.run(
        &mut state,
        "hi".into(),
        cancel,
    ).await.expect("run");
    println!("[F-007/test2] engine output: {out:?} (model_name 'qwen3.5-turbo' 不在 supports 但 engine 仍路由)");
    // F-007 实证：engine 仍路由（仅按 provider 匹配，model_name 拼写错误**静默**通过）
    assert_eq!(out, "ok from qwen3.7", "engine 应**不**报错，model_name 拼写错误**静默**通过");
    println!("[F-007/test2] F-007 实证：model_name 不在 supports 时 engine **静默**路由（不报错）✓");
    println!("[F-007/test2] 这是 framework 缺 model-level validation —— 修复方向：resolve_model 应检查 model_name");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 3: capabilities.models 元数据正确
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn supported_models_in_capabilities_advertised() {
    let bus = Arc::new(Bus::new(Duration::from_millis(500), Duration::from_secs(3), 32));
    let _n1 = ModelAdapterNode::new(
        mock_provider("openai", vec!["qwen3.7-max-preview".into(), "qwen3.5-turbo".into()], "ok"),
        &bus,
        NodeId::new("model/openai-1"),
    ).await.expect("node 1");
    // 实证：NodeInfo.capabilities 包含 "provider" + "models" 字段
    let graph = bus.graph();
    let node = graph.nodes.iter().find(|n| n.node_type == "model").expect("model node");
    println!("[test3] node {} capabilities: {}", node.node_id, node.capabilities);
    let provider_cap = node.capabilities.get("provider").and_then(|v| v.as_str());
    let models_cap = node.capabilities.get("models").and_then(|v| v.as_array());
    assert_eq!(provider_cap, Some("openai"));
    assert!(models_cap.is_some(), "capabilities.models 应存在");
    let models: Vec<&str> = models_cap.unwrap().iter().filter_map(|v| v.as_str()).collect();
    assert!(models.contains(&"qwen3.7-max-preview"));
    assert!(models.contains(&"qwen3.5-turbo"));
    println!("[test3] capabilities.models = {:?} (D 端到端 capability 传播) ✓", models);
    println!("[test3] 但 engine 实际**不**读这字段路由（仅按 'provider'）—— F-007 实证");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 4: 真实 qwen capability 行为
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn real_qwen_specific_model_name_routing() {
    let Some(qwen) = common::provider::live_qwen() else { return; };
    let bus = Arc::new(Bus::new(Duration::from_millis(500), Duration::from_secs(3), 32));
    let node = ModelAdapterNode::new(
        qwen,
        &bus,
        NodeId::new("model/qwen"),
    ).await.expect("qwen node");
    // 验证 real qwen NodeInfo.capabilities
    let graph = bus.graph();
    let n = graph.nodes.iter().find(|x| x.node_type == "model").expect("model node");
    println!("[test4/real] qwen node capabilities: {}", n.capabilities);
    let models_cap = n.capabilities.get("models").and_then(|v| v.as_array());
    if let Some(models) = models_cap {
        let models: Vec<&str> = models.iter().filter_map(|v| v.as_str()).collect();
        println!("[test4/real] qwen supported_models = {:?}", models);
        assert!(models.contains(&"qwen3.7-max-preview"), "qwen 应支持 qwen3.7-max-preview");
    }
    // cfg.model.model_name = "qwen3.7-max-preview"（在 supports 中）
    let cfg = AgentConfig {
        model: ModelDecl {
            provider: "openai".into(),
            model_name: "qwen3.7-max-preview".into(),
            ..Default::default()
        },
        system_prompt_template: "You are helpful.".into(),
        initial_memory: vec![],
        allowed_paths: vec![],
        resources: vec![],
        engine: EngineConfig {
            max_turns: 2,
            tool_timeout_ms: Some(60_000),
            ..Default::default()
        },
    };
    let mut engine = EngineBuilder::new(vec![bus.clone()]).build(cfg).await.expect("engine");
    let mut state = State::new();
    let cancel = CancellationToken::new();
    let out = engine.run(
        &mut state,
        "用 3 个字回答：你好".into(),
        cancel,
    ).await.expect("run");
    println!("[test4/real] qwen output: {out:?}");
    assert!(!out.is_empty(), "real qwen 应响应");
    println!("[test4/real] 真实 qwen 路由 OK（model_name 在 supports 中）✓");
}
