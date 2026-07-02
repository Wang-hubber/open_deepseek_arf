//! Live integration tests against DeepSeek API.
//!
//! These tests require a valid API key. They are `#[ignore]` by default.
//!
//! ```bash
//! export DEEPSEEK_API_KEY=sk-xxx
//! cargo test --package arf-model-adapter --test deepseek_live -- --ignored --nocapture
//! ```

use arf_core::ModelMessage;
use arf_model_adapter::{
    AnthropicConfig, AnthropicProvider, DeepSeekConfig, DeepSeekProvider, ModelParams, Provider,
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

// ═══════════════════════════════════════════════════════════════
// OpenAI format (DeepSeekProvider)
// ═══════════════════════════════════════════════════════════════

mod openai_format {
    use super::*;

    fn provider() -> DeepSeekProvider {
        let config = DeepSeekConfig::new(
            api_key(),
            vec!["deepseek-v4-flash".into(), "deepseek-v4-pro".into()],
        );
        DeepSeekProvider::new(config)
    }

    // [连通] 基础对话 — 非流式
    #[tokio::test]
    #[ignore]
    async fn basic_chat() {
        let p = provider();
        let msgs = vec![ModelMessage::new("user", "Say hello in one word.")];
        let result = p.chat("deepseek-v4-flash", msgs, vec![], empty_params()).await.unwrap();
        assert_eq!(result.finish_reason, "stop");
        assert!(!result.message.content.is_empty());
        assert!(result.usage.is_some());
        eprintln!("[basic_chat] content: {}", result.message.content);
        eprintln!("[basic_chat] usage: {:?}", result.usage);
    }

    // [连通] 多轮对话 — 模型理解上下文
    #[tokio::test]
    #[ignore]
    async fn multi_round_chat() {
        let p = provider();
        let msgs = vec![
            ModelMessage::new("user", "My name is Alice."),
            ModelMessage::new("assistant", "Nice to meet you, Alice!"),
            ModelMessage::new("user", "What is my name?"),
        ];
        let result = p.chat("deepseek-v4-flash", msgs, vec![], empty_params()).await.unwrap();
        assert!(result.message.content.to_lowercase().contains("alice"));
        eprintln!("[multi_round] content: {}", result.message.content);
    }

    // [工具] 单工具调用 — 模型返回 tool_calls
    #[tokio::test]
    #[ignore]
    async fn single_tool_call() {
        let p = provider();
        let tools = vec![ToolDef {
            name: "get_weather".into(),
            description: "Get current weather for a city".into(),
            parameters: serde_json::json!({
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "City name"}
                },
                "required": ["city"]
            }),
        }];
        let msgs = vec![ModelMessage::new("user", "What is the weather in Beijing?")];
        let result = p.chat("deepseek-v4-flash", msgs, tools, empty_params()).await.unwrap();
        assert_eq!(result.finish_reason, "tool_calls");
        let tc = result.tool_calls.as_ref().unwrap();
        assert!(!tc.is_empty(), "expected at least one tool call");
        assert_eq!(tc[0].name, "get_weather");
        eprintln!("[tool_call] name: {}, args: {}", tc[0].name, tc[0].arguments);
    }

    // [工具] 多工具调用 + 结果回传
    #[tokio::test]
    #[ignore]
    async fn multi_tool_call_with_results() {
        let p = provider();
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
        let msgs = vec![ModelMessage::new(
            "user",
            "What is the weather AND time in Shanghai?",
        )];
        let result = p.chat("deepseek-v4-flash", msgs, tools, empty_params()).await.unwrap();
        eprintln!("[multi_tool] finish_reason: {}", result.finish_reason);

        if result.finish_reason == "tool_calls" {
            let tc = result.tool_calls.as_ref().unwrap();
            eprintln!("[multi_tool] tool_calls count: {}", tc.len());

            // Build proper tool_calls format (type: "function", args as JSON string)
            let api_tool_calls: Vec<Value> = tc.iter().map(|t| {
                let args_str = serde_json::to_string(&t.arguments).unwrap_or_default();
                serde_json::json!({
                    "id": t.id,
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "arguments": args_str
                    }
                })
            }).collect();

            let mut msgs2 = vec![
                ModelMessage::new(
                    "user",
                    "What is the weather AND time in Shanghai?",
                ),
                ModelMessage::new("assistant", "").with_extra(
                    serde_json::json!({"tool_calls": api_tool_calls}),
                ),
            ];
            for tc in tc {
                let result_text = match tc.name.as_str() {
                    "get_weather" => "Sunny, 25°C",
                    "get_time" => "14:30 CST",
                    _ => "done",
                };
                msgs2.push(
                    ModelMessage::new("tool", result_text)
                        .with_tool_call_id(&tc.id)
                        .with_name(&tc.name),
                );
            }
            let result2 = p
                .chat("deepseek-v4-flash", msgs2, vec![], empty_params())
                .await
                .unwrap();
            eprintln!("[multi_tool] final: {}", result2.message.content);
            assert_eq!(result2.finish_reason, "stop");
        }
    }

    // [思考] 开启思考模式 — 返回 reasoning_content
    #[tokio::test]
    #[ignore]
    async fn thinking_enabled() {
        let p = provider();
        let params = ModelParams {
            thinking_enabled: true,
            extra: serde_json::json!({"reasoning_effort": "high"}),
            ..empty_params()
        };
        let msgs = vec![ModelMessage::new(
            "user",
            "Explain quantum computing in one paragraph.",
        )];
        let result = p
            .chat("deepseek-v4-pro", msgs, vec![], params)
            .await
            .unwrap();
        eprintln!("[thinking] content: {}", result.message.content);
        eprintln!("[thinking] extra: {:?}", result.message.extra);
        // deepseek-v4-pro with thinking should have reasoning_content
        let has_reasoning = !result.message.extra.is_null()
            && result.message.extra.get("reasoning_content").is_some();
        eprintln!("[thinking] has reasoning_content: {has_reasoning}");
    }

    // [思考] 关闭思考模式 — 验证非思考模式下仍正常返回
    #[tokio::test]
    #[ignore]
    async fn thinking_disabled() {
        let p = provider();
        let params = ModelParams {
            thinking_enabled: false,
            ..empty_params()
        };
        let msgs = vec![ModelMessage::new("user", "Say hello.")];
        let result = p
            .chat("deepseek-v4-flash", msgs, vec![], params)
            .await
            .unwrap();
        assert_eq!(result.finish_reason, "stop");
        assert!(!result.message.content.is_empty());
        let has_reasoning = !result.message.extra.is_null()
            && result.message.extra.get("reasoning_content").is_some();
        eprintln!("[thinking_off] content: {}", result.message.content);
        eprintln!("[thinking_off] has reasoning_content: {has_reasoning}");
    }

    // [流式] SSE 流式响应
    #[tokio::test]
    #[ignore]
    async fn streaming() {
        let p = provider();
        let msgs = vec![ModelMessage::new("user", "Count from 1 to 5 slowly.")];
        let (chunks, response) = p
            .chat_stream("deepseek-v4-flash", msgs, vec![], empty_params())
            .await
            .unwrap();
        eprintln!("[streaming] chunk count: {}", chunks.len());
        for (i, c) in chunks.iter().enumerate() {
            if c.chunk_type == "text" {
                eprintln!("[streaming] chunk[{i}]: {:?}", c.content);
            }
        }
        assert!(!chunks.is_empty(), "streaming should produce chunks");
        assert!(!response.message.content.is_empty());
        eprintln!("[streaming] full content: {}", response.message.content);
    }
}

// ═══════════════════════════════════════════════════════════════
// Anthropic format (AnthropicProvider → DeepSeek)
// ═══════════════════════════════════════════════════════════════

mod anthropic_format {
    use super::*;

    fn provider() -> AnthropicProvider {
        let mut config = AnthropicConfig::new(
            api_key(),
            vec!["deepseek-v4-flash".into()],
        );
        config.endpoint = "https://api.deepseek.com/anthropic/messages".into();
        AnthropicProvider::new(config)
    }

    // [连通] Anthropic 格式基础对话
    #[tokio::test]
    #[ignore]
    async fn basic_chat() {
        let p = provider();
        let msgs = vec![
            ModelMessage::new("system", "Respond briefly."),
            ModelMessage::new("user", "Say hello in one word."),
        ];
        let result = p.chat("deepseek-v4-flash", msgs, vec![], empty_params()).await.unwrap();
        assert!(!result.message.content.is_empty());
        eprintln!("[anthropic] content: {}", result.message.content);
        eprintln!("[anthropic] finish_reason: {}", result.finish_reason);
        eprintln!("[anthropic] usage: {:?}", result.usage);
    }

    // [连通] Anthropic 格式多轮对话
    #[tokio::test]
    #[ignore]
    async fn multi_round_chat() {
        let p = provider();
        let msgs = vec![
            ModelMessage::new("user", "My favorite color is blue."),
            ModelMessage::new("assistant", "Blue is a great choice!"),
            ModelMessage::new("user", "What did I say my favorite color is?"),
        ];
        let result = p.chat("deepseek-v4-flash", msgs, vec![], empty_params()).await.unwrap();
        assert!(result.message.content.to_lowercase().contains("blue"));
        eprintln!("[anthropic_multi] content: {}", result.message.content);
    }

    // [工具] Anthropic 格式工具调用
    #[tokio::test]
    #[ignore]
    async fn tool_call() {
        let p = provider();
        let tools = vec![ToolDef {
            name: "get_weather".into(),
            description: "Get current weather for a city".into(),
            parameters: serde_json::json!({
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"]
            }),
        }];
        let msgs = vec![ModelMessage::new(
            "user",
            "What is the weather in Tokyo?",
        )];
        let result = p
            .chat("deepseek-v4-flash", msgs, tools, empty_params())
            .await
            .unwrap();
        eprintln!("[anthropic_tool] finish_reason: {}", result.finish_reason);
        if let Some(tc) = &result.tool_calls {
            eprintln!("[anthropic_tool] tool: {} args: {}", tc[0].name, tc[0].arguments);
        }
    }
}
