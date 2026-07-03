# 任务 9.4.3：Pool overflow 三策略完整覆盖

> Phase 9 — 9.4 L4 模型能力大类 · 第 3 task（依赖 9.4.1）
> 父文档：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`（commit `8ad5247`）
> 前置：9.4.1（pool facade，4 mock + 1 F-002 实证 pass，5/5 test）
> 输出物：`docs/v1.x/phase9/audit-probe-9.4.3.md`
> 探查结论：**不预设**——本 doc 不写任何预期结果

---

## 设计思路

9.4.1 探查了 pool facade + 4 个直接 pool overflow mock test + F-002 实证。**9.4.3 补充 3 overflow 策略的完整覆盖**：
- 9.4.1 已有：mock Reject / Block(timeout) / Block 成功路径 / Queue(2) 满 / F-002 实证
- 9.4.3 需补充：
  - **real LLM test**：pool + 真实 qwen + 3 策略（验证 pool 在真实 LLM latency 下行为）
  - **3 策略对比**：同一场景（K=5 caller, N=2 pool）跑 3 次，分别用 Reject/Queue/Block，**对比**成功率/时延
  - **边界 case**：`Overflow::Block(Duration::ZERO)`（应立即 timeout）/ `Overflow::Queue(0)`（应立即 Full）/ `Overflow::Queue(usize::MAX)`（大 queue 永不 Full）

按父 spec §3 探查 4 步流程 + §4 find signals 跑。

---

## 与现有测试的边界

- 9.4.1 实证：4 mock pool overflow test（Reject / Block timeout / Block 成功 / Queue 满）+ F-002 实证
- **9.4.3 补充**：
  - real LLM 实证（pool + 真实 qwen）
  - 3 策略同场景对比
  - 边界 case（Block(0) / Queue(0) / Queue(MAX)）

---

## 探查步骤（按父 spec §3.1）

### Step 1 — 写最小代码路径（app 层 ≤ 90 行）

`pool_overflow_complete.rs`，mock + 真实 LLM，3-4 test cases：

3-4 test cases：

| # | test | 探查 |
|---|---|---|
| 1 | `real_qwen_with_pool_block_strategy` | pool N=1 + Overflow::Block(5s) + 真实 qwen（latency 1-5s）—— 验证 2 顺序调用，第 1 个释放后第 2 个拿到（**不**永久 hang） |
| 2 | `three_strategies_comparison` | 同场景（pool N=1, 2 个 caller 并发 acquire）：Reject → 第 2 立即 Full；Queue(1) → 第 2 入队等；Block(200ms) → 第 2 阻塞 200ms 后 Timeout。**对比** 3 策略行为 |
| 3 | `block_zero_duration_immediate_timeout` | `Overflow::Block(Duration::ZERO)` —— 期望立即 Timeout（不阻塞） |
| 4 | `queue_zero_or_max_boundary` | `Overflow::Queue(0)`（应立即 Full）/ `Overflow::Queue(usize::MAX)`（应永不 Full） |

**关键探查价值**：
- 单元 1：real LLM 验证（9.4.1 无此）
- 单元 2：3 策略对比（spec 要求）
- 单元 3-4：边界 case（9.4.1 未覆盖）

### Step 2 — framework 接触点 file:line

```bash
grep -n "pub enum Overflow" crates/arf-pool/src/overflow.rs
grep -n "pub enum PoolError" crates/arf-pool/src/lib.rs | head -3
grep -n "fn acquire\|Overflow::" crates/arf-pool/src/manager.rs | head -10
```

逐行解释：
- `Overflow` enum 3 variants：overflow.rs
- `PoolError` 4 variants：lib.rs:50-60（Full / Timeout / Closed / Acquire）
- `Pool::acquire` 实现：manager.rs + lib.rs:184
- 3 策略在 acquire 中的 dispatch

### Step 3 — framework 真实行为

```bash
cd /home/wangxie/open_deepseek_arf
DASHSCOPE_API_KEY=<env> \
  cargo test -p arf-e2e --test pool_overflow_complete -- --nocapture --test-threads=1 2>&1 | tee /tmp/pool_overflow_run.log
```

逐行解释：
- mock 3 测即时（边界 + 对比）
- 1 真实 LLM 测 ≈ 5-10s（pool + 真实 qwen Block 策略）

**Read `/tmp/pool_overflow_run.log` 后填 Step 4 `framework 行为`**。

### Step 4 — 判定 + 记录（按父 spec §3.3 输出 schema）

| 单元 | 等级 | 判分依据（含 file:line） |
|---|---|---|
| `pool_overflow_reject × §2.12` (3 策略完整) | **D** | 9.4.1 实证 + 9.4.3 边界 case 实证 |
| `pool_overflow_queue × §2.12` (3 策略完整) | **D** | 9.4.1 实证 + Queue(0) 边界实证 |
| `pool_overflow_block × §2.12` (3 策略完整) | **D** | 9.4.1 实证 + Block(0) 边界 + real LLM 实证 |
| `pool_overflow_real_llm × §2.12` (真实 LLM 边界) | **D**（待探查） | real qwen 端到端 |

按 §4 跑 signals（**重点：3 策略在真实 LLM latency 下行为**）：

```bash
# A3-001 在 pool 路径：检查 Overflow 字面量
grep -rn '"Queue"\|"Reject"\|"Block"' crates/arf-pool/src/ | head -5
# A4-001 在 pool 路径：pool acquire 集中
grep -n 'pub async fn acquire' crates/arf-pool/src/manager.rs crates/arf-pool/src/lib.rs | head -5
```

**C. 输出**：`audit-probe-9.4.3.md`。9.4.1 已覆盖大部分，9.4.3 补充 real LLM + 边界 + 3 策略对比。**预期 0 新 F-lesion**（F-002 critical 已 9.4.1 记）。

---

## 关键设计决策

- **probe 不写新 framework 代码**：9.4.3 是 9.4.1 收尾，framework 抽象已存在。本 task 纯探查。
- **mock 边界优先**：3 策略对比 + 边界 case（Block(0)/Queue(0)）—— 9.4.1 未覆盖
- **real LLM 测 1 个**：pool + 真实 qwen Block 策略—— 9.4.1 全 mock
- **预期 0 新 F-lesion**：9.4.1 已暴露 F-001/F-002 critical/F-003；9.4.3 仅补充覆盖
- **3 策略对比用表格呈现**（spec 要求）：成功率 / 时延 / 失败原因

---

## 验证命令（self-review）

```bash
# 跑通
DASHSCOPE_API_KEY=<env> \
  cargo test -p arf-e2e --test pool_overflow_complete -- --nocapture --test-threads=1

# Overflow 3 variants
cat crates/arf-pool/src/overflow.rs

# Pool acquire dispatch
grep -B 1 -A 30 "pub async fn acquire" crates/arf-pool/src/manager.rs | head -40

# §4 信号 cross-check
grep -rn '"Queue"\|"Reject"\|"Block"\|"Full"\|"Timeout"' crates/arf-pool/src/ | head -10

# 凭据安全
git grep -n 'sk-' -- crates/ docs/
```

---

## 与前序 task 的衔接

- 9.4.1 pool facade + 4 mock test + F-002 实证 + 5 F-lesion（F-001/F-002 critical/F-003/F-007/F-008）
- **9.4.3** Pool overflow 三策略完整覆盖（real LLM + 边界 + 对比）—— 9.4.1 收尾
- 9.4.2 Provider::supported_models capability 路由 + F-007 + F-008
- 9.4 大类收尾，9.5.x（McpNode 工具集成）下一大类

---

## 下一步

1. 用户审 task 9.4.3 doc（Gitee 精校）
2. 用户批 → 跑 Step 1-4 探查（mock + 真实 qwen）
3. 整理 `audit-probe-9.4.3.md`（3 策略对比表 + 边界实证）
4. self-review（凭据 / 一致性 / scope）
5. commit `pool_overflow_complete.rs` + commit `audit-probe-9.4.3.md`（granular）
6. 回 9.5.1（McpNode + FsDiscovery）—— 9.4 大类收尾，phase 9 下一大类