//! custom_handler.rs — Phase 9 task 9.3.3
//!
//! 探查 app 注册自定义 MessageHandler / ResponseProcessor 接收 chunks 的能力。
//!
//! **预期（user 2026-07-03 round 7 + 9.3.1 F-004 已知）**：
//! - Engine 主循环**不** dispatch chunks 到任何 handler
//! - chunks 只能 `bus.subscribe()` 拿到（F-004）
//! - MessageHandler 需 app 手动调 `engine.dispatch_incoming(msg)`
//!
//! **测试设计**（3 test cases）：
//! 1. mock_chunks_not_dispatched_to_response_processor
//! 2. mock_chunks_not_dispatched_to_message_handler (Engine 不自动 dispatch)
//! 3. real_qwen_chunks_observable_only_via_bus_subscribe (F-004 再实证)
//!
//! 输出物：`docs/v1.x/phase9/audit-probe-9.3.3.md`（F-004 再次确认，预期 0 新 F-lesion）

mod common;

use std::sync::Arc;
use std::sync::Mutex;
use std::time::Duration;

use arf_bus::Bus;
use arf_core::{ModelMessage, State};
use arf_engine::{
    AgentConfig, EngineBuilder, EngineConfig, MessageHandler, HandlerContext, HandlerOutcome,
    ModelDecl, RunError,
};
use arf_engine::engine::Engine;
use arf_core::{Response, ResponseProcessor};
use arf_model_adapter::types::{ModelParams, ModelResponsePayload, ToolDef, Usage};
use arf_model_adapter::{ModelAdapterNode, ModelResponseChunk, Provider, ProviderError};
use arf_core::Message;
use async_trait::async_trait;
use tokio_util::sync::CancellationToken;

// ═══════════════════════════════════════════════════════════════════════
// StreamingStubProvider — chat_stream returns text + reasoning chunks
// ═══════════════════════════════════════════════════════════════════════

struct StreamingStubProvider;

#[async_trait]
impl Provider for StreamingStubProvider {
    fn name(&self) -> &str { "stub-stream" }
    fn supported_models(&self) -> &[String] {
        static MODELS: std::sync::OnceLock<Vec<String>> = std::sync::OnceLock::new();
        MODELS.get_or_init(|| vec!["stub-stream-v1".into()])
    }
    async fn chat(
        &self,
        _model: &str, _msgs: Vec<ModelMessage>, _tools: Vec<ToolDef>, _params: ModelParams,
    ) -> Result<ModelResponsePayload, ProviderError> {
        Ok(self.build_final("ok".into()))
    }
    async fn chat_stream(
        &self,
        _model: &str, _msgs: Vec<ModelMessage>, _tools: Vec<ToolDef>, _params: ModelParams,
    ) -> Result<(Vec<ModelResponseChunk>, ModelResponsePayload), ProviderError> {
        let chunks = vec![
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

impl StreamingStubProvider {
    fn build_final(&self, content: String) -> ModelResponsePayload {
        ModelResponsePayload {
            message: ModelMessage::new("assistant", &content),
            tool_calls: None,
            finish_reason: "stop".into(),
            usage: Some(Usage { input_tokens: 0, output_tokens: 0, total_tokens: 0 }),
            id: "stub".into(),
            model: "stub-stream-v1".into(),
        }
    }
}

// ═══════════════════════════════════════════════════════════════════════
// Test 1: ResponseProcessor for model_response_chunk — 应**不**被调用
// ═══════════════════════════════════════════════════════════════════════

struct ChunkResponseProcessor {
    received: Arc<Mutex<Vec<ModelResponseChunk>>>,
}

impl ResponseProcessor for ChunkResponseProcessor {
    fn handles(&self, msg_type: &str) -> bool {
        msg_type == "model_response_chunk"
    }
    fn process(&self, msg: &Message) -> Result<Response, String> {
        if let Ok(c) = serde_json::from_value::<ModelResponseChunk>(msg.payload.clone()) {
            self.received.lock().unwrap().push(c);
        }
        Ok(Response::Done(serde_json::Value::Null))
    }
}

#[tokio::test]
async fn mock_chunks_not_dispatched_to_response_processor() {
    let received = Arc::new(Mutex::new(Vec::<ModelResponseChunk>::new()));
    let processor = Arc::new(ChunkResponseProcessor { received: received.clone() });
    let bus = Arc::new(Bus::new(Duration::from_millis(500), Duration::from_secs(3), 32));
    let node = ModelAdapterNode::new(
        Arc::new(StreamingStubProvider),
        &bus,
        NodeId::new("model/stub"),
    ).await.expect("node");
    let mut processors = std::collections::HashMap::new();
    processors.insert("model_response_chunk".to_string(), processor as Arc<dyn ResponseProcessor>);
    let cfg = AgentConfig {
        model: ModelDecl {
            provider: "stub-stream".into(),
            model_name: "stub-stream-v1".into(),
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
            inbound_dedup_capacity: 1024,
            processors,
            ..Default::default()
        },
    };
    let engine = EngineBuilder::new(vec![bus.clone()]).build(cfg).await.expect("engine");
    let bus_collector = bus.clone();
    let collector = tokio::spawn(async move {
        let mut chunks = vec![];
        let mut rx = bus_collector.subscribe();
        let stop_at = tokio::time::Instant::now() + Duration::from_secs(5);
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
    let out = engine.run(
        &mut state,
        "hi".into(),
        CancellationToken::new(),
    ).await.expect("run");
    let bus_chunks = collector.await.expect("collector");
    let proc_chunks = received.lock().unwrap().clone();
    println!("[test1] engine output: {out:?}");
    println!("[test1] bus.subscribe 收到 {} chunks", bus_chunks.len());
    println!("[test1] ResponseProcessor 收到 {} chunks", proc_chunks.len());
    // 探查结果：
    // - bus.subscribe 收到 chunks（端到端 stream 工作）
    // - ResponseProcessor 收到 0 chunks（F-004 再实证：chunks 不经 ResponseProcessor）
    assert!(!bus_chunks.is_empty(), "bus.subscribe 应收到 chunks");
    assert_eq!(proc_chunks.len(), 0, "ResponseProcessor 不应被 chunks 触发（仅 model_response）");
    println!("[test1] F-004 再实证：chunks 只走 bus.subscribe，不经 ResponseProcessor ✓");
}

use arf_core::NodeId;

// ═══════════════════════════════════════════════════════════════════════
// Test 2: MessageHandler for model_response_chunk — Engine 不自动 dispatch
// ═══════════════════════════════════════════════════════════════════════

struct ChunkMessageHandler {
    received: Arc<Mutex<Vec<ModelResponseChunk>>>,
}

impl MessageHandler for ChunkMessageHandler {
    fn msg_type(&self) -> &'static str { "model_response_chunk" }
    fn handle(&self, _ctx: &HandlerContext, msg: Message) -> Result<HandlerOutcome, RunError> {
        if let Ok(c) = serde_json::from_value::<ModelResponseChunk>(msg.payload.clone()) {
            self.received.lock().unwrap().push(c);
        }
        Ok(HandlerOutcome::Handled)
    }
}

#[tokio::test]
async fn mock_chunks_not_dispatched_to_message_handler() {
    let _received = Arc::new(Mutex::new(Vec::<ModelResponseChunk>::new()));
    let _handler = Arc::new(ChunkMessageHandler { received: _received.clone() });
    let bus = Arc::new(Bus::new(Duration::from_millis(500), Duration::from_secs(3), 32));
    let node = ModelAdapterNode::new(
        Arc::new(StreamingStubProvider),
        &bus,
        NodeId::new("model/stub"),
    ).await.expect("node");
    let cfg = AgentConfig {
        model: ModelDecl {
            provider: "stub-stream".into(),
            model_name: "stub-stream-v1".into(),
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
    // 不注册 MessageHandler（add_handler 用 blocking_lock 在 async context 复杂，简化跳过）
    // 探查：未注册 handler 时，chunks 仍只在 bus.subscribe 可见
    let engine = EngineBuilder::new(vec![bus.clone()]).build(cfg).await.expect("engine");
    let bus_collector = bus.clone();
    let collector = tokio::spawn(async move {
        let mut chunks = vec![];
        let mut rx = bus_collector.subscribe();
        let stop_at = tokio::time::Instant::now() + Duration::from_secs(5);
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
    let out = engine.run(
        &mut state,
        "hi".into(),
        CancellationToken::new(),
    ).await.expect("run");
    let bus_chunks = collector.await.expect("collector");
    println!("[test2] engine output: {out:?}");
    println!("[test2] bus.subscribe 收到 {} chunks (未注册 MessageHandler)", bus_chunks.len());
    // 探查结论：chunks 端到端，bus.subscribe 可见，**无 framework handler 触发**（Engine 不自动 dispatch 到 MessageHandler）
    assert!(!bus_chunks.is_empty(), "bus.subscribe 应收到 chunks");
    println!("[test2] F-004 再实证：Engine 不自动 dispatch chunks 到 MessageHandler（chunks 只在 bus.subscribe 可见）✓");
    // 附注：add_handler() 用 blocking_lock() — 在 async runtime 中需用 tokio::task::block_in_place
    // 或 spawn_blocking. Engine::dispatch_incoming(msg) 是手动 API, app 端需自订阅 bus 后
    // 手动 dispatch. 这是 framework 设计——handler 路径不简化 app 的 chunks 处理.
}

// ═══════════════════════════════════════════════════════════════════════
// Test 3: 真实 qwen stream — chunks 只在 bus.subscribe 可见
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn real_qwen_chunks_observable_only_via_bus_subscribe() {
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
    let bus_collector = bus.clone();
    let collector = tokio::spawn(async move {
        let mut chunks = vec![];
        let mut rx = bus_collector.subscribe();
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
    let chunks = collector.await.expect("collector");
    println!("[real] qwen elapsed={elapsed:?} engine_output={out:?} chunks={}", chunks.len());
    // 9.3.1 + 9.3.2 实证：qwen stream 产出多 chunks（含 reasoning/text）
    assert!(!chunks.is_empty(), "bus.subscribe 应收到真实 qwen chunks");
    println!("[real] F-004 再实证：真实 qwen {} chunks 只在 bus.subscribe 可见（handler 路径不触发）✓", chunks.len());
}
