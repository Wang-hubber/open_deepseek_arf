# Agent Execution — Session / Round / Turn 生命周期

ARF 将 OS 进程管理的三个核心机制——fork/exec（进程创建）、scheduler（调度）、IPC（进程间通讯）——适配到 Agent 运行时：Agent 是进程，Session 是地址空间，Tool Call 是系统调用，Handoff 是 IPC。

本文档描述当前实现的全部行为。所有引用标注了源文件位置。

---

## 1. OS 方案演进

> 本章描述 OS 如何处理进程生命周期与调度，帮助理解 ARF 设计动机。非技术实现对标。

### 1.1 进程创建：fork + exec

`fork()` 复制当前进程（文件描述符、地址空间、信号处理），`exec()` 用新程序镜像替换地址空间。核心价值在 **fork-exec 间隙**——子进程可在 exec 前修改环境（重定向 fd、设置 rlimit），修改对新程序透明。

### 1.2 进程调度

**CFS**（Linux 2.6.23）：红黑树，key 为 `vruntime`。每次调度选 vruntime 最小进程。时钟中断触发 `scheduler_tick()`，若当前进程 vruntime 超阈值则置 `TIF_NEED_RESCHED`，返回用户态前检查并调度。

**cgroup**：层级树组织进程，CPU 子系统按权重分配时间片——调度器的配置抽象层。

### 1.3 IPC

管道（pipe，字节流，亲缘进程）、信号（signal，异步，SIGKILL 不可捕获）、共享内存（mmap MAP_SHARED，最快但需同步）、消息队列（POSIX mq，优先级出队）。每种机制在延迟/吞吐/耦合上的取舍对应 ARF 中 Agent 间通讯的多种模式。

### 1.4 对 ARF 的映射

| OS 概念 | ARF 对应 | 实现位置 |
|---------|----------|---------|
| fork + exec 间隙 | `BaseAgent.__init__` DI 组装——创建引擎→注入全部 Protocol→启动 | `arf/agent/base.py` |
| CFS vruntime | `LoopStrategy.should_continue()`——每 turn 判断是否继续 | `arf/engine/graph.py` |
| cgroup 配额 | `max_turns`——每轮断路器 | `arf/engine/graph.py`（默认值）→ `_active_config()` 解析 per-agent → `should_continue`/`should_break` 判定 |
| 抢占标志 | `cancel_event` (asyncio.Event)——循环边界非阻塞检测 | `arf/engine/graph.py` |
| IPC 管道/信号/共享内存 | HandoffManager / AgentBus / PeerAgent / DictWorkspace | `arf/engine/handoff.py` |

---

## 2. 当前实现

Agent 执行体系分三层：**引擎层**（GraphEngine，循环控制 + 工具编排）、**装配层**（BaseAgent，DI 组装全部 Protocol）、**多 Agent 层**（HandoffManager，Agent 切换 + 状态隔离）。

### 2.1 执行边界：Session / Round / Turn

三个嵌套层级，各有独立的计数器、hook 事件和边界语义：

| 层级 | 定义 | 创建位置 | Hook 事件 | 计数器 | 断路器 |
|------|------|---------|-----------|--------|--------|
| **Session** | 客户端一次完整连接，可跨多轮对话 | `BaseAgent.__init__`（首次 `chat()` 时激活） | `session_start`（BaseAgent 触发 hook + GraphEngine emit EventBus 事件）、`session_end`（`stop()` 或 crash recovery） | `session_id: str` | — |
| **Round** | 一次 `chat()`/`astream()` 调用边界 | `BaseAgent.chat()` / `astream()` 入口 | `round_start`（BaseAgent 触发）、`round_end`（GraphEngine 循环退出后触发） | `interaction_round: int`（单调递增） | — |
| **Turn** | 模型调用→工具执行→模型再调用的单次迭代 | `while loop_strategy.should_continue()` 体内 | `pre/post_model_call`、`pre/post_tool_exec`（turn 内子事件，GraphEngine 触发） | `current_turn: int`（每 round 复位） | `max_turns`（每 round 断路器） |

**时序关系**：

```
Session  ──────────────────────────────────────────────────────►
         │                                          │
         ├─ Round 0 ─────────────►                   │
         │  │  (turn 从 0 计数)    │                  │
         │  ├─ Turn 0 ──►          │                  │
         │  ├─ Turn 1 ──►          │                  │
         │  └─ Turn 2 ──► [end]    │                  │
         │                         │                  │
         ├─ Round 1 ─────────────► │                  │
         │  │  (turn 复位到 0)      │                  │
         │  ├─ Turn 0 ──►          │                  │
         │  └─ Turn 1 ──► [end]    │                  │
         │                         │                  │
         └─ Round 2 ...            └─ stop() ────────┤
                                                      ▼
                                                   CLOSED
```

**Crash recovery**：`BaseAgent.chat()` 入口处检测（`arf/agent/base.py`）：

1. `session_id` 已在 `_active_sessions` → 续用已有 session，不触发任何 session 级 hook
2. `session_id` 不在 `_active_sessions` 但磁盘 state 中 `session_active == True` → 进程被 kill，触发 `session_end(reason="recovery")` hook，然后作为新 session 触发 `session_start`
3. 磁盘无 state 或 `session_active` 不存在 → 全新 session，触发 `session_start`

### 2.2 执行流程

```
BaseAgent.chat() / astream()                      arf/agent/base.py
│
├─ state_store.get(session_id)                         # StateStore 加载 State快照（崩溃恢复）
├─ crash recovery 判定 → [Hook] session_end(recovery)  # 仅在恢复场景
├─ new session? → [Hook] session_start                 # BaseAgent 触发 hook
├─ begin_round(深拷贝 + 工作区快照)                     # RoundManager Round检查点（undo 回滚）
├─ [Hook] round_start                                  # BaseAgent 触发
│
▼
GraphEngine.invoke() / astream()                     arf/engine/graph.py
│
├─ _close_tool_calls(state)                            # 保证消息序列完整性
│
├─ while LoopStrategy.should_continue(state):          # 入口门
│   │
│   ├─ step = LoopStrategy.next_step(state)            # 策略驱动分派
│   │   │                                             # ReAct: "call_model" / "execute_tools"
│   │
│   │   ├─── call_model ───────────────────────────────────
│   │   │
│   │   ├─ turn = current_turn + 1                     # turn 从 0 起步
│   │   ├─ [取消检查] / [EventBus] "user_input"
│   │   ├─ [模型路由] / [压缩判断] / [Hook] pre_model_call
│   │   ├─ [模型调用] _call_model / _stream_model       # 含 fallback chain
│   │   ├─ [Hook] post_model_call / [输出检查]
│   │   ├─ [工具解析] _pars_tool_calls()
│   │   │   ├─ 无 tool_calls → [State快照] state_store.put() → break
│   │   │   └─ 有 tool_calls → [State快照] assistant+tool_calls（崩溃恢复）
│   │   │                     暂存 _pending_tool_calls
│   │   │
│   │   ├─── execute_tools ────────────────────────────────
│   │   │
│   │   ├─ [工具守卫] _step_classify_tool_calls()
│   │   ├─ 被拒工具 → 注入 "[Blocked]" tool 消息
│   │   ├─ [Hook] pre_tool_exec
│   │   ├─ [工具执行] ConcurrentToolExecutor.execute()
│   │   ├─ [工具输出摘要] / [Hook] post_tool_exec
│   │   ├─ _close_tool_calls(state)                     # 兜底：孤儿 tool_calls 注入合成结果
│   │   ├─ [Handoff 检测] → _execute_handoff() → continue
│   │   ├─ [State快照] state_store.put()
│   │   └─ LoopStrategy.should_break(state)? → break
│   │   │
│   │   └─── 未知 step → 默认 fallback 到 call_model ────
│
├─ [Hook] round_end                                     # GraphEngine 触发
└─ [EventBus] "session_end"                             # GraphEngine emit 事件
```

### 2.3 状态机：Session → Round → Turn

每个层级有明确的状态转移。当前 Turn 受三层约束：

```
Session 状态：  [INACTIVE] ──chat()──► [ACTIVE] ──stop()──► [CLOSED]
                     ▲                      │
                     └── crash recovery ────┘ (session_end(recovery) → session_start)

Round 流转：   begin_round() → [round_start hook] → Engine Loop → [round_end hook] → close_round()

Turn 流转：    should_continue? → next_step() → dispatch
               call_model: turn++ → route → compact → pre_model → model_call
               → post_model → guard → parse → (text? break) → State快照 assistant+tool_calls
               execute_tools: guard → execute → close_tool_calls → handoff? → State快照 → should_break?
```

### 2.4 双模主循环：invoke / astream

`GraphEngine` 提供两条执行路径（`arf/engine/graph.py`），共享同一个 Agent Loop 逻辑骨架：

| 方法 | 返回 | 适用场景 |
|------|------|---------|
| `invoke()` | `AgentState`（同步返回） | CLI、评测回放、后台任务 |
| `astream()` | `AsyncGenerator[AgentEvent]` | SSE 流式、实时 UI |

**共享的核心方法**：
- `_close_tool_calls()`（消息序列完整性保证，在进入循环前调用）
- `_active_config()`（多 Agent 配置解析）
- `_resolve_tools_for_agent()`（工具定义获取）
- `_step_classify_tool_calls()`（Guard + Pipeline + Permission + Approval）
- `_execute_handoff()` / `_restore_from_handoff()`
- `_inject_hook_messages()`（退出码 2 消息注入）
- `_resolve_fallback()`（模型降级链）

**差异**：
- 模型调用层：`astream` 使用 `_stream_model` 逐 token 产出 `thinking_delta` 事件；`invoke` 使用 `_call_model` 一次性获取。
- 两条路径的 StateStore State快照行为一致：均在 appending assistant+tool_calls 后立即State快照（崩溃恢复——若进程在工具执行期间被 kill，下次启动从该状态继续），工具执行+handoff 后再次写 State快照。

### 2.5 循环策略

`LoopStrategy` 协议（`arf/core/protocols/engine.py`）定义三个门控方法：

```python
class LoopStrategy(Protocol):
    def should_continue(self, state: AgentState) -> bool: ...
    def should_break(self, state: AgentState) -> bool: ...
    def next_step(self, state: AgentState) -> str: ...
```

- `should_continue`：**入口门**——`False` 时跳过循环体，不进入下一 turn
- `should_break`：**出口门**——`True` 时退出循环，本轮是最后一 turn
- `next_step`：**分派门**——返回当前应执行的操作（`"call_model"` 或 `"execute_tools"`）

两个方法使用 `self.max_turns`。引擎在每 turn 从 `_active_config()` 获取当前活跃 Agent 的 `max_turns` 并同步到 `self.loop_strategy.max_turns`——单 Agent 和多 Agent 场景统一了取值路径。Handoff 切换 Agent 后立即刷新。

当前唯一实现是 `ReActStrategy`（`arf/engine/loop_strategies/react.py`）：

```python
class ReActStrategy:
    def __init__(self, max_turns: int = 50) -> None:
        self.max_turns = max_turns

    def should_continue(self, state: AgentState) -> bool:
        return state.get("current_turn", 0) < self.max_turns

    def should_break(self, state: AgentState) -> bool:
        return state.get("current_turn", 0) >= self.max_turns

    def next_step(self, state: AgentState) -> str:
        # 引擎每轮调用：user/system → call_model；assistant+tool_calls → execute_tools
        # tool result → call_model（observe → think）
        msgs = state.get("messages", [])
        if not msgs:
            return "call_model"
        last = msgs[-1]
        role = last.get("role", "")
        if role in ("user", "system"):
            return "call_model"
        if role == "assistant" and last.get("tool_calls"):
            return "execute_tools"
        return "call_model"
```

循环终止条件（四个独立路径）：

1. `should_continue()` 返回 `False`（入口阻断）
2. 模型返回纯文本，`break`（正常完成）
3. `should_break()` 返回 `True`（出口断路器触发）
4. `_cancelled()` 为 `True`，`break`（用户中断）

### 2.6 取消机制

`asyncio.Event` 作为取消信号（`arf/engine/graph.py`）：

```python
def _cancelled(self) -> bool:
    return self._cancel_event is not None and self._cancel_event.is_set()
```

每次 `while` 循环迭代开始前检测。`cancel_event` 通过 `set_cancel_event()` 注入（支持延迟绑定）。取消在循环边界安全退出——当前 turn 的模型调用和工具执行不会被中途打断。

### 2.7 会话断路器

`max_turns` 是每轮的硬限制。默认 50（`GraphEngine.__init__`）。`current_turn` 每 round 在 `BaseAgent.chat()/astream()` 中复位到 0（`arf/agent/base.py`）。

多 Agent 场景：`_active_config()` 返回子 Agent 的 `max_turns`（通过 `cfg.effective_advanced().max_turns`），引擎在 turn 边界使用该值判定。子 Agent 的 turn 在 `_execute_handoff()` 中 reset 为 0（`arf/engine/graph.py`）。返回主 Agent 时，从 StateStore 恢复主 Agent 的消息历史（`_restore_from_handoff()`），turn 计数随之恢复。

### 2.8 工具执行

`ConcurrentToolExecutor`（`arf/engine/tool_executor.py`）：

| 策略 | 实现 |
|------|------|
| `parallel` | `asyncio.gather` + `Semaphore(max_concurrency)` |
| `sequential` | `for` 循环逐个 `await` |

工具 params 中自动注入 `_agent_mode`（当前 active_agent 名称）、`_engine`（GraphEngine 引用）、`_state_store`（StateStore 引用）。工具可通过这些引用访问引擎能力。

工具执行结果（`ToolResult`）含 `success/data/error/duration_ms/rolled_back/rollback_error`。引擎在注入消息历史前，若配置了 compaction，会调用 `compaction.summarize_tool_output()` 截断超长输出。

`_step_classify_tool_calls()` 的工具守卫流水线（`arf/engine/graph.py`）：
1. **Pipeline**：检查 `SkillPipeline.can_execute()`，确保工具依赖顺序
2. **PathCheckToolGuard**：检查路径参数合法性（拒绝 `..` 穿越和绝对路径）
3. **ToolPermissionChecker**：deny → ask → allow 三级判定
4. **Approval**：`perm == "ask"` 且 `approval_enabled` 时，等待用户审批（60s 超时自动拒）

被拒工具注入 `"[Blocked] reason"` 作为 tool 消息，引擎继续循环（不会因单个工具被拒而中止）。

### 2.9 BaseAgent 装配

`BaseAgent.__init__()`（`arf/agent/base.py`）按固定顺序初始化子系统：

| 步骤 | 子系统 | 默认实现 | Protocol |
|------|--------|----------|----------|
| 1 | EventBus | `InMemoryEventBus` | `EventBus` |
| 2 | StateStore | `FileStateStore`（JSON 文件，原子写入） | `StateStore` |
| 3 | Resources | `ToolProvider` + `SkillProvider` + `ModelProvider` → `ResourceResolver` | `ToolResolver` |
| 4 | Memory | `FileMemoryStore`（writer/retriever 移至 plugins） | `MemoryStore` |
| 5 | Compaction | `SlidingWindowCompactor`（token 感知窗口压缩，`threshold=0.75`） | `CompactionStrategy` |
| 6 | Guardrails | `DefaultGuardRunner`（PathCheck + Permission + RegexOutput） | `GuardRunner` |
| 7 | Error Policy | `DefaultErrorPolicy`（tool_retry=2, model_5xx=fallback） | `ErrorPolicy` |
| 8 | Hooks | `SubprocessHookRunner`（子进程并行执行） | `HookRunner` |
| 9 | Tool Executor | `ConcurrentToolExecutor`（parallel/sequential） | `ToolExecutor` |
| 10 | Loop Strategy | `ReActStrategy`（max_turns=50） | `LoopStrategy` |

额外装配：
- **Planner**（可选）：协议已定义（`arf/core/protocols/engine.py`），但引擎侧尚未集成——`plan_execute` 循环策略未实现
- **Sub-agents**：遍历 `config.agents`，为每个子 Agent 创建独立 system prompt 和 ModelAdapter
- **HandoffManager**：从 `config.handover.rules` 构建规则表
- **ModelAdapter**：`_inject_model_calls()` 为每个模型配置创建适配器，注入 `_call_model` / `_stream_model` 闭包；可选包裹 `ModelCallProtector`（`arf/protection/protector.py`，rate limit + circuit breaker）
- **ModelRouter**：`TwoTierRouter`（LLM 分类器或 static），在每 turn 选择模型
- **UsageTracker**：监听 EventBus，累计 token 用量

所有 Protocol 通过 `**override_protocols` 可替换，支持测试注入 `InMemory*` doubles。

### 2.10 Handoff（多 Agent 切换）

`HandoffManager`（`arf/engine/handoff.py`）实现会话内 Agent 切换。

**检测**（`detect()`）：扫描 `state["tool_results"]`，查找 `{"handoff": true}` 信号。支持格式：`ToolResult.data` 嵌套、`FunctionBackend` 的 `{"result": {...}}` 包装、扁平 dict。

**Forward 流程**（`GraphEngine._execute_handoff()`，`arf/engine/graph.py`）：
1. 保存当前 Agent 状态到 `StateStore`（key: `{session_id}/{from_agent}`）
2. `HandoffManager.resolve()` 解析目标——单规则直接匹配，多规则 LLM 分类（fallback: 关键词匹配）
3. `RoundManager.record_handoff()` 记录切换（不创建新 checkpoint）
4. 加载目标 Agent 持久化状态（存在则恢复），否则 `HandoffManager.build_target_context()` 构建初始上下文
5. 上下文构建：截取最近 N 轮对话（默认 `raw_turns: 5`，可配置；过滤掉 tool_calls 和 tool 消息，只保留 user + 纯 assistant 消息）+ 可选 task_summary（system model 生成）
6. 重置 `current_turn = 0`，清除 `tool_results`，设置 `active_agent`
7. Emit `AgentEvent(type="agent_switch")` + log

**Return 流程**（`_restore_from_handoff()`，`arf/engine/graph.py`）：
1. 提取子 Agent 最后一条 assistant 消息作为结果
2. 保存子 Agent 最终状态
3. 从 StateStore 恢复主 Agent 消息历史
4. 替换原始 handoff 工具结果（role: "tool"）为子 Agent 的响应文本
5. 清除 `handoff_task` 字段，调用 `_close_tool_calls()` 保证消息完整性

### 2.11 持久化与回滚

ARF 有两套独立的持久化机制：Round检查点（undo 回滚）和 State快照（崩溃恢复），服务不同目的：

#### StateStore — State快照（崩溃恢复）

`arf/engine/checkpoint.py`，在引擎循环内多处调用 `put()`：

- `FileStateStore`：写入 `<state_dir>/<session_id>.json`，原子写入（tmp 文件 + rename）。**`tool_results` 不持久化**——`FileStateStore.put()` 调用 `data.pop("tool_results", None)`，因为工具结果是瞬态的。注意 `InMemoryStateStore` 不执行此过滤（测试 double，保留完整状态）。
- `InMemoryStateStore`：dict + deepcopy，用于测试。`snapshots` 列表记录每次 `put()` 调用。
- 用途：进程重启后从磁盘恢复会话状态。`_close_tool_calls()` 在 `invoke()/astream()` 入口保证消息序列完整性。
- 频率：每个 turn 至少一次（text-only）；有工具执行的 turn 两次（装配 assistant+tool_calls 后 + 工具执行完毕后）。

#### RoundManager — Round检查点（undo 回滚）

`arf/engine/round_manager.py`，在 `BaseAgent.chat()/astream()` 入口调用 `begin_round()`：

- `begin_round()`：深拷贝 AgentState + 快照 workspace 文件到 `memory/checkpoints/{round_num}/`。**每 round 一次**。
- `record_handoff()`：记录切换不创建新Round检查点——同一 round 内多次 handoff，undo 一次回退整个 round
- `undo(steps)`：pop N 个 round，返回 oldest popped 的 state_snapshot，恢复 workspace 文件
- 持久化：`rounds.json` 索引 + 每个检查点的 `state.json`。`_restore_from_disk()` 在 RoundManager 构造时重放，**支持跨进程重启后 undo**
- `max_undo_depth`：默认 3，deque 自动淘汰最旧 round

`GraphEngine.undo()`（`arf/engine/graph.py`）封装 RoundManager.undo，额外 emit `undo_executed` 事件。

#### 两者关系

| | StateStore（State快照） | RoundManager（Round检查点） |
|------|---------|------|
| 触发时机 | 引擎循环内多处 | 每个 `chat()/astream()` 入口 |
| 粒度 | 每 turn 多次 | **每 round 一次** |
| 持久化内容 | 会话状态（不含 tool_results） | 状态深拷贝 + 工作区文件快照 |
| 用途 | 崩溃恢复 | undo 回滚 |
| 恢复路径 | `StateStore.get()` + `_close_tool_calls()` | `RoundManager.undo()` |

两者独立运作：undo 回到 round 起点（从 RoundManager 快照恢复状态和文件），崩溃恢复从上次 `StateStore.put()` 的磁盘状态继续会话。

### 2.12 Hook 系统

`SubprocessHookRunner`（`arf/hooks/runner.py`）执行生命周期钩子。8 种事件类型（`arf/core/config_base.py`）：

| Hook 事件 | 触发位置 | Payload |
|-----------|---------|---------|
| `session_start` | `BaseAgent.chat()/astream()` | `{"session_id": ...}` |
| `round_start` | `BaseAgent.chat()/astream()` | `{"session_id": ..., "round": ...}` |
| `pre_model_call` | `GraphEngine.invoke()/astream()` | `{"messages": ...}` |
| `post_model_call` | `GraphEngine.invoke()/astream()` | `{"response": ...}` |
| `pre_tool_exec` | `GraphEngine.invoke()/astream()` | `{"tool_calls": ..., "turn": ...}` |
| `post_tool_exec` | `GraphEngine.invoke()/astream()` | `{"tool_calls": ..., "results": ..., "turn": ...}` |
| `round_end` | `GraphEngine.invoke()/astream()`（循环退出后） | `{"session_id": ..., "round": ...}` |
| `session_end` | `BaseAgent.stop()` 或 crash recovery 路径 | `{"session_id": ..., "reason": ...}` |

Hook 行为：
- 同事件类型的 hook **并行执行**（`asyncio.gather`）
- 超时杀死子进程，返回 `exit_code=-1`
- 退出码 2 + stdout 内容 → `injected_message`，引擎调用 `_inject_hook_messages()` 将 `[Hook: name] msg` 作为 system 消息注入 state
- 单 hook 失败不影响其他 hook
- `set_order()` 控制同事件类型的执行顺序
- Env var 模板：`$ARF_{CONTEXT_KEY}` 自动替换

### 2.13 EventBus 事件目录

引擎在执行过程中 emit 以下 `AgentEvent`（定义在 `arf/core/events.py`）：

| 事件 | 含义 | Emit 时机 |
|------|------|----------|
| `session_start` | Session 进入循环（EventBus 层面） | invoke/astream 入口 |
| `session_end` | Session 循环退出 | invoke/astream 出口 / cancel |
| `user_input` | 本 turn 的用户消息 | 每 turn 开始 |
| `model_call_start` | 模型调用开始 | 调用前（含 fallback_from 字段） |
| `model_call_end` | 模型调用结束 | 调用后（含 usage/content） |
| `thinking_delta` | 流式 token | astream 每个 chunk |
| `tool_call_start` | 工具执行开始 | pre_tool_exec hook 后 |
| `tool_call_end` | 工具执行完成 | 含 success/duration/result/rolled_back |
| `compaction_start/end` | 压缩执行 | 压缩前后 |
| `approval_required` | 需要用户审批 | 工具 perm=="ask" 时 |
| `approval_resolved` | 审批完成 | approved/denied/timeout |
| `agent_switch` | Agent 切换 | handoff 执行 |
| `guard_block` | 工具被守卫阻止 | pipeline/path/permission/approval |
| `guard_pass` | 工具通过守卫 | 所有检查通过 |
| `hook_start/end` | Hook 执行 | 每个 hook 事件类型前后 |
| `undo_executed` | Undo 完成 | `GraphEngine.undo()` |
| `rollback_executed` | 工具回滚完成 | 工具执行后（如有回滚） |
| `error` | 错误 | streaming 错误、handoff 失败 |
| `rate_limited` | 限流触发 | `ModelCallProtector` |
| `circuit_opened/half_open/closed` | 断路器状态转移 | `ModelCallProtector` |
| `breaker_blocked` | 断路器拦截请求 | `ModelCallProtector` |

### 2.14 配置

```yaml
# agent.yaml — Agent 执行相关字段
models:
  - type: quick
    model: deepseek-v4-flash
    context_window: 800000
  - type: deep
    model: deepseek-v4-pro
    context_window: 1000000

advanced:
  loop_strategy: react        # 仅 "react" 实现（plan_execute 未实现）
  max_turns: 50               # 每轮断路器
  max_undo_depth: 3           # undo 检查点窗口大小
  concurrency:
    strategy: parallel         # parallel | sequential
    max_concurrency: 5

# 多 Agent 配置（可选）
agents:
  - name: agent_b
    role: 工作 Agent
    task: 资源创建、模型配置、工具/技能生成

handover:
  rules:
    - from_agent: agent_a
      to_agent: agent_b
      trigger: "创建工具 生成技能 配置模型"
      context:
        raw_turns: 4
        task_summary: true
    - from_agent: agent_b
      to_agent: agent_a
      trigger: "完成 返回"
      context:
        raw_turns: 4
        task_summary: true
```

---

## 3. 演进方向

以下为已识别但**尚未实现**的方向。按优先级排列，部分已有协议定义或插件骨架但未集成到引擎主循环。

### 3.1 plan-execute 循环策略

**状态**：`Planner` 协议已定义（`arf/core/protocols/engine.py`），`arf/plugins/planner/` 目录存在但只含 `skills/` 和 `tools/` 骨架，无 Python 代码。`PlanExecuteStrategy` 未实现，`agent.yaml` 中 `loop_strategy: plan_execute` 选项不存在。

实现要点：
- Plan 阶段：system model 生成步骤化执行计划
- Execute 阶段：按计划推进，每步检查偏离（divergence detection）
- Replan 阈值：偏离超阈值时重新 plan

### 3.2 多 Agent DAG 编排

**状态**：未实现。当前 handoff 是链式的（主 Agent ↔ 子 Agent）。

目标：主 Agent 分解任务后同时 handoff 到多个子 Agent（fork），子 Agent 并行执行，主 Agent 收集结果（waitpid），合并后继续。

### 3.3 抢占式中断

**状态**：未实现。当前取消在循环边界响应（`_cancelled()` 在 while 循环开始处检测）。若模型调用耗时长，用户需等待当前 turn 完成。

目标：收到取消信号后立即中止 HTTP 请求（`httpx` / `openai` 支持 `cancel()`），不等完整响应。

### 3.4 暂停/恢复/检查点

**状态**：目前是round级别的检查点。当前round过程中某个turn的取消是"终止型"的——只能从最近的round检查点开始重新会话，本round内已执行的操作会被回滚（丢失，但是保证了round级别的事务一致性）。

可能方向：在当前循环边界安全停止，完整序列化 engine 状态（含 pending approvals、active pipelines、handoff 中间态），支持turn级别的更细粒度恢复。需要这么细节的回滚吗？空间换时间需要找到平衡点

### 3.6 循环控制抽象的更多默认实现：should_continue / should_break

**状态**：`should_continue(state) → bool`（入口）和 `should_break(state) → bool`（末尾）两个协议方法，统一由 `LoopStrategy` 实现，从当前活跃 Agent 的配置动态取值，当前仅监控 turn 计数。
目标：扩展更多循环控制的默认实现——token 预算、时间预算、工具调用次数上限等。
