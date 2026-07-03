# 任务 9.1.3：Bus + multi-bus 拓扑（NodeHandle.attach_to）

> Phase 9 — 第 1.3 task（依赖 9.1.1）
> 父文档：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`（commit `8ad5247`）
> 前置：任务 9.1.1 baseline / 9.1.2 多 Node 异构 均已 stable
> 输出物：`docs/v1.x/phase9/audit-probe-9.1.3.md`
> 探查结论：**不预设**——本 doc 不写任何预期结果

---

## 设计思路

9.1.2 是"一个 Bus 多个 Node"；9.1.3 换维度——"一个 Node 跨多个 Bus"：

- 建 2 个独立 `Bus`（`bus_a` / `bus_b`），各自独立的 online-nodes map / heartbeat / message_count
- 一个 `Node` 先 `bus_a.connect()`（primary），再 `handle.attach_to(bus_b, filter_b)`（attached）
- 该 Node 在 `bus_a` 用 `filter_a`、在 `bus_b` 用 `filter_b`——**per-subscription filter 各异**
- 各 Bus 另接一个 sender Node，验证跨 Bus 消息互不串扰

目的：探查 framework 在单 Node 跨多 Bus 时的真实行为——

- 每个 `Bus::graph()` 是否**独立**看见该 Node（同一 `NodeId` 在两个 Bus 的 graph 中各占一席）
- `send_via(bus_id, …)` 是否精确定向到指定 Bus（不泄漏到另一 Bus）
- `recv()` 是否跨所有 subscription 汇聚（bus_a / bus_b 的消息都能读到）
- per-subscription `filter_a` / `filter_b` 是否**独立**生效

按父 spec §3 探查 4 步流程 + §4 find signals 跑。9.1.1 / 9.1.2 已确认 baseline 与多 Node 干净，本 task 看"单 Node 跨多 Bus"这条正交维度是否仍干净。

---

## 与现有 arf-bus 单测的边界

`crates/arf-bus/src/connection.rs` 已有 3 个 attach 相关单测：
- `attach_to_adds_subscription`（:961）— subscriptions 列表含两条 BusId
- `multi_bus_recv_from_both`（:978）— 跨两 Bus recv
- `multi_bus_filters_isolated_per_subscription`（:1014）— per-subscription filter 隔离

**本 task 不重复功能验证**。9.1.3 是 e2e 层 + 方法论审查：
- 从 **app 视角**（arf-e2e，不碰 crate 内部）判定 `multi_bus_attach` 能力等级（D/C/E/F）
- 探查 `graph()` 在双 Bus 下的**独立性**（现有单测未覆盖 graph 聚合视角）
- 按 §4 find signals 审查抽象（现有单测不做抽象审查）

---

## 探查步骤（按父 spec §3.1）

### Step 1 — 写最小代码路径（app 层 ≤ 80 行）

**目标**：e2e test `multi_bus_attach.rs`，跑一个 Node 跨 2 Bus：

- `bus_a` / `bus_b` 各 `Bus::new(500ms, 2s, 32)`（同 9.1.1/9.1.2 参数）
- `worker` Node：`bus_a.connect(info, filter_a)` → `handle.attach_to(bus_b, filter_b)`
  - `filter_a` types=`[task_a]`，`filter_b` types=`[task_b]`（跨 Bus 白名单不同）
- `sender_a` 接 `bus_a`，`sender_b` 接 `bus_b`
- sender_a 发 `task_a` + `task_b`（broadcast）；sender_b 发 `task_a` + `task_b`

```bash
ls crates/arf-e2e/tests/
$EDITOR crates/arf-e2e/tests/multi_bus_attach.rs
```

逐行解释：
- 复用 9.1.2 的 `drain_types` 模式（不复用 E2EHarness，harness 绑 Engine）
- 全 mock Node，不依赖任何 LLM provider
- demo 控制在 ≤ 80 行

### Step 2 — framework 接触点 file:line

```bash
# attach_to / connect 注册路径
grep -n 'pub async fn attach_to\|pub async fn connect' crates/arf-bus/src/connection.rs
grep -n 'pub async fn send_via\|pub fn subscriptions' crates/arf-bus/src/connection.rs

# graph 独立性（每 Bus 一份 online-nodes map）
grep -n 'pub fn graph\b' crates/arf-bus/src/graph.rs
grep -n 'BusCommand::Connect' crates/arf-bus/src/lib.rs
```

逐行解释：
- 第 1 条：attach_to（:167）与 connect（:411）入口——**特别观察两者 body 是否重复**
- 第 2 条：send_via（:136 定向发）+ subscriptions（:294 列 BusId）
- 第 3 条：graph() 返回单 Bus 的 BusGraph（双 Bus 各调一次）
- 第 4 条：Connect 命令处理（各 Bus 独立 nodes map 注册）

**特别观察**：`connect`（connection.rs:411-458）与 `attach_to`（:167-210）的 body——register → subscribe-after → spawn_forward_task → push Subscription 四步**几乎逐行重复**。是否构成 §4 信号？（探查执行者判，不预设）

### Step 3 — framework 真实行为

```bash
cd /home/wangxie/open_deepseek_arf
cargo test -p arf-e2e --test multi_bus_attach -- --nocapture 2>&1 | tee /tmp/multi_bus_run.log
```

逐行解释：
- 跑 multi_bus_attach test
- `tee` 保留 stdout 供 Step 4 复核
- **探查观察**（不预设）：bus_a.graph() 与 bus_b.graph() 各看见 worker；worker 在 bus_a 只收 task_a、在 bus_b 只收 task_b（per-subscription filter）

**Read `/tmp/multi_bus_run.log` 后填 Step 4 的 `framework 行为` 字段**（基于实际运行输出，**不是** spec / 本 doc 描述）。

### Step 4 — 判定 + 记录（按父 spec §3.3 输出 schema）

**A. (capability, 情景) 单元判定**：

| 单元 | 等级 | 判分依据（含 file:line） |
|---|---|---|
| `multi_bus_attach × §2.0` | 待探查 | attach_to 后双 Bus 独立 graph + 定向 send_via 核 |
| `bus_health_observe × §2.0`（双 Bus） | 待探查 | 每 Bus graph 独立性 |
| `node_online_announcement × §2.0`（跨 Bus） | 待探查 | attach 时向 bus_b 广播 online，bus_a 不受影响 |
| 其他 L7 | 不适用 | — |

**B. 按 §4 find signals 跑**（重点：connect / attach_to 重复逻辑是否命中；per-subscription 状态是否引入数据重复）：

```bash
# A1-S1 / A4：connect vs attach_to body 重复
sed -n '167,210p' crates/arf-bus/src/connection.rs   # attach_to
sed -n '411,458p' crates/arf-bus/src/connection.rs   # connect

# A3-S3：Subscription 是否仍单一定义（多 subscription 时）
grep -rn 'pub struct Subscription\|struct Subscription' crates/

# A4-S1 延续：filter.matches 在多 subscription 下仍集中？
grep -rn 'filter.matches' crates/arf-bus/src/
```

逐行解释：
- connect / attach_to 并排看，判断重复注册逻辑是否达 §4 某信号阈值（或仅记为观察）
- Subscription 结构唯一性（每 Node 多 subscription，字段是否重复声明事实）
- filter.matches 调用点在 attach 场景是否新增散落

**C. 输出**：

`audit-probe-9.1.3.md`，按 §3.3 schema 填每 (capability, 情景) 单元 + 按 §4.3 填 Y 病灶登记（若有）。

---

## 关键设计决策

- **不复用 `E2EHarness`**：harness 绑 Engine，multi-bus 实验不需要
- **正交维度**：9.1.2 = 多 Node 单 Bus；9.1.3 = 单 Node 多 Bus——两条正交轴分别探查
- **filter 跨 Bus 异构**：`filter_a` / `filter_b` 白名单不同，验证 per-subscription 独立判定
- **graph 独立性是新探查点**：现有单测只验 recv / subscriptions，未验 `graph()` 双 Bus 视角
- **不预设结论**：所有等级与命中由探查执行者填

---

## 验证命令（self-review）

```bash
# 重现 Step 2 接触点
grep -n 'pub async fn attach_to\|pub async fn connect' crates/arf-bus/src/connection.rs
grep -n 'pub fn graph\b' crates/arf-bus/src/graph.rs

# 重现 Step 3 跑通 demo
cargo test -p arf-e2e --test multi_bus_attach -- --nocapture

# 复现 §4 signals
sed -n '167,210p' crates/arf-bus/src/connection.rs
sed -n '411,458p' crates/arf-bus/src/connection.rs
grep -rn 'pub struct Subscription' crates/
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

## 与 task 9.1.1 / 9.1.2 的衔接

- 9.1.1 证 baseline（单 Bus 单 Node）干净
- 9.1.2 证 baseline+1（单 Bus 多 Node）干净
- 9.1.3 验证正交维度（多 Bus 单 Node）是否仍干净
- 若 9.1.3 暴露**新**信号命中（如 connect/attach_to 重复达阈），是 framework 在跨 Bus 抽象上"边界不洁"的首个证据

---

## 下一步

1. 用户审 task 9.1.3 doc（Gitee 精校）
2. 用户批 → 跑 Step 1-4 探查
3. 整理 `audit-probe-9.1.3.md`
4. self-review（占位 / 一致性 / scope）
5. commit `multi_bus_attach.rs` + commit `audit-probe-9.1.3.md`（granular）
6. 进 task 9.1.4（Bus + barrier 多参与者）
