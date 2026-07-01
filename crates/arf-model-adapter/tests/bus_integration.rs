//! Bus 集成测试 —— Engine → Bus → ModelAdapterNode → DeepSeek API 完整链路.
//!
//! 这些测试验证整个消息闭环：Engine 发 model_call → Bus 路由 → Node 收 → Provider 调 API →
//! Node 发 model_response → Bus 路由 → Engine 收.
//!
//! 需要真实 API KEY，默认 `#[ignore]`.
//!
//! ```bash
//! export DEEPSEEK_API_KEY=sk-xxx
//! cargo test --package arf-model-adapter --test bus_integration -- --ignored --nocapture
//! ```

use std::sync::Arc;

use arf_bus::Bus;
use arf_core::{MessageFilter, NodeId, NodeInfo, ToMatch};
use arf_model_adapter::{
    DeepSeekConfig, DeepSeekProvider, ModelAdapterNode, ModelCallPayload, ModelParams, Provider,
    ToolDef,
};
use serde_json::Value;

fn api_key() -> String {
    std::env::var("DEEPSEEK_API_KEY").expect("DEEPSEEK_API_KEY not set")
}

fn empty_params() -> ModelParams {
    ModelParams {
        temperature: None,
        max_tokens: None,
        thinking_enabled: false,
        extra: Value::Null,
    }
}

fn test_bus() -> Bus {
    Bus::new(
        std::time::Duration::from_secs(10),
        std::time::Duration::from_secs(30),
        64,
    )
}

/// Minimal Engine-like node on the Bus — sends model_call, collects responses.
struct EngineStub {
    handle: arf_bus::NodeHandle,
}

impl EngineStub {
    async fn new(bus: &Bus, name: &str) -> Self {
        let info = NodeInfo {
            node_id: NodeId::new(format!("engine/{name}")),
            node_type: "engine".into(),
            capabilities: serde_json::json!({}),
            online_since: 0,
        };
        let filter = MessageFilter {
            types: Some(vec!["model_response".into(), "model_response_chunk".into()]),
            to_match: ToMatch::BroadcastAndDirectedToMe,
        };
        let handle = bus.connect(info, filter).await.unwrap();
        Self { handle }
    }

    /// Send a model_call and wait for the final model_response.
    /// In streaming mode, collect chunks along the way.
    async fn call(
        &mut self,
        target: &NodeId,
        messages: Vec<arf_core::ModelMessage>,
        tools: Vec<ToolDef>,
        params: ModelParams,
        stream: bool,
    ) -> (serde_json::Value, Vec<serde_json::Value>) {
        let payload = ModelCallPayload {
            messages,
            tools,
            model_params: params,
            stream,
        };
        self.handle
            .send(
                "model_call",
                vec![target.clone()],
                serde_json::to_value(&payload).unwrap(),
            )
            .await
            .unwrap();

        let mut chunks = Vec::new();
        // Collect until final model_response (non-chunk)
        loop {
            let msg = self.handle.recv().await.unwrap();
            if msg.msg_type == "model_response_chunk" {
                chunks.push(msg.payload);
            } else if msg.msg_type == "model_response" {
                return (msg.payload, chunks);
            }
        }
    }

    async fn disconnect(self) {
        self.handle.disconnect().await;
    }
}

// ═══════════════════════════════════════════════════════════════
// Bus 集成测试
// ═══════════════════════════════════════════════════════════════

/// Setup: create Bus, ModelAdapterNode, EngineStub.
async fn setup(
    model_name: &str,
) -> (Bus, Arc<ModelAdapterNode>, EngineStub, NodeId) {
    let bus = test_bus();
    let provider = Arc::new(DeepSeekProvider::new(DeepSeekConfig::new(
        api_key(),
        vec![model_name.into(), "deepseek-v4-pro".into()],
    )));
    let node_id = NodeId::new(format!("model/{model_name}"));
    let node = ModelAdapterNode::new(provider, &bus, node_id.clone())
        .await
        .unwrap();
    // Give node a tick to broadcast node_online
    tokio::time::sleep(std::time::Duration::from_millis(10)).await;
    let engine = EngineStub::new(&bus, "test-engine").await;
    (bus, node, engine, node_id)
}

async fn teardown(bus: Bus, node: Arc<ModelAdapterNode>, engine: EngineStub) {
    engine.disconnect().await;
    node.shutdown().await;
    bus.shutdown().await;
}

// ── 基础对话 ────────────────────────────────────────────────────────

// [连通] 基础对话 — 通过 Bus 发送 model_call，收到 model_response
#[tokio::test]
#[ignore]
async fn basic_chat() {
    let (bus, node, mut engine, node_id) = setup("deepseek-v4-flash").await;
    let msgs = vec![arf_core::ModelMessage::new("user", "Say hello in one word.")];
    let (response, chunks) =
        engine.call(&node_id, msgs, vec![], empty_params(), false).await;
    assert!(chunks.is_empty(), "non-streaming should have no chunks");
    assert_eq!(response["finish_reason"], "stop");
    assert!(!response["message"]["content"].as_str().unwrap_or("").is_empty());
    eprintln!("[basic_chat] content: {}", response["message"]["content"]);
    teardown(bus, node, engine).await;
}

// [连通] 多轮对话 — 模型通过 Bus 理解上下文
#[tokio::test]
#[ignore]
async fn multi_round_chat() {
    let (bus, node, mut engine, node_id) = setup("deepseek-v4-flash").await;
    let msgs = vec![
        arf_core::ModelMessage::new("user", "My name is Alice."),
        arf_core::ModelMessage::new("assistant", "Nice to meet you, Alice!"),
        arf_core::ModelMessage::new("user", "What is my name?"),
    ];
    let (response, _) = engine.call(&node_id, msgs, vec![], empty_params(), false).await;
    assert!(response["message"]["content"]
        .as_str()
        .unwrap_or("")
        .to_lowercase()
        .contains("alice"));
    eprintln!("[multi_round] content: {}", response["message"]["content"]);
    teardown(bus, node, engine).await;
}

// ── 工具调用 ────────────────────────────────────────────────────────

// [工具] 单工具调用 — 通过 Bus 收发 tool_calls
#[tokio::test]
#[ignore]
async fn single_tool_call() {
    let (bus, node, mut engine, node_id) = setup("deepseek-v4-flash").await;
    let tools = vec![ToolDef {
        name: "get_weather".into(),
        description: "Get current weather for a city".into(),
        parameters: serde_json::json!({
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"]
        }),
    }];
    let msgs = vec![arf_core::ModelMessage::new(
        "user",
        "What is the weather in Beijing?",
    )];
    let (response, _) = engine
        .call(&node_id, msgs, tools, empty_params(), false)
        .await;
    assert_eq!(response["finish_reason"], "tool_calls");
    let tc = response["tool_calls"].as_array().unwrap();
    assert!(!tc.is_empty());
    assert_eq!(tc[0]["name"], "get_weather");
    eprintln!("[tool_call] name: {}, args: {}", tc[0]["name"], tc[0]["arguments"]);
    teardown(bus, node, engine).await;
}

// [工具] 多工具 + 结果回传
#[tokio::test]
#[ignore]
async fn multi_tool_call_with_results() {
    let (bus, node, mut engine, node_id) = setup("deepseek-v4-flash").await;
    let tools = vec![
        ToolDef {
            name: "get_weather".into(),
            description: "Get current weather".into(),
            parameters: serde_json::json!({
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"]
            }),
        },
        ToolDef {
            name: "get_time".into(),
            description: "Get current time in a city".into(),
            parameters: serde_json::json!({
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"]
            }),
        },
    ];
    let msgs = vec![arf_core::ModelMessage::new(
        "user",
        "What is the weather AND time in Shanghai?",
    )];
    let (response, _) = engine
        .call(&node_id, msgs, tools, empty_params(), false)
        .await;
    eprintln!(
        "[multi_tool] finish_reason: {}",
        response["finish_reason"]
    );

    if response["finish_reason"] == "tool_calls" {
        let tc = response["tool_calls"].as_array().unwrap();
        eprintln!("[multi_tool] tool_calls count: {}", tc.len());

        let api_tool_calls: Vec<Value> = tc
            .iter()
            .map(|t| {
                let args_str = t["arguments"].to_string();
                serde_json::json!({
                    "id": t["id"],
                    "type": "function",
                    "function": { "name": t["name"], "arguments": args_str }
                })
            })
            .collect();

        let mut msgs2 = vec![
            arf_core::ModelMessage::new("user", "What is the weather AND time in Shanghai?"),
            arf_core::ModelMessage::new("assistant", "").with_extra(
                serde_json::json!({"tool_calls": api_tool_calls}),
            ),
        ];
        for t in tc {
            let result_text = match t["name"].as_str().unwrap_or("") {
                "get_weather" => "Sunny, 25°C",
                "get_time" => "14:30 CST",
                _ => "done",
            };
            msgs2.push(
                arf_core::ModelMessage::new("tool", result_text)
                    .with_tool_call_id(t["id"].as_str().unwrap_or(""))
                    .with_name(t["name"].as_str().unwrap_or("")),
            );
        }
        let (response2, _) = engine
            .call(&node_id, msgs2, vec![], empty_params(), false)
            .await;
        assert_eq!(response2["finish_reason"], "stop");
        eprintln!("[multi_tool] final: {}", response2["message"]["content"]);
    }
    teardown(bus, node, engine).await;
}

// ── 思考模式 ────────────────────────────────────────────────────────

// [思考] 开启思考模式 — reasoning_content 通过 Bus 传回
#[tokio::test]
#[ignore]
async fn thinking_enabled() {
    let (bus, node, mut engine, node_id) = setup("deepseek-v4-pro").await;
    let params = ModelParams {
        thinking_enabled: true,
        extra: serde_json::json!({"reasoning_effort": "high"}),
        ..empty_params()
    };
    let msgs = vec![arf_core::ModelMessage::new(
        "user",
        "Explain quantum computing in one paragraph.",
    )];
    let (response, _) = engine.call(&node_id, msgs, vec![], params, false).await;
    let has_reasoning = !response["message"]["extra"].is_null()
        && response["message"]["extra"]
            .get("reasoning_content")
            .is_some();
    eprintln!("[thinking] has reasoning_content: {has_reasoning}");
    eprintln!("[thinking] content: {}", response["message"]["content"]);
    teardown(bus, node, engine).await;
}

// [思考] 关闭思考模式 — thinking: {type:"disabled"} 正确发送
#[tokio::test]
#[ignore]
async fn thinking_disabled() {
    let (bus, node, mut engine, node_id) = setup("deepseek-v4-flash").await;
    let params = ModelParams {
        thinking_enabled: false,
        ..empty_params()
    };
    let msgs = vec![arf_core::ModelMessage::new("user", "Say hello.")];
    let (response, _) = engine.call(&node_id, msgs, vec![], params, false).await;
    assert_eq!(response["finish_reason"], "stop");
    assert!(!response["message"]["content"]
        .as_str()
        .unwrap_or("")
        .is_empty());
    let has_reasoning = !response["message"]["extra"].is_null()
        && response["message"]["extra"]
            .get("reasoning_content")
            .is_some();
    eprintln!("[thinking_off] content: {}", response["message"]["content"]);
    eprintln!("[thinking_off] has reasoning_content: {has_reasoning}");
    teardown(bus, node, engine).await;
}

// ── 流式响应 ────────────────────────────────────────────────────────

// [流式] SSE 流经 Bus 逐 chunk 到达 Engine
#[tokio::test]
#[ignore]
async fn streaming() {
    let (bus, node, mut engine, node_id) = setup("deepseek-v4-flash").await;
    let msgs = vec![arf_core::ModelMessage::new(
        "user",
        "Count from 1 to 5 slowly.",
    )];
    let (response, chunks) = engine
        .call(&node_id, msgs, vec![], empty_params(), true)
        .await;
    eprintln!("[streaming] chunk count: {}", chunks.len());
    for (i, c) in chunks.iter().enumerate() {
        if c["chunk_type"] == "text" {
            eprintln!(
                "[streaming] chunk[{i}]: {:?}",
                c["content"].as_str()
            );
        }
    }
    assert!(!chunks.is_empty(), "streaming should produce chunks");
    assert!(!response["message"]["content"]
        .as_str()
        .unwrap_or("")
        .is_empty());
    eprintln!("[streaming] full content: {}", response["message"]["content"]);
    teardown(bus, node, engine).await;
}

// ── 错误处理 ────────────────────────────────────────────────────────

// [错误] 无效 payload → 返回 error 响应而非 panic
#[tokio::test]
#[ignore]
async fn invalid_payload() {
    let (bus, node, mut engine, node_id) = setup("deepseek-v4-flash").await;
    // Send a malformed model_call (not JSON, just raw text)
    engine
        .handle
        .send(
            "model_call",
            vec![node_id.clone()],
            serde_json::json!("not a valid payload"),
        )
        .await
        .unwrap();
    let msg = engine.handle.recv().await.unwrap();
    assert_eq!(msg.msg_type, "model_response");
    assert!(msg.payload["error"].as_str().unwrap_or("").contains("invalid payload"));
    eprintln!("[error] response: {}", msg.payload);
    teardown(bus, node, engine).await;
}
