# 任务 9.4.1：ModelAdapterPoolNode facade（sub-bus 网关 + 真实并发边界）

> Phase 9 — 9.4 L4 模型能力大类 · 第 1 task（依赖 9.2.5）
> 父文档：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`（commit `8ad5247`）
> 前置：9.2.5（Engine 多 ModelAdapter 候选切换，真实双 LLM 验证 resolve_model）
> 输出物：`docs/v1.x/phase9/audit-probe-9.4.1.md`
> 探查结论：**不预设**——本 doc 不写任何预期结果

---

## 设计思路

9.2.5 探查了 Engine 解析**多 raw ModelAdapterNode**（每个直接挂 bus）；本 task (9.4.1) 探查 `ModelAdapterPoolNode` 作为**单 facade 暴露 + sub-bus 转发 + 真实并发负载 + 3 overflow 策略边界**：

- **Facade 模式**：top bus 上**只看到 1 个** `node_type="model"` 节点（NodeId `model/pool`），engine 不感知 pool 内部 N 个 resource
- **advertised_provider**：facade 在 top bus 声明 `capabilities.provider = "pool"`（与 sub-bus 上真实 provider 标识**不同**——registry.rs:254 唯一匹配）
- **sub-bus 网关**：facade 收到 model_call → pool acquire lease → forward 到 sub-bus → 等 model_response → forward 回 top bus → release lease
- **Pool 抽象**（`arf-pool::Pool<ModelAdapterResource>`）：bounded `max_size` + 3 overflow 策略（`Queue(n)` / `Reject` / `Block(timeout)`）
- **真实 LLM 边界探查**（按用户 2026-07-03 反馈）：
  - pool 注册 2-5 节点（**2 min-5 max 边界**）
  - 尝试多节点并发 2-4-7 个 model_call
  - 验证 3 种行为：**正常响应 / 临时挂载并响应（Block）/ 进入队列排队响应（Queue）**
  - **真实 DashScope qwen3.7-max-preview**（不是 mock）
- **9.4.2 探查**：Provider::supported_models capability-based 路由（依赖 9.4.1 facade）
- **9.4.3 探查**：Pool overflow 三策略完整覆盖（Queue / Reject / Block）

按父 spec §3 探查 4 步流程 + §4 find signals 跑。

---

## 与现有测试的边界

- `crates/arf-pool/tests/integration.rs::pool_node_with_engine_react_loop`（line 87）：
  **已实证** facade 模式 + ReAct 闭环（mock StubProvider，pool max_size=1 + Overflow::Block(2s)）
- **本 task 不重复**该 test；聚焦**端到端真实 LLM 边界探查**（pool N=2-5 × 并发 K=2-4-7 × 3 overflow 策略）：
  - facade advertised_provider 让 engine 正确解析（registry.rs:253 resolve_model）
  - pool lease 在 request 完成后正确释放（可被再次 acquire）
  - **真实 qwen 并发**在 (N=2, K=2) / (N=2, K=4) / (N=2, K=7) / (N=3, K=4) / (N=3, K=7) / (N=5, K=7) 等边界组合下行为正确
  - 3 overflow 策略（Queue(n) / Reject / Block(timeout)）在真实并发下的边界行为

---

## 探查步骤（按父 spec §3.1）

### Step 1 — 写最小代码路径（app 层 ≤ 90 行）

`pool_node_facade.rs`，**真实 LLM** 驱动（DashScope qwen3.7-max-preview via DASHSCOPE_API_KEY），
**3×3 矩阵 + 3 overflow 策略变体**：

```rust
async fn build_facade_with_pool(
    max_size: usize,           // 2, 3, 5（边界）
    overflow: Overflow,         // Queue(n) / Reject / Block(timeout)
) -> Arc<ModelAdapterPoolNode> {
    let top_bus = Arc::new(Bus::new(Duration::from_millis(500), Duration::from_secs(3), 32));
    let sub_bus = Arc::new(Bus::new(Duration::from_millis(500), Duration::from_secs(3), 32));

    let pool: Pool<ModelAdapterResource> = Pool::new(PoolConfig {
        max_size, overflow, idle_timeout: None,
    });
    // 真实 qwen provider 装 N 次到 pool
    let provider_factory = || -> Arc<dyn Provider> { live_qwen().expect("DASHSCOPE_API_KEY") };
    for _ in 0..max_size {
        let r = pool.provision(|| Ok(ModelAdapterResource::new(provider_factory()))).await.unwrap();
        pool.release(&r);
    }
    tokio::time::sleep(Duration::from_millis(50)).await;

    let pool_node = Arc::new(ModelAdapterPoolNode {
        node_id: NodeId::new("model/pool"),
        top_bus: top_bus.clone(),
        sub_bus: sub_bus.clone(),
        pool: Arc::new(pool),
        advertised_provider: "pool".into(),
        advertised_models: vec!["qwen3.7-max-preview".into()],
    });
    pool_node.clone().connect().await.unwrap();

    // 关键：sub-bus 装 N 个 ModelAdapterNode（每 resource 一个），
    // provider 标识 = "openai"（OpenAIProvider.name()），与 facade advertised_provider "pool" 不同
    // → Registry::resolve_model 唯一匹配 facade
    spawn_sub_bus_responders(&sub_bus, max_size, provider_factory);
    pool_node
}

async fn spawn_k_concurrent_runs(pool_node: Arc<ModelAdapterPoolNode>, k: usize) -> Vec<String> {
    let engine_pool = build_engines_against_facade(pool_node, k).await;
    let mut handles = Vec::with_capacity(k);
    for (i, engine) in engine_pool.into_iter().enumerate() {
        handles.push(tokio::spawn(async move {
            let mut state = State::new();
            let cancel = CancellationToken::new();
            engine.run(&mut state, format!("hi-{i}").into(), cancel).await
        }));
    }
    let results: Vec<String> = futures::future::join_all(handles).await
        .into_iter().map(|r| r.unwrap().unwrap_or_else(|e| format!("ERR:{e:?}")))
        .collect();
    results
}
```

**3×3 矩阵 + 3 overflow 策略变体**（每组合验证）：
- 节点数 N ∈ {2, 3, 5}（**边界**：2 min / 3 中 / 5 max）
- 并发 K ∈ {2, 4, 7}（**覆盖**：K ≤ N（正常）/ N < K ≤ N+queue（Block+Queue）/ K > N+queue（边界压力））
- overflow ∈ {Queue(N+5), Reject, Block(5s)}

测试矩阵 3×3 = 9 个 cell + 3 策略变体 ≈ **9-15 test cases**：

| # | test (N nodes, K calls, overflow) | 期望行为（不预设，仅文档化） |
|---|---|---|
| 1 | `pool_2n_2k_queue` (N=2, K=2, Queue(7)) | 2 立即响应（≤ pool 容量）|
| 2 | `pool_2n_4k_queue` (N=2, K=4, Queue(7)) | 2 立即 + 2 排队（pool 满 2/2，剩 2 入 Queue）|
| 3 | `pool_2n_7k_queue` (N=2, K=7, Queue(7)) | 2 立即 + 5 排队（池满 2/2，剩 5 入 Queue(7) 总共 7 待处理）|
| 4 | `pool_3n_4k_queue` (N=3, K=4, Queue(7)) | 3 立即 + 1 排队 |
| 5 | `pool_3n_7k_queue` (N=3, K=7, Queue(7)) | 3 立即 + 4 排队（Queue(7) 容纳 4 个）|
| 6 | `pool_5n_2k_queue` (N=5, K=2, Queue(7)) | 2 立即（5 节点只用 2）|
| 7 | `pool_5n_4k_queue` (N=5, K=4, Queue(7)) | 4 立即（5 节点只用 4）|
| 8 | `pool_5n_7k_queue` (N=5, K=7, Queue(7)) | 5 立即 + 2 排队 |
| 9 | `pool_2n_7k_reject` (N=2, K=7, Reject) | 2 立即 + 5 拒绝（PoolError::Full）|
| 10 | `pool_2n_4k_block` (N=2, K=4, Block(10s)) | 2 立即 + 2 Block 等待（前 2 释放后接）|
| 11 | `pool_2n_7k_block` (N=2, K=7, Block(10s)) | 2 立即 + 5 Block 等待（串行 4 轮）|
| 12 | `pool_5n_7k_reject` (N=5, K=7, Reject) | 5 立即 + 2 拒绝（确认 Reject 边界）|

**关键探查价值**：
- 单元 1-8 (Queue) = 完整 3×3 矩阵验证 `Overflow::Queue` 在 pool 满时 buffer K-N 个调用
- 单元 9, 12 (Reject) = 边界：calls > pool_size 时立即返 PoolError::Full
- 单元 10-11 (Block) = 边界：calls > pool_size 时阻塞等到 lease 释放
- 整体 = L4 model_pool_overflow + model_discovery capability

### Step 2 — framework 接触点 file:line

```bash
grep -n 'pub struct ModelAdapterPoolNode\|pub fn connect\|advertised_provider' crates/arf-model-adapter/src/pool_node.rs | head -10
grep -n 'pub async fn acquire\|pub fn provision\|pub async fn release' crates/arf-pool/src/lib.rs | head -10
grep -n 'pub enum Overflow\|pub enum PoolError' crates/arf-pool/src/overflow.rs crates/arf-pool/src/lib.rs | head -5
```

逐行解释（按 file:line 锚定 framework 接触点）：
- `ModelAdapterPoolNode`（pool_node.rs:34）— facade struct
- `ModelAdapterPoolNode::connect()`（pool_node.rs:46 区域）— 注册到 top bus + spawn forward loop
- `Pool::acquire()`（arf-pool/src/lib.rs:184）— 返 `Lease<R>`，Drop 时 auto-release
- `Pool::provision()`（arf-pool/src/lib.rs）— 加 resource 到 pool
- `Overflow::{Queue, Reject, Block}`（arf-pool/src/overflow.rs）
- `PoolError::{Full, Timeout, Closed}`（arf-pool/src/lib.rs:50）

### Step 3 — framework 真实行为

```bash
cd /home/wangxie/open_deepseek_arf
DASHSCOPE_API_KEY=<env> \
  cargo test -p arf-e2e --test pool_node_facade -- --nocapture --test-threads=1 2>&1 | tee /tmp/pool_facade_run.log
```

逐行解释：
- **真实 LLM 驱动**：每 pool resource 装一个 OpenAIProvider + qwen3.7-max-preview
- 9-12 test cases 各跑独立 Pool + bus
- 观察：matrix 单元 (N, K) 下实际并发响应时间、成功率、pool 满/排队/拒绝行为
- **关键时序断言**：(N=2, K=7, Queue) → 期望总耗时 ≈ (K / N) × 单 LLM latency

**Read `/tmp/pool_facade_run.log` 后填 Step 4 `framework 行为`**（真实 qwen latency，不 mock）。

### Step 4 — 判定 + 记录（按父 spec §3.3 输出 schema）

| 单元 | 等级 | 判分依据（含 file:line） |
|---|---|---|
| `model_discovery × §2.12` (facade auto) | 待探查 | `ModelAdapterPoolNode.connect()`（pool_node.rs:46）+ `Registry::resolve_model` 匹配 advertised_provider（registry.rs:254） |
| `model_pool_overflow × §2.12` (3 策略边界) | 待探查 | `Pool::acquire` + `Overflow::{Queue,Reject,Block}`（arf-pool/src/overflow.rs）；3×3 矩阵 + 3 策略变体 实证 |
| `pool_lease_lifecycle × §2.12` | 待探查 | `Lease::drop` auto-release（arf-pool/src/lib.rs:184） |
| `model_discovery_capability × §2.12` (L4 完整) | 不适用（留 9.4.2） | Provider::supported_models 路由 |

按 §4 跑 signals（**重点：pool/facade 路径是否引入新病灶**，A3-001 / A4-001 在 pool 抽象路径是否加剧）：

```bash
# A3-001 在 pool 路径：检查 "model" / "provider" / "model_call" 字面量
grep -rn '"model"\|"provider"\|"model_call"\|"model_response"' crates/arf-model-adapter/src/ crates/arf-pool/src/ | grep -v test | head -15
# A4-001 在 pool 路径：pool acquire/release 是否引入新 correlation_id 散落
grep -n 'correlation_id' crates/arf-model-adapter/src/pool_node.rs crates/arf-pool/src/ -r 2>/dev/null | head -5

# §4 信号 cross-check
sed -n '180,200p' crates/arf-pool/src/lib.rs
```

**C. 输出**：`audit-probe-9.4.1.md`。pool/facade 路径是 framework 既有抽象（Phase 6/7 完成），若引入新病灶应在 pool_node.rs 或 arf-pool/src/manager.rs。

---

## 关键设计决策

- **probe 真实 LLM**：按用户 2026-07-03 反馈，9.4.1 用真实 DashScope qwen3.7-max-preview
  （不是 mock），验证 facade 在真实网络 + 真实 latency 下的并发边界。
- **不写新 framework 代码**：9.4.1 是 foundation task，**所有 framework 抽象已存在**
  （Phase 6 §2.P10 Pool / Phase 7 §3.4 ModelAdapterPoolNode）。本 task 纯探查。
- **3×3 矩阵覆盖边界**：N ∈ {2, 3, 5} × K ∈ {2, 4, 7}，9 cell + 3 overflow 变体 ≈ 12 test cases。
  每个 cell 验证 pool 在 (N, K) 组合下的具体行为。
- **3 overflow 策略变体**：
  - `Queue(N+5)` = buffer K-N 个调用（max buffer 给 matrix 留余地）
  - `Reject` = 立即 Full（边界压力测试）
  - `Block(10s)` = 阻塞等到 lease 释放（适合验证 release 生命周期）
- **advertised_provider 用 "pool"**：与 sub-bus 上真实 provider 标识"openai"**不同**，
  避免 `Registry::resolve_model` 误匹配 sub-bus 节点。
- **sub-bus 装 N 个 ModelAdapterNode**：每 pool resource 对应一个 sub-bus node，
  实际接受 model_call。
- **不测 cancel 路径**：9.2.4 已探查 cancel，9.4.1 不重复。
- **不预设期望时序**：framework 实际并发行为（latency、queue order）需实证。
- **时序断言（粗粒度）**：(N=2, K=7, Block) 总耗时 应大于单 LLM 响应（否则 queue 没生效）。

---

## 验证命令（self-review）

```bash
# 跑通（真实 LLM 驱动，key 经 env）
DASHSCOPE_API_KEY=<env> \
  cargo test -p arf-e2e --test pool_node_facade -- --nocapture --test-threads=1

# 既有 integration test（参照模板）
cargo test -p arf-pool --test integration -- --nocapture

# ModelAdapterPoolNode connect path
sed -n '40,80p' crates/arf-model-adapter/src/pool_node.rs

# Pool acquire + Overflow 策略
sed -n '180,200p' crates/arf-pool/src/lib.rs   # Pool::acquire impl
cat crates/arf-pool/src/overflow.rs            # Overflow enum

# §4 信号 cross-check
grep -rn '"model"\|"provider"\|"model_call"\|"model_response"' crates/arf-model-adapter/src/ crates/arf-pool/src/ | grep -v test
grep -n 'correlation_id' crates/arf-model-adapter/src/pool_node.rs crates/arf-pool/src/ -r 2>/dev/null

# 凭据安全
git grep -n 'sk-' -- crates/ docs/
git grep -n '9943d44\|ab948' -- crates/ docs/
```

---

## 与前序 task 的衔接

- 9.2.1 mock chat 骨架
- 9.2.2 真实 LLM ReAct
- 9.2.3 Checkpoint
- 9.2.4 cancel / replay
- 9.2.5 多 ModelAdapter 候选切换（真实双 LLM）
- **9.4.1** ModelAdapterPoolNode facade + **3×3 矩阵 + 3 overflow 策略**（真实 LLM 边界）
- 后续：
  - **9.3.x**（streaming）— 9.4.1 后回做（用户反馈：先生产侧后消费侧 → 9.4 优先，9.3 后补）
  - 9.4.2（Provider::supported_models capability 路由）
  - 9.4.3（Pool overflow 三策略完整覆盖 — 9.4.1 已覆盖大部分，留细节）

---

## 下一步

1. 用户审 task 9.4.1 doc（Gitee 精校）
2. 用户批 → 跑 Step 1-4 探查（**真实 DashScope qwen3.7-max-preview**）
3. 整理 `audit-probe-9.4.1.md`（含 3×3 矩阵实测数据）
4. self-review（凭据 / 一致性 / scope）
5. commit `pool_node_facade.rs` + commit `audit-probe-9.4.1.md`（granular）
6. 回做 9.3.1（streaming）补 spec 顺序
