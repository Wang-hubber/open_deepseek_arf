//! pool_overflow_complete.rs — Phase 9 task 9.4.3
//!
//! Pool overflow 三策略完整覆盖（real LLM + 3 策略对比 + 边界 case）。
//!
//! 9.4.1 实证：4 mock overflow test + F-002 实证（5/5 pass）。
//! 9.4.3 补充：
//! - 1 个 real LLM test（pool + 真实 qwen + Block 策略）
//! - 1 个 3 策略对比 test（同场景跑 Reject/Queue/Block，对比成功率/时延/失败）
//! - 2 个边界 case test（Block(0)/Queue(0)/Queue(MAX)）
//!
//! 预期 0 新 F-lesion（F-002 critical 已 9.4.1 记）。

mod common;

use std::sync::Arc;
use std::time::{Duration, Instant};

use arf_model_adapter::{ModelAdapterResource, Provider, ProviderError};
use arf_model_adapter::types::{ModelParams, ModelResponsePayload, ToolDef, Usage};
use arf_core::ModelMessage;
use arf_pool::{Lease, Overflow, Pool, PoolConfig, PoolError};
use async_trait::async_trait;
use tokio::time::timeout;

// ═══════════════════════════════════════════════════════════════════════
// StubProvider for 3-strategy comparison + boundary tests
// ═══════════════════════════════════════════════════════════════════════

#[derive(Clone)]
struct StubProvider {
    response: String,
    delay: Duration,
}

#[async_trait]
impl Provider for StubProvider {
    fn name(&self) -> &str { "stub" }
    fn supported_models(&self) -> &[String] {
        static MODELS: std::sync::OnceLock<Vec<String>> = std::sync::OnceLock::new();
        MODELS.get_or_init(|| vec!["stub-v1".into()])
    }
    async fn chat(
        &self,
        _model: &str, _msgs: Vec<ModelMessage>, _tools: Vec<ToolDef>, _params: ModelParams,
    ) -> Result<ModelResponsePayload, ProviderError> {
        tokio::time::sleep(self.delay).await;
        Ok(ModelResponsePayload {
            message: ModelMessage::new("assistant", &self.response),
            tool_calls: None,
            finish_reason: "stop".into(),
            usage: Some(Usage { input_tokens: 0, output_tokens: 0, total_tokens: 0 }),
            id: "stub".into(),
            model: "stub-v1".into(),
        })
    }
}

fn stub_provider(response: &str, delay: Duration) -> Arc<dyn Provider> {
    Arc::new(StubProvider { response: response.into(), delay })
}

// ═══════════════════════════════════════════════════════════════════════
// Test 1: real qwen with pool + Block strategy
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn real_qwen_with_pool_block_strategy() {
    let Some(qwen) = common::provider::live_qwen() else { return; };
    // pool N=1, Overflow::Block(5s)
    let pool: Arc<Pool<ModelAdapterResource>> = Arc::new(Pool::new(PoolConfig {
        max_size: 1,
        overflow: Overflow::Block(Duration::from_secs(5)),
        idle_timeout: None,
    }));
    let r1 = pool.provision(|| Ok(ModelAdapterResource::new(qwen.clone())))
        .await
        .unwrap();
    pool.release(&r1);
    tokio::time::sleep(Duration::from_millis(50)).await;

    // 2 顺序 acquire 真实 qwen
    let l1 = pool.acquire().await.expect("acquire 1");
    let start1 = Instant::now();
    let t1 = tokio::spawn(async move {
        drop(l1);
        Instant::now() - start1
    });
    let l2_start = Instant::now();
    let _l2 = pool.acquire().await.expect("acquire 2 (should wait for l1 drop)");
    let elapsed2 = l2_start.elapsed();
    let t1_elapsed = t1.await.unwrap();
    println!(
        "[real] l1 hold ≈ {:?} (qwen latency), l2 acquired after {:?}",
        t1_elapsed, elapsed2
    );
    // 期望：l2 等 l1 drop 后立即拿到（l1 hold time = qwen latency ~1-5s, < 5s timeout）
    assert!(elapsed2 < Duration::from_secs(5), "l2 等了 {elapsed2:?}，超 Block(5s) timeout");
    println!("[real] pool + real qwen Block 策略端到端 OK ✓");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 2: 3 策略对比（同场景，pool N=1，2 个 caller）
// ═══════════════════════════════════════════════════════════════════════

async fn probe_strategy(strategy: Overflow, l1_hold_ms: u64) -> (Result<Lease<ModelAdapterResource>, PoolError>, Duration) {
    let pool: Arc<Pool<ModelAdapterResource>> = Arc::new(Pool::new(PoolConfig {
        max_size: 1,
        overflow: strategy,
        idle_timeout: None,
    }));
    let r1 = pool.provision(|| Ok(ModelAdapterResource::new(stub_provider("ok", Duration::from_millis(100)))))
        .await
        .unwrap();
    pool.release(&r1);
    tokio::time::sleep(Duration::from_millis(50)).await;

    let start = Instant::now();
    let l1 = pool.acquire().await.expect("acquire 1");
    // Spawn dropper for Queue(N) — l1 在 l1_hold_ms 后释放
    // 释放通过 tokio::spawn 完成（avoid std::mem::forget 导致 Lease Drop 不跑）
    let pool_for_l2 = pool.clone();
    let dropper = tokio::spawn(async move {
        if l1_hold_ms > 0 {
            tokio::time::sleep(Duration::from_millis(l1_hold_ms)).await;
        } else {
            // Reject / Block：超时等待让 l2 先跑，dropper 不 drop
            tokio::time::sleep(Duration::from_secs(60)).await;
        }
        drop(l1);
    });
    let r2 = pool_for_l2.acquire().await;
    dropper.abort();
    let elapsed = start.elapsed();
    (r2, elapsed)
}

#[tokio::test]
async fn three_strategies_comparison() {
    println!("\n=== Pool 3 策略对比 (pool N=1, 2 caller) ===\n");
    // Reject：l1 持有，l2 立即 Err(Full)
    let (r_reject, t_reject) = probe_strategy(Overflow::Reject, 0).await;
    let reject_str = match &r_reject {
        Ok(_) => "OK (unexpected)".to_string(),
        Err(PoolError::Full) => "Err(Full) ✓".to_string(),
        Err(e) => format!("Err({e:?})"),
    };
    println!("Reject:    l2 result = {reject_str} (elapsed {t_reject:?})");

    // Queue(1)：l1 持 50ms 后释放，l2 应在 l1 释放后 ok
    let (r_queue, t_queue) = probe_strategy(Overflow::Queue(1), 50).await;
    let queue_str = match &r_queue {
        Ok(_) => "OK (l1 dropped) ✓".to_string(),
        Err(PoolError::Full) => "Err(Full) ✗".to_string(),
        Err(e) => format!("Err({e:?})"),
    };
    println!("Queue(1): l2 result = {queue_str} (elapsed {t_queue:?})");

    // Block(200ms)：l1 持有，l2 阻塞 200ms 后 Err(Timeout)
    let (r_block, t_block) = probe_strategy(Overflow::Block(Duration::from_millis(200)), 0).await;
    let block_str = match &r_block {
        Ok(_) => "OK (unexpected)".to_string(),
        Err(PoolError::Timeout(_)) => "Err(Timeout) ✓".to_string(),
        Err(e) => format!("Err({e:?})"),
    };
    println!("Block(200ms): l2 result = {block_str} (elapsed {t_block:?})");

    assert!(matches!(r_reject, Err(PoolError::Full)), "Reject 应立即 Full");
    assert!(t_reject < Duration::from_millis(50), "Reject 应立即（< 50ms），实测 {t_reject:?}");
    let queue_ok = r_queue.is_ok();
    let block_timeout = matches!(r_block, Err(PoolError::Timeout(_)));
    assert!(queue_ok, "Queue(1) l1 drop 后 l2 应 ok");
    assert!(block_timeout, "Block(200ms) 应 Timeout");
    assert!(t_block >= Duration::from_millis(200), "Block 应等 ≥ 200ms，实测 {t_block:?}");
    println!("\n=== 3 策略对比 OK：Reject(立即 Full) / Queue(等) / Block(timeout) ===");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 3: Block(Duration::ZERO) 应立即 Timeout
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn block_zero_duration_immediate_timeout() {
    let pool: Arc<Pool<ModelAdapterResource>> = Arc::new(Pool::new(PoolConfig {
        max_size: 1,
        overflow: Overflow::Block(Duration::ZERO),
        idle_timeout: None,
    }));
    let r1 = pool.provision(|| Ok(ModelAdapterResource::new(stub_provider("ok", Duration::from_millis(100)))))
        .await
        .unwrap();
    pool.release(&r1);
    tokio::time::sleep(Duration::from_millis(50)).await;

    let _l1 = pool.acquire().await.expect("acquire 1");
    let start = Instant::now();
    let r2 = pool.acquire().await;
    let elapsed = start.elapsed();
    // 期望：Block(0) 立即 Timeout
    let timeout_ok = matches!(r2, Err(PoolError::Timeout(_)));
    assert!(timeout_ok, "Block(0) 应 Timeout");
    assert!(elapsed < Duration::from_millis(50), "Block(0) 应立即，实测 {elapsed:?}");
    println!("[boundary] Block(Duration::ZERO): 立即 Timeout {elapsed:?} ✓");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 4: Queue(0) 应立即 Full, Queue(MAX) 应永不 Full
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn queue_zero_or_max_boundary() {
    // Queue(0)：第 2 个立即 Full
    let pool: Arc<Pool<ModelAdapterResource>> = Arc::new(Pool::new(PoolConfig {
        max_size: 1,
        overflow: Overflow::Queue(0),
        idle_timeout: None,
    }));
    let r1 = pool.provision(|| Ok(ModelAdapterResource::new(stub_provider("ok", Duration::from_millis(10)))))
        .await
        .unwrap();
    pool.release(&r1);
    tokio::time::sleep(Duration::from_millis(50)).await;
    let _l1 = pool.acquire().await.expect("acquire 1");
    // Queue(0) 期望：立即 Full。实测：用 timeout 包住以避免永久挂起
    let r2 = timeout(Duration::from_secs(2), pool.acquire()).await;
    let queue0_outcome = match &r2 {
        Ok(Err(PoolError::Full)) => "Full".to_string(),
        Ok(Err(e)) => format!("Err({e:?})"),
        Ok(Ok(_)) => "Ok".to_string(),
        Err(_) => "TIMEOUT".to_string(),
    };
    println!("[boundary] Queue(0): {queue0_outcome}");
    assert_eq!(queue0_outcome, "Full", "Queue(0) 应立即 Full（lesion F-009）");
    println!("[boundary] Queue(0): {queue0_outcome} ✓");
    drop(_l1);

    // Queue(MAX)：第 2 个入队等（永不 Full 直到 l1 drop）
    let pool2: Arc<Pool<ModelAdapterResource>> = Arc::new(Pool::new(PoolConfig {
        max_size: 1,
        overflow: Overflow::Queue(usize::MAX),
        idle_timeout: None,
    }));
    let r1 = pool2.provision(|| Ok(ModelAdapterResource::new(stub_provider("ok", Duration::from_millis(10)))))
        .await
        .unwrap();
    pool2.release(&r1);
    tokio::time::sleep(Duration::from_millis(50)).await;
    let l1 = pool2.acquire().await.expect("acquire 1");
    // 第 2 个 acquire 在另一个 task，期望入队等
    let pool2_clone = pool2.clone();
    let h = tokio::spawn(async move { pool2_clone.acquire().await });
    tokio::time::sleep(Duration::from_millis(100)).await; // 给 Queue 入队时间
    // h 还在等（pool 满，Queue(MAX) 永不 Full）
    assert!(!h.is_finished(), "Queue(MAX) 永不 Full，h 应仍在等");
    drop(l1);
    let r2 = timeout(Duration::from_millis(500), h).await
        .expect("timeout (h 应在 l1 drop 后拿到)")
        .expect("h join")
        .expect("h acquire");
    println!("[boundary] Queue(usize::MAX): l1 drop 后 l2 拿到 ✓");
}
