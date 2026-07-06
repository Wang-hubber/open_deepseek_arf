# 02 — Agent Runtime / Engine Loop

> ARFV1 × DeepAgents 原子级对标 · 覆盖 ReAct 循环、Checkpoint 管道、中间件钩子、工具执行语义、错误处理、取消。

---

## 1. Core ReAct Loop Structure

### ARFV1
- File(s): `crates/arf-engine/src/engine.rs:295-417` (`Engine::run` + `run_inner`)
- Implementation: 显式 `loop` 包绕 `do_model_turn` + `do_tool_turns_concurrent`；在 5 个不动点（`BeforeModelCall` / `AfterModelCall` / `BeforeToolExec` / `AfterToolExec` / `RoundEnd`）插入 `Checkpoint` 求值；turn_count 与 round_count 在 `State.over_view` 上单调递增。`tool_calls.is_empty()` 即视为纯文本终止。
- Strengths: 单线 `loop`，无 awaiter/graph；可直接在中间插入 Cancel / max_turns / SessionNotPreSaved guard；turn 与 round 分层计数。
- Weaknesses: 单 ReAct 回合无 fine-grained interrupt slot（无 `before_agent` 概念）；纯顺序控制流，无法 LangGraph 那种并行 branch。

### DeepAgents
- File(s): `libs/deepagents/deepagents/graph.py:353-1025` (`create_deep_agent`) + `libs/ARCHITECTURE.md:50-61`
- Implementation: LangGraph Runtime 通过 `compiled_graph.invoke(...)` 驱动状态机；每 turn 由 LLM 节点 → 工具节点 → 回到 LLM 节点；middleware 在节点间拦截。AgentState 上保留 messages，循环由 LangGraph Runtime 隐式推进。
- Strengths: 自动持久化、断点恢复（checkpointer）；可在节点间声明多个分支。
- Weaknesses: 调用栈不可见，middleware 顺序以 list 隐式控制；ReAct 终止条件（无 tool_calls）由 LangChain `Agent` 内部封装，应用层不可见。

### Gap Analysis
- Parity: ⚠️ Partial
- Severity: 🟠 Important
- Recommendation: 现状可接受；建议在 `Engine::run_inner` 暴露 `before_agent` / `after_agent` 钩子（与 `Checkpoint::RoundEnd` 解耦），保留"wrap_model_call-around"语义。

---

## 2. Hook Granularity

### ARFV1
- File(s): `crates/arf-core/src/checkpoint.rs:9-20` (`Checkpoint` enum, 5 variants) + `crates/arf-engine/src/engine.rs:354-405` (5 个调用点)
- Implementation: 5 个固定 `Checkpoint` 点；`CheckpointRule` 在每个点评估 `when(&State) -> bool`，若真则 `build(&State) -> Box<dyn ActionMessage>` 并 dispatch；调用方只能通过 `dispatch_incoming(...)` 接收结果（在 `wait_for_strategy:1280-1282`）。
- Strengths: 同步、声明式；HRTB 闭包可在任何生命周期共享 `&State`；构建产物是强类型 `ActionMessage`，可挂回 Bus。
- Weaknesses: 无 `wrap_*_call` 的 request-mutation 语义（如修改 tool 列表 / system_prompt）；无 `before_agent` 一次性钩子。

### DeepAgents
- File(s): `libs/ARCHITECTURE.md:55-61` + `deepagents/middleware/filesystem.py:pre-model_call` 区域的 `wrap_model_call` + `before_agent` (`deepagents/middleware/patch_tool_calls.py:14`)
- Implementation: 6 类钩子：`before_agent`（运行前）/ `wrap_model_call`（同步包装 LLM 请求）/ `awrap_model_call`（异步）/ `modify_request`（修改 messages+tools+prompt）/ `wrap_tool_call`（包装工具调用）/ `after_agent`（运行后）。
- Strengths: 可在 LLM 调用前改 `request.tools`、改 `request.system_message`（如 `SkillsMiddleware.awrap_model_call:1060-1065`，`SubAgentMiddleware.awrap_model_call:864-870`，`SummarizationMiddleware.awrap_model_call:2185-2190`，`RubricMiddleware.awrap_model_call:440-441`）；支持 sync + async 双形态。
- Weaknesses: 钩子间不能直接共享 state（须走 `state_schema` typed state）；`modify_request` 返回新 `ModelRequest`，无直接 mutate。

### Gap Analysis
- Parity: ❌ Missing
- Severity: 🔴 Critical
- Recommendation: 在 `CheckpointRule` 之上添加 `BeforeModelRequest` / `ModifyModelRequest` 钩子（签名 `Fn(&State, &mut ModelRequest) -> Option<ActionMessage>`），与 `ToolExclusionMiddleware` 一一对应；专用于 provider-specific 工具裁剪 / 提示注入。

---

## 3. Tool List Manipulation at Runtime

### ARFV1
- File(s): `crates/arf-agent/src/config.rs:83-107`（`AgentConfig.tools: Vec<ToolSpec>`） + `crates/arf-engine/src/registry.rs`（tool registry）
- Implementation: Tool 列表在 build 时冻结为 `Vec<ToolSpec>`；运行期 LLM 看到完整集合。Engine 通过 `target`/`owner_of_tool` 选择接收方（`engine.rs:1032-1035`），但**不出于权限或模型能力裁剪列表**。
- Strengths: 简单可预测；可见性 = 可执行性。
- Weaknesses: 无法对单次请求隐藏工具（例如 "无 shell 模型的 execute 工具下线" 场景）；`AgentMiddleware` 类的 use case 必须用 `tool_exec` 路由而非模型可见性。

### DeepAgents
- File(s): `deepagents/middleware/_tool_exclusion.py:31-66` + `deepagents/middleware/filesystem.py`（capability-aware 过滤） + `graph.py:967-968`（在 stack 末尾追加）
- Implementation: `_ToolExclusionMiddleware` 在 `wrap_model_call` 中过滤 `request.tools`；在 stack 末尾追加，以保证不会被任何 wrap call 重新注入（`graph.py:965-967` 注释："excluded tool names are stripped last and cannot be restored by a custom wrap_model_call"）。`FilesystemMiddleware` 在 backend 无 `execute` 能力时主动隐藏 `execute` tool。
- Strengths: 真正 per-request 的工具可见性裁剪；后置保证不绕过。
- Weaknesses: 必须以 middleware 形式实现（不能配置化）。

### Gap Analysis
- Parity: ❌ Missing
- Severity: 🟠 Important
- Recommendation: 引入 `ToolVisibilityPolicy`（依 `provider` / `model` / `checkpoint` 上下发的 `Fn(&State) -> Vec<ToolSpec>`），在 `do_model_turn` 之前 pub `model_call` 前过滤；与 `_ToolExclusionMiddleware` 等价。

---

## 4. System Prompt Manipulation

### ARFV1
- File(s): `crates/arf-agent/src/config.rs:93`（`system_prompt_template: String`）+ `crates/arf-engine/src/engine.rs:prepare_round`（运行时 inject）
- Implementation: 单 string 模板 + `initial_memory` / `skills`，由 `prepare_round` 在 round 边界装配一次后整体发送给 model；无法 pre-call mutation。
- Strengths: 简单；可缓存整段 prefix。
- Weaknesses: 无 prefix/base/suffix 分段；无每 turn 注入；无 `cache_control` 粒度。

### DeepAgents
- File(s): `graph.py:984-1001`（4 段装配）+ `graph.py:139-153`（`SystemPromptConfig`） + `graph.py:71-136`（`BASE_AGENT_PROMPT` + 注释 114-136）
- Implementation: `prefix` → `profile_base`（或 BASE_AGENT_PROMPT 兜底）→ `suffix` → `HarnessProfile.system_prompt_suffix`；每段可独立为 `str` 或 `SystemMessage`（携带 `cache_control` markers）；空白行 join，保留 cache markers。
- Strengths: 4 段拼装，cache markers 可精确定位；profile 可按 provider/model 调优。
- Weaknesses: 装配在 build 时静态完成；运行时只通过 `modify_request` 改，不重建段。

### Gap Analysis
- Parity: ⚠️ Partial
- Severity: 🟡 Useful
- Recommendation: V1.x 引入 `SystemPromptSegments { prefix, base, suffix }`，与 `HarnessProfile` 联动；保留模板字符串兼容。

---

## 5. ResponseProcessor Dispatch Table

### ARFV1
- File(s): `crates/arf-core/src/processor.rs:21-30` (`ResponseProcessor` trait) + `crates/arf-engine/src/config.rs:22` (`EngineConfig.processors: HashMap<String, Arc<dyn ResponseProcessor>>`)
- Implementation: App 注册 `processors: HashMap<msg_type, Arc<dyn ResponseProcessor>>`；`wait_for_strategy:1296-1305` 在收到匹配 `correlation_id` 后查表调用；错误转为 `RunError::Processor { msg_type, reason }`（F-025 修复，原本静默 swallow）。
- Strengths: 显式 dispatch；error 透传；与 msg_type constants (`crates/arf-core/src/msg_type.rs`) 联动。
- Weaknesses: `model_response` / `tool_result` 在白名单内（直走 Engine）—— 不能通过 `processors` 拦截（这是有意设计，但限制了可见性）。

### DeepAgents
- File(s): `langchain.agents.factory.create_agent`；DeepAgents 不暴露 per-msg-type processor 概念
- Implementation: 无等价 dispatch 表；custom 行为通过 `wrap_model_call` 或 graph 节点实现。
- Strengths: 模型端 unified 入口。
- Weaknesses: 失去 msg_type 维度的精确派遣。

### Gap Analysis
- Parity: ✅
- Severity: — (ARFV1 胜出)

---

## 6. MessageHandler Registration (HashMap msg_type → Vec<handler>)

### ARFV1
- File(s): `crates/arf-engine/src/dispatcher.rs:69-110` (`HandlerRegistry`) + 调用点 `engine.rs:1280-1282`
- Implementation: `HashMap<String, Vec<Arc<dyn MessageHandler>>>`，每个 msg_type 可注册多个 handler，按注册顺序试，首个返回 `Handled` 胜出；提供 `register` / `replace` 两种 API。
- Strengths: built-in dispatch；F-024 修复引入 `dispatch_incoming` 后，Engine 主 recv 循环不被阻塞（`engine.rs:1280-1281` 注释："Engine's main wait loop is not blocked — handlers must be quick or spawn their own task"）。
- Weaknesses: Handler 必须 `Send+Sync`；handler 不能直接修改 `State`，须发回 Bus（`dispatcher.rs:42-54` 注释）。

### DeepAgents
- File(s): 通过 middleware (single dispatch per middleware class) 间接表达；无显式 msg_type 维度的多 handler 注册点
- Implementation: LangGraph runtime 通过 channels/nodes 调度，没有 `dispatcher.rs` 形态。
- Strengths: 状态直接 typed schema 共享。
- Weaknesses: 没有"一个 msg_type N 个 handler"的语义；并发处理需增加 node。

### Gap Analysis
- Parity: ✅ (ARFV1 独占)

---

## 7. Tool Permission Gating

### ARFV1
- File(s): `crates/arf-engine/src/engine.rs:972-1025` (`do_tool_turn` permission 分支) + `crates/arf-agent/src/config.rs:98-104`
- Implementation: `ToolPermission::{Allow, Ask, Deny}`；`Deny` 短路：插入 tool role + content `"tool call denied by policy: ..."`，直接返回；`Ask` 发 `permission_request`（`crates/arf-core/src/msg_type.rs:22`），await `permission_response`，deny 同样短路；`Allow` 走正常路径。
- Strengths: 决策点收敛一处；Ask 模式有真正的 round-trip 等待。
- Weaknesses: `Allow` 是默认；人类授权通过 UI 节点单点集成，不是 middleware 形式。

### DeepAgents
- File(s): `deepagents/middleware/permissions.py` + `graph.py:946-951`（`HumanInTheLoopMiddleware` 注入） + `_fs_interrupt.py`
- Implementation: FilesystemPermission 规则作用于 file 工具；HITL 中间件在 `interrupt_on` 配置的工具触发 LangGraph interrupt。`gp_interrupt_on` 由 `_build_interrupt_on_from_permissions` 派生（`graph.py:880-885`）。
- Strengths: 规则驱动；HITL 中断可恢复。
- Weaknesses: 不直接对 LLM 任意工具生效；只针对内置 fs 工具与 `interrupt_on` 字典。

### Gap Analysis
- Parity: ⚠️ Partial
- Severity: 🟠 Important
- Recommendation: ARFV1 的 `Ask` 即已实现 round-trip HITL；DeepAgents 模型优势在 path-level 文件权限。建议把 `FilesystemPermission` 风格的 glob 引擎下沉到 `arf-mcp` 节点。

---

## 8. Tool Timeout Enforcement

### ARFV1
- File(s): `crates/arf-engine/src/config.rs:44` (`tool_timeout_ms: Some(30_000)`) + `crates/arf-engine/src/engine.rs:` (`send_and_await` 用 `tokio::time::timeout` 包装)
- Implementation: 默认 30s；在 `send_and_await` 内通过 `tokio::time::timeout` 包绕，超时 cancel + `MaybeRecordOutbound` 失败。
- Strengths: 集中超时；与 `CancellationToken` 协同。
- Weaknesses: 单值全局，不可按工具 override。

### DeepAgents
- File(s): `deepagents/backends/protocol.py`（`execute_accepts_timeout`） + `deepagents/backends/sandbox.py`（BaseSandbox）
- Implementation: SandboxBackend 自带 timeout 字段；本地 shell 用 `asyncio.wait_for`；文件系统 backend 无 timeout 概念。
- Strengths: per-tool 调用可传 `timeout` 参数。
- Weaknesses: timeout 不在 ReAct 主循环 enforcement，只在 backend 内部。

### Gap Analysis
- Parity: ⚠️ Partial
- Severity: 🟡 Useful
- Recommendation: 在 `ToolSpec` 加 `timeout_ms: Option<u64>`，覆盖 `EngineConfig.tool_timeout_ms`。

---

## 9. Tool DAG Execution (`blocked_by` / `blocking`)

### ARFV1
- File(s): `crates/arf-mcp/src/`（ToolNode 拓扑层） + `crates/arf-mcp/src/node.rs`（Kahn sort）
- Implementation: `ToolCall` payload 含 `blocked_by: Vec<Uuid>` / `blocking: bool`；MCP 节点内部 Kahn 拓扑排序后并行/串行调度。
- Strengths: 真正的 DAG；跨工具依赖可表达。
- Weaknesses: 仅 MCP 节点内部使用，Engine 主循环仍按 LLM 输出顺序 `do_tool_turns_concurrent`（`engine.rs:401-403`），未将 `blocked_by` 上拉为引擎级语义。

### DeepAgents
- File(s): `LangGraph ToolNode`（顺序执行，无 DAG）
- Implementation: 单次 LLM 返回的 tool_calls 顺序进入 ToolNode；不引入跨 tool 拓扑。
- Strengths: 简单。
- Weaknesses: 无法表达 "summary 后再 grep" 这类先后约束。

### Gap Analysis
- Parity: ✅ (ARFV1 独占)
- Severity: — 
- Recommendation: 文档化 MCP-only 限制，并提供 `EngineConfig.tool_dag: bool` 开关，将其上拉为引擎语义。

---

## 10. Concurrent Tool Execution

### ARFV1
- File(s): `crates/arf-engine/src/engine.rs:401-403`（`do_tool_turns_concurrent`）+ 注释 391-395
- Implementation: 单次 `model_response` 返回多个 `tool_calls` 时，`tokio::join!` 并发 send+await；注释显式 "per-tool Checkpoint::Before/AfterToolExec are not fired when running concurrently"。
- Strengths: 显著降低 wall-clock。
- Weaknesses: checkpoint 只在批前后触发一次，不能 per-tool 干预状态。

### DeepAgents
- File(s): LangGraph `ToolNode` 默认按 message 串行执行。
- Implementation: 单 sequence 内顺序 invoke；并发需 multiple nodes 显式编排。
- Strengths: 顺序确定性。
- Weaknesses: 长工具调用整体延迟放大。

### Gap Analysis
- Parity: ✅ (ARFV1 胜出)
- Recommendation: 当 `do_tool_turns_concurrent` 启动 per-tool 时，提供 `BatchCheckpoint` 标志区分。

---

## 11. First-Error-Wins in Tool Batch

### ARFV1
- File(s): `crates/arf-engine/src/engine.rs:407-409`
- Implementation: `tool_results.into_iter().find_map(|r| r.err())` —— 第一个 `Err` 终结 `run_inner`。
- Strengths: 失败快速传播，避免"继续跑剩余工具浪费 token"。
- Weaknesses: 后续结果丢弃，无法上下文模型 "5 个中 1 个失败"。

### DeepAgents
- File(s): LangGraph `ToolNode.handle_tool_errors` 默认吞错为 `ToolMessage(content=error)`。
- Implementation: 每个 tool 失败独立转 ToolMessage，model 看到错误。
- Strengths: 模型可逐个处理。
- Weaknesses: 没有"快速失败"语义，长失败链路继续消耗。

### Gap Analysis
- Parity: ⚠️ Partial (各取一边)
- Severity: 🟡 Useful
- Recommendation: 引入 `EngineConfig.tool_batch_failure: enum FirstWins | PerTool | AllOrNothing`，默认 `PerTool`。

---

## 12. Text-Only Response Termination

### ARFV1
- File(s): `crates/arf-engine/src/engine.rs:382-388`（`tool_calls.is_empty()` 判断） + `Checkpoint::RoundEnd` 触发
- Implementation: 检测 `tool_calls.is_empty()` → `evaluate_and_dispatch(RoundEnd, ...)` → `return Ok(content)`。
- Strengths: 终止条件明确；触发 RoundEnd 让用户在模型 "纯文本" 之前拦截修改。
- Weaknesses: 单点判定，模型若发"text+空 tool_calls" 与纯文本等价（依赖 LLM）。

### DeepAgents
- File(s): LangChain `Agent.should_continue_agent`；无 tool_calls 自然结束。
- Implementation: 不等价的实现细节包在 LangChain。
- Strengths: 一致。
- Weaknesses: 无法拦截 RoundEnd（无对应点）。

### Gap Analysis
- Parity: ✅ (ARFV1 优势在于显式 RoundEnd 钩子)

---

## 13. Max Turns Termination

### ARFV1
- File(s): `crates/arf-agent/src/config.rs:127` (`max_turns: u32` 默认 10) + `engine.rs:348-352`, `357-361`, `376-380`, `411-415`（4 处检查）
- Implementation: 4 处 max_turns 检查：loop 入口、BeforeModelCall 后、AfterModelCall 后、tool batch 后；超限返回 `RunError::MaxTurnsExceeded`。
- Strengths: 多点保险，防止 "刚好 model+tool 一对算 2 turn"。
- Weaknesses: 默认 10 在长任务下可能不足；无 config 持久化按任务类型 override。

### DeepAgents
- File(s): `recursion_limit` LangGraph 配置。
- Implementation: LangGraph `config["recursion_limit"]` 默认 25。
- Strengths: 集成于 Graph config。
- Weaknesses: 与"turn"概念不对齐——一次 model call + 多个 tool node 可能算多个 recursion step。

### Gap Analysis
- Parity: ✅

---

## 14. Cancellation Handling

### ARFV1
- File(s): `crates/arf-engine/src/engine.rs:299` (`cancel: CancellationToken`) + `engine.rs:1239-1246` (`tokio::select! biased`)
- Implementation: `tokio::select! { biased; _ = cancel.cancelled() => Err(Stopped), res = handle.recv() => ... }`；7 处 `cancel.is_cancelled()` 早期 return (`engine.rs:343,367,376,396,409,510,561`)。
- Strengths: `biased` 保证 cancel 优先；`Stopped` 是 typed error。
- Weaknesses: handoff 时各自 `tokio::spawn` 计时器 (`engine.rs:706-709`)，时间到了 cancel 但 reply 仍可能 race 进入。

### DeepAgents
- File(s): LangGraph `interrupt` + runtime cancel。
- Implementation: 通过 `Command(resume=...)` 恢复；纯 durable 机制，非 token-based。
- Strengths: 跨重启可恢复。
- Weaknesses: 无 in-flight 即时取消；只有 node 间 boundary。

### Gap Analysis
- Parity: ⚠️ Partial
- Severity: 🟠 Important
- Recommendation: ARFV1 应引入 "durable cancel checkpoint"，取消点写入 session_store，下次启动 detect 恢复后跳到 dispatch。

---

## 15. RunError Variants

### ARFV1
- File(s): `crates/arf-engine/src/error.rs`（7 个变体）
- Implementation: `Stopped` / `MaxTurnsExceeded { max_turns }` / `MemberFailed` / `Processor { msg_type, reason }` / `SnapshotFailed { session_id, reason }` / `SessionNotPreSaved { session_id }` / `Bus(..)` / `Internal(..)`。
- Strengths: 细粒度，App 可针对性处理。
- Weaknesses: 无 `ContextOverflow` / `RateLimited` / `AuthFailed` —— 只 generically `Bus`。

### DeepAgents
- 无对应枚举；异常即 exception。
- Implementation: LangGraph 把错误塞进 `state["messages"]` ToolMessage。
- Strengths: 简单。
- Weaknesses: 调用栈丢失。

### Gap Analysis
- Parity: ✅ (ARFV1 优势)
- Recommendation: 增加 `ContextOverflow` / `RateLimited` / `ProviderError` 三种细分。

---

## 16. Engine `reset_state` for Ephemeral Re-use

### ARFV1
- File(s): `crates/arf-engine/src/engine.rs:1338-1353`
- Implementation: `reset_state(&State)` 清空 `messages`、`turn_count`、`round_count`、`last_user_message`、`wait_events`；失败当 `EngineError::OutboxNotEmpty`。
- Strengths: 同一 Engine 可被 SubagentPool 借出/归还；outbox 检查防半提交。
- Weaknesses: `collect_outbox_pending` 始终返回 `[]`（占位，`engine.rs:1358-1360` 注释 "Kept as a method so future implementations can swap in JSONL scanning"）—— 当前无强保证。

### DeepAgents
- 无 single-Engine 复用概念；subagent 是独立 compiled graph。
- Implementation: 物理隔离。
- Strengths: 隔离干净。
- Weaknesses: 无 pool 概念，资源利用率低。

### Gap Analysis
- Parity: ✅ (ARFV1 独占)

---

## 17. Engine Ephemeral Mode (`run_once`)

### ARFV1
- File(s): `crates/arf-engine/src/engine.rs:1464-1480` (`run_once`) + `TaskInput`/`TaskResult`
- Implementation: `run_once(state, task_input, cancel) -> TaskResult` —— 包绕 `run` 并度量 `turns_consumed = turn_count - turns_before`。
- Strengths: 配合 `reset_state` 给 SubagentPool 提供一次性 Engine 入口；返回 `turns_consumed` 让 pool 做 billing/限流。
- Weaknesses: 无 `TaskResult.pending_peer_messages` 内容（注释空 vec，`engine.rs:1478`）。

### DeepAgents
- File(s): `CompiledSubAgent` / `AsyncSubAgent` / 自己的 graph。
- Implementation: 每个 subagent 是独立 compiled graph，无复用 Engine 概念。
- Strengths: 隔离。
- Weaknesses: 重型。

### Gap Analysis
- Parity: ✅ (ARFV1 独占)
- Recommendation: 实现 `pending_peer_messages` 收集，增强 multi-hop 委托能力。

---

## 18. Required Scaffolding Guard

### ARFV1
- File(s): 无等价机制；`EngineConfig` 不分 "不可去除" 与 "可裁剪" 类。
- Implementation: 任意 `CheckpointRule` 可被 app 删；无最小集保护。
- Strengths: 全灵活。
- Weaknesses: App 误删 `BeforeModelCall` 不会 panic，但 RoundEnd 必然存在无强制。

### DeepAgents
- File(s): `graph.py:323-350` (`_REQUIRED_MIDDLEWARE`) + `graph.py:976-982` (`_verify_excluded_middleware_coverage`)
- Implementation: `FilesystemMiddleware` 与 `SubAgentMiddleware` 标记为 `_REQUIRED_MIDDLEWARE`；`_apply_excluded_middleware` 对其 raise `ValueError`（`graph.py:336-337` 注释："`_apply_excluded_middleware` raises `ValueError` rather than proceeding with a silently degraded agent"）。
- Strengths: 强保证安全相关 middleware 不被裁。
- Weaknesses: 错误信息仅 `ValueError`，未结构化。

### Gap Analysis
- Parity: ❌ Missing
- Severity: 🟠 Important
- Recommendation: 引入 `EngineConfig.required_checkpoints: BTreeSet<Checkpoint>`，提供 `verify_required` 在 build 期 raise `EngineError::MissingRequired`。

---

## 19. Middleware Ordering — Three Filter Passes

### ARFV1
- File(s): 无对应概念（单个 list）。
- Implementation: 不适用。

### DeepAgents
- File(s): `graph.py:952-964`（3 个 filter 连续调用）
- Implementation: 顺序：
  1. `_apply_excluded_middleware(...)` （第一次：剥离 profile 标记不可用的）
  2. `_apply_custom_middleware(deepagent_middleware, middleware, core_names=_main_core_names)`（在 `_main_core_names` 集合内插入 user middleware）
  3. `_apply_excluded_middleware(...)`（再次：剥离剩下的，确保不被 middleware 恢复）
- Strengths: 确保 user middleware 不能复活被 excluded 的核心。
- Weaknesses: 调试时 stack 重建逻辑复杂。

### Gap Analysis
- Parity: ⚠️ Partial (V1 不存在 list 顺序管理)
- Severity: 🟡 Useful
- Recommendation: 引入 `CheckpointRule.with_priority(u32)`，让 app 可显式控制顺序。

---

## 20. PatchToolCalls — Repair Dangling Tool Calls

### ARFV1
- File(s): `engine.rs:1060-1064`（单点 cancel-sentinel 注入） + 显式 `[cancelled mid-execution] {tc.name}` tool_msg
- Implementation: 单 tool cancel 时直接 push `tool_msg.content = "[cancelled mid-execution] ..."`，不解析历史 dangling。
- Strengths: per-turn 修复。
- Weaknesses: 无历史扫描；若 resume 后中途 dangling，`engine.rs` 不自动修复。

### DeepAgents
- File(s): `deepagents/middleware/patch_tool_calls.py:14-46`
- Implementation: `before_agent` 扫描所有 AIMessage 比 `answered_ids = {msg.tool_call_id for msg in messages if msg.type == "tool"}`；dangling 时插入 `ToolMessage(content="...cancelled...", tool_call_id=...)`；并 `RemoveMessage(id=REMOVE_ALL_MESSAGES)` 重写。
- Strengths: 进入即清洗；区分 `"cancelled"` 与 `"invalid_tool_call"`（`patch_tool_calls.py:40-43`）。
- Weaknesses: 重写 messages list 才能 patch，对 `DeltaChannel` 有边界要求。

### Gap Analysis
- Parity: ❌ Missing
- Severity: 🟡 Useful
- Recommendation: 引入 `BeforeAgent` Checkpoint 阶段扫描 dangling，注入同样 sentinel `ToolMessage`，无需重写 message list。

---

## 21. Core-Name Splice Point

### ARFV1
- File(s): 无对应。
- Implementation: 不适用。

### DeepAgents
- File(s): `graph.py:930-934`（`_main_core_names` 捕获 + 注释 928-930）
- Implementation: "Names of the core stack, captured before the tail is appended so new user middleware can splice in ahead of the profile/prompt-caching/memory tail"；`_apply_custom_middleware(... core_names=_main_core_names)` 保证 user middleware 不能插到 HarnessProfile 之后。
- Strengths: 关键边界保护。
- Weaknesses: 在 user middleware 之前/之后都需要稳定 splice point，否则 cache_prefix 失效。

### Gap Analysis
- Parity: ⚠️ Partial
- Severity: 🟡 Useful
- Recommendation: 等 V1 引入 CheckpointRule priority 后，定义 `CheckpointRule::SPLICE` 锚点（user rule 只能插在此前）。

---

## 总体缺口 / 下一步

| # | 能力 | 严重度 | 现状 | 建议 |
|---|------|------|------|------|
| 1 | Wrap-around modify_request hook | 🔴 | 缺 | 引入 `BeforeModelRequest` 钩子 |
| 2 | Per-request tool 列表裁剪 | 🟠 | 缺 | `ToolVisibilityPolicy` |
| 3 | 系统 prompt 4 段装配 | 🟡 | 单模板 | `SystemPromptSegments` |
| 4 | Required scaffolding guard | 🟠 | 缺 | `required_checkpoints` |
| 5 | PatchToolCalls 历史修复 | 🟡 | 单点 | BeforeAgent 扫 dangling |
| 6 | Durable cancel checkpoint | 🟠 | token-only | 引入持久化 cancel 事件 |
| 7 | 并发批 checkpoint 支持 | 🟡 | 缺 | `BatchCheckpoint` flag |
| 8 | ContextOverflow / RateLimited 错误 | 🟡 | 泛指 `Bus` | 增加 error 变体 |

ARFV1 在 ReAct 循环的**强类型 / 多 Bus / 错误细分**层面领先；主要追赶方向集中在 LangChain middleware 生态（per-request mutation + prompt 段装配 + 中间件 splice）。
