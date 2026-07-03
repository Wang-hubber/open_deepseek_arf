# 任务 9.1.4：Bus + barrier 多参与者

> Phase 9 — 第 1.4 task（依赖 9.1.1）
> 父文档：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`（commit `8ad5247`）
> 前置：9.1.1 baseline / 9.1.2 多 Node / 9.1.3 multi-bus 均已 stable
> 输出物：`docs/v1.x/phase9/audit-probe-9.1.4.md`
> 探查结论：**不预设**——本 doc 不写任何预期结果

---

## 设计思路

前 3 个 task 探查了拓扑（单/多 Node、单/多 Bus）的**结构**；9.1.4 换维度——探查 Bus 的**同步原语** barrier：

- 一个 `Bus`，接 ≥ 3 个 participant Node
- 主流程调 `Bus::barrier(participants, timeout)` 广播 `barrier_request`
- 各 participant 从 `recv()` 收到 `barrier_request` → 提取 `correlation_id` → `NodeHandle::barrier_ack(cid)`
- 收 `BarrierReceipt`，核 `acked` / `missing` / `timed_out`

**两条路径都探**（正常 + 部分）：
- **全 ack 路径**：3 participant 全 ack → acked=3 / missing 空 / timed_out=false
- **部分 ack 路径**：只 2 participant ack（第 3 个不响应）→ missing 含第 3 个 / timed_out=true

目的：探查 framework barrier 协议的真实行为——

- 多参与者时 `barrier_request` 是否**广播**到全部 participant（而非逐一 p2p）
- `correlation_id` 匹配 + `participants_set` 过滤是否正确（无关 ack 被忽略）
- best-effort 语义：超时未 ack 者是否准确进 `missing`

按父 spec §3 探查 4 步流程 + §4 find signals 跑。

---

## 与现有 arf-bus 单测的边界

`crates/arf-bus/src/lib.rs` 已有 barrier 单测：
- `barrier_with_real_ack_via_handle`（:1528）— 单 participant ack 路径
- `barrier_no_responses_all_missing`（:1595）— 零响应全 missing
- `barrier_ignores_mismatched_correlation_id`（:1610）— cid 不匹配被忽略
- `barrier_empty_participants`（:1638）— 空列表立即返回

**本 task 不重复上述**。9.1.4 是 e2e 层 + 方法论审查：
- 从 **app 视角**（arf-e2e）判定 `barrier_sync` 能力等级（现有单测在 crate 内、多为单 participant）
- 探查 **≥ 3 participant 并发 ack**（现有单测未覆盖多参与者并发）
- 探查 **全 ack + 部分 ack 两路径对照**（现有单测分散、未在一处对照多参与者）
- 按 §4 find signals 审查 barrier 协议抽象（现有单测不做抽象审查）

---

## 探查步骤（按父 spec §3.1）

### Step 1 — 写最小代码路径（app 层 ≤ 80 行）

**目标**：e2e test `barrier_multi.rs`，跑 barrier 全 ack + 部分 ack：

- `Bus::new(500ms, 2s, 32)`（同前 task 参数）
- 3 participant：`p/1` `p/2` `p/3`，各 `bus.connect(info, filter)`（filter 含 `barrier_request` 或 types=None）
- 每个"要 ack 的"participant spawn 后台 task：`loop { recv; if barrier_request → 解析 payload.correlation_id → barrier_ack }`
- **场景 A（全 ack）**：p1/p2/p3 都 spawn acker → `bus.barrier([p1,p2,p3], 1s)` → 断言 acked.len()==3, missing 空, !timed_out
- **场景 B（部分 ack）**：只 p1/p2 spawn acker（p3 静默）→ `bus.barrier([p1,p2,p3], 300ms)` → 断言 acked.len()==2, missing==[p3], timed_out

```bash
ls crates/arf-e2e/tests/
$EDITOR crates/arf-e2e/tests/barrier_multi.rs
```

逐行解释：
- 参考 barrier_request payload schema：`{"correlation_id": "<uuid>", "participants": [...]}`（lib.rs:302-305）
- participant 手动解析 `payload["correlation_id"].as_str()` → `Uuid::parse_str`
- 全 mock Node，不依赖 LLM provider；demo ≤ 80 行

**特别观察**：participant 侧解析 `correlation_id` 是 **stringly-typed JSON 手挖**（无类型化契约）——是否 §4 信号？（探查执行者判，不预设）

### Step 2 — framework 接触点 file:line

```bash
# barrier 广播 + 收集
grep -n 'pub async fn barrier\b' crates/arf-bus/src/lib.rs          # :285
grep -n 'pub struct BarrierReceipt' crates/arf-bus/src/lib.rs       # :55
grep -n 'barrier_request\|barrier_ack' crates/arf-bus/src/lib.rs    # 协议消息类型

# participant ack 路径
grep -n 'pub async fn barrier_ack' crates/arf-bus/src/connection.rs # :325

# forward task 是否放行 barrier_request（对比 heartbeat 拦截）
sed -n '363,397p' crates/arf-bus/src/connection.rs
```

逐行解释：
- 第 1 条：barrier 入口（participants + timeout → BarrierReceipt）
- 第 2 条：BarrierReceipt 字段（correlation_id / acked / missing / timed_out）
- 第 3 条：协议消息类型 barrier_request（Bus 发）/ barrier_ack（participant 发）
- 第 4 条：barrier_ack 构造（payload 写 correlation_id）
- 第 5 条：forward task 只拦 heartbeat_request，barrier_request 经 filter 后 forward 给 participant

**特别观察**：`correlation_id` 在 barrier() 生成（lib.rs:290）→ 塞进 barrier_request payload（:303）→ participant 手挖 payload → barrier_ack payload 回填（connection.rs:330）→ barrier() 手挖比对（lib.rs:333-338）。这条 **stringly-typed 契约链** 跨 4 个 file:line 点，是否达 §4 A3 / A1 信号？（不预设）

### Step 3 — framework 真实行为

```bash
cd /home/wangxie/open_deepseek_arf
cargo test -p arf-e2e --test barrier_multi -- --nocapture 2>&1 | tee /tmp/barrier_multi_run.log
```

逐行解释：
- 跑 barrier_multi test（含场景 A + B）
- `tee` 保留 stdout 供 Step 4 复核
- **探查观察**（不预设）：场景 A receipt.acked 集合 == {p1,p2,p3}；场景 B receipt.missing == [p3] 且 timed_out

**Read `/tmp/barrier_multi_run.log` 后填 Step 4 的 `framework 行为` 字段**（基于实际运行输出，**不是** spec / 本 doc 描述）。

### Step 4 — 判定 + 记录（按父 spec §3.3 输出 schema）

**A. (capability, 情景) 单元判定**：

| 单元 | 等级 | 判分依据（含 file:line） |
|---|---|---|
| `barrier_sync × §2.0`（全 ack） | 待探查 | barrier 多参与者并发 ack 核 |
| `barrier_sync × §2.0`（部分 ack） | 待探查 | missing / timed_out best-effort 语义核 |
| 其他 L7 | 不适用 | — |

**B. 按 §4 find signals 跑**（重点：barrier 协议的 stringly-typed 契约链 + 协议消息类型硬编码字符串）：

```bash
# A3-S4 / A1：correlation_id 契约链跨点
grep -rn 'correlation_id' crates/arf-bus/src/lib.rs crates/arf-bus/src/connection.rs | grep -v test

# A4：barrier 协议消息类型字符串散落（"barrier_request" / "barrier_ack" 硬编码点）
grep -rn '"barrier_request"\|"barrier_ack"' crates/ | grep -v test

# A3-S3：BarrierReceipt 是否单一定义
grep -rn 'struct BarrierReceipt' crates/
```

逐行解释：
- correlation_id 手挖/回填链是否构成 A3 / A1 信号（或仅观察）
- 协议消息类型字符串（"barrier_request" / "barrier_ack"）散落几处——A4 视角
- BarrierReceipt 唯一性

**C. 输出**：

`audit-probe-9.1.4.md`，按 §3.3 schema 填每 (capability, 情景) 单元 + 按 §4.3 填 Y 病灶登记（若有）。

---

## 关键设计决策

- **不复用 `E2EHarness`**：harness 绑 Engine，barrier 原语实验不需要
- **两路径对照**：全 ack（正常）+ 部分 ack（timeout）在同一 test 内对照，凸显 best-effort 语义
- **≥ 3 participant 并发**：现有单测多为单 participant，本 task 验证多参与者并发 ack 汇聚
- **participant 用后台 spawn task ack**：模拟真实异步响应；避免主线程手动 poll
- **探查重点**：barrier 协议的 stringly-typed correlation_id 契约链是否洁净
- **不预设结论**：所有等级与命中由探查执行者填

---

## 验证命令（self-review）

```bash
# 重现 Step 2 接触点
grep -n 'pub async fn barrier\b\|pub struct BarrierReceipt' crates/arf-bus/src/lib.rs
grep -n 'pub async fn barrier_ack' crates/arf-bus/src/connection.rs

# 重现 Step 3 跑通 demo
cargo test -p arf-e2e --test barrier_multi -- --nocapture

# 复现 §4 signals
grep -rn 'correlation_id' crates/arf-bus/src/lib.rs crates/arf-bus/src/connection.rs | grep -v test
grep -rn '"barrier_request"\|"barrier_ack"' crates/ | grep -v test
```

---

## 输出 schema 提示

按父 spec §3.3 输出 schema：

```
单元              : <capability name> × §2.0
能力等级           : <D / C / E / F>
判分依据           : <具体观察 + framework 接触点 file:line>
framework 行为   : <run / grep / Read 得到的真实行为>
信号命中（来自 §4）: <signal ID> × <file:line> × <命中形态>
信号是否构成病灶   : Y / N
影响面            : 若 Y，描述
```

Y 项 → 按父 spec §4.3 schema 登记病灶 ID。

---

## 与 task 9.1.1–9.1.3 的衔接

- 9.1.1–9.1.3 探查 Bus **结构维度**（Node / Bus 拓扑）均洁净（9.1.3 记 1 项 DRY 观察）
- 9.1.4 转 **同步原语维度**（barrier）——首个非结构性协议探查
- 若 9.1.4 暴露协议契约（correlation_id / 消息类型字符串）的信号命中，是 framework 在"约定式协议"上边界不洁的证据

---

## 下一步

1. 用户审 task 9.1.4 doc（Gitee 精校）
2. 用户批 → 跑 Step 1-4 探查
3. 整理 `audit-probe-9.1.4.md`
4. self-review（占位 / 一致性 / scope）
5. commit `barrier_multi.rs` + commit `audit-probe-9.1.4.md`（granular）
6. 进 task 9.1.5（Bus + 异常：lagged / 掉线 / 重连）— 收尾 9.1 大类
