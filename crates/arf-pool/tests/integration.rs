//! Phase 6 task 6.19 — Pool integration tests.
//!
//! Demonstrates that a `Pool<ModelAdapterResource>` (or `Pool<McpResource>`)
//! can back a `PoolNode` Bus bridge and serve Engine requests through a
//! discovery route.

use std::sync::Arc;
use std::time::Duration;

use arf_bus::Bus;
use arf_core::{Message, MessageFilter, NodeId, NodeInfo, State, ToMatch};
use arf_engine::{AgentConfig, EngineBuilder, EngineConfig, ModelDecl};
use arf_pool::{Overflow, Pool, PoolConfig};
use arf_model_adapter::{ModelAdapterPoolNode, ModelAdapterResource};
use arf_model_adapter::provider::Provider;
use arf_model_adapter::types::{ModelParams, ModelResponsePayload, Usage};
use arf_model_adapter::ProviderError;
use arf_core::ModelMessage;
use async_trait::async_trait;
use tokio_util::sync::CancellationToken;
use uuid::Uuid;

struct StubProvider;
#[async_trait]
impl Provider for StubProvider {
    fn name(&self) -> &str {
        "stub"
    }
    fn supported_models(&self) -> &[String] {
        static MODELS: std::sync::LazyLock<Vec<String>> =
            std::sync::LazyLock::new(|| vec![String::from("stub-v1")]);
        &MODELS
    }
    async fn chat(
        &self,
        _model_name: &str,
        _messages: Vec<ModelMessage>,
        _tools: Vec<arf_model_adapter::types::ToolDef>,
        _params: ModelParams,
    ) -> Result<ModelResponsePayload, ProviderError> {
        Ok(ModelResponsePayload {
            message: ModelMessage::new("assistant", "ok"),
            tool_calls: None,
            finish_reason: "stop".into(),
            usage: Some(Usage {
                input_tokens: 0,
                output_tokens: 0,
                total_tokens: 0,
            }),
            id: "stub".into(),
            model: "stub-v1".into(),
        })
    }
}

#[tokio::test]
async fn pool_with_model_adapter_resource() {
    // 1. Build pool
    let pool: Pool<ModelAdapterResource> = Pool::new(PoolConfig {
        max_size: 2,
        overflow: Overflow::Reject,
        idle_timeout: None,
    });
    let _r1 = pool
        .provision(|| Ok(ModelAdapterResource::new(Arc::new(StubProvider))))
        .await
        .unwrap();
    let _r2 = pool
        .provision(|| Ok(ModelAdapterResource::new(Arc::new(StubProvider))))
        .await
        .unwrap();
    pool.release(&_r1);
    pool.release(&_r2);
    tokio::time::sleep(Duration::from_millis(50)).await;

    // 2. Acquire both — should succeed (max_size=2)
    let l1 = pool.acquire().await.unwrap();
    let l2 = pool.acquire().await.unwrap();
    assert_eq!(l1.resource().call_count(), 0);
    assert_eq!(l2.resource().call_count(), 0);

    // 3. Try to acquire third — should fail (Reject)
    let res3 = pool.acquire().await;
    assert!(matches!(res3, Err(arf_pool::PoolError::Full)));
}

#[tokio::test]
async fn pool_node_with_engine_react_loop() {
    // 1. Two buses: top (Engine) + sub (PoolNode → resource)
    let top_bus = Arc::new(Bus::new(
        Duration::from_secs(1),
        Duration::from_secs(3),
        16,
    ));
    let sub_bus = Arc::new(Bus::new(
        Duration::from_secs(1),
        Duration::from_secs(3),
        16,
    ));

    // 2. Pool with 1 resource
    let pool: Pool<ModelAdapterResource> = Pool::new(PoolConfig {
        max_size: 1,
        overflow: Overflow::Block(Duration::from_secs(2)),
        idle_timeout: None,
    });
    let _r1 = pool
        .provision(|| Ok(ModelAdapterResource::new(Arc::new(StubProvider))))
        .await
        .unwrap();
    pool.release(&_r1);
    tokio::time::sleep(Duration::from_millis(50)).await;

    // 3. ModelAdapterPoolNode facade on top bus (node_type="model",
    //    advertised_provider="pool" — distinct from "stub" on sub bus so
    //    Registry.resolve_model uniquely matches the pool).
    let pool_node = Arc::new(ModelAdapterPoolNode {
        node_id: NodeId::new("model/pool"),
        top_bus: top_bus.clone(),
        sub_bus: sub_bus.clone(),
        pool: Arc::new(pool),
        advertised_provider: "pool".into(),
        advertised_models: vec!["stub-v1".into()],
    });
    pool_node.clone().connect().await.unwrap();

    // 4. Sub-bus responder: replies to model_call with model_response
    let sub_resp_h = tokio::spawn({
        let bus = sub_bus.clone();
        async move {
            let mut rx = bus.subscribe();
            let stop_at = tokio::time::Instant::now() + Duration::from_secs(10);
            while let Ok(m) = tokio::time::timeout_at(stop_at, rx.recv()).await {
                let m = match m { Ok(m) => m, Err(_) => return };
                if m.msg_type == "model_call" {
                    if let Some(cid) = m.payload.get("correlation_id")
                        .and_then(|v| v.as_str())
                        .and_then(|s| Uuid::parse_str(s).ok())
                    {
                        let resp = Message::with_from_bus(
                            String::from("model_response"),
                            NodeId::new("model/stub"),
                            vec![],
                            serde_json::json!({
                                "correlation_id": cid.to_string(),
                                "message": {
                                    "content": "ok from pool",
                                    "tool_calls": [],
                                },
                            }),
                            bus.id,
                        );
                        let _ = bus.send(resp).await;
                    }
                }
            }
        }
    });

    // 5. Engine on top bus (sees both buses → resolves ModelDecl.provider="pool"
    //    to model/pool facade, which forwards to sub-bus responder).
    let cfg = AgentConfig {
        model: ModelDecl {
            provider: "pool".into(),
            model_name: "stub-v1".into(),
            ..Default::default()
        },
        system_prompt_template: "You are helpful.".into(),
        initial_memory: vec![],
        allowed_paths: vec![],
        resources: vec![],
        engine: EngineConfig {
            // model_call auto-derived from ModelDecl.provider.
            max_turns: 3,
            tool_timeout_ms: Some(5_000),
            ..Default::default()
        },
    };
    let mut engine = EngineBuilder::new(vec![top_bus.clone(), sub_bus.clone()]).build(cfg).await.unwrap();

    let mut state = State::new();
    let cancel = CancellationToken::new();
    let output = tokio::time::timeout(
        Duration::from_secs(5),
        engine.run(&mut state, "use the pool".into(), cancel),
    )
    .await
    .expect("run timed out")
    .expect("run should succeed");
    assert_eq!(output, "ok from pool");
    // 2026-07-02: system prefix 现采不入 state.messages；对话仅 user + assistant
    assert_eq!(state.messages.len(), 2);

    sub_resp_h.abort();
}