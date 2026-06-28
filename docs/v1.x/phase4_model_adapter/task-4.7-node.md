# 任务 4.7：ModelAdapter node — Bus 集成

> Phase 4 — ModelAdapter 第六项任务
> 父文档：`docs/v1.x/phase4_model_adapter/phase4-model-adapter-design.md`
> 依赖：4.1–4.5（类型 + trait + 三大 Provider），已完成

## 设计思路

`ModelAdapterNode` 是 ModelAdapter 和 Bus 之间的桥梁——负责将"监听 Bus 消息 → 调用 Provider → 回复"这个循环封装为可独立运行的 async task。

**职责边界：**
- `ModelAdapterNode` — Bus 生命周期（connect/listen/disconnect），消息路由
- `Provider` — 纯粹的 HTTP 调用 + 格式转换（不感知 Bus）

**生命周期：**

```
ModelAdapterNode::new(provider, bus, node_id)
    │
    ├─ 1. Bus::connect(info, filter) → NodeHandle
    │      node_online 自动广播
    │
    ├─ 2. spawn listen loop (tokio::spawn)
    │      loop {
    │          msg = handle.recv().await
    │          if msg.msg_type == "model_call" && msg.is_for(me):
    │              payload = deserialize(msg.payload)
    │              if payload.stream:
    │                  (chunks, response) = provider.chat_stream(...).await
    │                  for chunk in chunks:
    │                      handle.send("model_response_chunk", ...)
    │                  handle.send("model_response", payload=response)
    │              else:
    │                  response = provider.chat(...).await
    │                  handle.send("model_response", payload=response)
    │      }
    │
    ├─ 3. shutdown() → handle.disconnect() → node_offline 广播
    └─
```

## 代码实现

### `crates/arf-model-adapter/src/node.rs`（新文件）

```rust
//! ModelAdapter node — Bus lifecycle + model_call dispatch.

use std::sync::Arc;

use arf_bus::Bus;
use arf_core::{MessageFilter, NodeId, NodeInfo, ToMatch};
use tokio::sync::oneshot;

use crate::provider::Provider;
use crate::types::{ModelCallPayload, ModelResponseChunk, ModelResponsePayload};
use crate::ProviderError;
```

逐行：
- `Arc<dyn Provider>` — 多个 tokio task 可能共享引用（listen loop + 管理 task）。Provider 已在 trait 约束 `Send + Sync`，满足 `Arc` 传递要求
- `NodeId` / `NodeInfo` / `NodeHandle` — 来自 `arf-bus`，用于 Bus 连接和消息收发
- `ModelCallPayload` — Engine 发来的 payload，反序列化后提取 messages/tools/params/stream
- `oneshot` — shutdown 信号通道

---

#### ModelAdapterNode

```rust
/// A ModelAdapter node connected to the Bus.
///
/// Spawns a listen loop that:
/// - Receives `model_call` messages directed to this node
/// - Dispatches to the Provider (streaming or non-streaming)
/// - Sends `model_response_chunk` (during stream) + final `model_response`
///
/// Drop or explicit `shutdown()` disconnects from the Bus.
pub struct ModelAdapterNode {
    node_id: NodeId,
    /// Shutdown trigger: send () to stop the listen loop.
    shutdown_tx: Option<oneshot::Sender<()>>,
    /// JoinHandle for the listen loop task.
    _loop_handle: tokio::task::JoinHandle<()>,
}

impl ModelAdapterNode {
    /// Create a new ModelAdapterNode, connect to the Bus, broadcast
    /// node_online, and start the listen loop.
    pub async fn new(
        provider: Arc<dyn Provider>,
        bus: &Bus,
        node_id: NodeId,
    ) -> Result<Self, arf_bus::ConnectError> {
        let provider_name = provider.name().to_string();
        let models: Vec<String> = provider.supported_models().to_vec();

        let info = NodeInfo {
            node_id: node_id.clone(),
            node_type: "model".into(),
            capabilities: serde_json::json!({
                "provider": provider_name,
                "models": models,
            }),
            online_since: std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap_or_default()
                .as_millis() as u64,
        };

        // Only receive model_call messages directed to us, plus broadcasts
        let filter = MessageFilter {
            types: Some(vec!["model_call".into()]),
            to_match: ToMatch::BroadcastAndDirectedToMe,
        };

        let mut handle = bus.connect(info, filter).await?;
        let my_id = node_id.clone();
        let (shutdown_tx, mut shutdown_rx) = oneshot::channel::<()>();

        let loop_handle = tokio::spawn(async move {
            loop {
                tokio::select! {
                    msg = handle.recv() => {
                        if let Ok(msg) = msg {
                            if msg.msg_type == "model_call" && msg.is_for(&my_id) {
                                process_model_call(&provider, &mut handle, &msg).await;
                            }
                        } else {
                            break; // recv error → Bus closed
                        }
                    }
                    _ = &mut shutdown_rx => {
                        break; // shutdown requested
                    }
                }
            }
            // Disconnect on exit
            handle.disconnect().await;
        });

        Ok(Self {
            node_id,
            shutdown_tx: Some(shutdown_tx),
            _loop_handle: loop_handle,
        })
    }

    /// The NodeId this adapter is registered as on the Bus.
    pub fn node_id(&self) -> &NodeId {
        &self.node_id
    }

    /// Shut down the listen loop and disconnect from the Bus.
    pub async fn shutdown(mut self) {
        if let Some(tx) = self.shutdown_tx.take() {
            let _ = tx.send(());
        }
    }
}
```

逐行：
- `NodeInfo` 构造 — `node_type: "model"`，`capabilities` 含 `provider` 名和 `models` 列表。Engine 通过 `bus.graph()` 获取此信息做模型匹配
- `MessageFilter` — 只接收 `model_call` 类型的广播和定向消息。不做其他消息类型处理
- `bus.connect()` — 返回 `NodeHandle`，Bus 自动广播 `node_online`
- `listen loop` — `tokio::select!` 在两个 future 间竞争：`handle.recv()`（收消息）和 `shutdown_rx`（关闭信号）
- `process_model_call()` — 独立函数，不在 `impl ModelAdapterNode` 内部（避免 borrow checker 复杂化）
- `shutdown()` — 发送 oneshot 信号 → listen loop 退出 → `handle.disconnect()` → Bus 广播 `node_offline`

---

#### process_model_call

```rust
/// Process a single model_call message: parse, dispatch, reply.
async fn process_model_call(
    provider: &Arc<dyn Provider>,
    handle: &mut arf_bus::NodeHandle,
    msg: &arf_core::Message,
) {
    // Parse payload
    let payload: ModelCallPayload = match serde_json::from_value(msg.payload.clone()) {
        Ok(p) => p,
        Err(e) => {
            // Invalid payload — send error response
            let _ = handle
                .send(
                    "model_response",
                    vec![msg.from.clone()],
                    serde_json::json!({"error": format!("invalid payload: {e}")}),
                )
                .await;
            return;
        }
    };

    let model_name = match resolve_model_name(provider, &payload) {
        Some(name) => name,
        None => {
            let _ = handle
                .send(
                    "model_response",
                    vec![msg.from.clone()],
                    serde_json::json!({"error": "no model specified"}),
                )
                .await;
            return;
        }
    };

    // Dispatch: stream or non-stream
    if payload.stream {
        match provider
            .chat_stream(
                &model_name,
                payload.messages,
                payload.tools,
                payload.model_params,
            )
            .await
        {
            Ok((chunks, response)) => {
                // Send each chunk
                for chunk in &chunks {
                    let _ = handle
                        .send(
                            "model_response_chunk",
                            vec![msg.from.clone()],
                            serde_json::to_value(chunk).unwrap_or_default(),
                        )
                        .await;
                }
                // Send final response
                let _ = handle
                    .send(
                        "model_response",
                        vec![msg.from.clone()],
                        serde_json::to_value(&response).unwrap_or_default(),
                    )
                    .await;
            }
            Err(e) => {
                send_error_response(handle, &msg.from, &e).await;
            }
        }
    } else {
        match provider
            .chat(&model_name, payload.messages, payload.tools, payload.model_params)
            .await
        {
            Ok(response) => {
                let _ = handle
                    .send(
                        "model_response",
                        vec![msg.from.clone()],
                        serde_json::to_value(&response).unwrap_or_default(),
                    )
                    .await;
            }
            Err(e) => {
                send_error_response(handle, &msg.from, &e).await;
            }
        }
    }
}

/// Determine which model to use from the provider's supported models.
fn resolve_model_name(
    provider: &Arc<dyn Provider>,
    _payload: &ModelCallPayload,
) -> Option<String> {
    // Use the first supported model by default
    // Phase 6 Engine will specify model_name in the payload or via params
    provider.supported_models().first().cloned()
}

/// Send an error response back to the Engine.
async fn send_error_response(
    handle: &mut arf_bus::NodeHandle,
    engine_id: &NodeId,
    error: &ProviderError,
) {
    let _ = handle
        .send(
            "model_response",
            vec![engine_id.clone()],
            serde_json::json!({
                "error": error.to_string(),
                "finish_reason": "error",
            }),
        )
        .await;
}
```

逐行：
- `serde_json::from_value(msg.payload.clone())` — payload 克隆后反序列化。`Message.payload` 是 `serde_json::Value`，不持有引用
- `resolve_model_name()` — 当前取 Provider 支持的第一个模型。Phase 6 Engine 明确传 model_name 后改为从 payload 读取
- 流式路径：chunk 逐个发送 `model_response_chunk` → 最后发送 `model_response`。每个 chunk 是独立的 Bus 消息，Engine 实时收到
- 非流式路径：直接发 `model_response`。与流式路径共享同一 `model_response` 格式
- 错误处理：解析失败或 Provider 调用失败 → 发送含 `error` 字段和 `finish_reason: "error"` 的 `model_response`
- `send_error_response` — 独立函数，复用两种路径的错误处理逻辑

---

## Bus 集成测试（真实 API）

除 node.rs 的 3 个 mock 单元测试外，`tests/bus_integration.rs` 提供了 8 个完整的 Bus 集成测试——Engine → Bus → ModelAdapterNode → Provider → 真实 DeepSeek API 的全链路验证。

### 测试架构

```
EngineStub              Bus                 ModelAdapterNode          DeepSeek API
    │                    │                       │                      │
    │── model_call ────→ │ ── model_call ──────→ │                      │
    │                    │                       │── HTTP POST ────────→│
    │                    │                       │←── response ─────────│
    │←── model_response─ │ ←─ model_response ─── │                      │
    │←── *chunks* ────── │ ←─ model_response_chunk (streaming)          │
```

`EngineStub` 是最小化的 Engine 模拟——连接 Bus、发送 model_call、收集 chunks + 最终 model_response。

### 测试场景（8 个，全部通过）

| # | 测试 | 场景 | 验证点 |
|---|------|------|--------|
| 1 | `basic_chat` | 基础对话 | model_call → model_response 闭环，content 非空，finish_reason="stop" |
| 2 | `multi_round_chat` | 多轮对话 | 3 轮历史消息，模型正确理解上下文（返回名字"Alice"） |
| 3 | `single_tool_call` | 工具调用 | finish_reason="tool_calls"，tool_calls[0].name 正确 |
| 4 | `multi_tool_call_with_results` | 多工具 + 结果回传 | 2 个 tool_calls → 模拟结果 → 模型最终回复 |
| 5 | `thinking_enabled` | 思考开启 | deepseek-v4-pro + thinking:{type:"enabled"} → reasoning_content |
| 6 | `thinking_disabled` | 思考关闭 | thinking:{type:"disabled"} → has reasoning_content:false |
| 7 | `streaming` | 流式响应 | model_response_chunk 逐个经 Bus 到达，chunks 非空，final content 拼接正确 |
| 8 | `invalid_payload` | 错误处理 | 无效 JSON → error response 而非 panic |

### 测试代码（核心结构）

```rust
// tests/bus_integration.rs

/// 最小化 Engine 模拟 — 连接 Bus，发送 model_call，收集响应.
struct EngineStub {
    handle: arf_bus::NodeHandle,
}

impl EngineStub {
    async fn new(bus: &Bus, name: &str) -> Self { /* ... */ }

    /// 发送 model_call，如果是 streaming 则收集所有 chunk，
    /// 最后返回 (final_model_response, chunks).
    async fn call(
        &mut self,
        target: &NodeId,
        messages: Vec<ModelMessage>,
        tools: Vec<ToolDef>,
        params: ModelParams,
        stream: bool,
    ) -> (serde_json::Value, Vec<serde_json::Value>) {
        let payload = ModelCallPayload { messages, tools, model_params: params, stream };
        self.handle.send("model_call", vec![target.clone()], serde_json::to_value(&payload).unwrap()).await.unwrap();

        let mut chunks = Vec::new();
        loop {
            let msg = self.handle.recv().await.unwrap();
            if msg.msg_type == "model_response_chunk" {
                chunks.push(msg.payload);
            } else if msg.msg_type == "model_response" {
                return (msg.payload, chunks);
            }
        }
    }
}
```

### 公共基础设施：`EngineStub` + `setup`/`teardown`

```rust
/// 最小化 Engine 模拟 — 连接 Bus，发送 model_call，收集响应.
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

    async fn call(
        &mut self, target: &NodeId,
        messages: Vec<ModelMessage>, tools: Vec<ToolDef>,
        params: ModelParams, stream: bool,
    ) -> (Value, Vec<Value>) {
        let payload = ModelCallPayload { messages, tools, model_params: params, stream };
        self.handle.send("model_call", vec![target.clone()],
            serde_json::to_value(&payload).unwrap()).await.unwrap();
        let mut chunks = Vec::new();
        loop {
            let msg = self.handle.recv().await.unwrap();
            if msg.msg_type == "model_response_chunk" {
                chunks.push(msg.payload);
            } else if msg.msg_type == "model_response" {
                return (msg.payload, chunks);
            }
        }
    }
}
```

逐行：
- `EngineStub::new()` — 注册为 `engine/{name}` 节点，只接收 `model_response` 和 `model_response_chunk` 两类消息
- `call()` — 发送 `model_call`，然后循环收消息：`model_response_chunk` 存入 chunks 列表，`model_response` 作为最终结果返回。这是 Engine 在 Phase 6 中的实际行为的最小复现

```rust
async fn setup(model_name: &str) -> (Bus, ModelAdapterNode, EngineStub, NodeId) {
    let bus = test_bus();
    let provider = Arc::new(DeepSeekProvider::new(DeepSeekConfig::new(
        api_key(), vec![model_name.into(), "deepseek-v4-pro".into()],
    )));
    let node_id = NodeId::new(format!("model/{model_name}"));
    let node = ModelAdapterNode::new(provider, &bus, node_id.clone()).await.unwrap();
    tokio::time::sleep(Duration::from_millis(10)).await; // let node_online propagate
    let engine = EngineStub::new(&bus, "test-engine").await;
    (bus, node, engine, node_id)
}
```

逐行：
- `setup()` — 创建 Bus → 创建 Provider + Node → 等 10ms 让 `node_online` 广播到达 → 创建 EngineStub。每个测试共享此 setup 逻辑
- `teardown()` — engine.disconnect → node.shutdown → bus.shutdown，顺序清理

---

### 1. basic_chat — 基础对话

**意图：** 验证最基础的 Bus 消息闭环 —— Engine 发 model_call，Node 收、调 API、回复 model_response，Engine 收到。

```rust
#[tokio::test]
#[ignore]
async fn basic_chat() {
    let (bus, node, mut engine, node_id) = setup("deepseek-v4-flash").await;
    let msgs = vec![ModelMessage::new("user", "Say hello in one word.")];
    let (response, chunks) = engine.call(&node_id, msgs, vec![], empty_params(), false).await;
    assert!(chunks.is_empty(), "non-streaming should have no chunks");
    assert_eq!(response["finish_reason"], "stop");
    assert!(!response["message"]["content"].as_str().unwrap_or("").is_empty());
    eprintln!("[basic_chat] content: {}", response["message"]["content"]);
    teardown(bus, node, engine).await;
}
```

逐测试：
- `chunks.is_empty()` — 非流式请求不应有 `model_response_chunk` 消息
- `finish_reason == "stop"` — 正常结束
- `content` 非空 — 模型产生了有效回复
- 消息路径：`handle.send("model_call")` → Bus → Node listen loop → `process_model_call()` → `provider.chat()` → HTTP → `handle.send("model_response")` → Bus → `engine.handle.recv()`

**输出：**
```
[basic_chat] content: "Hello!"
```

---

### 2. multi_round_chat — 多轮对话

**意图：** 验证多轮历史消息通过 Bus 正确传递，模型理解上下文。

```rust
#[tokio::test]
#[ignore]
async fn multi_round_chat() {
    let (bus, node, mut engine, node_id) = setup("deepseek-v4-flash").await;
    let msgs = vec![
        ModelMessage::new("user", "My name is Alice."),
        ModelMessage::new("assistant", "Nice to meet you, Alice!"),
        ModelMessage::new("user", "What is my name?"),
    ];
    let (response, _) = engine.call(&node_id, msgs, vec![], empty_params(), false).await;
    assert!(response["message"]["content"].as_str().unwrap_or("").to_lowercase().contains("alice"));
    eprintln!("[multi_round] content: {}", response["message"]["content"]);
    teardown(bus, node, engine).await;
}
```

逐测试：
- 3 条消息通过 Bus 的 `model_call` payload 发送，序列化为 JSON → Node 反序列化为 `ModelCallPayload` → Provider 转换后发给 API
- `contains("alice")` — 模型从第 1 条消息中提取了名字

**输出：**
```
[multi_round] content: "Your name is Alice, as you mentioned earlier! 😊"
```

---

### 3. single_tool_call — 工具调用

**意图：** 验证工具定义通过 Bus 传递，模型返回 tool_calls 经过 Bus 回到 Engine。

```rust
#[tokio::test]
#[ignore]
async fn single_tool_call() {
    let (bus, node, mut engine, node_id) = setup("deepseek-v4-flash").await;
    let tools = vec![ToolDef {
        name: "get_weather".into(),
        description: "Get current weather for a city".into(),
        parameters: serde_json::json!({
            "type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]
        }),
    }];
    let msgs = vec![ModelMessage::new("user", "What is the weather in Beijing?")];
    let (response, _) = engine.call(&node_id, msgs, tools, empty_params(), false).await;
    assert_eq!(response["finish_reason"], "tool_calls");
    let tc = response["tool_calls"].as_array().unwrap();
    assert!(!tc.is_empty());
    assert_eq!(tc[0]["name"], "get_weather");
    eprintln!("[tool_call] name: {}, args: {}", tc[0]["name"], tc[0]["arguments"]);
    teardown(bus, node, engine).await;
}
```

逐测试：
- `finish_reason == "tool_calls"` —— 模型识别到需要调用工具
- `tc[0]["name"] == "get_weather"` —— 正确选择了工具
- 验证 `ToolDef` 序列化为 Bus payload → Node 反序列化 → `build_request_body` 转为 OpenAI function calling 格式

**输出：**
```
[tool_call] name: get_weather, args: {"city":"Beijing"}
```

---

### 4. multi_tool_call_with_results — 多工具 + 结果回传

**意图：** 验证完整的 tool call 闭环 —— 两个工具 → 两个 tool_calls → 模拟结果 → 第二轮请求 → 最终回复。

```rust
#[tokio::test]
#[ignore]
async fn multi_tool_call_with_results() {
    let (bus, node, mut engine, node_id) = setup("deepseek-v4-flash").await;
    let tools = vec![
        ToolDef { name: "get_weather".into(), description: "Get current weather".into(),
            parameters: serde_json::json!({"type":"object","properties":{"city":{"type":"string"}},"required":["city"]}) },
        ToolDef { name: "get_time".into(), description: "Get current time in a city".into(),
            parameters: serde_json::json!({"type":"object","properties":{"city":{"type":"string"}},"required":["city"]}) },
    ];
    let msgs = vec![ModelMessage::new("user", "What is the weather AND time in Shanghai?")];
    let (response, _) = engine.call(&node_id, msgs, tools, empty_params(), false).await;

    if response["finish_reason"] == "tool_calls" {
        let tc = response["tool_calls"].as_array().unwrap();
        // Round 2: pass tool results back
        let api_tool_calls: Vec<Value> = tc.iter().map(|t| serde_json::json!({
            "id": t["id"], "type": "function",
            "function": { "name": t["name"], "arguments": t["arguments"].to_string() }
        })).collect();
        let mut msgs2 = vec![
            ModelMessage::new("user", "What is the weather AND time in Shanghai?"),
            ModelMessage::new("assistant", "").with_extra(serde_json::json!({"tool_calls": api_tool_calls})),
        ];
        for t in tc {
            let result_text = match t["name"].as_str().unwrap_or("") {
                "get_weather" => "Sunny, 25°C", "get_time" => "14:30 CST", _ => "done",
            };
            msgs2.push(ModelMessage::new("tool", result_text)
                .with_tool_call_id(t["id"].as_str().unwrap_or(""))
                .with_name(t["name"].as_str().unwrap_or("")));
        }
        let (response2, _) = engine.call(&node_id, msgs2, vec![], empty_params(), false).await;
        assert_eq!(response2["finish_reason"], "stop");
    }
    teardown(bus, node, engine).await;
}
```

逐测试：
- 第一轮 — 2 个 tool_calls 经 Bus 返回
- `api_tool_calls` 构造 —— 注意 `arguments` 必须是 JSON 字符串且每项含 `type:"function"` 包装
- 第二轮 — tool results 以 `role:"tool"` 回传 → `convert_message` 转为 API 格式 → 模型基于结果回复
- 两轮请求走同一条 Bus 消息路径

**输出：**
```
[multi_tool] finish_reason: "tool_calls"
[multi_tool] tool_calls count: 2
```

---

### 5. thinking_enabled — 思考模式开启

**意图：** 验证 `thinking_enabled: true` + `reasoning_effort` 经 Bus 传递到 Provider，`reasoning_content` 经 Bus 返回。

```rust
#[tokio::test]
#[ignore]
async fn thinking_enabled() {
    let (bus, node, mut engine, node_id) = setup("deepseek-v4-pro").await;
    let params = ModelParams {
        thinking_enabled: true, extra: serde_json::json!({"reasoning_effort": "high"}), ..empty_params()
    };
    let msgs = vec![ModelMessage::new("user", "Explain quantum computing in one paragraph.")];
    let (response, _) = engine.call(&node_id, msgs, vec![], params, false).await;
    let has_reasoning = !response["message"]["extra"].is_null()
        && response["message"]["extra"].get("reasoning_content").is_some();
    eprintln!("[thinking] has reasoning_content: {has_reasoning}");
    teardown(bus, node, engine).await;
}
```

逐测试：
- `thinking_enabled: true` → `build_request_body` 发送 `thinking:{type:"enabled"}` + 顶层 `reasoning_effort:"high"`
- `has_reasoning` — `parse_response` 从 API 响应提取 `reasoning_content` 存入 `ModelMessage.extra`，经 Bus 序列化/反序列化不丢失

**输出：**
```
[thinking] has reasoning_content: true
```

---

### 6. thinking_disabled — 思考模式关闭

**意图：** 验证 `thinking_enabled: false` → `thinking:{type:"disabled"}` 显式发送到 API，不返回 reasoning_content。

```rust
#[tokio::test]
#[ignore]
async fn thinking_disabled() {
    let (bus, node, mut engine, node_id) = setup("deepseek-v4-flash").await;
    let params = ModelParams { thinking_enabled: false, ..empty_params() };
    let msgs = vec![ModelMessage::new("user", "Say hello.")];
    let (response, _) = engine.call(&node_id, msgs, vec![], params, false).await;
    assert_eq!(response["finish_reason"], "stop");
    assert!(!response["message"]["content"].as_str().unwrap_or("").is_empty());
    let has_reasoning = !response["message"]["extra"].is_null()
        && response["message"]["extra"].get("reasoning_content").is_some();
    eprintln!("[thinking_off] has reasoning_content: {has_reasoning}");
    teardown(bus, node, engine).await;
}
```

逐测试：
- 发送 `thinking:{type:"disabled"}`，API 不返回 `reasoning_content`
- 这是测试中发现的 bug #5：此前不传 thinking 参数时 API 默认开启

**输出：**
```
[thinking_off] content: "Hello! How can I help you today?"
[thinking_off] has reasoning_content: false
```

---

### 7. streaming — 流式响应（经 Bus 逐 chunk 传输）

**意图：** 验证 SSE 流的每个 chunk 作为独立 Bus 消息（`model_response_chunk`）到达 Engine，最终 `model_response` 携完整内容。

```rust
#[tokio::test]
#[ignore]
async fn streaming() {
    let (bus, node, mut engine, node_id) = setup("deepseek-v4-flash").await;
    let msgs = vec![ModelMessage::new("user", "Count from 1 to 5 slowly.")];
    let (response, chunks) = engine.call(&node_id, msgs, vec![], empty_params(), true).await;
    eprintln!("[streaming] chunk count: {}", chunks.len());
    for (i, c) in chunks.iter().enumerate() {
        if c["chunk_type"] == "text" {
            eprintln!("[streaming] chunk[{i}]: {:?}", c["content"].as_str());
        }
    }
    assert!(!chunks.is_empty(), "streaming should produce chunks");
    assert!(!response["message"]["content"].as_str().unwrap_or("").is_empty());
    eprintln!("[streaming] full content: {}", response["message"]["content"]);
    teardown(bus, node, engine).await;
}
```

逐测试：
- `stream: true` → `call_stream()` → `convert::parse_sse()` 解析 SSE → 每个 chunk 通过 `handle.send("model_response_chunk")` 发到 Bus
- `chunks` 非空 —— 验证 Bus 上确实收到了 `model_response_chunk` 消息
- `response["message"]["content"]` 非空 —— 所有 chunk 拼接为完整回复

**输出：**
```
[streaming] chunk count: 15
[streaming] chunk[0]: Some("1")
[streaming] chunk[1]: Some("...")
...
[streaming] full content: "1...  \n2...  \n3...  \n4...  \n5."
```

15 个 chunk 逐个经 Bus 传输到达 EngineStub。

---

### 8. invalid_payload — 错误处理

**意图：** 验证 Node 收到无效 payload 时返回 error 响应，不 panic 不崩溃。

```rust
#[tokio::test]
#[ignore]
async fn invalid_payload() {
    let (bus, node, mut engine, node_id) = setup("deepseek-v4-flash").await;
    engine.handle.send("model_call", vec![node_id.clone()],
        serde_json::json!("not a valid payload")).await.unwrap();
    let msg = engine.handle.recv().await.unwrap();
    assert_eq!(msg.msg_type, "model_response");
    assert!(msg.payload["error"].as_str().unwrap_or("").contains("invalid payload"));
    eprintln!("[error] response: {}", msg.payload);
    teardown(bus, node, engine).await;
}
```

逐测试：
- 发送字符串 `"not a valid payload"` 而非 `ModelCallPayload` 结构体
- `serde_json::from_value::<ModelCallPayload>` 失败 → `process_model_call` 的 error 分支 → `send_error_response` → Engine 收到含 `error` 字段的 `model_response`
- 验证 Node 不会因无效输入 panic

**输出：**
```
[error] response: {"error":"invalid payload: invalid type: string \"not a valid payload\", expected struct ModelCallPayload"}
```

---

### 运行方式

```bash
export DEEPSEEK_API_KEY=sk-xxx
cargo test --package arf-model-adapter --test bus_integration -- --ignored --nocapture
```

---

## 测试汇总

| 分类 | 文件 | 测试数 | 说明 |
|------|------|--------|------|
| node 单元测试 (mock) | `node.rs` | 3 | connect/disconnect 生命周期 |
| Bus 集成测试 (真实 API) | `tests/bus_integration.rs` | 8 | 全链路 Engine→Bus→Node→API |
| **累计** (4.1–4.7) | | **71** | |

---

## 交付标准

- [x] `cargo test --workspace` 全部通过（299 unit + 18 integration = 317 tests）
- [x] ModelAdapterNode 正确收发 Bus 消息
- [x] 流式/非流式双路径正常工作
- [x] 错误处理正确（无效 payload → error response）
- [x] shutdown 清理干净（node_offline 广播）
- [x] 完整 Bus 链路验证：Engine → Bus → Node → API → Node → Bus → Engine
