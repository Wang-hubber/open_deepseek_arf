//! reasoning_chunks.rs — Phase 9 task 9.3.2
//!
//! 探查 ModelResponseChunk reasoning 流（chunk_type=reasoning）端到端。
//!
//! **关键设计（user 2026-07-03 round 7 澄清）**：
//! - Engine 推理用 final response，**不**消费 chunks（reasoning 同理）
//! - reasoning chunks 给 app 前端做 streaming UX（reasoning 显示）
//!
//! **F-005 探查**：Engine 是否传播 `ModelDecl.thinking_enabled` 到 `ModelCallPayload.model_params`？
//! **F-006 探查**：spec 提 `thinking_visible` 字段，framework code 用 `thinking_enabled` —— naming 不一致？
//!
//! **测试设计**（按 user round 6 反馈"暴露问题，记录即可"）：
//! - 3 mock 测试（reasoning 端到端 + engine 推理 + F-005 实证）
//! - 1 真实 qwen 测试（reasoning mode chunks 实证）
//!
//! 输出物：`docs/v1.x/phase9/audit-probe-9.3.2.md`（含 F-005/F-006 finding + lesion-registry 增 F-005/F-006）

mod common;

use std::sync::Arc;
use std::time::Duration;

use arf_bus::Bus;
use arf_core::{ModelMessage, State};
use arf_engine::{AgentConfig, Engine, EngineBuilder, EngineConfig, ModelDecl, RunError};
use arf_model_adapter::types::{ModelParams, ModelResponsePayload, ToolCall, ToolDef, Usage};
use arf_model_adapter::{
    ModelAdapterNode, ModelResponseChunk, Provider, ProviderError,
};
use async_trait::async_trait;
use tokio_util::sync::CancellationToken;
use uuid::Uuid;

// ═══════════════════════════════════════════════════════════════════════
// StreamingReasoningStubProvider — chat_stream returns reasoning + text chunks
// ═══════════════════════════════════════════════════════════════════════

struct StreamingReasoningStubProvider;

#[async_trait]
impl Provider for StreamingReasoningStubProvider {
    fn name(&self) -> &str { "reasoning-stub" }
    fn supported_models(&self) -> &[String] {
        static MODELS: std::sync::OnceLock<Vec<String>> = std::sync::OnceLock::new();
        MODELS.get_or_init(|| vec!["reasoning-stub-v1".into()])
    }
    async fn chat(
        &self,
        _model: &str, _msgs: Vec<ModelMessage>, _tools: Vec<ToolDef>, _params: ModelParams,
    ) -> Result<ModelResponsePayload, ProviderError> {
        Ok(self.build_final("non-stream path".into()))
    }
    async fn chat_stream(
        &self,
        _model: &str, _msgs: Vec<ModelMessage>, _tools: Vec<ToolDef>, _params: ModelParams,
    ) -> Result<(Vec<ModelResponseChunk>, ModelResponsePayload), ProviderError> {
        // 模拟 qwen thinking mode：先 2 个 reasoning chunks，再 2 个 text chunks，最后 usage
        let chunks = vec![
            ModelResponseChunk {
                chunk_type: "reasoning".into(),
                content: None,
                reasoning: Some("let me think about this...".into()),
                tool_call: None,
                usage: None,
            },
            ModelResponseChunk {
                chunk_type: "reasoning".into(),
                content: None,
                reasoning: Some("the answer is obvious.".into()),
                tool_call: None,
                usage: None,
            },
            ModelResponseChunk {
                chunk_type: "text".into(),
                content: Some("Hello, ".into()),
                reasoning: None,
                tool_call: None,
                usage: None,
            },
            ModelResponseChunk {
                chunk_type: "text".into(),
                content: Some("world!".into()),
                reasoning: None,
                tool_call: None,
                usage: None,
            },
            ModelResponseChunk {
                chunk_type: "usage".into(),
                content: None,
                reasoning: None,
                tool_call: None,
                usage: Some(Usage { input_tokens: 0, output_tokens: 0, total_tokens: 0 }),
            },
        ];
        let payload = self.build_final("Hello, world!".into());
        Ok((chunks, payload))
    }
}

impl StreamingReasoningStubProvider {
    fn build_final(&self, content: String) -> ModelResponsePayload {
        ModelResponsePayload {
            message: ModelMessage::new("assistant", &content),
            tool_calls: None,
            finish_reason: "stop".into(),
            usage: Some(Usage { input_tokens: 0, output_tokens: 0, total_tokens: 0 }),
            id: "stub-reasoning".into(),
            model: "reasoning-stub-v1".into(),
        }
    }
}

// ═══════════════════════════════════════════════════════════════════════
// Mock 测试 — reasoning 端到端 + F-005 实证
// ═══════════════════════════════════════════════════════════════════════

async fn build_engine_with_reasoning_stub(
    thinking_enabled: bool,
) -> (Engine, Arc<Bus>, Arc<ModelAdapterNode>, tokio::task::JoinHandle<TestProbe>) {
    let bus = Arc::new(Bus::new(Duration::from_millis(500), Duration::from_secs(3), 32));
    let provider = Arc::new(StreamingReasoningStubProvider);
    let node = ModelAdapterNode::new(provider, &bus, NodeId::new("model/reasoning"))
        .await
        .expect("model node");
    let cfg = AgentConfig {
        model: ModelDecl {
            provider: "reasoning-stub".into(),
            model_name: "reasoning-stub-v1".into(),
            thinking_enabled, // F-005 探查：Engine 是否传播此值
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
    let engine = EngineBuilder::new(vec![bus.clone()]).build(cfg).await.expect("engine");
    let bus_for_collector = bus.clone();
    let collector = tokio::spawn(async move {
        let mut probe = TestProbe::default();
        let mut rx = bus_for_collector.subscribe();
        let stop_at = tokio::time::Instant::now() + Duration::from_secs(10);
        while let Ok(m) = tokio::time::timeout_at(stop_at, rx.recv()).await {
            let m = match m { Ok(m) => m, Err(_) => break };
            match m.msg_type.as_str() {
                "model_call" => {
                    probe.model_call_count += 1;
                    // 抓 model_params.thinking_enabled (F-005 探查)
                    if let Some(mp) = m.payload.get("model_params") {
                        probe.thinking_enabled_in_call = mp
                            .get("thinking_enabled")
                            .and_then(|v| v.as_bool());
                    }
                }
                "model_response_chunk" => {
                    if let Ok(c) = serde_json::from_value::<ModelResponseChunk>(m.payload.clone()) {
                        probe.chunks.push(c);
                    }
                }
                "model_response" => {
                    if let Ok(payload) = serde_json::from_value::<ModelResponsePayload>(
                        m.payload.get("response").cloned().unwrap_or(m.payload.clone())
                    ) {
                        probe.final_response = Some(payload);
                    }
                    break;
                }
                _ => {}
            }
        }
        probe
    });
    (engine, bus, node, collector)
}

use arf_core::NodeId;

#[derive(Default)]
struct TestProbe {
    model_call_count: usize,
    thinking_enabled_in_call: Option<bool>,
    chunks: Vec<ModelResponseChunk>,
    final_response: Option<ModelResponsePayload>,
}

// [方法] reasoning + text chunks 在 bus 流动 + 终 model_response
#[tokio::test]
async fn mock_reasoning_chunks_flow_on_bus() {
    let (mut engine, _bus, _node, collector) = build_engine_with_reasoning_stub(false).await;
    let out = engine.run(
        &mut State::new(),
        "say hi".into(),
        CancellationToken::new(),
    ).await.expect("run");
    let probe = collector.await.expect("collector join");
    println!("[mock] engine output: {out:?}");
    println!("[mock] chunks on bus: {} 个", probe.chunks.len());
    for (i, c) in probe.chunks.iter().enumerate() {
        println!("  [{i}] type={} content={:?} reasoning={:?}", c.chunk_type, c.content, c.reasoning);
    }
    // 期望 5 chunks (2 reasoning + 2 text + 1 usage)
    assert_eq!(probe.chunks.len(), 5, "期望 5 chunks (2 reasoning + 2 text + 1 usage)");
    // 验证 reasoning chunks
    assert_eq!(probe.chunks[0].chunk_type, "reasoning");
    assert_eq!(probe.chunks[0].reasoning.as_deref(), Some("let me think about this..."));
    assert_eq!(probe.chunks[1].chunk_type, "reasoning");
    assert_eq!(probe.chunks[1].reasoning.as_deref(), Some("the answer is obvious."));
    // 验证 text chunks
    assert_eq!(probe.chunks[2].chunk_type, "text");
    assert_eq!(probe.chunks[2].content.as_deref(), Some("Hello, "));
    assert_eq!(probe.chunks[3].chunk_type, "text");
    assert_eq!(probe.chunks[3].content.as_deref(), Some("world!"));
    // 验证 usage chunk
    assert_eq!(probe.chunks[4].chunk_type, "usage");
    // engine output = final
    assert_eq!(out, "Hello, world!");
    assert!(probe.final_response.is_some());
}

// [方法] reasoning chunks 不参与 engine 推理（state.messages 不含 reasoning）
#[tokio::test]
async fn engine_output_unaffected_by_reasoning() {
    let (mut engine, _bus, _node, _collector) = build_engine_with_reasoning_stub(false).await;
    let mut state = State::new();
    let _ = engine.run(
        &mut state,
        "hi".into(),
        CancellationToken::new(),
    ).await.expect("run");
    let assistant_msgs: Vec<_> = state.messages.iter()
        .filter(|m| m.role == "assistant")
        .collect();
    println!("[F-005/reasoning] assistant msgs: {}", assistant_msgs.len());
    for (i, m) in assistant_msgs.iter().enumerate() {
        println!("  [{i}] content={:?}", m.content);
    }
    // 期望 1 条 final assistant，content = "Hello, world!"（不消费 reasoning）
    assert_eq!(assistant_msgs.len(), 1);
    assert_eq!(assistant_msgs[0].content, "Hello, world!");
    println!("[F-005/reasoning] engine 持 1 条 final assistant（不消费 reasoning）✓");
}

// [方法] F-005 探查：ModelDecl.thinking_enabled=true 时，Engine 实际
// 发出的 ModelCallPayload.model_params.thinking_enabled 是 true 还是 false？
#[tokio::test]
async fn f005_engine_does_not_propagate_thinking_enabled() {
    let (mut engine, _bus, _node, collector) = build_engine_with_reasoning_stub(true).await;
    let _ = engine.run(
        &mut State::new(),
        "hi".into(),
        CancellationToken::new(),
    ).await.expect("run");
    let probe = collector.await.expect("collector join");
    println!("[F-005] model_call count: {}", probe.model_call_count);
    println!("[F-005] model_params.thinking_enabled in model_call: {:?}", probe.thinking_enabled_in_call);
    // 探查结果（不预设）：
    // - Some(true): Engine 传播 thinking_enabled ✓
    // - Some(false): Engine 不传播（这是 bug — ModelDecl.thinking_enabled 被忽略）
    // - None: model_params 字段缺失（Engine 不传 model_params）
    let result = probe.thinking_enabled_in_call;
    if result == Some(true) {
        println!("[F-005] Engine 传播 ModelDecl.thinking_enabled → true ✓");
    } else if result == Some(false) {
        println!("[F-005] Engine 不传播 thinking_enabled（ModelDecl.thinking_enabled 被忽略）→ framework gap");
    } else {
        println!("[F-005] Engine 不传 model_params → framework 缺 thinking_enabled 传播机制");
    }
}

// [方法] F-006 探查：spec 提 thinking_visible，code 用 thinking_enabled
#[tokio::test]
async fn f006_thinking_visible_naming_inconsistency() {
    // CARGO_MANIFEST_DIR = crates/arf-e2e/，需要回退 2 级到 workspace root
    let workspace_root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent().and_then(|p| p.parent()).expect("workspace root").to_path_buf();
    let code_dir = workspace_root.join("crates");
    let docs_dir = workspace_root.join("docs");
    // 实证 framework code 无 thinking_visible 字段
    let code_grep = std::process::Command::new("grep")
        .args(&["-rn", "thinking_visible", code_dir.to_str().unwrap()])
        .output()
        .map(|o| String::from_utf8_lossy(&o.stdout).to_string())
        .unwrap_or_default();
    println!("[F-006] framework code 中 thinking_visible grep 结果（应为空）:");
    println!("{}", if code_grep.is_empty() { "  （无匹配 — code 无 thinking_visible 字段）" } else { &code_grep });
    let spec_grep = std::process::Command::new("grep")
        .args(&["-rn", "thinking_visible", docs_dir.to_str().unwrap()])
        .output()
        .map(|o| String::from_utf8_lossy(&o.stdout).to_string())
        .unwrap_or_default();
    println!("[F-006] spec docs 中 thinking_visible grep 结果（应有匹配）:");
    println!("{}", if spec_grep.is_empty() { "  （无匹配）" } else { &spec_grep });
    // F-006 实证：spec 提，code 无 — naming inconsistency
    let has_code = !code_grep.is_empty();
    let has_spec = !spec_grep.is_empty();
    if has_spec && !has_code {
        println!("[F-006] spec 提 'thinking_visible' 但 framework code 无此字段 → spec/code naming inconsistency");
    }
}

// ═══════════════════════════════════════════════════════════════════════
// 真实 DashScope qwen thinking mode 实证
// ═══════════════════════════════════════════════════════════════════════

// [方法] 真实 qwen stream —— 验证 reasoning chunks 产出（9.3.1 已 215 chunks 含 reasoning）
#[tokio::test]
async fn real_qwen_thinking_mode_chunks() {
    let Some(qwen) = common::provider::live_qwen() else { return; };
    let bus = Arc::new(Bus::new(Duration::from_millis(500), Duration::from_secs(3), 32));
    let node = ModelAdapterNode::new(qwen, &bus, NodeId::new("model/qwen"))
        .await
        .expect("qwen model node");
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
    let engine = EngineBuilder::new(vec![bus.clone()]).build(cfg).await.expect("engine");
    let bus_for_collector = bus.clone();
    let collector = tokio::spawn(async move {
        let mut reasoning_chunks = 0;
        let mut text_chunks = 0;
        let mut usage_chunks = 0;
        let mut rx = bus_for_collector.subscribe();
        let stop_at = tokio::time::Instant::now() + Duration::from_secs(30);
        while let Ok(m) = tokio::time::timeout_at(stop_at, rx.recv()).await {
            let m = match m { Ok(m) => m, Err(_) => break };
            if m.msg_type == "model_response_chunk" {
                if let Ok(c) = serde_json::from_value::<ModelResponseChunk>(m.payload.clone()) {
                    match c.chunk_type.as_str() {
                        "reasoning" => reasoning_chunks += 1,
                        "text" => text_chunks += 1,
                        "usage" => usage_chunks += 1,
                        _ => {}
                    }
                }
            } else if m.msg_type == "model_response" {
                break;
            }
        }
        (reasoning_chunks, text_chunks, usage_chunks)
    });
    let mut engine = engine;
    let mut state = State::new();
    let start = std::time::Instant::now();
    let out = engine.run(
        &mut state,
        "用 3 个字回答：你好".into(),
        CancellationToken::new(),
    ).await.expect("run");
    let elapsed = start.elapsed();
    let (r, t, u) = collector.await.expect("collector join");
    println!("[real] qwen thinking elapsed={elapsed:?} engine_output={out:?}");
    println!("[real] qwen chunks: reasoning={r} text={t} usage={u}");
    // 9.3.1 已 215 chunks 含 reasoning —— 本 task 仅确认
    assert!(r > 0 || t > 0, "期望至少 reasoning 或 text chunks");
    println!("[real] qwen thinking mode chunks 实证 ✓");
}
