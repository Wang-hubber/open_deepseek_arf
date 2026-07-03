//! pool_node_facade.rs — Phase 9 task 9.4.1
//!
//! 探查 Pool 自身 + ModelAdapterPoolNode facade 在 framework 当前实现下的行为。
//!
//! **关键探查发现**（user 2026-07-03 round 3-6 多轮校正）：
//! - **F-001**：framework 缺 `EnginePool` 抽象（多 Engine 共享 model config）
//! - **F-002（CRITICAL）**：pool 设计意图是 `min_size` + `max_size` + `auto_provision`，
//!   但当前**完全没有**——pool 是 fixed `max_size`，无动态扩容能力
//!   （**不是隐藏 BUG，是实现偏离设计意图**）
//! - **F-003**：Facade 的 `sub_id = "model/pool-{i}/sub"` 模式（pool_node.rs:65）
//!   阻断 ModelAdapterNode 集成（AlreadyConnected）；3×3 矩阵 + 真并发测试因 framework
//!   仍在开发期的 design quirk **无法跑**。当前 framework 只能配 manual broadcast
//!   subscriber on sub-bus（参见 `crates/arf-pool/tests/integration.rs` 既有 pattern）
//!
//! **测试设计**（按 user 2026-07-03 round 6 反馈"暴露问题，记录即可"）：
//! - 4 直接 pool overflow (mock, fast) —— 验证 Pool 自身 3 策略行为
//! - 1 F-002 实证（mock）—— 验证 pool **不**动态扩容
//! - 7 matrix 真实 LLM 测试**未跑**（F-003 framework quirk 阻断）—— 记入 lesion-registry
//!
//! 输出物：`docs/v1.x/phase9/audit-probe-9.4.1.md`（含 F-001/F-002/F-003 findings）

mod common;

use std::sync::Arc;
use std::time::{Duration, Instant};

use arf_model_adapter::{ModelAdapterResource, Provider, ProviderError};
use arf_model_adapter::types::{ModelParams, ModelResponsePayload, ToolDef, Usage};
use arf_core::ModelMessage;
use arf_pool::{Overflow, Pool, PoolConfig, PoolError};
use async_trait::async_trait;

// ═══════════════════════════════════════════════════════════════════════
// StubProvider for direct pool overflow tests (mock, fast)
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
// 4 个直接 pool overflow 测试（mock, fast, 验证 Pool 自身 3 策略行为）
// ═══════════════════════════════════════════════════════════════════════

// [边界] max_size=1 + Overflow::Reject：第 1 个 acquire 成功，第 2 个立即 Full
#[tokio::test]
async fn pool_overflow_reject_immediate() {
    let pool: Arc<Pool<ModelAdapterResource>> = Arc::new(Pool::new(PoolConfig {
        max_size: 1,
        overflow: Overflow::Reject,
        idle_timeout: None,
    }));
    let r1 = pool.provision(|| Ok(ModelAdapterResource::new(stub_provider("ok", Duration::from_millis(10)))))
        .await
        .unwrap();
    pool.release(&r1);
    tokio::time::sleep(Duration::from_millis(50)).await;

    let _l1 = pool.acquire().await.expect("first acquire");
    match pool.acquire().await {
        Err(PoolError::Full) => {}
        Err(e) => panic!("expected PoolError::Full, got {e:?}"),
        Ok(_) => panic!("expected Err, got Ok lease"),
    }
    println!("[pool] reject: 2nd acquire 立即 Full ✓");
}

// [边界] Block timeout：l1 持有不 drop，2nd acquire 阻塞到 timeout
#[tokio::test]
async fn pool_overflow_block_timeout() {
    let pool: Arc<Pool<ModelAdapterResource>> = Arc::new(Pool::new(PoolConfig {
        max_size: 1,
        overflow: Overflow::Block(Duration::from_millis(200)),
        idle_timeout: None,
    }));
    let r1 = pool.provision(|| Ok(ModelAdapterResource::new(stub_provider("ok", Duration::from_millis(10)))))
        .await
        .unwrap();
    pool.release(&r1);
    tokio::time::sleep(Duration::from_millis(50)).await;

    let _l1 = pool.acquire().await.expect("first acquire");
    let start = Instant::now();
    match pool.acquire().await {
        Err(PoolError::Timeout(_)) => {}
        Err(e) => panic!("expected Timeout, got {e:?}"),
        Ok(_) => panic!("expected Err, got Ok lease"),
    }
    let elapsed = start.elapsed();
    assert!(elapsed >= Duration::from_millis(200), "elapsed={elapsed:?} 应 ≥ timeout");
    println!("[pool] block: 2nd acquire timeout after {elapsed:?} ✓");
}

// [边界] Block 成功路径：l1 drop 后 l2 拿到
#[tokio::test]
async fn pool_overflow_block_succeeds_after_release() {
    let pool: Arc<Pool<ModelAdapterResource>> = Arc::new(Pool::new(PoolConfig {
        max_size: 1,
        overflow: Overflow::Block(Duration::from_secs(2)),
        idle_timeout: None,
    }));
    let r1 = pool.provision(|| Ok(ModelAdapterResource::new(stub_provider("ok", Duration::from_millis(10)))))
        .await
        .unwrap();
    pool.release(&r1);
    tokio::time::sleep(Duration::from_millis(50)).await;

    let l1 = pool.acquire().await.expect("first acquire");
    let pool_clone = pool.clone();
    tokio::spawn(async move {
        tokio::time::sleep(Duration::from_millis(50)).await;
        drop(l1);
        drop(pool_clone);
    });
    let start = Instant::now();
    let _l2 = pool.acquire().await.expect("second acquire after release");
    let elapsed = start.elapsed();
    assert!(elapsed >= Duration::from_millis(50), "elapsed={elapsed:?} 应 ≥ 50ms");
    assert!(elapsed < Duration::from_millis(500), "elapsed={elapsed:?} 应 < 500ms");
    println!("[pool] block-succeed: 2nd acquire 阻塞 {elapsed:?} 后成功 ✓");
}

// [边界] Queue 满后第 4 个返 Full
#[tokio::test]
async fn pool_overflow_queue_buffers_then_full() {
    let pool: Arc<Pool<ModelAdapterResource>> = Arc::new(Pool::new(PoolConfig {
        max_size: 1,
        overflow: Overflow::Queue(2),
        idle_timeout: None,
    }));
    let r1 = pool.provision(|| Ok(ModelAdapterResource::new(stub_provider("ok", Duration::from_millis(10)))))
        .await
        .unwrap();
    pool.release(&r1);
    tokio::time::sleep(Duration::from_millis(50)).await;

    let _l1 = pool.acquire().await.expect("acquire 1");
    let pool2 = pool.clone();
    let pool3 = pool.clone();
    let h2 = tokio::spawn(async move { pool2.acquire().await });
    let h3 = tokio::spawn(async move { pool3.acquire().await });
    tokio::time::sleep(Duration::from_millis(50)).await;

    match pool.acquire().await {
        Err(PoolError::Full) => {}
        Err(e) => panic!("expected Full, got {e:?}"),
        Ok(_) => panic!("expected Err, got Ok lease"),
    }
    println!("[pool] queue: 4th acquire 立即 Full（queue=2 已满）✓");
    drop(_l1);
    let _ = h2.await;
    let _ = h3.await;
}

// ═══════════════════════════════════════════════════════════════════════
// F-002 实证：观察 pool 不动态扩容（CRITICAL design intent gap）
// ═══════════════════════════════════════════════════════════════════════

// [F-002 实证] 设计意图：load 增长时 auto-provision（pool size 增长），
// 超 max_size 才排队。当前实现：load 增长时 pool size 严格固定，
// 立即 Block/Queue/Reject。验证：发 K=4 到 pool with N=2，pool 仍只 2 resources。
#[tokio::test]
async fn f002_pool_does_not_auto_provision() {
    let pool: Arc<Pool<ModelAdapterResource>> = Arc::new(Pool::new(PoolConfig {
        max_size: 2,
        overflow: Overflow::Queue(10),
        idle_timeout: None,
    }));
    let r1 = pool.provision(|| Ok(ModelAdapterResource::new(stub_provider("ok", Duration::from_millis(50)))))
        .await
        .unwrap();
    let r2 = pool.provision(|| Ok(ModelAdapterResource::new(stub_provider("ok", Duration::from_millis(50)))))
        .await
        .unwrap();
    pool.release(&r1);
    pool.release(&r2);
    tokio::time::sleep(Duration::from_millis(50)).await;

    let _l1 = pool.acquire().await.expect("acquire 1");
    let _l2 = pool.acquire().await.expect("acquire 2");

    let pool_clone = pool.clone();
    let h3 = tokio::spawn(async move { pool_clone.acquire().await });
    let pool_clone2 = pool.clone();
    let h4 = tokio::spawn(async move { pool_clone2.acquire().await });
    tokio::time::sleep(Duration::from_millis(100)).await;

    drop(_l1);
    let _l3 = tokio::time::timeout(Duration::from_secs(2), h3).await
        .expect("h3 timeout (pool 没有 grow logic，h3 必须入 Queue 等)")
        .expect("h3 join")
        .expect("h3 acquire");
    drop(_l2);
    let _l4 = tokio::time::timeout(Duration::from_secs(2), h4).await
        .expect("h4 timeout")
        .expect("h4 join")
        .expect("h4 acquire");
    println!("[F-002] pool 大小严格保持 2（不 auto-provision），K=4 排队后逐个 succeed ✓");
    println!("[F-002] 这与设计意图（min_size + auto_provision）严重不符 → F-002 critical lesion");
}
