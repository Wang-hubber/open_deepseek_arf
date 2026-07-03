# 任务 9.2.1：Engine + 单 ModelAdapter

> Phase 9 — 9.2 B 单 agent 骨架 · 第 1 task（依赖 9.1.x）— **首个引入 Engine 的 task**
> 父文档：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`（commit `8ad5247`）
> 前置：9.1 A 总线基线大类已收尾（9.1.1–9.1.5），2 病灶 A4-001 / A3-001 已登记
> 输出物：`docs/v1.x/phase9/audit-probe-9.2.1.md`
> 探查结论：**不预设**——本 doc 不写任何预期结果

---

## 设计思路

9.1.x 探查纯 Bus 层（Node 拓扑 + barrier + 容错）；9.2.1 **首次引入 Engine**，进入情景 **§2.1（单 agent 无 tool）**。探查最基础的 agent 骨架：

- `EngineBuilder` 组装一个 `Engine` + 单个 `ModelAdapterNode`（无 tool / 无 MCP / 无 session store）
- provider 用 scripted mock（离线，返回单条 text response，不依赖任何 LLM）
- `engine.run(user_input)` 跑最小 chat：user → `model_call` → `model_response` → final text
- 探查 **chat 能力等级** + Engine 组装接触点 + **验证已登记病灶在 Engine 层的实证蔓延**

**本 task 的双重目的**：
1. **能力判定**：`chat`（L1）× §2.1 —— Engine + 单 ModelAdapter 端到端 chat 是否 D
2. **病灶回归实证**：A4-001（correlation_id convert 散落）与 A3-001（消息类型标识散落）此前判定"贯穿全框架 request-response 协议"——9.2.1 是**首次在 Engine 主循环**（model_call/model_response 请求-响应对）实证它们是否真的蔓延到 Engine 层。若实证，强化两病灶的影响面。

按父 spec §3 探查 4 步流程 + §4 find signals 跑。

---

## 与现有测试 / harness 的关系

- **复用 `E2EHarness`**（与 9.1.x 不同）：9.2 起 Engine 是探查核心，harness 正是"tempdir + bus + nodes + engine"的标准组装路径，探查 Engine 就应经它
- `react_loop.rs::react_single_round_text`（react_loop.rs:50）已用 `E2EHarness::new(simple_mock)` 验证单 round 纯文本**功能**
- **本 task 不重复功能验证**：9.2.1 独立写 `engine_single_model.rs`，聚焦：
  - chat 能力**等级判定**（react_loop 只验功能，不判 D/C/E/F）
  - Engine 组装**接触点回溯**（EngineBuilder→Engine→ModelAdapterNode→消息流，react_loop 不做源码审查）
  - **§4 signal 审查 + 病灶蔓延实证**（react_loop 不做抽象审查）

---

## 探查步骤（按父 spec §3.1）

### Step 1 — 写最小代码路径（app 层 ≤ 60 行）

**目标**：`engine_single_model.rs`，最小 Engine chat：

```rust
let mut h = E2EHarness::new(ProviderKind::Mock(simple_mock("hello from model"))).await.unwrap();
let out = h.run_react("hi").await.expect("run");
assert_eq!(out, "hello from model");
h.assert_state_messages(2);                 // user + assistant
// Engine 接触点可观察量：
//   - engine.agent_id() / config() / session_id()
//   - primary_bus().graph() 含 engine node + model adapter node
```

```bash
ls crates/arf-e2e/tests/
$EDITOR crates/arf-e2e/tests/engine_single_model.rs
```

逐行解释：
- `simple_mock("...")` = scripted provider 返回单条 text（provider.rs:138）
- `run_react` 跑完整 ReAct（此处单 round，无 tool → 一次 model_call 即 final）
- 额外断言 Engine 侧可观察量（agent_id / graph 含 engine + model node），验证组装拓扑

### Step 2 — framework 接触点 file:line

```bash
# Engine 组装
grep -n 'pub async fn build\|impl EngineBuilder\|ModelDecl\|ResourceSpec' crates/arf-engine/src/*.rs | head
grep -n 'pub struct Engine\b\|pub async fn run\|fn dispatch_incoming' crates/arf-engine/src/engine.rs

# ModelAdapterNode 组装 + model_call/model_response 消息流
grep -n 'pub async fn new\|model_call\|model_response' crates/arf-model-adapter/src/node.rs

# 病灶蔓延实证：Engine 主循环的 correlation_id + 消息类型
grep -n 'correlation_id\|"model_call"\|"model_response"' crates/arf-engine/src/engine.rs
```

逐行解释：
- 第 1 条：EngineBuilder::build 组装（ModelDecl 声明 model / ResourceSpec 资源规格）
- 第 2 条：Engine::run 主循环 + dispatch_incoming 分发
- 第 3 条：ModelAdapterNode 如何收 model_call、发 model_response
- 第 4 条：**A4-001 / A3-001 蔓延实证锚点**——Engine 主循环是否也手写 correlation_id 塞挖 + 裸字面量 "model_call"/"model_response"

**特别观察**：model_call→model_response 是 Engine 的核心 request-response 对，其 correlation_id 关联（A4-001）与消息类型字面量（A3-001）是否在 engine.rs 复现散落。

### Step 3 — framework 真实行为

```bash
cd /home/wangxie/open_deepseek_arf
cargo test -p arf-e2e --test engine_single_model -- --nocapture 2>&1 | tee /tmp/engine_single_run.log
```

逐行解释：
- 跑 engine_single_model test
- `tee` 保留 stdout 供 Step 4 复核
- **探查观察**（不预设）：out=="hello from model"；graph 含 2 node（engine + model adapter）

**Read `/tmp/engine_single_run.log` 后填 Step 4 的 `framework 行为` 字段**（基于实际运行输出）。

### Step 4 — 判定 + 记录（按父 spec §3.3 输出 schema）

**A. (capability, 情景) 单元判定**：

| 单元 | 等级 | 判分依据（含 file:line） |
|---|---|---|
| `chat × §2.1` | 待探查 | Engine + 单 ModelAdapter 端到端 chat 核 |
| `model_switch × §2.1` | **不适用**（单 model 无切换，留 9.2.5） | — |
| `streaming_response × §2.1` | **不适用**（留 9.3 J 流式大类） | — |
| `thinking_visible × §2.1` | 待探查（若 mock 不含 reasoning，标不适用） | — |

**B. 按 §4 find signals 跑**（重点：病灶蔓延实证 + Engine 特有新 signal）：

```bash
# A4-001 蔓延：Engine model_call/model_response 的 correlation_id 手写塞挖
grep -rn 'correlation_id' crates/arf-engine/src/ | grep -v test

# A3-001 蔓延：Engine 裸字面量消息类型
grep -rn '"model_call"\|"model_response"' crates/arf-engine/src/ crates/arf-model-adapter/src/ | grep -v test

# Engine 特有新抽象是否引入 signal（AgentConfig / ModelDecl / ResourceSpec）
grep -rn 'pub struct AgentConfig\|pub struct ModelDecl\|pub struct ResourceSpec' crates/
```

逐行解释：
- 前两条：实证 A4-001 / A3-001 是否蔓延至 Engine（若是，在 audit 标"已登记病灶蔓延点"，不新登记）
- 第三条：Engine 新抽象是否触发新 §4 signal（新病灶则新登记 + 追加 lesion-registry）

**C. 输出**：

`audit-probe-9.2.1.md`，按 §3.3 schema 填 + 按 §4.3 填 Y 病灶（若新病灶 → 追加 `lesion-registry.md`；若仅蔓延已有病灶 → 在 audit 记蔓延点，更新 lesion-registry 对应病灶的 file:line 影响面）。

---

## 关键设计决策

- **复用 E2EHarness**：9.2 起 Engine 是核心，经标准组装路径探查
- **scripted mock**：离线、确定性，不依赖 LLM provider（simple_mock 单 text）
- **单 round 无 tool**：最纯粹的 chat 骨架，隔离 tool/session/流式等后续维度
- **病灶回归实证**：本 task 首次让 A4-001/A3-001 在 Engine 层受检，是两病灶"贯穿全框架"论断的第一个 Engine 证据
- **不预设结论**：所有等级与命中由探查执行者填

---

## 验证命令（self-review）

```bash
grep -n 'pub async fn build' crates/arf-engine/src/*.rs
cargo test -p arf-e2e --test engine_single_model -- --nocapture
grep -rn 'correlation_id' crates/arf-engine/src/ | grep -v test
grep -rn '"model_call"\|"model_response"' crates/arf-engine/src/ crates/arf-model-adapter/src/ | grep -v test
```

---

## 输出 schema 提示

按父 spec §3.3 输出 schema：

```
单元              : <capability name> × §2.1
能力等级           : <D / C / E / F>
判分依据           : <具体观察 + framework 接触点 file:line>
framework 行为   : <run / grep / Read 得到的真实行为>
信号命中（来自 §4）: <signal ID> × <file:line> × <命中形态>
信号是否构成病灶   : Y / N
影响面            : 若 Y，描述
```

Y 项（新病灶）→ 登记 + 追加 `lesion-registry.md`；已有病灶蔓延 → 更新对应病灶影响面。

---

## 与前序 task 的衔接

- 9.1.x 探查纯 Bus 层（结构/协议/容错），2 病灶 A4-001 / A3-001 判定"贯穿全框架"但仅在 Bus 层实证
- 9.2.1 首次引入 Engine，**实证两病灶是否蔓延至 Engine 主循环**（model_call/model_response 请求-响应）
- 9.2.1 是 9.2 大类基座；9.2.2（ReAct 主循环）/ 9.2.3（Checkpoint）/ 9.2.4（interrupt）/ 9.2.5（多 model）在此之上

---

## 下一步

1. 用户审 task 9.2.1 doc（Gitee 精校）
2. 用户批 → 跑 Step 1-4 探查
3. 整理 `audit-probe-9.2.1.md`（+ 病灶蔓延 / 新病灶则更新 `lesion-registry.md`）
4. self-review（占位 / 一致性 / scope）
5. commit `engine_single_model.rs` + commit `audit-probe-9.2.1.md`（granular）
6. 进 9.2.2（Engine + ReAct 主循环 chat）
