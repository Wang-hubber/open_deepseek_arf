# Agent 抽象能力矩阵与抽象质量审计

> Phase 9 spec — 探查报告形态。本 spec **只暴露**问题，**不**给重构方案；fix 决策留给后续 phase。

## §0 目标

本 spec 同时承担两个目的：

- **目的 A — 能力矩阵**：盘点 Agent 在运行时应具备的抽象能力，覆盖 framework 是否能直接供 / 组合可达 / 扩展可达 / 失败。
- **目的 B — 抽象质量审计**：在能力合格的前提下，对 framework 抽象本身做四信条 audit（原子化 / 正交 / 数据唯一 / 处理集中），暴露"绕远路"病灶。

两条判定原则独立打分：能力合格但抽象脏 ⇒ 标记为 `PASS-AUDIT_FAIL`；能力缺失 ⇒ `FAIL`。

判定规则详见 §1。能力场景覆盖详见 §2。审计 find signals 详见 §3。已暴露病灶清单详见 §4。

---

## §1 判定原则

### §1.1 能力合格判定

对每个 capability X（一个可观测的 agent 动作），按以下顺序命中即合格，越浅越好：

| 等级 | 含义 | 判定细则 |
|---|---|---|
| **D — Direct** | framework 端到端供 | EngineBuilder / McpNode / SessionStore / Compactor 等的公开 API 直接实现 X；app 不写非业务代码即可跑 |
| **C — Composable** | 组合可达 | framework 已有 2-5 个 primitive，app 用 ≤ 50 行 glue 拼出 X（典型场景：CheckpointRule + 自定义 build closure 实现新副作用） |
| **E — Extensible** | 扩展可达 | framework 已声明的 trait/ext 抽象允许 app 通过 impl trait 实现 X。可用扩展点限于：`Node` / `ActionMessage` / `Tool` / `DiscoveryBackend` / `RuntimeModule` / `Summarizer` / `SessionStore` / `MessageHandler` / `ResponseProcessor` / `OnMemberFailedHandler` / `CheckpointRule` 构造 |
| **F — FAIL** | 缺 primitive + 缺扩展点 | framework 必须加 primitive 或 trait 才能让 X 落地 |

**判分按 D → C → E → F 顺序匹配**，首个命中即定格。

### §1.2 抽象质量判定（四信条）

只对状态为 D / C / E 的 capability 做 audit。每条信条独立评 P / F，**不强制平均**——A3 触雷往往致命，A4 失败可能容忍。

#### 信条 A1 — 原子化
一个抽象做且只做一件事。

#### 信条 A2 — 正交
抽象之间相互独立，可自由组合。

#### 信条 A3 — 数据唯一
一份事实只在一处声明。

#### 信条 A4 — 处理集中
同类转换在单一接缝完成。

### §1.3 网格表达

每个 capability 在矩阵中的格记录：

```
能力 X        =  D / C / E / F
抽象质量 audit =  A1: P/F   A2: P/F   A3: P/F   A4: P/F
备注          =  具体病灶 / 证据
```

### §1.4 探查顺序

1. 写出该情景的最小代码路径（app 层 ≤ 80 行）
2. 沿路径标记每个 framework 接触点（按 type + method）
3. 对每个接触点回 framework 代码，记录真实行为
4. 判定 X 的能力等级（D / C / E / F）
5. 若 D / C / E，逐信条 audit
6. 记录具体病灶：file:line 与代码片段

---

## §2 能力矩阵

### §2.1 情景 0 — 框架空载（baseline）

**描述**：仅 Bus 跑起来，无 Engine / 无 Node；用于确认总线本身闭合。

| 能力 | 判定 | 抽象 audit |
|---|---|---|
| bus_health_observe（`bus.graph()` 拿在线节点 + 消息数 + uptime） | D | A1 P / A2 P / A3 P / A4 P |
| barrier_sync（`bus.barrier()` 协调多参与者） | D | A1 P / A2 P / A3 P / A4 P |
| multi_bus_attach（`NodeHandle.attach_to()` 跨多 bus） | D | A1 P / A2 P / A3 P / A4 P |
| heartbeat（`heartbeat_request` 自 ack + 超时清理） | D | A1 P / A2 P / A3 P / A4 P |
| node_online_announcement（`NodeInfo` 上线广播） | D | A1 P / A2 P / A3 P / A4 P |

### §2.2 情景 1 — 单 agent 无 tool

**描述**：`EngineBuilder::new(bus).build(AgentConfig)`，无 `ResourceSpec`。

| 能力 | 判定 | 抽象 audit |
|---|---|---|
| chat（agent ↔ model 对话，输出文本） | D | A1 P / A2 P / A3 P / A4 P |
| streaming_response（`ModelResponseChunk` 流式增量） | D | A1 P / A2 P / A3 P / A4 P |
| thinking_visible（reasoning_content 透传） | D | A1 P / A2 P / A3 P / A4 P |
| interrupt（CancellationToken 取消当前 turn） | D | A1 P / A2 P / A3 P / A4 P |
| checkpoint_rules（5 个 Checkpoint 位置注入副作用） | D | A1 P / A2 P / A3 P / A4 P |
| bus_health_observe | D | 同 §2.1 |
| model_switch（多 model 候选自动选择） | **待核** | **A3 ？: V1 `AgentConfig.models: Vec<ModelDecl>` 是否真被 Engine 消费？V2 `AgentConfig.model = single` 含义如何？** |
| session_persist | E（需 app 配 `SqliteSessionStore`） | **A4 待核: persist 时机在 5 Checkpoint 触发，是否与 SessionStore 唯一入口语义一致** |
| session_recover | E | 同上 |

### §2.3 情景 2 — 单 agent + 单 MCP（本地）

**描述**：在 §2.2 之上加 `McpNode::local(ns, root).connect(bus).await`，并配 `ResourceSpec{node_type="mcp"}`。

| 能力 | 判定 | 抽象 audit |
|---|---|---|
| tool_use（让 LLM 调用已注册工具） | D | A1 P / A2 P / **A3 F: ToolSpec 双类型，见 §4.2-病灶 A3-001** / A4 P |
| tool_discovery（`FsDiscovery.list_tools()` 扫 filesystem） | D | 同上 |
| tool_permission（`ToolPermission::{Allow, Ask, Deny}`） | D | **A4 待核: declaration 在 arf-agent，runtime validation 落在哪里？散度待核** |
| skill_list_progressive（只列名+描述，不加载 body） | D | **A3 待核: 与静态 tool 是否同表？** |
| skill_load_on_demand（`use_skill` 协议） | D | **A3 待核: use_skill 是 ActionMessage 还是 McpNode 内部 msg？** |
| skill_tool_progressive（skill body 暴露的 tool 临时注册） | D | **A3 F 与 A4 F 见 §4.2-病灶 A3-003** |
| skill_resource_load（`load_skill_resource` + `LoadedResource`） | D | A1 P / A2 P / A3 P / A4 P |
| mcp_local_runtime（`LocalRuntime` + `ScriptTool`） | D | A1 P / A2 P / A3 P / A4 P |
| route_resolution（Strict → `"mcp/{ns}"`） | D | A1 P / A2 P / A3 P / A4 P |

### §2.4 情景 2-插 — 单 MCP skill → tool 渐进加载专项

**描述**：MCP **不**把 tool 静态注入 system_prompt；只暴露 skill 名字+描述。LLM 在对话中触发 `use_skill` 后，MCP 拉 skill body，body 中声明的 tool 临时加入可调用池；多次 load 幂等、可 cache。

| 能力 | 判定 | 抽象 audit |
|---|---|---|
| skill_list_progressive | D | A1 P / A2 P / A3 P / A4 P |
| skill_load_on_demand | D | **A3 F 见 §4.2-病灶 A3-002** |
| skill_tool_progressive_register | D | **A3 F: 静态 tool 与 skill tool 是否合并表？见 §4.2-病灶 A3-003** / **A4 F: 命名空间 dedup 集中度？** |
| skill_resource_load | D | A1 P / A2 P / A3 P / A4 P |

### §2.5 情景 3 — 单 agent + 多个独立 MCP（route 表多选）

**描述**：≥ 2 个 namespace 的 `McpNode` 接入同一 bus；`AgentConfig.resources` 含 ≥ 2 `ResourceSpec` 指 mcp。

| 能力 | 判定 | 抽象 audit |
|---|---|---|
| route_resolution（Strict to multiple NodeIds） | D | A1 P / A2 P / A3 P / A4 P |
| tool_discovery（跨 MCP dedup） | D | **A3 待核: 同名 tool 在不同 namespace 时 `BuildError::AmbiguousTool` 行为边界** |
| tool_use（cross-mcp dispatch） | D | A4 P |

### §2.6 情景 4 — Discovery 路由（capability 模糊匹配）

**描述**：用 `Route::Discovery(Capability)` 替代 §2.5 的 Strict 路由；Engine 按 `NodeInfo.capabilities` 模糊匹配。

| 能力 | 判定 | 抽象 audit |
|---|---|---|
| route_resolution（Discovery） | D | A1 P / A2 P / A3 P / A4 P |
| tool_discovery（capability-based subset） | D | **A3 待核: `ResourceSpec.capabilities` 与 `NodeInfo.capabilities` 数据来源唯一性** |

### §2.7 情景 5 — 多 agent + 共享 MCP pool

**描述**：多个 Engine 共享同一 `MCPPoolNode`（top ↔ sub bus facade）。

| 能力 | 判定 | 抽象 audit |
|---|---|---|
| mcp_pool_overflow（`Overflow::{Queue, Reject, Block}`） | D | A1 P / A2 P / A3 P / A4 P |
| tool_use（跨 agent） | D | 同 §2.3 |
| a2a_peer（port 用） | C / E | 见 §2.8 |

### §2.8 情景 6 — 多 agent + A2A peer

**描述**：多 Engine 经 Bus 用 `PeerMessage` / `PeerReply` 对等通信（Phase 8 F1 已加）。

| 能力 | 判定 | 抽象 audit |
|---|---|---|
| a2a_peer | D | A1 P / A2 P / A3 P / A4 P |
| route_resolution（peer） | D | A1 P / A2 P / A3 P / A4 P |

### §2.9 情景 7 — 单 agent + subagent 委派

**描述**：Engine 通过 `SubagentDelegate` / `SubagentResult` 嵌套委派子 agent（Phase 8 F1 已加）。

| 能力 | 判定 | 抽象 audit |
|---|---|---|
| subagent_delegate | D | A1 P / A2 P / A3 P / A4 P |
| engine_nesting（嵌套 Engine 生命周期） | D | **A4 待核: 嵌套 Engine 的 shutdown 顺序与 state 隔离边界** |
| route_resolution（subagent） | D | A1 P / A2 P / A3 P / A4 P |

### §2.10 情景 8 — 长会话持久化

**描述**：`EngineBuilder::with_session_store(SqliteSessionStore).with_session_id(...)`，5 个 Checkpoint 都触发 snapshot。

| 能力 | 判定 | 抽象 audit |
|---|---|---|
| session_persist | D | **A4 P 但 trigger 散度待核: 5 Checkpoint 各调一次 snapshot_if_configured，与 SessionStore 单一入口的关系** |
| session_recover | D | A1 P / A2 P / A3 P / A4 P |
| member_failed_handling（`OnMemberFailedHandler`） | D | A1 P / A2 P / A3 P / A4 P |

### §2.11 情景 9 — 长会话压缩

**描述**：`Compactor` + `Summarizer` + `when_context_over(0.7, 4)` 工厂产 `CheckpointRule`，自动触发。

| 能力 | 判定 | 抽象 audit |
|---|---|---|
| context_compact | D | A1 P / A2 P / A3 P / A4 P |
| custom_summarizer | E（impl `Summarizer` trait） | A1 P / A2 P / A3 P / A4 P |
| checkpoint_rules（自动 inject 规则） | D | 同 §2.2 |

### §2.12 情景 10 — 中途 interrupt + 恢复

**描述**：CancellationToken 触发 + SessionStore 检查点 + replay。

| 能力 | 判定 | 抽象 audit |
|---|---|---|
| interrupt（cancel + restore from latest checkpoint） | D | **A4 待核: 中断-恢复协议是否跨 Bus 一致？跨 crate 行为是否一致？** |
| session_recover | D | 同 §2.10 |

### §2.13 情景 11 — 流式响应

**描述**：`ModelResponseChunk`（chunk_type ∈ `{text, reasoning, tool_call, usage}`）+ `ModelToolCallDelta` 透传 LLM 流。

| 能力 | 判定 | 抽象 audit |
|---|---|---|
| streaming_response | D | A1 P / A2 P / A3 P / A4 P |
| custom_handler（处理 `model_response_chunk`） | E（impl `MessageHandler`） | A1 P / A2 P / A3 P / A4 P |

### §2.14 情景 12 — 模型自动发现

**描述**：用 `ModelAdapterPoolNode` 或多 `ModelAdapter` 节点，让 Engine 按 `Provider::supported_models` 路由。

| 能力 | 判定 | 抽象 audit |
|---|---|---|
| model_discovery（按 provider / model_name） | D | A1 P / A2 P / A3 P / A4 P |
| model_switch（多 model 候选） | D | 同 §2.2 |
| pool_overflow（model side，`Overflow::{Queue, Reject, Block}`） | D | A1 P / A2 P / A3 P / A4 P |

### §2.15 情景覆盖总览

| # | 情景 | 涉及组件 | 关键能力 |
|---|---|---|---|
| 0 | 框架空载 | Bus | 健康 / barrier / multi-bus / heartbeat |
| 1 | 单 agent 无 tool | Bus + EngineBuilder + Engine + ModelAdapter | chat / stream / thinking / interrupt / checkpoint / model_switch(?) |
| 2 | 单 agent + 单 MCP（本地） | + McpNode / FsDiscovery / LocalRuntime / ScriptTool | tool_use / skill_* / permission / route |
| 2-插 | skill→tool 渐进 | + SkillIndex / use_skill | skill_load / skill_tool_progressive |
| 3 | 多 MCP 静态 route | + AgentConfig.resources 多 spec | route 多选 / cross-mcp dedup |
| 4 | Discovery 路由 | + Route::Discovery / Capability | capability 模糊匹配 |
| 5 | 多 agent + MCP pool | + MCPPoolNode / McpResource / Pool | pool_overflow / 跨 agent tool |
| 6 | A2A peer | + PeerMessage / PeerReply | a2a / peer route |
| 7 | subagent 委派 | + SubagentDelegate / SubagentResult | subagent_delegate / engine_nesting |
| 8 | 持久化 | + EngineBuilder.with_session_store / SqliteSessionStore | session_persist / member_failed |
| 9 | 压缩 | + Compactor / Summarizer / when_context_over | context_compact / custom_summarizer |
| 10 | interrupt + 恢复 | + CancellationToken + replay | interrupt / recover |
| 11 | 流式响应 | + ModelResponseChunk / chunk_type | streaming / custom_handler |
| 12 | 模型自动发现 | + ModelAdapterPoolNode / Provider::supported_models | model_discovery / pool_overflow |

---

## §3 抽象质量 audit

### §3.1 探查流程

对 §2 中每个 D / C / E 的能力，按以下 4 步跑：

1. **静态扫描**（grep 为主） → 抓 A3 / A4
2. **签名审查**（读 trait/struct） → 抓 A1 / A2
3. **回归验证**（cargo test） → 静态不能消歧的项
4. **判读定性** → 综合出 P / F，每信条独立评

### §3.2 四信条 find signals

每个 signal 是一个**可观察的具体形态**（代码层肉眼可验证）。spec 探查阶段按 signal 找证据。

#### A1 原子化（一个抽象做且只做一件事）

| Signal | 形态 |
|---|---|
| **A1-S1** | trait / struct 的方法名暗示多职责：含 `and / or / with_xxx_and_yyy` 模式 |
| **A1-S2** | doc comment 同时描述 ≥ 2 个不相关领域（"既...又..."） |
| **A1-S3** | trait 方法分布在多个生命周期阶段（init / run / persist / shutdown 各占一些），而不是聚焦单一阶段 |

#### A2 正交性（抽象可独立替换）

| Signal | 形态 |
|---|---|
| **A2-S1** | cross-import 强依赖：改 module A 必然联动修改 module B 的 import 链 |
| **A2-S2** | 字段交叉引用：一个 struct 字段直接引用另一个 crate 的具体类型，而不是抽象 trait 边界 |

#### A3 数据唯一（一份事实只在一处声明）

| Signal | 形态 |
|---|---|
| **A3-S1** | 同名字段跨 crate 重叠：同一字段名在 ≥ 2 个 crate 各自 `pub struct { pub xxx }` |
| **A3-S2** | serde alias 兼容字段：出现 `#[serde(alias = "...")]`（暗示历史演化有重复） |
| **A3-S3** | 同名 struct 跨 crate：例如 `State` / `Task` / `SessionStore` / `ToolSpec` 在多个 crate 各占一席 |
| **A3-S4** | 同义不同形的并行类型：例如 ToolSpec core vs agent 表达相关概念但形状不同 |

#### A4 处理集中（同类转换在单一接缝完成）

| Signal | 形态 |
|---|---|
| **A4-S1** | filter 动词散落：`filter` / `MessageFilter::matches` / `fn filter_xxx` 在 ≥ 2 处独立实现 |
| **A4-S2** | validate 动词散落：`validate(` / `ConfigError` 在 ≥ 2 处独立入口 |
| **A4-S3** | permission 动词散落：`ToolPermission` / `Permission::` 在声明层和 runtime 层各占 |
| **A4-S4** | convert 动词散落：`impl From` / `convert.rs` 在 ≥ 2 处独立实现同义转换 |

每个 signal 在探查时需**至少一个 file:line 实例作锚点**。

### §3.3 病灶登记表 schema

```
ID           : A3-001
信条         : A3
Signal       : A3-S3
触发情景     : §2.3 / §2.4（单 agent + 单 MCP 本地 / skill 渐进）
file:line    : crates/arf-core/src/tool.rs、crates/arf-agent/src/tool.rs
重复事实     : name + description + parameters 字段在两处 struct 重叠
判定         : F（违反 A3）
影响面       : 所有 Tool 注册路径；Engine 实际用哪一份待核
证据         : 两 struct `pub struct ToolSpec` 各占；core 一份 agent 一份
```

不含建议修法、紧迫度字段（fix 决策留给后续 phase spec）。

### §3.4 探查顺序

1. **先探 §2 fail 项**：覆盖能力缺失，作为 §4.1 矩阵 fail 清单
2. **再按 §3.2 find signals 跑**：对 D / C / E 单元逐信条 grep，记录命中位置
3. **最后整理 §4.3 跨情景同源**：把跨 ≥ 2 情景命中的同一条 signal 抽出来

---

## §4 病灶报告（仅事实，不含 fix 决策）

### §4.1 矩阵失败的能力清单（§2 中待核或预判 F 的能力）

| 情景 | 能力 | 状态 | 备注 |
|---|---|---|---|
| §2.2 | model_switch | 待核 | V1 `models: Vec<ModelDecl>` 是否真被 EngineBuilder 消费？ |
| §2.2 | session_persist / recover | E，但 A4 待核 | persist 触发散度 vs SessionStore 入口一致性 |
| §2.3 | tool_use / tool_discovery | 已判 F | A3-S4 命中 ToolSpec 双类型（§4.2-病灶 A3-001） |
| §2.3 | tool_permission | 已判 F | A4-S3 命中 permission 接缝不清（§4.2-病灶 A4-001） |
| §2.4 | skill_load_on_demand | 已判 F | A3-S1 命中 `use_skill` 协议归属（§4.2-病灶 A3-002） |
| §2.4 | skill_tool_progressive | 已判 F | A3-S4 + A4-S4（§4.2-病灶 A3-003） |
| §2.5 | tool_discovery 跨 MCP dedup | 待核 | AmbiguousTool 行为边界 |
| §2.6 | tool_discovery capability-based | 待核 | ResourceSpec.capabilities 与 NodeInfo.capabilities 数据唯一性 |
| §2.9 | engine_nesting | 待核 | 嵌套 Engine shutdown 顺序与 state 隔离 |
| §2.10 | session_persist | A4 待核 | 5 Checkpoint 触发散度 vs SessionStore 单一入口 |
| §2.12 | interrupt | A4 待核 | 中断-恢复跨 Bus / 跨 crate 行为一致性 |

### §4.2 抽象病灶清单（按信条分组）

按 §3.3 schema 登记。本节条目均为**预判**，探查时须用 file:line 锚点核实。

#### A1 原子化

> 探查阶段未发现 A1 三 signal（A1-S1 / S2 / S3）的明确命中点。预判 + 待核：当 AgentConfig V2 engine 字段与 EngineConfig 内嵌特性出现"职责混合"信号时需复核。

#### A2 正交性

> 探查阶段未发现 A2 两 signal（A2-S1 / S2）的明确命中点。预判 + 待核：MCPPoolNode 与 McpNode 表面存在两套 advert 模式（见 §2.7），若实际逻辑依赖 App 列举则触 A2-S2。

#### A3 数据唯一

**病灶 A3-001 — ToolSpec 双类型**
```
ID           : A3-001
信条         : A3
Signal       : A3-S3 / A3-S4
触发情景     : §2.3 / §2.4
file:line    : crates/arf-core/src/tool.rs、crates/arf-agent/src/tool.rs
重复事实     : name + description + parameters 三字段在两处 struct 重叠；
              agent::ToolSpec 多 3 字段（permission / parameter_filter / parameters 详细 schema）
判定         : F（违反 A3-S3 / A3-S4）
影响面       : 所有 Tool 注册路径；Engine 实际用 core::ToolSpec 还是 agent::ToolSpec 待核
证据         : 两 struct `pub struct ToolSpec` 各占；core 一份 agent 一份；都没有 alias
```

**病灶 A3-002 — `use_skill` 协议归属不明**
```
ID           : A3-002
信条         : A3
Signal       : A3-S1
触发情景     : §2.4
file:line    : crates/arf-mcp/src/skill.rs、crates/arf-mcp/src/node.rs (message_loop)
重复事实     : use_skill 协议至少需要一处 impl 决定走 Bus ActionMessage 还是 McpNode 内部
判定         : F（违反 A3-S1：协议层单一归属不清）
影响面       : skill 加载与 skill body 暴露的 tool 入 Engine 的 path 边界
证据         : McpNode 入向消息 5 类其中 use_skill 待核其归属
```

**病灶 A3-003 — 静态 tool 与 skill tool 表合并策略不明**
```
ID           : A3-003
信条         : A3
Signal       : A3-S4 + A4-S4
触发情景     : §2.4
file:line    : crates/arf-mcp/src/discovery.rs (tool_map), crates/arf-mcp/src/skill.rs (SkillIndex)
重复事实     : 静态 tool 在 DiscoveryBackend.tool_map；skill tool 在 SkillIndex 各自维护
判定         : F（违反 A3-S4：同义不同形的并行类型；违反 A4-S4：convert 散落）
影响面       : Engine 路由 tool 时跨两个表合并时机；app 不易预测 tool 命名空间
证据         : McpNode 同时持有 DiscoveryBackend 与 SkillIndex 两套结构；运行时合并行为待核
```

**病灶 A3-004 — AgentConfig V1 / V2 并存**
```
ID           : A3-004
信条         : A3
Signal       : A3-S3 / A3-S4 / A3-S1
触发情景     : §2.2
file:line    : crates/arf-agent/src/config.rs:24 (V1)，crates/arf-engine/src/config.rs:7 (V2)
重复事实     : 
  - model / system_prompt 字段跨 crate 重叠（A3-S1）
  - 同名 struct AgentConfig 在两 crate 各占一席（A3-S3）
  - V1.models: Vec vs V2.model single, V1.subagents/teammates vs V2.resources 同义不同形（A3-S4）
判定         : F（违反 A3 三 signal）
影响面       : 全部 AgentConfig 用户；py-arf 入口；用户配置不知用哪边
证据         : V1 不被 EngineBuilder 消费；V2 才是实际装配入口
```

**病灶 A3-005 — `State` 双 crate 平行**
```
ID           : A3-005
信条         : A3
Signal       : A3-S3
触发情景     : §2.10 / §2.11 / §2.12
file:line    : crates/arf-state/src/lib.rs (302 行, 平行), crates/arf-core/src/state.rs:42 (实际用)
重复事实     : State { messages, tasks } 在 arf-state 与 arf-core 各一个 pub struct；
              arf-state 还含独立 Task struct 含 blocked_by + blocking 双向锁
判定         : F（违反 A3-S3）
影响面       : crates 下 grep 'use arf_state' 零命中 → arf-state::State 是 dead feature
证据         : e2e 测试统一用 arf_core::State::new()；arf-state 唯一价值是承载 A2A Task 双向锁设计意图
```

**病灶 A3-006 — ResourceSpec.resource_name 兼容 alias**
```
ID           : A3-006
信条         : A3
Signal       : A3-S2
触发情景     : §2.3 - §2.7
file:line    : crates/arf-agent/src/resource.rs
重复事实     : `resource_name` 字段带 `serde(alias = "name")` 兼容旧配置
判定         : F（违反 A3-S2：alias 暗示历史演化有重复）
影响面       : 配置 schema 历史包袱，新配置应统一为 resource_name
证据         : alias 字段的存在直接表征数据多源
```

#### A4 处理集中

**病灶 A4-001 — ToolPermission 接缝不清**
```
ID           : A4-001
信条         : A4
Signal       : A4-S3
触发情景     : §2.3
file:line    : crates/arf-agent/src/tool.rs (declaration)，runtime validation 位置待核
重复事实     : permission 三态在 declaration 层；runtime 校验 entry 待核（可能在 Engine 也可能在 McpNode）
判定         : F（违反 A4-S3：permission 动词散落）
影响面       : tool_permission 行为一致性；用户对 Ask 行为的预期
证据         : declaration 在 agent::ToolSpec；runtime 校验进 EngineConfig.processors 还是 Tool 处理？待核
```

**观察 OBS-001 — snapshot 触发散度（非病灶，记录备查）**
```
ID           : OBS-001（不是病灶 ID，仅作观察项）
信条         : A4 倾向但未达 F
Signal       : (非标 signal — 触发散落观察)
触发情景     : §2.10
file:line    : crates/arf-engine/src/engine.rs: snapshot_if_configured 在 5 个 Checkpoint 各调一次
现状         : 5 个 Checkpoint 各自 invoke snapshot_if_configured()；入口集中（SessionStore::snapshot 单一签名），触发分散（业务侧）
初步判定     : 不属病灶 — 数据入口唯一（SessionStore::snapshot），触发分布是合理的层级展开；但应验证 5 处 snapshot 字段一致
影响面       : snapshot 语义一致性 — 各 checkpoint snapshot 的 CheckpointSnapshot 字段含义需核
证据         : 入口集中但每次 checkpoint 传的 CheckpointSnapshot 字段含义需验证 5 处一致
```

### §4.3 跨情景同源病灶

| Signal | 命中 ≥ 2 情景的病灶 ID | 触雷位置 |
|---|---|---|
| **A3-S1** （同名字段跨 crate） | A3-001, A3-004 | ToolSpec / AgentConfig |
| **A3-S3** （同名 struct 跨 crate） | A3-001, A3-004, A3-005 | ToolSpec / AgentConfig / State |
| **A3-S4** （同义不同形） | A3-001, A3-003, A3-004 | ToolSpec 双类型 / skill-tool 双表 / AgentConfig V1V2 |

**解读**：A3-S1 / A3-S3 / A3-S4 在 framework 内反复暴露，**不是孤例**。这些 signal 是 framework 当前最显著的"绕远路"信号。

### §4.4 全文证据索引（file:line 反查表）

| Signal | 当前已知命中位置 |
|---|---|
| A1-S1 | 探查未发现明确命中 |
| A1-S2 | 探查未发现明确命中 |
| A1-S3 | 探查未发现明确命中 |
| A2-S1 | 探查未发现明确命中 |
| A2-S2 | 探查阶段未确认，待核 |
| **A3-S1** | `crates/arf-agent/src/config.rs:24`, `crates/arf-engine/src/config.rs:7` (model/system_prompt 跨 crate) |
| **A3-S2** | `crates/arf-agent/src/resource.rs` (resource_name alias="name") |
| **A3-S3** | `crates/arf-core/src/tool.rs`, `crates/arf-agent/src/tool.rs` (ToolSpec)；`crates/arf-agent/src/config.rs:24`, `crates/arf-engine/src/config.rs:7` (AgentConfig)；`crates/arf-state/src/lib.rs`, `crates/arf-core/src/state.rs:42` (State) |
| **A3-S4** | core::ToolSpec vs agent::ToolSpec；DiscoveryBackend.tool_map vs SkillIndex (skill tool)；AgentConfig V1 models vs V2 model |
| A4-S1 | 探查未发现明确命中（MessageFilter::matches 仅 arf-core::filter.rs 一处） |
| A4-S2 | 探查未发现明确命中（validate() 仅 arf-agent::config.rs::validate 一处） |
| **A4-S3** | `crates/arf-agent/src/tool.rs` (ToolPermission declaration)；runtime 校验位置待核 |
| A4-S4 | 探查阶段未确认，待核（`crates/arf-model-adapter/src/convert.rs` 独立性未核） |

---

## §5 验证计划

### §5.1 探查回归
- 每次 framework 代码变更，重新跑 §2 矩阵判断 + §3 信号 grep
- §4 病灶清单不应**新增**
- §4 已记录病灶的 file:line 锚点必须仍能 verify

### §5.2 spec 落地边界
- §4 病灶条目**只**含 §3.3 schema 字段，不含 fix 决策段
- 后续 phase spec（含 phase 9 实施 spec、phase 10 及以后的抽象收敛 spec）才能开 fix 入口
- 本 spec 不写 docs/api/ 章节

### §5.3 docs 关系
- 本 spec 不进 docs/api/
- docs/api/ 重构由**专门 phase**承接，避免与本 spec 混在一处
- 后续 phase spec 引用本 spec 的 §4 病灶作为输入，但不复制条目

---

## 附：spec 与后续 phase 的接口契约

本 spec 只交付"探查报告"，不含 fix 决策。后续 phase 在引用本 spec 时，按以下契约：

1. **本 spec 是病灶的"信源"**：后续 phase 重构时引用 §4 病灶 ID 为唯一定位
2. **本 spec 不含 fix 策略**：fix 决策在后续 phase 的实施 spec 里产生
3. **本 spec 不动 docs/api/**：docs 重建由专门 phase 承接
4. **本 spec 是只读快照**：framework 代码变更后，§4 病灶需重新验证（§5.1 探查回归）

待 Phase 9 实际启动时，再创建 `phase9-implementation-spec.md`，从本 spec §4 病灶出发编排任务。
