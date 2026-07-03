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

具体 task 编号 / 顺序见 §6 phase 9 task 列表（55 个）。spec 与探查输出**不体现时间花费**——探查耗费随执行现场决定，不入仓。

---

## §1 能力矩阵（方法论 1）

### §1.1 Master capability 列表

按 agent lifecycle 与资源形态分 6 类：

**L1 — 基础对话**
- chat
- streaming_response
- thinking_enabled

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
- 每个情景默认还包含 L1（chat / streaming_response / thinking_enabled）、L7（bus_health_observe / heartbeat 等总线侧）、interrupt 等基础能力
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

**输出不含时间字段**（不写实际跑多久、task 估时等）。

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

**病灶汇总**：每个 Y 病灶除在本 task 的 `audit-probe-9.X.Y.md` §D 首次现场登记外，须**追加**到 phase 9 病灶中心登记册 `lesion-registry.md`（总表 + 详情 + `OPEN/FIXED/WONTFIX` 状态跟踪）。该登记册是后续 fix phase 的**唯一病灶输入源**。

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
- spec 与所有探查输出**不含时间字段**（不写 task 估时 / 实操耗时 / 总估时等）

### §5.3 docs 关系
- 本 spec 不进 docs/api/
- docs/api/ 重构由**专门 phase** 承接
- task 输出物不进 docs/api/

---

## §6 Phase 9 task 列表（55 task）

按"依赖深度递推"组织，每类按大类 → 子类编号（9.X.Y）。**task 表格不列时间字段**——时间投入由执行者自行决定，不入仓。

### 9.1 — A 总线基线（5 task）

| Task | 探查 | 依赖 |
|---|---|---|
| 9.1.1 | Bus + 单一 Node + heartbeat | 无 |
| 9.1.2 | Bus + 多 Node（异构 node_type） | 9.1.1 |
| 9.1.3 | Bus + multi-bus 拓扑（NodeHandle.attach_to） | 9.1.1 |
| 9.1.4 | Bus + barrier 多参与者 | 9.1.1 |
| 9.1.5 | Bus + 异常（lagged / 掉线 / 重连） | 9.1.1 |

### 9.2 — B 单 agent 骨架（5 task）

| Task | 探查 | 依赖 |
|---|---|---|
| 9.2.1 | Engine + 单 ModelAdapter | 9.1.x |
| 9.2.2 | Engine + ReAct 主循环 chat | 9.2.1 |
| 9.2.3 | Engine + 5 Checkpoint + 自定义 Rule | 9.2.1 |
| 9.2.4 | Engine + CancellationToken interrupt 协同 | 9.2.1 |
| 9.2.5 | Engine + 多 ModelAdapter 候选切换 | 9.2.1 |

### 9.3 — J 流式响应（3 task）

| Task | 探查 | 依赖 |
|---|---|---|
| 9.3.1 | ModelResponseChunk 文本流（chunk_type=text） | 9.2.1 |
| 9.3.2 | ModelResponseChunk reasoning 流（chunk_type=reasoning + thinking_enabled） | 9.3.1 |
| 9.3.3 | 自定义 MessageHandler 处理 chunk | 9.3.1 |

### 9.4 — K 模型发现（3 task）

| Task | 探查 | 依赖 |
|---|---|---|
| 9.4.1 | ModelAdapterPoolNode facade（sub-bus 网关） | 9.2.5 |
| 9.4.2 | Provider::supported_models capability-based 路由 | 9.4.1 |
| 9.4.3 | Pool overflow 三策略（Queue / Reject / Block） | 9.4.1 |

### 9.5 — C 工具集成 / McpNode（7 task）

| Task | 探查 | 依赖 |
|---|---|---|
| 9.5.1 | McpNode + FsDiscovery | 9.4.x |
| 9.5.2 | McpNode + HttpDiscovery（JSON-RPC initialize + tools/list） | 9.5.1 |
| 9.5.3 | McpNode + 自定义 DiscoveryBackend | 9.5.1 |
| 9.5.4 | McpNode + LocalRuntime | 9.5.1 |
| 9.5.5 | McpNode + RemoteRuntime | 9.5.1 |
| 9.5.6 | McpNode + 自定义 RuntimeModule（sandbox） | 9.5.1 |
| 9.5.7 | McpNode + ScriptTool（python/bash/rustc）+ cancel | 9.5.4 |

### 9.6 — D Skill 渐进加载（5 task）

| Task | 探查 | 依赖 |
|---|---|---|
| 9.6.1 | skill_list_progressive（只列名+描述） | 9.5.1 |
| 9.6.2 | skill_load_on_demand（`use_skill` 协议） | 9.6.1 |
| 9.6.3 | skill_tool_progressive_register（skill body → tool） | 9.6.2 |
| 9.6.4 | skill_resource_load（`load_skill_resource` + `LoadedResource`） | 9.5.1 |
| 9.6.5 | Skill 全套联合（4 项联动） | 9.6.1 |

### 9.7 — E 多 MCP 拓扑（3 task）

| Task | 探查 | 依赖 |
|---|---|---|
| 9.7.1 | 多 MCP + Static route（Strict → multiple NodeIds） | 9.5.1 |
| 9.7.2 | 多 MCP + Discovery route（`Route::Discovery(Capability)`） | 9.7.1 |
| 9.7.3 | 多 MCP + 跨 MCP dedup（同名 tool / AmbiguousTool） | 9.5.1 |

### 9.8 — F MCP pool（7 task）— 重点细拆

| Task | 探查 | 依赖 |
|---|---|---|
| 9.8.1 | 单 agent + 单 MCP pool（facade + lease） | 9.5.x |
| 9.8.2 | 单 agent + 多 MCP pool（per-namespace） | 9.8.1 |
| 9.8.3 | 多 agent + 共享 MCP pool（1 pool） | 9.8.1 |
| 9.8.4 | 多 agent + 共享 MCP pool（2 pools） | 9.8.3 |
| 9.8.5 | 多 agent + 共享 MCP pool（多 pools） | 9.8.4 |
| 9.8.6 | pool overflow `Overflow::Queue` | 9.8.1 |
| 9.8.7 | pool overflow `Overflow::Reject` + `Block` | 9.8.1 |

### 9.9 — G Multi-agent 拓扑（7 task）

| Task | 探查 | 依赖 |
|---|---|---|
| 9.9.1 | 双 agent 独立（无连接） | 9.2.x |
| 9.9.2 | 双 agent + peer（A2A / PeerMessage + PeerReply） | 9.9.1 |
| 9.9.3 | 双 agent + subagent 委派（1 层） | 9.9.1 |
| 9.9.4 | 双 agent + subagent 嵌套（2 层） | 9.9.3 |
| 9.9.5 | 3+ agent + subagent 嵌套（3 层） | 9.9.4 |
| 9.9.6 | 3+ agent + peer 全连通 | 9.9.2 |
| 9.9.7 | 3+ agent + peer + subagent 同时 | 9.9.6 + 9.9.5 |

### 9.10 — H 持久化（5 task）

| Task | 探查 | 依赖 |
|---|---|---|
| 9.10.1 | EngineBuilder + SqliteSessionStore 端到端 | 9.2.x |
| 9.10.2 | SessionMeta / SessionData 序列化字段 | 9.10.1 |
| 9.10.3 | 5 Checkpoint 各调 `snapshot` 行为一致性 | 9.10.1 |
| 9.10.4 | 跨 session_id load / restore | 9.10.1 |
| 9.10.5 | 自定义 SessionStore impl trait | 9.10.1 |

### 9.11 — I 压缩（3 task）

| Task | 探查 | 依赖 |
|---|---|---|
| 9.11.1 | Compactor + 默认 Summarizer（LLM-backed） | 9.10.1 |
| 9.11.2 | `when_context_over` CheckpointRule 触发 | 9.11.1 |
| 9.11.3 | 自定义 Summarizer | 9.11.1 |

### 9.12 — L 扩展点实现（5 task）

| Task | 探查 | 依赖 |
|---|---|---|
| 9.12.1 | 自定义 DiscoveryBackend（实现 tool 3 方法 + 留 skill 默认） | 9.5.3 |
| 9.12.2 | 自定义 RuntimeModule（自定义 execute 策略） | 9.5.6 |
| 9.12.3 | 自定义 Tool（含 / 不含 cancel） | 9.5.x |
| 9.12.4 | 自定义 CheckpointRule（every_n_rounds / when_context_over） | 9.2.3 |
| 9.12.5 | 自定义 OnMemberFailedHandler（FailSession / SwitchTo） | 9.13.1 |

### 9.13 — M 异常与边界（4 task）

| Task | 探查 | 依赖 |
|---|---|---|
| 9.13.1 | Node 掉线（OnMemberFailedAction::FailSession） | 9.1.5 |
| 9.13.2 | Node 掉线（SwitchTo alternative） | 9.13.1 |
| 9.13.3 | Tool Permission `Ask` 路径 | 9.5.x |
| 9.13.4 | Tool Permission `Deny` 路径 | 9.5.x |

### §6.X 总览

| 项 | 数 |
|---|---|
| 总 task 数 | 55 |
| 任务依赖最浅 | 9.1.1（无依赖） |
| 任务依赖最深 | 9.9.7（依赖 9.9.6 + 9.9.5） |

每个 task 的探查细节 / 命令 / 输出 schema 见独立 task doc（`docs/v1.x/phase9/task-9.X.Y.md`）。task doc 内容依 §3 探查 4 步流程 + §3.3 输出 schema + §4.3 病灶登记 schema 写。

---

## 附：spec 与后续 phase 的接口契约

本 spec 只交付 4 种方法论，不预设探查结论。

后续 phase 引用本 spec 的方式：
1. **方法论 1 / 2 / 3**：按本 spec 跑出真实 (capability, 情景) 单元判定
2. **方法论 4**：按本 spec 的信条与 signals 跑出真实病灶登记
3. **后续 fix phase**：引用 task 输出物中的"信号构成病灶 Y" 项作为 fix 入口

待 phase 9 任一 task 完成后，才能从中归纳综合报告。
