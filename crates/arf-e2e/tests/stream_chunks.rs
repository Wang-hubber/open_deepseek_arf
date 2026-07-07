//! stream_chunks.rs — Phase 9 task 9.3.1
//!
//! 探查 ModelResponseChunk 文本流（chunk_type=text）端到端。
//!
//! **关键发现**（探查前已知）：
//! - `ModelCallPayload.stream` 默认 `true`（model-adapter/src/types.rs:50）
//! - 即使 engine 的 `ModelCall`（core）无 stream 字段，adapter 仍以 stream 模式处理
//! - **F-004 framework gap**：Engine `wait_for_strategy`（engine.rs:683-684）只接
//!   `model_response`，**chunks 发出但 engine 不消费**
//!
//! **测试设计**（按 user 2026-07-03 round 6 反馈"暴露问题，记录即可"）：
//! - 3 mock 测试（chunks 端到端行为）
//! - 1 真实 qwen stream 测试（端到端 + 真实 DashScope SSE）
//!
//! 输出物：`docs/v1.x/phase9/audit-probe-9.3.1.md`（含 F-004 实证 + lesion-registry 增 F-004）

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
// StreamingStubProvider — chat_stream returns text chunks
// ═══════════════════════════════════════════════════════════════════════

struct StreamingStubProvider {
    /// 文本 chunks（按序累积成 final response.content）
    chunks: Vec<String>,
}

#[async_trait]
impl Provider for StreamingStubProvider {
    fn name(&self) -> &str { "streaming-stub" }
    fn supported_models(&self) -> &[String] {
        static MODELS: std::sync::OnceLock<Vec<String>> = std::sync::OnceLock::new();
        MODELS.get_or_init(|| vec!["streaming-stub-v1".into()])
    }
    async fn chat(
        &self,
        _model: &str, _msgs: Vec<ModelMessage>, _tools: Vec<ToolDef>, _params: ModelParams,
    ) -> Result<ModelResponsePayload, ProviderError> {
        // 兜底：直接返 final payload（不通过 stream）
        Ok(self.build_final_response("non-stream path".into()))
    }
    async fn chat_stream(
        &self,
        _model: &str, _msgs: Vec<ModelMessage>, _tools: Vec<ToolDef>, _params: ModelParams,
    ) -> Result<(Vec<ModelResponseChunk>, ModelResponsePayload), ProviderError> {
        // 模拟 SSE 流：每 chunk 一个 text delta + 最后 finish chunk
        let mut chunks = Vec::with_capacity(self.chunks.len() + 1);
        for c in &self.chunks {
            chunks.push(ModelResponseChunk {
                chunk_type: "text".into(),
                content: Some(c.clone()),
                reasoning: None,
                tool_call: None,
                usage: None,
            });
        }
        chunks.push(ModelResponseChunk {
            chunk_type: "usage".into(),
            content: None,
            reasoning: None,
            tool_call: None,
            usage: Some(Usage { input_tokens: 0, output_tokens: 0, total_tokens: 0 }),
        });
        let final_content = self.chunks.join("");
        let payload = self.build_final_response(final_content);
        Ok((chunks, payload))
    }
}

impl StreamingStubProvider {
    fn build_final_response(&self, content: String) -> ModelResponsePayload {
        ModelResponsePayload {
            message: ModelMessage::new("assistant", &content),
            tool_calls: None,
            finish_reason: "stop".into(),
            usage: Some(Usage { input_tokens: 0, output_tokens: 0, total_tokens: 0 }),
            id: "stub-stream".into(),
            model: "streaming-stub-v1".into(),
        }
    }
}

// ═══════════════════════════════════════════════════════════════════════
// Mock 测试 — chunks 端到端 + F-004 实证
// ═══════════════════════════════════════════════════════════════════════

// [方法] build engine + model node + collector，验证 chunks 在 bus 上
struct ChunksCollector {
    chunks: Vec<ModelResponseChunk>,
    final_response: Option<ModelResponsePayload>,
}

async fn build_engine_with_streaming_stub(chunks: Vec<String>) -> (Engine, Arc<Bus>, Arc<ModelAdapterNode>, tokio::task::JoinHandle<ChunksCollector>) {
    let bus = Arc::new(Bus::new(Duration::from_millis(500), Duration::from_secs(3), 32));
    let provider = Arc::new(StreamingStubProvider { chunks });
    let node = ModelAdapterNode::new(provider, &bus, NodeId::new("model/stream"))
        .await
        .expect("model node");
    let cfg = AgentConfig {
        model: ModelDecl {
            provider: "streaming-stub".into(),
            model_name: "streaming-stub-v1".into(),
            ..Default::default()
        },
        system_prompt_template: "You are helpful.".into(),
        initial_memory: vec![],
        allowed_paths: vec![],
tools: vec![],
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
        let mut collector = ChunksCollector { chunks: vec![], final_response: None };
        let mut rx = bus_for_collector.subscribe();
        let stop_at = tokio::time::Instant::now() + Duration::from_secs(10);
        while let Ok(m) = tokio::time::timeout_at(stop_at, rx.recv()).await {
            let m = match m { Ok(m) => m, Err(_) => break };
            if m.msg_type == "model_response_chunk" {
                if let Ok(chunk) = serde_json::from_value::<ModelResponseChunk>(m.payload.clone()) {
                    collector.chunks.push(chunk);
                }
            } else if m.msg_type == "model_response" {
                if let Ok(payload) = serde_json::from_value::<ModelResponsePayload>(m.payload.get("response").cloned().unwrap_or(m.payload.clone())) {
                    collector.final_response = Some(payload);
                }
                break;
            }
        }
        collector
    });
    (engine, bus, node, collector)
}

use arf_core::NodeId;

// [方法] mock chunks 在 bus 流动 + 终 model_response
#[tokio::test]
async fn mock_chunks_flow_on_bus() {
    let (mut engine, _bus, _node, collector) = build_engine_with_streaming_stub(
        vec!["Hello".into(), ", ".into(), "world!".into()]
    ).await;
    let out = engine.run(
        &mut State::new(),
        "say hi".into(),
        CancellationToken::new(),
    ).await.expect("run");
    println!("[mock] engine output: {out:?}");
    let coll = collector.await.expect("collector join");
    println!("[mock] chunks on bus: {} 个", coll.chunks.len());
    for (i, c) in coll.chunks.iter().enumerate() {
        println!("  [{i}] type={} content={:?}", c.chunk_type, c.content);
    }
    assert_eq!(coll.chunks.len(), 4, "期望 4 chunks (3 text + 1 usage)");
    assert_eq!(coll.chunks[0].content.as_deref(), Some("Hello"));
    assert_eq!(coll.chunks[1].content.as_deref(), Some(", "));
    assert_eq!(coll.chunks[2].content.as_deref(), Some("world!"));
    assert_eq!(coll.chunks[3].chunk_type, "usage");
    assert_eq!(out, "Hello, world!", "engine output 应等于累积 final content");
    assert!(coll.final_response.is_some(), "应收到 final model_response");
    assert_eq!(coll.final_response.unwrap().message.content, "Hello, world!");
}

// [方法] F-004 实证：engine 端不持有中间 chunks（state.messages 末尾只 final）
#[tokio::test]
async fn f004_engine_ignores_chunks_internally() {
    let (mut engine, _bus, _node, _collector) = build_engine_with_streaming_stub(
        vec!["chunk1".into(), "chunk2".into(), "chunk3".into()]
    ).await;
    let mut state = State::new();
    let _ = engine.run(
        &mut state,
        "hi".into(),
        CancellationToken::new(),
    ).await.expect("run");
    // 验证 state.messages 末尾是 final assistant content，**不**含中间 chunks 累积
    let assistant_msgs: Vec<_> = state.messages.iter()
        .filter(|m| m.role == "assistant")
        .collect();
    println!("[F-004] assistant msgs in state: {}", assistant_msgs.len());
    for (i, m) in assistant_msgs.iter().enumerate() {
        println!("  [{i}] content={:?}", m.content);
    }
    // 期望：只有 1 条 assistant 消息（final），content = "chunk1chunk2chunk3" 累积
    // （**不**含 3 条中间 chunk 消息——engine 不消费 model_response_chunk）
    assert_eq!(assistant_msgs.len(), 1, "engine 应只持 1 条 final assistant 消息");
    assert_eq!(assistant_msgs[0].content, "chunk1chunk2chunk3");
    println!("[F-004] engine 持有 1 条 final assistant 消息（不消费中间 chunks）→ F-004 framework gap 实证 ✓");
}

// [方法] F-004 实证：engine 端不暴露 chunks 给 app 层（无 streaming callback API）
#[tokio::test]
async fn f004_no_streaming_callback_api_for_app() {
    // 当前 framework 探查：app 想看 chunks 必须自订阅 bus（无 framework 提供的 hook）
    let (mut engine, _bus, _node, _collector) = build_engine_with_streaming_stub(
        vec!["a".into(), "b".into()]
    ).await;
    let mut state = State::new();
    let _ = engine.run(
        &mut state,
        "hi".into(),
        CancellationToken::new(),
    ).await.expect("run");
    // engine.run 返 String（final content），**不**返 stream/chunks API
    // → app 层无法通过 framework 拿 chunks（只能自订阅 bus）
    // 这是 F-004 的一部分：缺 stream event callback API
    println!("[F-004] engine.run 返 String（无 stream API）→ app 拿不到 chunks");
}

// ═══════════════════════════════════════════════════════════════════════
// 真实 DashScope qwen stream 端到端
// ═══════════════════════════════════════════════════════════════════════

// [方法] 真实 LLM stream 端到端：qwen SSE 流式 chunks 在 bus 流动
#[tokio::test]
async fn real_qwen_stream_chunks_observable() {
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
tools: vec![],
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
        let mut chunks = vec![];
        let mut rx = bus_for_collector.subscribe();
        let stop_at = tokio::time::Instant::now() + Duration::from_secs(30);
        while let Ok(m) = tokio::time::timeout_at(stop_at, rx.recv()).await {
            let m = match m { Ok(m) => m, Err(_) => break };
            if m.msg_type == "model_response_chunk" {
                if let Ok(c) = serde_json::from_value::<ModelResponseChunk>(m.payload.clone()) {
                    chunks.push(c);
                }
            } else if m.msg_type == "model_response" {
                break;
            }
        }
        chunks
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
    let chunks = collector.await.expect("collector join");
    println!("[real] qwen stream elapsed={elapsed:?} engine_output={out:?} chunks={} 个", chunks.len());
    for (i, c) in chunks.iter().take(5).enumerate() {
        println!("  [{i}] type={} content={:?}", c.chunk_type, c.content);
    }
    // 真实 qwen stream 应产出多个 text chunks
    assert!(chunks.len() >= 2, "期望 ≥ 2 chunks，实测 {} 个（流式证据）", chunks.len());
    assert!(!out.is_empty(), "engine output 非空");
    println!("[real] qwen stream 实证：{} chunks 在 bus 流动 + engine 获 final response ✓", chunks.len());
}
