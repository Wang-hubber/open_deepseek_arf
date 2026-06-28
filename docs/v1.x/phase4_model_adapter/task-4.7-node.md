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

## 测试

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use arf_bus::Bus;
    use std::time::Duration;
    use crate::provider::Provider;
    // Uses a mock provider (same as in provider.rs tests)
}
```

### node.rs — 3 tests

| 测试 | 覆盖 |
|------|------|
| node_connects_and_broadcasts | Bus::connect 成功，graph 可见 |
| node_receives_model_call | 模拟 model_call → node 回复 model_response |
| node_shutdown_cleanup | shutdown 后 graph 不再含此节点 |

需要真实的 Bus 实例 + 定向消息发送。

---

## 测试汇总

| 分类 | 测试数 |
|------|--------|
| node.rs | 3 |
| **累计** (4.1–4.7) | **60** |

---

## 交付标准

- `cargo test --workspace` 全部通过（295 + 3 = 298 tests）
- ModelAdapterNode 正确收发 Bus 消息
- 流式/非流式双路径正常工作
- 错误处理正确（无效 payload → error response）
- shutdown 清理干净（node_offline 广播）
