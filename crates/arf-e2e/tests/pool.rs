//! [E2E] Pool — bounded resource lifecycle (`Pool<R>` + pool facade nodes).
//!
//! Test angles covered:
//! - [方法] Pool with `Overflow::Queue(N)` — leases auto-release on drop,
//!   idle count bounces back to max_size.
//! - [边界] Pool with `Overflow::Reject` — acquire past max_size returns
//!   `PoolError::Full` without blocking.
//! - [方法] ModelAdapterPoolNode on a real Bus — registers as
//!   `node_type="model"` (Phase 7 auto-discovery), bridges model_call from
//!   top Bus through a `Pool<ModelAdapterResource>` to a sub Bus
//!   ModelAdapterNode and back.

mod common;

use std::sync::Arc;
use std::time::Duration;

use arf_bus::Bus;
use arf_core::{Message, MessageFilter, NodeId, NodeInfo, ToMatch};
use arf_engine::{AgentConfig, Engine, EngineBuilder, EngineConfig, ModelDecl};
use arf_model_adapter::{ModelAdapterNode, ModelAdapterPoolNode, ModelAdapterResource, Provider};
use arf_pool::{Overflow, Pool, PoolConfig, PoolError, Resource};
use common::provider::{scripted, text_response};
use serde_json::json;
use tokio_util::sync::CancellationToken;
use uuid::Uuid;

// ── Test 1: Pool with Queue overflow serves serial clients ────────────────

// [方法] Pool<max=2, overflow=Queue(2)> + 3× acquire/release 循环 → 每次循环
// 后 idle_count 应回到 2（lease Drop 自动归还）。
#[tokio::test]
async fn pool_queue_overflow_serves_serial_clients() -> anyhow::Result<()> {
    let pool: Pool<Dummy> = Pool::with_resources(
        PoolConfig {
            max_size: 2,
            overflow: Overflow::Queue(2),
            idle_timeout: None,
        },
        (0..2).map(|_| Dummy::new()).collect(),
    );

    for cycle in 0..3 {
        // Hold both leases inside a tight scope; both Drop on scope exit.
        {
            let _lease_a = pool.acquire().await.expect("acquire a");
            let _lease_b = pool.acquire().await.expect("acquire b");
            assert_eq!(pool.total_count().await, 2);
        }
        // Drop is async; give the queue a moment to bounce back.
        tokio::time::sleep(Duration::from_millis(30)).await;
        assert_eq!(
            pool.idle_count().await,
            2,
            "cycle {cycle}: idle should bounce back to 2"
        );
    }
    Ok(())
}

// ── Test 2: Pool with Reject overflow returns Full when saturated ─────────

// [边界] Pool<max=1, overflow=Reject> + 两个 acquire — 第二个立即返回 Full。
#[tokio::test]
async fn pool_reject_overflow_returns_full() -> anyhow::Result<()> {
    let pool: Pool<Dummy> = Pool::with_resources(
        PoolConfig {
            max_size: 1,
            overflow: Overflow::Reject,
            idle_timeout: None,
        },
        vec![Dummy::new()],
    );

    let _lease = pool.acquire().await.expect("first acquire");
    let result = pool.acquire().await;
    let is_full = matches!(result, Err(PoolError::Full));
    assert!(is_full, "expected PoolError::Full, got full={is_full}");
    Ok(())
}

// ── Test 3: ModelAdapterPoolNode bridges model_call across two Buses ──────

// [方法] ModelAdapterPoolNode 注册成 node_type="model"，advertised_provider="pool"
// → engine 按 ModelDecl.provider="pool" 在 top bus 找到它 → 转发 model_call 到
// sub bus 上的 ModelAdapterNode（provider="scripted"）→ 响应回来后 PoolNode 转回
// engine。验证 facade 模式在 Phase 7 auto-discovery 下也能跑通。
#[tokio::test]
async fn model_adapter_pool_node_bridges_model_call_across_buses() -> anyhow::Result<()> {
    let top_bus = Arc::new(Bus::new(
        Duration::from_millis(500),
        Duration::from_secs(2),
        32,
    ));
    let sub_bus = Arc::new(Bus::new(
        Duration::from_millis(500),
        Duration::from_secs(2),
        32,
    ));

    // Sub: register the actual ModelAdapterNode under `model/real`.
    let provider: Arc<dyn Provider> = scripted(vec![text_response("from-the-pool")]);
    let _sub_node = ModelAdapterNode::new(
        provider.clone(),
        &sub_bus,
        NodeId::new("model/real"),
    )
    .await?;

    // Pool of ModelAdapterResource (one resource).
    let pool: Pool<ModelAdapterResource> = Pool::with_resources(
        PoolConfig {
            max_size: 1,
            overflow: Overflow::Reject,
            idle_timeout: None,
        },
        vec![ModelAdapterResource::new(provider.clone())],
    );

    // ModelAdapterPoolNode: facade on top_bus with advertised_provider="pool".
    // Different from the real provider name on sub_bus so Registry.resolve_model
    // uniquely matches the pool node (deterministic test).
    let pool_node = Arc::new(ModelAdapterPoolNode {
        node_id: NodeId::new("model/pool"),
        top_bus: top_bus.clone(),
        sub_bus: sub_bus.clone(),
        pool: Arc::new(pool),
        advertised_provider: "pool".into(),
        advertised_models: vec!["scripted-v1".into()],
    });
    pool_node.clone().connect().await?;
    tokio::time::sleep(Duration::from_millis(50)).await;

    // EngineBuilder sees BOTH buses so its graph snapshot includes
    // model/pool (top) and model/real (sub). With ModelDecl.provider="pool",
    // resolve_model picks model/pool → model_call flows through the facade.
    let cfg = AgentConfig {
        model: ModelDecl {
            provider: "pool".into(),
            model_name: "scripted-v1".into(),
            ..Default::default()
        },
        resources: vec![],
        system_prompt_template: "You are helpful.".into(),
        initial_memory: vec![],
        allowed_paths: vec![],
        tools: vec![],
        engine: EngineConfig {
            max_turns: 5,
            tool_timeout_ms: Some(3000),
            ..Default::default()
        },
    };
    let mut engine = EngineBuilder::new(vec![top_bus.clone(), sub_bus.clone()]).build(cfg).await?;
    let mut state = arf_core::State::new();
    let cancel = CancellationToken::new();
    let out = tokio::time::timeout(
        Duration::from_secs(10),
        engine.run(&mut state, "use the pool".into(), cancel),
    )
    .await
    .expect("engine run timed out")
    .expect("engine run failed");
    assert_eq!(out, "from-the-pool");
    assert_eq!(state.messages.len(), 2); // user + assistant
    Ok(())
}

// ── Test 4: facade dispatches concurrently (F-003 spawn-per-task) ─────────

// [并发] ModelAdapterPoolNode with Pool<max=4> + 4 concurrent model_calls.
// A concurrent delay node on the sub bus sleeps 300ms per request but handles
// requests in parallel (spawn-per-request). With a SERIAL facade run_loop the
// facade forwards req N+1 only after req N's response returns → total ≈ 4×300ms.
// With the spawn-per-task facade all 4 leases are used concurrently → total ≈
// 300ms. Assert total < 900ms to discriminate the two regimes.
#[tokio::test]
async fn facade_spawns_per_request_concurrent() -> anyhow::Result<()> {
    let top_bus = Arc::new(Bus::new(
        Duration::from_millis(500),
        Duration::from_secs(5),
        64,
    ));
    let sub_bus = Arc::new(Bus::new(
        Duration::from_millis(500),
        Duration::from_secs(5),
        64,
    ));

    // Concurrent delay node on the sub bus: spawns a task per model_call that
    // sleeps then replies model_response with the same correlation_id.
    spawn_concurrent_delay_node(
        sub_bus.clone(),
        NodeId::new("model/delay"),
        Duration::from_millis(300),
    )
    .await?;

    // Facade with a pool of 4 resources (Reject overflow — should never hit it
    // because we send exactly 4 with 4 leases available).
    let provider: Arc<dyn Provider> = scripted(vec![text_response("ignored")]);
    let pool: Pool<ModelAdapterResource> = Pool::with_resources(
        PoolConfig {
            max_size: 4,
            overflow: Overflow::Reject,
            idle_timeout: None,
        },
        (0..4)
            .map(|_| ModelAdapterResource::new(provider.clone()))
            .collect(),
    );
    let facade_id = NodeId::new("model/pool");
    let facade = Arc::new(ModelAdapterPoolNode {
        node_id: facade_id.clone(),
        top_bus: top_bus.clone(),
        sub_bus: sub_bus.clone(),
        pool: Arc::new(pool),
        advertised_provider: "pool".into(),
        advertised_models: vec!["scripted-v1".into()],
    });
    facade.clone().connect().await?;
    tokio::time::sleep(Duration::from_millis(50)).await;

    // A fake engine: sends 4 model_calls, collects 4 model_responses.
    let mut eng = top_bus
        .connect(
            NodeInfo {
                node_id: NodeId::new("engine/test"),
                node_type: "engine".into(),
                capabilities: json!({}),
                online_since: 0,
            },
            MessageFilter {
                types: Some(vec!["model_response".into()]),
                to_match: ToMatch::BroadcastAndDirectedToMe,
            },
        )
        .await?;

    let cids: Vec<Uuid> = (0..4).map(|_| Uuid::new_v4()).collect();
    let start = std::time::Instant::now();
    for cid in &cids {
        eng.send(
            "model_call",
            vec![facade_id.clone()],
            json!({"correlation_id": cid.to_string(), "messages": [], "tools": []}),
        )
        .await?;
    }

    let mut got = 0;
    while got < 4 {
        let m = tokio::time::timeout(Duration::from_secs(3), eng.recv())
            .await
            .expect("timed out collecting model_response")?;
        if m.msg_type == "model_response" {
            got += 1;
        }
    }
    let elapsed = start.elapsed();
    assert!(
        elapsed < Duration::from_millis(900),
        "expected concurrent dispatch (~300ms), got {elapsed:?} — facade is serial"
    );

    eng.disconnect().await;
    Ok(())
}

// ── Test 5: facade returns model_response{error} on pool acquire failure ──

// [边界] Facade with Pool<max=1, Reject>; the single lease is held externally so
// the facade's acquire() fails. The facade must reply model_response with an
// `error` field + the request's correlation_id (no silent failure / hang).
#[tokio::test]
async fn facade_acquire_error_returns_error_response() -> anyhow::Result<()> {
    let top_bus = Arc::new(Bus::new(
        Duration::from_millis(500),
        Duration::from_secs(5),
        32,
    ));
    let sub_bus = Arc::new(Bus::new(
        Duration::from_millis(500),
        Duration::from_secs(5),
        32,
    ));

    let provider: Arc<dyn Provider> = scripted(vec![text_response("ignored")]);
    let pool: Arc<Pool<ModelAdapterResource>> = Arc::new(Pool::with_resources(
        PoolConfig {
            max_size: 1,
            overflow: Overflow::Reject,
            idle_timeout: None,
        },
        vec![ModelAdapterResource::new(provider.clone())],
    ));
    // Hold the only lease so the facade's acquire() returns Full.
    let _held = pool.acquire().await.expect("hold the only lease");

    let facade_id = NodeId::new("model/pool");
    let facade = Arc::new(ModelAdapterPoolNode {
        node_id: facade_id.clone(),
        top_bus: top_bus.clone(),
        sub_bus: sub_bus.clone(),
        pool: pool.clone(),
        advertised_provider: "pool".into(),
        advertised_models: vec!["scripted-v1".into()],
    });
    facade.clone().connect().await?;
    tokio::time::sleep(Duration::from_millis(50)).await;

    let mut eng = top_bus
        .connect(
            NodeInfo {
                node_id: NodeId::new("engine/test"),
                node_type: "engine".into(),
                capabilities: json!({}),
                online_since: 0,
            },
            MessageFilter {
                types: Some(vec!["model_response".into()]),
                to_match: ToMatch::BroadcastAndDirectedToMe,
            },
        )
        .await?;

    let cid = Uuid::new_v4();
    eng.send(
        "model_call",
        vec![facade_id.clone()],
        json!({"correlation_id": cid.to_string(), "messages": [], "tools": []}),
    )
    .await?;

    let resp = tokio::time::timeout(Duration::from_secs(3), eng.recv())
        .await
        .expect("timed out waiting for error response")?;
    assert_eq!(resp.msg_type, "model_response");
    assert!(
        resp.payload.get("error").is_some(),
        "expected error field in payload, got {}",
        resp.payload
    );
    assert_eq!(
        resp.payload.get("correlation_id").and_then(|v| v.as_str()),
        Some(cid.to_string().as_str()),
        "error response must carry the request correlation_id"
    );

    eng.disconnect().await;
    Ok(())
}

/// Spawn a concurrent model node on `bus` that, for each `model_call`, sleeps
/// `delay` then replies `model_response` (with the same correlation_id) to the
/// request sender. Unlike `ModelAdapterNode`, it handles requests in parallel
/// (spawn-per-request), so it does not itself serialize the facade.
async fn spawn_concurrent_delay_node(
    bus: Arc<Bus>,
    node_id: NodeId,
    delay: Duration,
) -> anyhow::Result<()> {
    let info = NodeInfo {
        node_id: node_id.clone(),
        node_type: "model".into(),
        capabilities: json!({"provider": "delay", "models": ["scripted-v1"]}),
        online_since: 0,
    };
    let filter = MessageFilter {
        types: Some(vec!["model_call".into()]),
        to_match: ToMatch::BroadcastAndDirectedToMe,
    };
    let mut handle = bus.connect(info, filter).await?;
    let bus_for_loop = bus.clone();
    tokio::spawn(async move {
        loop {
            match handle.recv().await {
                Ok(msg) if msg.msg_type == "model_call" => {
                    let cid = msg
                        .payload
                        .get("correlation_id")
                        .and_then(|v| v.as_str())
                        .map(|s| s.to_string());
                    let reply_to = msg.from.clone();
                    let bus = bus_for_loop.clone();
                    let node_id = node_id.clone();
                    tokio::spawn(async move {
                        tokio::time::sleep(delay).await;
                        let payload = json!({
                            "message": {"role": "assistant", "content": "ok"},
                            "finish_reason": "stop",
                            "correlation_id": cid,
                        });
                        let reply = Message::with_from_bus(
                            "model_response".to_string(),
                            node_id,
                            vec![reply_to],
                            payload,
                            bus.id,
                        );
                        let _ = bus.send(reply).await;
                    });
                }
                Ok(_) => continue,
                Err(_) => break,
            }
        }
    });
    Ok(())
}

// ── Test helper: a no-op Resource for the Pool primitive tests ────────────

/// A simple counting Resource used to verify lease acquire/release semantics.
struct Dummy {
    counter: Arc<std::sync::atomic::AtomicUsize>,
}

impl Dummy {
    fn new() -> Self {
        Self {
            counter: Arc::new(std::sync::atomic::AtomicUsize::new(0)),
        }
    }
}

impl Resource for Dummy {
    fn kind(&self) -> &str {
        "dummy"
    }
    fn try_acquire(&self) -> Result<(), String> {
        Ok(())
    }
    fn release(&self) {}
}

impl std::fmt::Debug for Dummy {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Dummy").finish()
    }
}