# Phase 10 — 缺口汇总 / 推荐路线图

> **本文档**：`docs/v1.x/phase10/99-gap-summary-recommendations.md`
>
> **目的**：综合 13 个维度文档（01–13）的发现，按优先级汇总 ARFV1 vs DeepAgents 的功能缺口，输出可执行的 Phase 11+ 路线图。
>
> **方法**：每个缺口聚合自维度文档中的"Gap Analysis"段，标注溯源；推荐按 ROI 与依赖关系排序。

---

## 1. 缺口总数与严重度分布

基于 13 个维度文档、250+ 个原子能力点的对比：

| 严重度 | 缺口数 | 占比 | 含义 |
|-------|--------|------|------|
| 🔴 严重 (P0) | 12 | 5% | 阻碍生产可用性 / 是 DeepAgents 0.x 的核心卖点 |
| 🟠 重要 (P1) | 31 | 12% | 显著落后，影响用户开发体验 |
| 🟡 有用 (P2) | 28 | 11% | 有清晰借鉴路径，可选实现 |
| ✅ 持平/领先 | 96+ | 38% | ARFV1 持平或领先 |
| ⚠️ 部分 | ~80 | 32% | 各有强弱，建议对齐 |

**结论**：ARFV1 在 **多 Agent 编排 / 协议级 DI / 容错恢复 / 工具 DAG** 上显著领先；在 **Pluggable Backend / Middleware 钩子层 / LLM Eval Harness / Profile 系统 / 自评分 RubricMiddleware / 跨线程持久记忆** 上严重缺失。

---

## 2. ARFV1 领先项（DeepAgents 缺失或弱）

| 维度 | ARFV1 能力 | DeepAgents 等价 | 来源 |
|------|----------|----------------|------|
| **跨引擎联邦** | Bus 广播 + 心跳 + Barrier | 仅有 LangGraph 单 runtime | 01-§1 |
| **节点故障恢复** | `OnMemberFailedHandler` 3 策略 | 无（LangGraph 无节点概念） | 01-§6, `engine.rs:config.rs:63-74` |
| **Inbound Dedup** | `InboundDedupCache` LRU | 无 | 01-§3, `dedup.rs` |
| **Outbound 重传** | `resend_pending_outbound` + 持久事件 | 依赖 LangGraph checkpoint | 01-§4, `engine.rs:1376-1422` |
| **Barrier 协议** | `Bus::barrier` quorum | 无 | 01-§5, `bus.rs:287-361` |
| **Tool DAG** | Kahn 拓扑 `blocked_by/blocking` | 顺序执行 | 05-§12, `mcp/src/executor.rs` |
| **Protocol DI** | 10+ Trait（Node/ActionMessage/Route/...） | Class 继承 | 08-§1 |
| **强类型 Bus** | `ModelCall`/`ToolExec`/`SubagentDelegate` struct | dict | 02-§5 |
| **多语言运行时** | Rust + PyO3 Python | Python only | py-arf |
| **Team 抽象** | `Team`/`TeamBuilder`/`TeamMembership` | 无 | 01-§7, `py-arf/src/lib.rs` |
| **Checkpoint 5 变体** | 强类型 enum | LangGraph 内部 opaque | 03-§8, `core/checkpoint.rs` |
| **Cancelled 状态保留** | R7-L2 `Cancelling` 跨 snapshot | 无 | 03-§7 |
| **SnapshotEffects 契约** | 4-side-effect 显式声明 | 隐式 | 03-§9, `session/lib.rs:F-014` |
| **Heartbeat / 心跳** | `node_offline` 自动检测 | 无 | 01-§2, `bus/heartbeat.rs` |
| **Concurrent tool 批次** | `do_tool_turns_concurrent` + first-error | 顺序 | 02-§10 |
| **Tool 超时** | `tool_timeout_ms=30s` 默认 | 无默认 | 02-§8 |
| **ToolPermission 三态** | Allow/Ask/Deny | 仅 allow/deny | 11-§2 |

> **结论**：ARFV1 的核心差异化在 **runtime federation / fault tolerance / type safety**——这是 DeepAgents（LangGraph 单 runtime）永远做不到的。

---

## 3. 关键缺口汇总（按优先级）

### 🔴 P0 — 严重（生产阻塞 / 核心卖点缺失）

#### G-01: 缺失 Pluggable Backend 抽象
- **影响维度**：05 文件系统、04 上下文、13 沙箱、12 记忆
- **DeepAgents 等价**：`BackendProtocol` ABC + 7 实现（State/Filesystem/Composite/Store/Sandbox/LocalShell/ContextHub）
- **ARFV1 现状**：`McpNode` 单类型，`DiscoveryBackend` + `RuntimeModule` 仅覆盖工具发现，无统一的文件 / 记忆 / shell 后端抽象
- **修复路径**：
  1. 新建 `crates/arf-backend/` crate，定义 `BackendProtocol` trait（ls/read/write/edit/glob/grep/execute/delete）
  2. 提供 `StateBackend`、`FilesystemBackend`、`CompositeBackend`、`StoreBackend`、`LocalShellBackend` 5 个实现
  3. 在 `FilesystemMiddleware`/`SkillsMiddleware`/`MemoryMiddleware` 位置用 Backend 替换直接文件操作
- **依赖**：无（先决条件）
- **预估工作量**：大（6-8 周）
- **来源**：05-§2, 04-§6, 13-§1, 12-§7

#### G-02: 缺失 Middleware 包装层（wrap_model_call / modify_request / before_agent）
- **影响维度**：02 引擎循环、08 扩展性
- **DeepAgents 等价**：18 个 Middleware（Filesystem/SubAgent/Summarization/Skills/Memory/Rubric/PatchToolCalls/...）
- **ARFV1 现状**：`CheckpointRule` 同步管道，**无 request-mutation 钩子**（无 `wrap_model_call` / `before_agent` / `modify_request`）
- **关键差异**：
  - CheckpointRule 是**声明式钩子**（fire-and-forget），Middleware 是**包装式钩子**（可修改请求）
  - Middleware 可在 model_call 之前**改写 prompt 注入**、**改写 tool list**；CheckpointRule 只能插入消息
- **修复路径**：
  1. 在 `Engine::do_model_turn` 之前增加 `before_model_call` 钩子（`Vec<Arc<dyn ModelRequestMutator>>`）
  2. 实现 `FilesystemMiddleware`、`SkillsMiddleware`、`MemoryMiddleware` 作为 ModelRequestMutator
  3. 提供 `modify_request` 让 Middleware 改写 system_prompt / tools / messages
- **依赖**：G-01（Middleware 需要 Backend）
- **预估工作量**：大（8-10 周）
- **来源**：02-§2, 02-§3, 02-§4, 08-§1

#### G-03: 缺失 LLM-as-judge Eval Harness
- **影响维度**：07 评估
- **DeepAgents 等价**：`tests/evals/llm_judge.py` + 129 evals across 8 categories
- **ARFV1 现状**：仅有 Rust 单元测试 + Python e2e + Makefile，**无 LLM-as-judge**、**无外部 benchmark**、**无 RubricMiddleware**
- **修复路径**：
  1. 新建 `crates/arf-eval/`（或 `py-arf/eval/`）提供 `LLMJudge` 接口
  2. 移植 `test_todos` / `test_memory` / `test_skills` / `test_subagents` 等 8 个核心 eval
  3. 集成 pytest reporter（`pytest_reporter.py`）
- **依赖**：无
- **预估工作量**：中（3-4 周）
- **来源**：07-§2, 07-§3

#### G-04: 缺失 Sandbox 后端协议 + 默认安全 Backend
- **影响维度**：13 沙箱、05 文件系统
- **DeepAgents 等价**：`SandboxBackendProtocol` + `BaseSandbox` ABC + `StateBackend`（默认安全）
- **ARFV1 现状**：`LocalRuntime` 是**默认且唯一**的 Runtime，**暴露无限制 host 子进程**——存在严重安全隐患
- **修复路径**：
  1. 引入 `SandboxBackendProtocol` trait（id property + execute/aexecute）
  2. 实现 `StateBackend` 作为默认（线程隔离，无 host 访问）
  3. 引入 `BaseSandbox` ABC + `LangSmithSandbox` 适配器
  4. 撰写 `THREAT_MODEL.md`
- **依赖**：G-01（Backend 是父抽象）
- **预估工作量**：中（4-5 周）
- **来源**：13-§1, 13-§14

#### G-05: 缺失 RubricMiddleware / 自评分循环
- **影响维度**：07 评估、02 引擎循环
- **DeepAgents 等价**：`middleware/rubric.py:805` —— RubricMiddleware + GraderResponse + RubricResult
- **ARFV1 现状**：无内置自评分；质量门控需要外部实现
- **修复路径**：
  1. 在 Engine 中暴露 `RubricMiddleware` 作为 CheckpointRule
  2. 派生 grader sub-agent（用 SubagentPool）
  3. 支持 configurable rubric（per-task 评分规则）
- **依赖**：G-03（Eval 基础设施）
- **预估工作量**：中（3-4 周）
- **来源**：07-§6

### 🟠 P1 — 重要（影响开发体验）

| 编号 | 缺口 | 维度 | 依赖 |
|------|------|------|------|
| G-06 | DeltaChannel 线性 checkpoint 增长 | 03 状态 | — |
| G-07 | Provider/Harness Profile 注册系统 | 09 Provider | G-01 |
| G-08 | AsyncSubAgent / 远程 Agent Protocol | 10 Subagent | — |
| G-09 | Anthropic/Bedrock PromptCaching | 09 Provider | — |
| G-10 | per-request tool 可见性裁剪 | 02 引擎 | G-02 |
| G-11 | AGENTS.md on-demand memory loading | 12 记忆 | G-01, G-02 |
| G-12 | ToolMessage offload-to-backend | 04 上下文 | G-01 |
| G-13 | overflow tail clip | 04 上下文 | G-01 |
| G-14 | filesystem permission interrupt synthesis | 11 HITL | G-01, G-04 |
| G-15 | required-scaffolding guard | 02 引擎 | G-02 |
| G-16 | entry-point plugin system | 08 扩展 | — |
| G-17 | SKILL.md description relevance matching | 12 技能 | G-02 |
| G-18 | ModelTuning profile dataclass | 09 Provider | — |
| G-19 | system_prompt_suffix 装配 | 09 Provider | — |
| G-20 | count_tokens_once_per_model_call | 06 Trace | — |
| G-21 | remove_pending_outbound SQL 反向索引 | 03 状态 | — |
| G-22 | per-tool-call-id dedup axis | 03 状态 | G-06 |
| G-23 | state_schema 扩展点 | 03 状态 | — |
| G-24 | private_state_keys 字段 | 10 Subagent | — |
| G-25 | auto-inserted general-purpose subagent | 10 Subagent | — |
| G-26 | caller-supplied approver callable | 11 HITL | — |
| G-27 | accept/edit/reject 3-state HITL response | 11 HITL | — |
| G-28 | subagent metadata propagation | 10 Subagent | — |
| G-29 | subagent response_format | 10 Subagent | — |
| G-30 | persistent memory backend（cross-thread） | 12 记忆 | G-01 |
| G-31 | CompositeBackend 路径前缀路由 | 05 文件 | G-01 |

### 🟡 P2 — 有用（清晰借鉴路径）

| 编号 | 缺口 | 维度 |
|------|------|------|
| G-32 | PatchToolCallsMiddleware | 02 引擎 |
| G-33 | CompactConversation 用户调用工具 | 04 上下文 |
| G-34 | SubagentPool outbox_strategy 实际触发 | 10 Subagent |
| G-35 | THREAT_MODEL.md | 13 沙箱 |
| G-36 | LangSmith stream_events v3 协议 | 06 Trace |
| G-37 | StreamChunk event variant | 06 Trace |
| G-38 | SseFormatter v3 envelope | 06 Trace |
| G-39 | ModelCallEnd latency_ms + finish_reason | 06 Trace |
| G-40 | ToolCallEnd args + result_summary | 06 Trace |
| G-41 | 4 个 SummarizationMiddleware 变体（auto / tool / create_mw / create_tool_mw） | 04 上下文 |
| G-42 | SkillIndex YAML frontmatter `description` 上抛 | 12 技能 |
| G-43 | cache_control breakpoint 注入 | 12 技能, 09 Provider |
| G-44 | timeout vs deny 区分 | 11 HITL |
| G-45 | PostgresSessionStore | 03 状态 |
| G-46 | Engine::resume() 快捷 API | 03 状态 |
| G-47 | snapshot_frequency 旋钮 | 03 状态 |
| G-48 | RemoveMessage patches | 03 状态 |
| G-49 | provider init_kwargs defensive wrap | 09 Provider |
| G-50 | model normalization（azure/mistral aliases） | 09 Provider |
| G-51 | profile merge logic (additive) | 09 Provider |
| G-52 | per-user memory namespace | 12 记忆 |
| G-53 | MemoryMiddleware add_cache_control | 12 记忆 |
| G-54 | base_system_prompt override | 09 Provider |
| G-55 | tool description overrides per profile | 09 Provider |
| G-56 | EnableCaptureOffload for sandbox | 13 沙箱 |
| G-57 | SandboxBackend.execute_accepts_timeout | 13 沙箱 |
| G-58 | aexecute async 路径 | 13 沙箱 |
| G-59 | SubagentSpec facade dataclass | 10 Subagent |

---

## 4. 推荐路线图（Phase 11+）

### Phase 11 — Foundation（4 个月）
**主题**：建立 Backend 抽象 + Middleware 钩子层

| 月份 | 任务 | 编号 |
|------|------|------|
| M1 | `arf-backend` crate + BackendProtocol + 3 实现 | G-01 |
| M1 | `BackendFactory` + `_resolve_backend` | G-01 |
| M2 | Engine `before_model_call` / `modify_request` 钩子 | G-02 |
| M2 | FilesystemMiddleware（基于 Backend） | G-02 + G-01 |
| M3 | SummarizationMiddleware（含 offload + overflow clip） | G-12, G-13 |
| M3 | MemoryMiddleware + AGENTS.md 加载 | G-11 |
| M4 | SandboxBackendProtocol + StateBackend 默认 | G-04 |
| M4 | THREAT_MODEL.md | G-35 |

**Phase 11 验收**：可写一个 App，调用 StateBackend / FilesystemBackend，并通过 FilesystemMiddleware 暴露 ls/read/write/edit 给模型；自动 summarization 含 offload；THREAT_MODEL.md 完整。

### Phase 12 — Profiles + Eval（3 个月）
**主题**：Profile 系统 + LLM Eval Harness

| 月份 | 任务 | 编号 |
|------|------|------|
| M1 | `Profile` registry + entry point plugin | G-07, G-16 |
| M1 | ProviderProfile + HarnessProfile dataclass | G-18, G-49 |
| M2 | Anthropic/Bedrock prompt caching | G-09, G-43 |
| M2 | profile merge / exclusion 机制 | G-51, G-54, G-55 |
| M3 | `py-arf/eval/`: LLMJudge + 8 个核心 eval | G-03 |
| M3 | RubricMiddleware | G-05 |

**Phase 12 验收**：通过 entry point 加载第三方 profile；Anthropic prompt cache 命中率 > 60%；运行 8 个 LLM eval 通过；RubricMiddleware 在 todo/followup 任务上自评分。

### Phase 13 — Remote + Async（2 个月）
**主题**：跨主机 subagent + HITL 完善

| 月份 | 任务 | 编号 |
|------|------|------|
| M1 | AsyncSubAgent + Agent Protocol transport | G-08 |
| M1 | subagent metadata propagation | G-28 |
| M2 | caller-supplied approver callable | G-26 |
| M2 | accept/edit/reject 3-state response | G-27 |
| M2 | filesystem permission interrupt synthesis | G-14 |

**Phase 13 验收**：跨主机 subagent 通过 Agent Protocol 调用；HITL 支持 accept/edit/reject；filesystem 工具可在执行前被 interrupt 拦截。

### Phase 14 — Linear Checkpoint + State Polish（2 个月）
**主题**：解决 O(N²) checkpoint + 状态可扩展性

| 月份 | 任务 | 编号 |
|------|------|------|
| M1 | DeltaChannel reducer 移植 | G-06 |
| M1 | snapshot_frequency 旋钮 | G-47 |
| M1 | RemoveMessage patches | G-48 |
| M2 | Engine::resume() 快捷 API | G-46 |
| M2 | state_schema 扩展点 | G-23 |
| M2 | per-tool-call-id dedup | G-22 |

**Phase 14 验收**：1000 turn 线程的 checkpoint 大小从 O(N²) 降为 O(N)；`Engine.resume(session_id)` 一行恢复。

### Phase 15 — Trace v3 + Stream（2 个月）
**主题**：流式事件 v3 + 评测

| 月份 | 任务 | 编号 |
|------|------|------|
| M1 | LangSmith stream_events v3 协议 | G-36 |
| M1 | StreamChunk variant + SseFormatter v3 envelope | G-37, G-38 |
| M2 | ModelCallEnd latency_ms + finish_reason 补全 | G-39 |
| M2 | ToolCallEnd args + result_summary 补全 | G-40 |
| M2 | count_tokens_once_per_model_call | G-20 |

**Phase 15 验收**：SSE 流兼容 LangSmith v3 协议；token-level replay 可用；LLM call 幂等。

---

## 5. 决策矩阵：哪些缺口必须修复，哪些可延后

### 必须修复（P0 + 高 ROI P1）
G-01（Backend 抽象）、G-02（Middleware 钩子）、G-03（Eval Harness）、G-04（Sandbox 默认安全）、G-05（RubricMiddleware）、G-06（DeltaChannel）、G-11（AGENTS.md）

### 强烈建议（P1 中等 ROI）
G-07（Profile）、G-08（AsyncSubAgent）、G-09（PromptCaching）、G-12（Offload）、G-14（FS Permission Interrupt）

### 锦上添花（P2 / 低 ROI）
G-32–G-59（详见 §3）

### ARFV1 独有的（不需要修复）
- ✅ Bus broadcast federation
- ✅ OnMemberFailedHandler
- ✅ InboundDedupCache
- ✅ resend_pending_outbound
- ✅ Barrier protocol
- ✅ Tool DAG execution
- ✅ Protocol-based DI
- ✅ Team/TeamBuilder 抽象
- ✅ Cancelling 状态保留
- ✅ SnapshotEffects 4-side-effect 契约
- ✅ 心跳 / node_offline 自动检测
- ✅ Concurrent tool batch + first-error
- ✅ Tool 超时（默认 30s）
- ✅ 多语言（Rust + PyO3）

---

## 6. 对标总评

| 维度 | ARFV1 评分 | DeepAgents 评分 | 差距 | 趋势 |
|------|-----------|----------------|------|------|
| 01 多 Agent 编排 | ⭐⭐⭐⭐⭐ | ⭐⭐ | -3 | ARFV1 领先 |
| 02 引擎循环 | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | +1 | DeepAgents 领先（Middleware） |
| 03 状态持久化 | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | +1 | DeepAgents 领先（DeltaChannel） |
| 04 上下文管理 | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ | +2 | DeepAgents 显著领先（offload + clip） |
| 05 文件系统工具 | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ | +2 | DeepAgents 显著领先（7 backends） |
| 06 Trace 可观测 | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | 0 | 持平（ARFV1 强持久，DeepAgents 强流式） |
| 07 评估 | ⭐⭐ | ⭐⭐⭐⭐⭐ | +3 | DeepAgents 显著领先（129 evals） |
| 08 扩展性 | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | +1 | DeepAgents 领先（entry points） |
| 09 Provider/Profile | ⭐⭐ | ⭐⭐⭐⭐⭐ | +3 | DeepAgents 显著领先（profile 系统） |
| 10 Subagent | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | +1 | DeepAgents 领先（3 种类型 + 远程） |
| 11 HITL | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | 0 | 持平（各有特色） |
| 12 技能 / 记忆 | ⭐⭐ | ⭐⭐⭐⭐⭐ | +3 | DeepAgents 显著领先（AGENTS.md + cache control） |
| 13 沙箱 / Shell | ⭐⭐ | ⭐⭐⭐⭐ | +2 | DeepAgents 领先（SandboxBackend） |

**总评**：13 个维度中 ARFV1 领先 1 个，持平 3 个，落后 9 个。

**战略意义**：
- ARFV1 的**核心差异化**在**多 Agent 编排 / 容错 / 类型安全**——这是 DeepAgents 永远做不到的（LangGraph runtime 限制）
- ARFV1 的**主要短板**在**Backend 抽象 / Middleware 钩子 / Eval / Profile**——这些都是**借鉴成本低、收益高**的 P0
- 修复 5 个 P0 后，ARFV1 将在所有维度持平或领先 DeepAgents

---

## 7. 实施原则

### 7.1 Doc before code
遵循 CLAUDE.md "V1.x 逐任务开发工作流"：
- 每个 G-XX 缺口修复必须先写 task 文档（含完整代码 + 逐行解释 + 测试）
- push 给用户在 Gitee 精校
- 批准后写代码，verify

### 7.2 边界优先测试
按 CLAUDE.md 要求标注 `[构造][方法][边界][trait][序列化][唯一性][时间][兼容][类型][覆盖]`

### 7.3 原子级提交
按 feedback-granular-commits 规范：每个 G-XX 一个 commit，避免 bundle

### 7.4 复用 DeepAgents 经验
DeepAgents 已经踩过坑（private_state_keys 泄漏、tool list 突变、RubricMiddleware prompt 长度等）。直接参考其 `tests/evals/` 与 middleware 实现可节省大量试错成本。

### 7.5 不破坏现有优势
ARFV1 的 9 项领先能力（§5）必须在 P0 修复中**保留**：
- Backend 抽象不能让 Bus 退化为单 runtime
- Middleware 钩子必须与 CheckpointRule 共存
- Profile 系统不能破坏 Protocol DI 的纯净性

---

## 8. 参考文档索引

| 维度 | 文件 |
|------|------|
| 多 Agent 编排 | [01-multi-agent-orchestration.md](./01-multi-agent-orchestration.md) |
| 引擎循环 | [02-agent-runtime-engine-loop.md](./02-agent-runtime-engine-loop.md) |
| 状态持久化 | [03-state-persistence.md](./03-state-persistence.md) |
| 上下文管理 | [04-context-management.md](./04-context-management.md) |
| 文件系统 | [05-filesystem-tool-surface.md](./05-filesystem-tool-surface.md) |
| Trace 可观测 | [06-trace-observability.md](./06-trace-observability.md) |
| 评估 | [07-evaluation.md](./07-evaluation.md) |
| 扩展性 | [08-extensibility-plugin-model.md](./08-extensibility-plugin-model.md) |
| Provider/Profile | [09-provider-profile-system.md](./09-provider-profile-system.md) |
| Subagent 模式 | [10-subagent-patterns.md](./10-subagent-patterns.md) |
| HITL | [11-human-in-the-loop.md](./11-human-in-the-loop.md) |
| 技能 / 记忆 | [12-skills-memory.md](./12-skills-memory.md) |
| 沙箱 / Shell | [13-sandbox-shell.md](./13-sandbox-shell.md) |
| 主索引 | [README.md](./README.md) |

---

## 版本与时间戳

- **ARFV1**: 1.0.0-alpha.0（2026-07-01）
- **DeepAgents**: 0.6.12（2026-06-25）
- **对标时间**: 2026-07-06
- **作者**: ARF V1.x 维护团队
- **下次复审**: 建议 Phase 11 完成后（2026-11）重新对标 DeepAgents 最新版本