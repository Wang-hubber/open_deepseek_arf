//! [E2E] Pool — bounded resource lifecycle (`Pool<R>` + `PoolNode`).
//!
//! Test angles covered:
//! - [方法] Pool with `Overflow::Queue(N)` — leases auto-release on drop,
//!   idle count bounces back to max_size.
//! - [边界] Pool with `Overflow::Reject` — acquire past max_size returns
//!   `PoolError::Full` without blocking.
//! - [方法] PoolNode on a real Bus — bridges model_call from top Bus through
//!   a `Pool<ModelAdapterResource>` to a sub Bus ModelAdapterNode and back.
//!
//! These exercise the framework primitives directly + the `PoolNode` facade
//! in `crates/arf-pool/src/node.rs`.

mod common;

use std::sync::Arc;
use std::time::Duration;

use arf_bus::Bus;
use arf_core::NodeId;
use arf_engine::{AgentConfig, Engine, EngineBuilder, ModelConfig};
use arf_model_adapter::{ModelAdapterNode, ModelAdapterResource, Provider};
use arf_pool::{Overflow, Pool, PoolConfig, PoolError, PoolNode, Resource};
use common::provider::{scripted, text_response};
use tokio_util::sync::CancellationToken;

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

// ── Test 3: PoolNode bridges model_call across two Buses via a pool ───────

// [方法] PoolNode(Arc<Pool<ModelAdapterResource>>) 监听 top Bus 上的 model_call
// → 通过 PoolNode 桥接到 sub Bus 上同名的 ModelAdapterNode → 响应回来时
// PoolNode 把响应转发回 top Bus，engine 收到回应。
//
// 验证 PoolNode 是真实的 Bus facade 模式，不是单元测试桩。
#[tokio::test]
async fn pool_node_bridges_model_call_across_buses() -> anyhow::Result<()> {
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

    // Sub: register the actual ModelAdapterNode under `model/pool`.
    let provider: Arc<dyn Provider> = scripted(vec![text_response("from-the-pool")]);
    let _sub_node = ModelAdapterNode::new(
        provider.clone(),
        &sub_bus,
        NodeId::new("model/pool"),
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

    // Connect PoolNode; it forwards model_call to model/pool on sub bus.
    let pool_node = Arc::new(PoolNode {
        node_id: NodeId::new("pool/top"),
        top_bus: top_bus.clone(),
        sub_bus: sub_bus.clone(),
        pool: Arc::new(pool),
    });
    pool_node.clone().connect().await;
    tokio::time::sleep(Duration::from_millis(50)).await;

    // Note: the Engine sends model_call as BROADCAST (not strictly routed to
    // the target node — pool/top picks it up by being a `model_call`
    // subscriber). To keep this test focused on PoolNode, we DO NOT connect
    // any additional ModelAdapterNode on the top bus — only the PoolNode
    // and the engine. The PoolNode forwards the call to model/pool on the
    // sub bus, which provides the actual response.
    let cfg = AgentConfig {
        agent_id: "pool-agent".into(),
        model_config: ModelConfig {
            provider: "scripted".into(),
            model: "scripted-v1".into(),
        },
        system_prompt_template: "You are helpful.".into(),
        initial_memory: vec![],
        max_turns: 5,
        tool_timeout_ms: Some(3000),
        permissions: Default::default(),
        routes: {
            let mut r = std::collections::HashMap::new();
            r.insert(
                "model_call".into(),
                arf_core::Route::strict(vec![NodeId::new("pool/top")]),
            );
            r
        },
        checkpoint_rules: vec![],
        processors: Default::default(),
        on_member_failed: None,
        tools_include: None,
        tools_exclude: vec![],
        skills_include: None,
        skills_exclude: vec![],
    };
    let mut engine = EngineBuilder::new(vec![top_bus.clone()]).build(cfg).await?;
    let mut state = arf_core::State::new();
    let cancel = CancellationToken::new();
    let out = tokio::time::timeout(
        Duration::from_secs(10),
        engine.run(&mut state, "use the pool".into(), cancel),
    )
    .await
    .expect("engine run timed out")
    .expect("engine run failed");
    // The pool forwards model_call to sub_bus's model_node, which replies
    // with "from-the-pool".
    assert_eq!(out, "from-the-pool");
    assert_eq!(state.messages.len(), 3); // system + user + assistant
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
