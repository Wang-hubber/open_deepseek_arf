# 任务 9.1.1：Bus + 单一 Node + heartbeat（baseline 探查）

> Phase 9 — 第 1.1 task（依赖最浅，baseline）
> 父文档：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`（commit `8ad5247`）
> 输出物：`docs/v1.x/phase9/audit-probe-9.1.1.md`（独立 commit）
> 探查结论：**不预设**——本 doc 不写任何预期结果，只定义探查步骤

---

## 设计思路

本 task 探查 ARF 框架最基础的形态：

- 仅一个 `Bus` 实例跑起来
- 一个 `Node` 接入
- heartbeat 协议跑通

这是 phase 9 全部 55 task 的 **base case**。所有后续 task 累加复杂度前必须确认 baseline 稳定。

按父 spec §3 探查 4 步流程 + §4 find signals 跑，即使 baseline 也按信号规则过一遍——**避免漏掉已经在 baseline 暴露的"抽象不洁"**。

---

## 探查步骤（按父 spec §3.1）

### Step 1 — 写最小代码路径（app 层 ≤ 80 行）

**目标**：写一个能跑通 `Bus` + 单一 `Node` + heartbeat 的最小 demo。

**先看现有约定**：

```bash
ls crates/arf-e2e/tests/
sed -n '1,80p' crates/arf-e2e/tests/common/harness.rs
```

逐行解释：
- `ls` —— 列出现有 e2e 测试文件，按命名约定（`react_loop.rs` / `mcp_facade.rs` 等）跟现有保持一致
- `sed ... harness.rs` —— 看 `E2EHarnessBuilder` 公共 API，决定 Step 1 写 demo 时是否复用 harness

**写 demo**：

```bash
# 新建 e2e test 文件
$EDITOR crates/arf-e2e/tests/baseline_bus.rs  # 用 harness 构建 bus + dummy node
```

逐行解释：
- demo 控制在 ≤ 80 行（spec §3.1 第 1 条）
- 用现有 `E2EHarnessBuilder` 复用 setup（避免重新搭 Bus / Node）
- **不调任何 LLM provider**——避免外部因素混淆 bus 行为观察

**验证 Demo 编译**：

```bash
cargo build -p arf-e2e --tests 2>&1 | head -20
```

逐行解释：
- 编译通过即可，不跑 run——Step 4 才真正触发

### Step 2 — framework 接触点 file:line

```bash
# Bus 构造 & connect
grep -n 'pub fn new\b\|impl Bus' crates/arf-bus/src/lib.rs | head -10
grep -n 'pub async fn connect\|pub fn connect' crates/arf-bus/src/lib.rs

# heartbeat 实现
grep -n 'pub fn\|pub async fn\|handle_heartbeat_tick\|heartbeat_request' crates/arf-bus/src/heartbeat.rs
grep -n 'handle_heartbeat_ack\|HeartbeatAck' crates/arf-bus/src/lib.rs

# Node trait
grep -n 'pub.*trait Node\|on_message\|snapshot\|restore' crates/arf-core/src/node.rs
```

逐行解释：
- 第 1 条：找 `Bus` 构造函数位置
- 第 2 条：找 `Bus::connect` 入参 / 返回
- 第 3 条：找 heartbeat 时钟 + 协议消息位置
- 第 4 条：找 heartbeat ack / 转发逻辑
- 第 5 条：找 `Node` trait 定义 + 4 个核心方法的位置

**Record these file:line into a temp note**（执行探查时手写）。

### Step 3 — framework 真实行为

```bash
cd /home/wangxie/open_deepseek_arf
cargo test -p arf-e2e --test baseline_bus -- --nocapture 2>&1 | tee /tmp/baseline_bus_run.log
```

逐行解释：
- 跑 `baseline_bus` 测试
- `--nocapture` —— 保留 stdout（包括 RUST_LOG 等 trace 消息）
- `tee` —— 输出到日志文件供 Step 4 复核

**Read 后填 Step 4 的 `framework 行为` 字段**（基于实际运行输出，**不是** spec 描述）。

### Step 4 — 判定 + 记录（按父 spec §3.3 输出 schema）

**A. 判定 (capability, 情景) 单元等级**：

| 单元 | 等级 | 判分依据（含 file:line） |
|---|---|---|
| `bus_health_observe × §2.0` | 待探查 | Read `crates/arf-bus/src/lib.rs::graph()` 实现 + 看返回 `BusGraph` 字段 |
| `heartbeat × §2.0` | 待探查 | Read `crates/arf-bus/src/heartbeat.rs` + 看 ack 是否自动注入 |
| `node_online_announcement × §2.0` | 待探查 | Read `Bus::connect` 看是否 broadcast 一次 `node_online` |
| `multi_bus_attach × §2.0` | **不适用**（本 task 单 bus） | — |
| `barrier_sync × §2.0` | **不适用**（需 ≥ 2 参与者） | — |
| `checkpoint_rules × §2.0` | **不适用**（无 Engine） | — |

注：表格中"待探查"是给执行者填的占位，**不预设任何结果**。

**B. 按 §4 find signals 跑**（即使 baseline 也跑）：

```bash
# A1 / A2 信号（多在类型签名）
grep -rn 'pub trait Node\b' crates/arf-core/src/
grep -n 'fn on_message\|fn snapshot\|fn restore' crates/arf-core/src/node.rs

# A3 信号（数据唯一 — 检查字段重叠）
grep -n 'pub struct Bus\b' crates/arf-bus/src/
grep -n 'pub struct NodeHandle\|pub struct Subscription\|pub struct BusGraph' crates/arf-bus/src/

# A4 信号（处理集中 — 检查 filter / validate / permission / convert 散落）
grep -rn 'fn filter\b\|MessageFilter::matches' crates/arf-bus/src/
grep -rn 'fn validate\b' crates/
```

逐行解释：
- 信号命中也只是信号；命中 ≠ 病灶，需 §3.3 进一步判 "信号是否构成病灶 Y/N"
- 即使 baseline 不命中也要"显式确认未命中"——避免与 framework 代码演进混淆

**C. 输出**：

填充 `audit-probe-9.1.1.md`，按父 spec §3.3 schema 填：
- 每个 (capability, 情景) 单元：能力等级 / 判分依据 / framework 行为 / 信号命中 / 信号是否构成病灶 / 影响面
- 信号命中 Y → 按父 spec §4.3 病灶登记 schema 登记
- 信号命中 N → 进"观察记录"

---

## 关键设计决策

- **不依赖任何 LLM provider**：用 harness 复用 + 跳过真实 model call
- **复用 `E2EHarnessBuilder`**：避免 demo setup 重复
- **探查 4 步全跑**：即使 baseline 也按 §4 signals 全跑，避免漏"已在 baseline 触雷"的信号
- **结果独立 commit**：`audit-probe-9.1.1.md` 是独立 commit；如果写了新 e2e test 文件（`baseline_bus.rs`），则另 commit
- **不预设任何结论**：本 doc 不写预期结果，全部由探查执行者填

---

## 验证命令（self-review）

```bash
# 重现 Step 2 framework 接触点
grep -n 'pub fn new\b' crates/arf-bus/src/lib.rs
grep -n 'pub.*connect\b' crates/arf-bus/src/lib.rs
grep -n 'heartbeat_request\|HeartbeatAck' crates/arf-bus/src/

# 重现 Step 3 跑通 demo
cargo test -p arf-e2e --test baseline_bus -- --nocapture
```

每条命令在执行时记录输出，照搬到 `audit-probe-9.1.1.md` 的 file:line 字段。

---

## 输出 schema 提示

按父 spec §3.3 输出 schema：

```
单元              : bus_health_observe × §2.0
能力等级           : <D / C / E / F>
判分依据           : <具体观察 + framework 接触点 file:line>
framework 行为   : <run / grep / Read 得到的真实行为>
信号命中（来自 §4）: <signal ID> × <file:line> × <命中形态>
信号是否构成病灶   : Y / N（Y = 命中 + 影响面足够大；N = 命中但无可观察影响）
影响面            : 若 Y，描述
```

Y 项 → 按父 spec §4.3 schema 登记病灶 ID（如 `A1-001`）。

---

## 下一步

1. 用户审本 task doc
2. 用户批 → 跑 Step 1-4 探查
3. 整理 `audit-probe-9.1.1.md`
4. self-review（占位 / 一致性 / scope）
5. commit `audit-probe-9.1.1.md` + commit e2e test 新文件（如有）
6. 进 task 9.1.2（Bus + 多 Node 异构 node_type）
