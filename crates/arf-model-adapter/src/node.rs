//! ModelAdapter node — Bus lifecycle + model_call dispatch.

use std::sync::Arc;

use arf_bus::Bus;
use arf_core::{MessageFilter, NodeId, NodeInfo, ToMatch};
use tokio::sync::oneshot;
use uuid::Uuid;

use crate::provider::Provider;
use crate::types::ModelCallPayload;

/// A ModelAdapter node connected to the Bus.
pub struct ModelAdapterNode {
    node_id: NodeId,
    shutdown_tx: Option<oneshot::Sender<()>>,
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
                        match msg {
                            Ok(msg) => {
                                if msg.msg_type == "model_call" && (msg.is_for(&my_id) || msg.is_broadcast()) {
                                    process_model_call(&provider, &mut handle, &msg).await;
                                }
                            }
                            Err(_) => break, // Bus closed
                        }
                    }
                    _ = &mut shutdown_rx => {
                        break; // shutdown requested (or sender dropped)
                    }
                }
            }
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

/// Process a single model_call message: parse, dispatch, reply.
async fn process_model_call(
    provider: &Arc<dyn Provider>,
    handle: &mut arf_bus::NodeHandle,
    msg: &arf_core::Message,
) {
    // Extract correlation_id from the request payload — the engine
    // uses it to match responses. The engine's `ModelCall` struct
    // serializes `correlation_id` at the top level of the payload.
    let request_cid = msg
        .payload
        .get("correlation_id")
        .and_then(|v| v.as_str())
        .and_then(|s| Uuid::parse_str(s).ok());

    let payload: ModelCallPayload = match serde_json::from_value(msg.payload.clone()) {
        Ok(p) => p,
        Err(e) => {
            let _ = handle
                .send(
                    "model_response",
                    vec![msg.from.clone()],
                    serde_json::json!({
                        "error": format!("invalid payload: {e}"),
                        "correlation_id": request_cid.map(|c| c.to_string()),
                    }),
                )
                .await;
            return;
        }
    };

    let model_name = resolve_model_name(provider);
    let engine_id = msg.from.clone();

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
                for chunk in &chunks {
                    let _ = handle
                        .send(
                            "model_response_chunk",
                            vec![engine_id.clone()],
                            serde_json::to_value(chunk).unwrap_or_default(),
                        )
                        .await;
                }
                let _ = handle
                    .send_response(
                        "model_response",
                        vec![engine_id.clone()],
                        serde_json::to_value(&response).unwrap_or_default(),
                        request_cid,
                    )
                    .await;
            }
            Err(e) => {
                send_error_response(handle, &engine_id, &e, request_cid).await;
            }
        }
    } else {
        match provider
            .chat(
                &model_name,
                payload.messages,
                payload.tools,
                payload.model_params,
            )
            .await
        {
            Ok(response) => {
                let _ = handle
                    .send_response(
                        "model_response",
                        vec![engine_id.clone()],
                        serde_json::to_value(&response).unwrap_or_default(),
                        request_cid,
                    )
                    .await;
            }
            Err(e) => {
                send_error_response(handle, &engine_id, &e, request_cid).await;
            }
        }
    }
}

/// Determine which model to use.
fn resolve_model_name(provider: &Arc<dyn Provider>) -> String {
    provider
        .supported_models()
        .first()
        .cloned()
        .unwrap_or_else(|| "unknown".into())
}

/// Send an error response back to the Engine.
async fn send_error_response(
    handle: &mut arf_bus::NodeHandle,
    engine_id: &NodeId,
    error: &crate::ProviderError,
    correlation_id: Option<Uuid>,
) {
    let _ = handle
        .send_response(
            "model_response",
            vec![engine_id.clone()],
            serde_json::json!({
                "error": error.to_string(),
                "finish_reason": "error",
            }),
            correlation_id,
        )
        .await;
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::provider::Provider;
    use crate::types::{ModelParams, ModelResponsePayload, ToolDef, Usage};
    use arf_core::ModelMessage;
    use async_trait::async_trait;
    use std::time::Duration;

    /// A mock provider for testing node integration.
    struct MockProvider {
        name: String,
        models: Vec<String>,
    }

    #[async_trait]
    impl Provider for MockProvider {
        fn name(&self) -> &str {
            &self.name
        }
        fn supported_models(&self) -> &[String] {
            &self.models
        }
        async fn chat(
            &self,
            _model_name: &str,
            _messages: Vec<ModelMessage>,
            _tools: Vec<ToolDef>,
            _params: ModelParams,
        ) -> Result<ModelResponsePayload, crate::ProviderError> {
            Ok(ModelResponsePayload {
                message: ModelMessage::new("assistant", "mock reply"),
                tool_calls: None,
                finish_reason: "stop".into(),
                usage: Some(Usage {
                    input_tokens: 5,
                    output_tokens: 3,
                    total_tokens: 8,
                }),
                id: "mock-id".into(),
                model: "mock-model".into(),
            })
        }
    }

    fn mock_provider() -> Arc<dyn Provider> {
        Arc::new(MockProvider {
            name: "mock".into(),
            models: vec!["mock-model".into()],
        })
    }

    fn test_bus() -> Bus {
        Bus::new(Duration::from_secs(1), Duration::from_secs(3), 16)
    }

    // [构造] node 创建后 graph 可见
    #[tokio::test]
    async fn node_connects_and_appears_in_graph() {
        let bus = test_bus();
        let provider = mock_provider();
        let node_id = NodeId::new("model/mock-model");

        let node = ModelAdapterNode::new(provider, &bus, node_id.clone())
            .await
            .unwrap();

        let graph = bus.graph();
        let found = graph
            .nodes
            .iter()
            .any(|n| n.node_id == node_id && n.node_type == "model");
        assert!(found, "node should appear in bus graph after connect");

        node.shutdown().await;
        bus.shutdown().await;
    }

    // [方法] model_call → model_response 往返
    #[tokio::test]
    async fn node_receives_model_call_and_responds() {
        let bus = test_bus();
        let provider = mock_provider();
        let node_id = NodeId::new("model/mock-model");

        let _node = ModelAdapterNode::new(provider, &bus, node_id.clone())
            .await
            .unwrap();

        // Connect a "fake engine" to send model_call and receive model_response
        let engine_info = NodeInfo {
            node_id: NodeId::new("engine/test"),
            node_type: "engine".into(),
            capabilities: serde_json::json!({}),
            online_since: 0,
        };
        let mut engine_handle = bus
            .connect(
                engine_info,
                MessageFilter {
                    types: Some(vec!["model_response".into()]),
                    to_match: ToMatch::BroadcastAndDirectedToMe,
                },
            )
            .await
            .unwrap();

        // Send model_call to the model node
        engine_handle
            .send(
                "model_call",
                vec![node_id.clone()],
                serde_json::to_value(&ModelCallPayload {
                    messages: vec![ModelMessage::new("user", "hello")],
                    tools: vec![],
                    model_params: ModelParams {
                        temperature: None,
                        max_tokens: None,
                        thinking_enabled: false,
                        extra: serde_json::Value::Null,
                    },
                    stream: false,
                })
                .unwrap(),
            )
            .await
            .unwrap();

        // Receive model_response
        let response = engine_handle.recv().await.unwrap();
        assert_eq!(response.msg_type, "model_response");
        assert!(response.is_for(&NodeId::new("engine/test")));

        let payload: serde_json::Value =
            serde_json::from_value(response.payload).unwrap();
        assert_eq!(payload["message"]["content"], "mock reply");
        assert_eq!(payload["finish_reason"], "stop");

        engine_handle.disconnect().await;
        _node.shutdown().await;
        bus.shutdown().await;
    }

    // [清理] shutdown 后 graph 不再含此节点
    #[tokio::test]
    async fn node_shutdown_removes_from_graph() {
        let bus = test_bus();
        let provider = mock_provider();
        let node_id = NodeId::new("model/mock-model");

        let node = ModelAdapterNode::new(provider, &bus, node_id.clone())
            .await
            .unwrap();

        assert_eq!(bus.graph().nodes.len(), 1);

        node.shutdown().await;
        // Give the async disconnect a moment to propagate
        tokio::time::sleep(Duration::from_millis(50)).await;

        let graph = bus.graph();
        assert!(
            graph.nodes.is_empty(),
            "graph should be empty after shutdown: {:?}",
            graph.nodes
        );

        bus.shutdown().await;
    }
}
