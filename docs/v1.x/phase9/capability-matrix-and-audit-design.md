# Agent 抽象能力探查与抽象审查方法论手册

> Phase 9 方法论手册。本 spec **定义 4 种方法论**，用于逐 task 推进探查；不预设任何问题。

## §0 目标

phase 9 是一次系统性盘清 ARF framework。本 spec 提供 4 种方法论，让每个 task 按方法论跑出真实探查结果：

- **方法论 1 — 能力矩阵**（§1）：盘点 Agent 应当具备的能力；对每个能力单元判定 framework 是否能直接供 / 组合可达 / 扩展可达 / 失败
- **方法论 2 — 情景矩阵**（§2）：以 agent 部署形态递进（无 tool → 单 MCP → 多 MCP → 多 agent → 持久化 → 压缩 → A2A → interrupt → 流式 → 模型发现）；每个情景明确涉及组件与应探查的能力子集
- **方法论 3 — 探查方式**（§3）：每 (capability, 情景) 单元的统一探查流程，含命令模板与输出 schema
- **方法论 4 — 抽象审查方式**（§4）：对 framework 抽象本身做四信条审查（原子化 / 正交 / 数据唯一 / 处理集中）的判定规则（含 find signals），用于发现"绕远路"病灶；**信号命中不等于病灶**，需配影响面判定

后续 task 按这 4 种方法论运行。每个 task 的输出物包括：
- 该 task 命中的真实 capability × 情景 单元的能力等级（D / C / E / F）
- 该 task 命中的真实信号（按方法论 4 的 find signals 跑）
- 完整 file:line 锚点

本 spec **不含任何预设问题**——既无预判病灶，也无预设 pass / fail 假设。每个 task 跑完才出真实结论。

---

## §1 能力矩阵（方法论 1）

### §1.1 Master capability 列表

按 agent lifecycle 与资源形态分 6 类：

**L1 — 基础对话**
- chat
- streaming_response
- thinking_visible

**L2 — 工具使用**
- tool_use
- tool_discovery
- multi_tool_concurrent
- tool_dag_exec（blocked_by + blocking）
- tool_permission（Allow / Ask / Deny）

**L3 — 技能与资源**
- skill_list_progressive
- skill_load_on_demand
- skill_tool_progressive_register
- skill_resource_load

**L4 — 模型**
- model_discovery
- model_switch
- model_pool_overflow

**L5 — Agent 间协作**
- subagent_delegate
- a2a_peer
- engine_nesting

**L6 — 会话与容错**
- session_persist
- session_recover
- context_compact
- interrupt
- member_failed_handling

**L7 — 总线与运行时**
- bus_health_observe
- barrier_sync
- multi_bus_attach
- heartbeat
- node_online_announcement
- checkpoint_rules（5 个 Checkpoint 位置）

**L8 — 扩展点**
- custom_runtime（`RuntimeModule` trait）
- custom_discovery（`DiscoveryBackend` trait）
- custom_summarizer（`Summarizer` trait）
- custom_session_store（`SessionStore` trait）
- custom_checkpoint_rule（`CheckpointRule` factory）
- custom_tool（`Tool` trait）

### §1.2 能力等级判分（D / C / E / F）

| 等级 | 含义 | 判定细则 |
|---|---|---|
| **D — Direct** | framework 端到端供 | 公开 API 直接实现；app 不写非业务代码 |
| **C — Composable** | 组合可达 | framework 已有 2-5 个 primitive，app 用 ≤ 50 行 glue 拼出 |
| **E — Extensible** | 扩展可达 | framework 已声明的 trait/ext 抽象允许 app 实现。可扩展点：`Node` / `ActionMessage` / `Tool` / `DiscoveryBackend` / `RuntimeModule` / `Summarizer` / `SessionStore` / `MessageHandler` / `ResponseProcessor` / `OnMemberFailedHandler` / `CheckpointRule` 构造 |
| **F — FAIL** | 缺 primitive + 缺扩展点 | framework 必须加 primitive / trait 才能让 X 落地 |

**判分按 D → C → E → F 顺序匹配**，首个命中即定格。

### §1.3 能力矩阵的探查输出

每个 task 在自己的探查结果中按 capability 分别判定等级，产出格式见 §3 输出 schema。

---

## §2 情景矩阵（方法论 2）

13 + 1 情景按 agent 部署形态递进：

| # | 情景 | 涉及核心组件 | 关键能力子集（delta） | 探查覆盖度 |
|---|---|---|---|---|
| 0 | 框架空载（baseline） | Bus | L7-全部  | 未探查 |
| 1 | 单 agent 无 tool | + EngineBuilder / Engine / AgentConfig / ModelAdapter | L1 / L4 / L6 / L7 中除 L2 / L5 外 | 未探查 |
| 2 | 单 agent + 单 MCP（本地） | + McpNode / FsDiscovery / LocalRuntime / ScriptTool | + tool_use / tool_discovery / tool_permission / mcp_local_runtime / route_resolution | 未探查 |
| 2-插 | 单 MCP skill→tool 渐进 | + SkillIndex / `use_skill` 协议 / load_skill_resource | + skill_list_progressive / skill_load_on_demand / skill_tool_progressive_register / skill_resource_load | 未探查 |
| 3 | 单 agent + 多 MCP（route 表多选，Strict） | + 多 McpNode 多 namespace | + cross-mcp tool_discovery (dedup) | 未探查 |
| 4 | 单 agent + 多 MCP（Discovery 路由） | + `Route::Discovery` / `Capability` / `DiscoveryCache` | + route_resolution (Discovery) / capability-based subset | 未探查 |
| 5 | 多 agent + 共享 MCP pool | + `MCPPoolNode` / `McpResource` / `Pool` | + mcp_pool_overflow / cross-agent tool_use | 未探查 |
| 6 | 多 agent + A2A peer | + `PeerMessage` / `PeerReply` | + a2a_peer / engine-nesting(只读) | 未探查 |
| 7 | 单 agent + subagent 委派 | + `SubagentDelegate` / `SubagentResult` | + subagent_delegate | 未探查 |
| 8 | 长会话持久化 | + `EngineBuilder::with_session_store` / `SqliteSessionStore` / `SessionData` / `CheckpointSnapshot` | + session_persist / member_failed_handling | 未探查 |
| 9 | 长会话压缩 | + `Compactor` / `Summarizer` / `when_context_over` | + context_compact / custom_summarizer | 未探查 |
| 10 | 中途 interrupt + 恢复 | + `CancellationToken` + replay from session | + interrupt | 未探查 |
| 11 | 流式响应 | + `ModelResponseChunk` / `ModelToolCallDelta` / `chunk_type` tagged union | + streaming_response (chunked) | 未探查 |
| 12 | 模型自动发现 | + `ModelAdapterPoolNode` / `Provider::supported_models` | + model_discovery / model_pool_overflow | 未探查 |

**表注**：
- "关键能力子集（delta）"是该情景**相对前一情景新增**的能力，不是该情景全部能力；前一情景的能力默认继承
- 每个情景默认还包含 L1（chat / streaming_response / thinking_visible）、L7（bus_health_observe / heartbeat 等总线侧）、interrupt 等基础能力
- §1.1 的 L8（扩展点）每个情景都默认存在（任何 trait 可被实现）

**每个情景的探查输出**包含：
- 该情景下 delta 涉及的所有 capability 子集的实际能力等级
- 该情景的最小可跑代码路径（≤ 80 行）
- framework 真实行为 vs spec 预期

---

## §3 探查方式（方法论 3）

### §3.1 探查 4 步流程

每个 (capability, 情景) 单元：

1. **最小代码路径** — 写出 app 层能跑该情景的最小代码（≤ 80 行）
2. **framework 接触点** — 沿路径标记每个 framework 调用（按 type + method），列出 file:line
3. **回到 framework 代码** — 对每个接触点回 framework 源，记录真实行为（不是 spec 描述）
4. **判 + 记** — 判定能力等级（D / C / E / F），若 D / C / E，再按 §4 信号做审查

### §3.2 命令模板

各步骤常用命令：

```bash
# 步骤 2：framework 接触点
grep -n 'pub fn\|pub async fn\|pub struct\|pub trait' crates/<crate>/src/<module>.rs

# 步骤 3：行为确认
cargo test -p <crate> --no-fail-fast 2>&1 | head -50

# 步骤 4：触发实际执行
cargo test -p arf-e2e --test <scenario> -- --nocapture
```

### §3.3 输出 schema

每个 (capability, 情景) 单元的产出：

```
单元              : <capability name> × <情景 #>
能力等级           : D / C / E / F
判分依据           : <具体观察 + framework 接触点 file:line>
framework 行为   : <run / grep / Read 得到的真实行为>
信号命中（来自 §4）: <signal ID> × <file:line> × <命中形态>
信号是否构成病灶   : Y / N（Y = 命中 + 影响面足够大；N = 命中但无可观察影响）
影响面            : 若 Y，描述该信号的影响
```

Y 项进"病灶登记"（按 §4.3 schema），N 项进"观察记录"。

---

## §4 抽象审查方式（方法论 4）

### §4.1 四信条

| 信条 | 定义 |
|---|---|
| **A1 原子化** | 一个抽象做且只做一件事 |
| **A2 正交** | 抽象之间相互独立，可自由替换 |
| **A3 数据唯一** | 一份事实只在一处声明 |
| **A4 处理集中** | 同类转换在单一接缝完成 |

### §4.2 find signals（按信条分组）

每个 signal 是一个**可观察的具体形态**（代码层肉眼可验证）。

#### A1 原子化

| Signal | 形态 |
|---|---|
| **A1-S1** | trait / struct 的方法名暗示多职责：含 `and / or / with_xxx_and_yyy` 模式 |
| **A1-S2** | doc comment 同时描述 ≥ 2 个不相关领域（"既...又..."） |
| **A1-S3** | trait 方法分布在多个生命周期阶段，而不是聚焦单一阶段 |

#### A2 正交性

| Signal | 形态 |
|---|---|
| **A2-S1** | cross-import 强依赖：改 module A 必然联动修改 module B 的 import 链 |
| **A2-S2** | 字段交叉引用：一个 struct 字段直接引用另一个 crate 的具体类型，而不是抽象 trait 边界 |

#### A3 数据唯一

| Signal | 形态 |
|---|---|
| **A3-S1** | 同名字段跨 crate 重叠 |
| **A3-S2** | serde alias 兼容字段（暗示历史演化有重复） |
| **A3-S3** | 同名 struct 跨 crate 各占一席 |
| **A3-S4** | 同义不同形的并行类型：相关概念但形状不同 |

#### A4 处理集中

| Signal | 形态 |
|---|---|
| **A4-S1** | filter 动词散落 |
| **A4-S2** | validate 动词散落 |
| **A4-S3** | permission 动词散落：声明层与 runtime 层各占 |
| **A4-S4** | convert 动词散落 |

### §4.3 病灶登记 schema

仅当 §3.3 中"信号是否构成病灶"判 Y 时才登记：

```
病灶 ID       : <首次登记顺序编号，如 A3-001>
信条           : A1 / A2 / A3 / A4
Signal         : <signal ID，如 A3-S3>
触发情景       : §2 <情景 #>
file:line      : <具体观察点>
命中形态       : <该信号在此处的具体表现>
影响面         : <该信号在此处的可观察影响范围>
```

**病灶不是预设的**，是探查跑出来的。每个 task 跑完，登记真实命中的项。

### §4.4 探查回归

每次 framework 代码变更：
- 重跑已完成的 task，确认 §3.3 输出无变化
- 新 task 跑出新命中，确认新病灶 ID 与现有 ID 不重复

---

## §5 验证计划

### §5.1 探查回归
- 每个 task 跑完，file:line 锚点必须在当前 HEAD 上可用 `Read` / `grep -n` 复现
- 任何 framework 代码变更触发已有 task 的重新跑（不能仅以记忆为据）

### §5.2 spec 落地边界
- 本 spec 是方法论手册，**不预设任何探查结论**
- 每个 task 输出物独立 commit，含 ≥ 1 个 (capability, 情景) 单元的真实探查
- 后续 phase（fix / 重构 / docs 重建）引用 task 输出物为输入

### §5.3 docs 关系
- 本 spec 不进 docs/api/
- docs/api/ 重构由**专门 phase** 承接
- task 输出物不进 docs/api/

---

## 附：spec 与后续 phase 的接口契约

本 spec 只交付 4 种方法论，不预设探查结论。

后续 phase 引用本 spec 的方式：
1. **方法论 1 / 2 / 3**：按本 spec 跑出真实 (capability, 情景) 单元判定
2. **方法论 4**：按本 spec 的信条与 signals 跑出真实病灶登记
3. **后续 fix phase**：引用 task 输出物中的"信号构成病灶 Y" 项作为 fix 入口

待 phase 9 任一 task 完成后，才能从中归纳综合报告。
