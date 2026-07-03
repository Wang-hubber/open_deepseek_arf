# 任务 9.1.2：Bus + 多 Node 异构（baseline+1）

> Phase 9 — 第 1.2 task（依赖 9.1.1）
> 父文档：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`（commit `8ad5247`）
> 前置：任务 9.1.1 baseline 已 stable
> 输出物：`docs/v1.x/phase9/audit-probe-9.1.2.md`
> 探查结论：**不预设**——本 doc 不写任何预期结果

---

## 设计思路

在 baseline（单 Node）之上加**多 Node 异构**：

- 同一 `Bus` 接入 ≥ 2 个 `Node`
- 各 `Node` 的 `NodeInfo.node_type` 不同（如 `engine` / `mcp` / `model` / `trace`）
- 各 `Node` 的 `MessageFilter` 可能不同（type 白名单 + ToMatch 模式不同）

目的：探查 framework 在多 Node 共存时的真实行为——

- `bus.graph()` 是否同时看见所有 Node
- 不同 filter 是否正确过滤 broadcast 消息（每 Node 收到的消息类型独立）
- `NodeInfo` 多 Node 时 capabilities 字段是否独立

按父 spec §3 探查 4 步流程 + §4 find signals 跑（baseline 已确认干净，本 task 看累加复杂度后是否仍干净）。

---

## 探查步骤（按父 spec §3.1）

### Step 1 — 写最小代码路径（app 层 ≤ 80 行）

**目标**：写一个 e2e test，跑 ≥ 3 个异构 Node 同 Bus：

- 一个 `engine` 类型 Node（filter：`types=Some(vec!["model_call", "tool_exec"])`, `ToMatch::All`）
- 一个 `mcp` 类型 Node（filter：`types=None`, `ToMatch::All`）
- 一个 `model` 类型 Node（filter：`types=Some(vec!["model_response_chunk", "model_response"])`, `ToMatch::All`）

每个 Node 提供一个独立 `dummy_id`（`<type>/<unique>`）；

```bash
ls crates/arf-e2e/tests/
sed -n '1,30p' crates/arf-e2e/tests/common/harness.rs
```

逐行解释：
- `ls` —— 列出现有 e2e 命名
- `harness.rs head` —— 看 harness 公共 API（不复用 harness，因为它已绑 Engine；本 task 不需要 Engine）

**写 demo**：

```bash
$EDITOR crates/arf-e2e/tests/multi_node_heterogeneous.rs
```

逐行解释：
- `cargo build -p arf-e2e --tests` 通过即可，不跑 run
- 关键：不依赖任何外部 LLM provider，全用 mock Node
- demo 控制在 ≤ 80 行

### Step 2 — framework 接触点 file:line

```bash
# connect / NodeHandle / filter 路由
grep -n 'pub async fn connect' crates/arf-bus/src/connection.rs
grep -n 'pub fn matches\|MessageFilter::matches' crates/arf-core/src/lib.rs
grep -n 'pub struct NodeHandle\b\|pub struct Subscription' crates/arf-bus/src/connection.rs

# 多 Node graph 聚合
grep -n 'pub fn graph\b\|impl Bus' crates/arf-bus/src/graph.rs
grep -n 'pub fn nodes' crates/arf-bus/src/graph.rs
```

逐行解释：
- 第 1 条：找 connect 入参 / 返回类型
- 第 2 条：MessageFilter::matches 入口（决定 Node 是否接收某 message）
- 第 3 条：NodeHandle / Subscription struct
- 第 4 条：bus graph() 返回 BusGraph 包含所有 Node
- 第 5 条：BusGraph::nodes 字段

**特别观察**：`graph()` 是否按 connect 顺序返回 Node？是否有重复或丢失？（baseline 仅 1 Node，看不出问题）

### Step 3 — framework 真实行为

```bash
cd /home/wangxie/open_deepseek_arf
cargo test -p arf-e2e --test multi_node_heterogeneous -- --nocapture 2>&1 | tee /tmp/multi_node_run.log
```

逐行解释：
- 跑 multi_node_heterogeneous test
- `tee` 保留 stdout 供 Step 4 复核
- **预期探查**：graph() 看见 ≥ 3 个 Node（不同 node_type），filter 之间互不干扰

**Read 后填 Step 4 的 `framework 行为` 字段**（基于实际运行输出，**不是** spec 描述）。

### Step 4 — 判定 + 记录（按父 spec §3.3 输出 schema）

**A. (capability, 情景) 单元判定**：

| 单元 | 等级 | 判分依据（含 file:line） |
|---|---|---|
| `bus_health_observe × §2.0` | 待探查 | 多 Node graph 聚合行为核 |
| `multi_bus_attach × §2.0` | **不适用**（本 task 单 bus；9.1.3 才涉及 attach_to） | — |
| `node_online_announcement × §2.0` | 待探查 | 多 Node 是否都广播 node_online |
| 其他 L7 | 不适用 | — |

**B. 按 §4 find signals 跑**（重点看 baseline 未触雷的 signals 在多 Node 下是否仍干净）：

```bash
# A3-S3 同名 struct 跨 crate（多 Node 时 NodeHandle / Subscription 是否仍单一定义）
grep -rn 'pub struct NodeHandle\|pub struct Subscription\|pub struct NodeInfo' crates/

# A4-S1 filter 散落（多 filter 并存时是否仍集中）
grep -rn 'fn filter\b\|MessageFilter::matches\|fn matches' crates/

# baseline 唯一观察 A 跟踪（heartbeat 不增 message_count）跨多 Node 是否仍成立
grep -n 'message_count.fetch_add' crates/arf-bus/src/lib.rs
```

逐行解释：
- 多 Node 场景比 baseline 更可能暴露 A3（同名字段跨多条 record 时的唯一性）
- Step 4 输出与 9.1.1 同样规格

**C. 输出**：

`audit-probe-9.1.2.md`，按 §3.3 schema 填每 (capability, 情景) 单元 + 按 §4.3 填 Y 病灶登记。

---

## 关键设计决策

- **不复用 `E2EHarness`**：harness 已绑 Engine，且含 `inject_tool_exec_responder`，多 Node 实验不需要
- **mock Node**：定义最小的 dummy Node（不调任何外部 LLM）
- **filter 异构**：3 Node 用不同 filter 组合，验证 message dispatch 时各 filter 独立判断
- **探查重点**：基线多 Node 累加后，framework 是否还符合 spec §4 signals 全干净
- **不预设结论**：所有等级与命中由探查执行者填

---

## 验证命令（self-review）

```bash
# 重现 Step 2 接触点
grep -n 'pub async fn connect' crates/arf-bus/src/connection.rs
grep -n 'pub fn matches' crates/arf-core/src/lib.rs

# 重现 Step 3 跑通 demo
cargo test -p arf-e2e --test multi_node_heterogeneous -- --nocapture

# 复现 §4 signals
grep -rn 'pub struct NodeHandle\|pub struct Subscription' crates/arf-bus/
grep -n 'message_count.fetch_add' crates/arf-bus/src/lib.rs
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

Y 项 → 按父 spec §4.3 schema 登记病灶 ID（如 `A3-001`）。

---

## 与 task 9.1.1 的衔接

- 9.1.1 已证 baseline 干净
- 9.1.2 验证 baseline+1（多 Node）是否仍干净
- 若 9.1.2 暴露**新**信号命中，是 framework 在累加复杂度时"边界不洁"的证据

---

## 下一步

1. 用户审 task 9.1.2 doc
2. 用户批 → 跑 Step 1-4 探查
3. 整理 `audit-probe-9.1.2.md`
4. self-review（占位 / 一致性 / scope）
5. commit `audit-probe-9.1.2.md` + commit e2e test 新文件
6. 进 task 9.1.3（multi-bus 拓扑）
