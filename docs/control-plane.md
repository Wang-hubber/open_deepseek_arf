# ARF Control Plane Architecture

> 完整控制平面：从 Agent 初始化到每次 invoke 的控制流全景，涵盖组装、调度、Hook、中断全部环节。

## 1. 架构分层

控制平面分为四层：

| 层 | 文件 | 职责 |
|----|------|------|
| **组装层** | `arf/agent/base.py`, `arf/agent/config.py` | DI 装配全部 Protocol 实现，`agent.yaml` → 运行实例 |
| **引擎层** | `arf/engine/graph.py`, `arf/engine/round_manager.py`, `arf/engine/tool_executor.py` | invoke/astream 主循环，状态机转换 |
| **横切层** | `arf/hooks/`, `arf/guardrails/`, `arf/errors/`, `arf/protection/`, `arf/compaction/`, `arf/memory/` | Hook 生命周期、安全守卫、错误恢复、API 保护、窗口压缩 |
| **传输层** | `arf/observability/`, `arf/event_bus.py`, `arf/streaming/` | 事件总线、追踪、用量统计、SSE 流 |

---

## 2. 完整控制流转图

### 2.1 入口: BaseAgent.chat() / astream()

```
BaseAgent.chat(user_message, session_id)
  (arf/agent/base.py)

  1. FileStateStore.get(session_id)  → 加载已有 state 或创建新的
  2. 崩溃恢复: 扫描磁盘上未跟踪的活跃 state
  3. 构建 AgentState:
     {
       session_id, agent_name, messages: [...user msg...],
       current_model: <首个模型>, current_turn: 0,
       interaction_round: 0, metadata: {}
     }
  4. RoundManager.begin_round(state) → deepcopy 快照到 checkpoints/{round}/
  5. Hook: session_start  (如果是新会话)
  6. Hook: round_start
  7. engine.invoke(state) 或 engine.astream(state)
```

### 2.2 引擎主循环: GraphEngine._execute()

```
GraphEngine._execute(state)
  (arf/engine/graph.py:1585)

  _close_tool_calls(state) → 消息序列修复
  interaction_round += 1
  yield session_start event

  ╔══════════════════════════════════════════╗
  ║  while ReActStrategy.should_continue:   ║
  ║    ↓                                     ║
  ║  check _cancelled() ────→ break (取消)   ║
  ║    ↓                                     ║
  ║  next_step(state):                       ║
  ║    · 消息为空 → "call_model"              ║
  ║    · 最后是 user/system → "call_model"   ║
  ║    · 最后是 assistant+tool_calls         ║
  ║      → "execute_tools"                   ║
  ║    · 最后是 tool → "call_model"          ║
  ║    ↓                                     ║
  ║  if "call_model":                        ║
  ║    _step_call_model(state) ───┐          ║
  ║  if "execute_tools":           │          ║
  ║    _step_execute_tools(state) ─┘          ║
  ║    ↓                                     ║
  ║  if should_break(): break                ║
  ╚══════════════════════════════════════════╝

  Hook: round_end (hook_runner + plugin_runner)
  yield session_end event
  StateStore.put() → 最终持久化
```

### 2.3 分支 A: _step_call_model(state)

```
_step_call_model(state)  (graph.py:1080-1350)

1. yield user_input event
2. ModelRouter.route(query, history) → 快慢模型选择
   (router 为 None 时直接使用 state["current_model"])
3. Compaction.should_compact(state)
   ├─ 检查 _compaction_cooldown
   ├─ 检查 last_token_usage > threshold * window_size
   └─ compact():
       分割消息 → 旧消息归档到 context_summary
       保留最近 N 条 → yield compaction_start/end
4. ToolResolver.get_tool_definitions() → MCP 查询可用工具
5. System Prompt 组装:
   ├─ Prefix (role + critical_rules) → 稳定前缀, 利于 prompt cache
   ├─ $INVENTORY → MCP 工具清单替换
   ├─ $MEMORY → memory.md 内容替换
   ├─ $WORKSPACE → 工作区文件列表替换
   └─ $TURN_BUDGET → 剩余轮次数替换
6. _repair_messages(state):
   ├─ 移除 system 角色消息 (放入 system_prompt)
   ├─ 确保以 user 开头
   ├─ 移除非法角色
   ├─ 补齐缺失的 tool 消息 (注入合成结果)
   └─ 移除孤立 tool 消息 (无匹配 assistant.tool_calls)

   ┌─────────────────────────────────────────┐
   │ Hook: pre_model_call                    │
   │  context: {messages, model,             │
   │           messages_count, session_id}   │
   │  消费者: 提示词注入, 消息拦截           │
   └─────────────────────────────────────────┘

7. Model Call:
   stream 路径:
   ├─ adapter.chat_stream_full() → AsyncGenerator
   ├─ 处理 text / thinking / tool_call_chunk / tool_call / usage / error
   ├─ 400 错误 → _try_repair_400() → 消息修复后重试
   └─ 其他错误 → _choose_recovery()
   non-stream 路径 (fallback):
   ├─ adapter.chat_complete() → 完整响应
   └─ 解析 content, tool_calls, usage, reasoning, finish_reason

8. 错误恢复 _choose_recovery(stop_reason, error_text) → RecoveryDecision:
   ├─ finish_reason == "length" → "continue" (追加续写消息)
   ├─ "prompt too long" / "context exceeded" → "compact"
   ├─ "timeout" / "rate" / "unavailable" / "connection" → "backoff"
   └─ 其他 → "fail"
   _apply_recovery(decision):
   ├─ 管理独立预算: continuation_attempts / compact_attempts / transport_attempts
   ├─ continue: 追加 "Continue..." 消息, 下一轮模型继续
   ├─ compact: 触发压缩, 修复消息后重试
   └─ backoff: 指数退避 + 抖动, 预算耗尽抛 RuntimeError

9. 模型回退 _resolve_fallback(model_name, exc):
   ErrorPolicy.on_model_error() → 如果是 5xx + 配置 "fallback"
   → ModelRouter.fallback_from(model_name)

   ┌─────────────────────────────────────────┐
   │ Hook: post_model_call                   │
   │  context: {response: {...}}             │
   │  消费者: 响应日志, 内容过滤             │
   └─────────────────────────────────────────┘

10. 结果处理:
    ├─ 有 tool_calls:
    │   追加 assistant msg (含 tool_calls) 到 state["messages"]
    │   设置 state["_pending_tool_calls"]
    │   设置 state["last_token_usage"]
    │   循环继续 → next_step 返回 "execute_tools"
    └─ 纯文本:
        追加 assistant msg
        StateStore.put() → 持久化
        break (本轮结束)
```

### 2.4 分支 B: _step_execute_tools(state)

```
_step_execute_tools(state)  (graph.py:1355-1580)

1. 取出 state["_pending_tool_calls"]

2. _step_classify_tool_calls() → 统一的守卫流水线:
   ├─ Pipeline 依赖检查: 检查 active_pipeline 中的步骤依赖
   ├─ SessionModeManager.resolve(agent_policy):
   │   AUTO → allow
   │   PLAN → deny (副作用工具) / allow (只读工具)
   │   ASK → PermissionRegistry.evaluate(tool_name, params)
   │         ├─ deny → denied_calls
   │         ├─ ask → 进入审批流
   │         └─ allow → valid_calls
   └─ 审批流 (ASK 模式):
       ├─ 工具在 approval_allowlist → 自动通过
       ├─ streaming 模式:
       │   emit approval_required 事件
       │   注册到 pending_approvals 列表
       │   → _wait_approvals() 异步等待
       └─ invoke 模式:
           emit approval_required 事件
           → await asyncio.Event.wait(timeout=60s)
           外部通过 engine.approve(decision_id, bool) 解析

3. Emit: guard_block / guard_pass / approval_required 事件

   ┌─────────────────────────────────────────┐
   │ Hook: post_permission                   │
   │  context: {tool_calls, denied,          │
   │           session_id, turn}             │
   │  ← HumanLoop 插件挂载点                 │
   └─────────────────────────────────────────┘

   ┌─────────────────────────────────────────┐
   │ Hook: pre_tool_exec                     │
   │  context: {tool_calls, turn}            │
   │  消费者: 工具调用拦截, 参数修改         │
   └─────────────────────────────────────────┘

4. ConcurrentToolExecutor.execute(tool_calls):
   _check_params(tool_name, params) 对每个调用:
   ├─ DirectoryBoundary 解析:
   │   whitelist → 工具专属边界
   │   sandbox → SandboxManager 沙箱路径
   │   default → 默认工作区边界
   ├─ PathCheckToolGuard.check() (6 项检查, 最先失败获胜):
   │   1. 路径穿越 (.. 检测)
   │   2. 绝对路径 (/ 开头)
   │   3. 路径深度超配额
   │   4. 路径数量超配额
   │   5. 符号链接穿越
   │   6. 边界包含 (白名单验证)
   └─ ContentGuard.check_dangerous():
       检测: 管道到shell, eval, 递归根删除等危险模式
   → 被阻止 → ToolResult(blocked=True)
   → 安全 → 执行工具
   执行策略:
   ├─ sequential: 逐个执行, 注入 _agent_mode/_engine/_state_store/_workspace
   └─ parallel: asyncio.gather + Semaphore(max_concurrency)

5. 工具结果追加到 state["messages"]
6. Compaction.summarize_tool_output() (可选, >2000字符时)

   ┌─────────────────────────────────────────┐
   │ Hook: post_tool_exec                    │
   │  context: {tool_calls, results, turn}   │
   │  消费者: 结果后处理, 数据管道           │
   └─────────────────────────────────────────┘

   ┌─────────────────────────────────────────┐
   │ Hook: sandbox_persist                   │
   │  context: {session_id, turn}            │
   │  ← UNDO / Checkpoint 插件挂载点         │
   └─────────────────────────────────────────┘

7. ErrorPolicy 断路器:
   连续 _tool_failures >= 5 → abort session

8. _reset_recovery_state() → 清空恢复计数器
```

### 2.5 中断与恢复

```
取消机制:
  engine._cancelled() 检查 asyncio.Event.is_set()
  └─ 在主循环每次迭代检查 → break → emit session_end + 保存状态

轮次级撤销:
  GraphEngine.undo(steps=1)
  └─ RoundManager.undo(steps):
       1. 从 deque 弹出 N 个 RoundTransaction
       2. 恢复最旧弹出轮次的工作区文件 (从 checkpoints/{round}/)
       3. 返回 deepcopy 的 AgentState
       4. emit undo_executed 事件
  RoundManager 参数: max_undo_depth=3 (可配置)
  持久化: data/checkpoints/rounds.json + state.json

工具级回滚:
  ActionRunner 执行 → 工具失败:
  └─ RollbackManager.handle(failed, remaining):
       1. 回滚失败的可执行单元 (尽最大努力)
       2. 取消依赖它的下游单元
       3. 兄弟单元不受影响
       4. emit rollback_executed 事件
```

### 2.6 Assembly 层: BaseAgent 装配顺序

```
BaseAgent.__init__(AgentConfig, AppContext, **override_protocols)

  1. EventBus          → InMemoryEventBus (pub-sub, 容量 1000)
  2. StateStore        → FileStateStore (原子写入, tmp+rename)
  3. MCP 资源管理      → McpClientManager(tools/skills/models/plugins dirs)
  4. PluginProvider    → 加载 hook-only plugins
  5. FileWatcher       → 可选, 文件变更检测 + 热加载
  6. Memory            → FileMemoryStore + 可选 writer/retriever
  7. PluginRuntime     → 进程内 plugin 运行时上下文
  8. Guardrails:
     ├─ InputGuard     → NoneInputGuard (透传)
     ├─ OutputGuard    → RegexOutputGuard (敏感信息清洗)
     ├─ ToolGuard      → PathCheckToolGuard (路径沙箱)
     ├─ ContentGuard   → 危险行为 + 敏感信息检测
     └─ SandboxManager → 会话隔离沙箱
  9. SessionMode       → SessionModeManager(global_mode)
  10. PermissionLists  → 从 perm_cfg 构建
  11. GuardRunner      → DefaultGuardRunner 装配上述守卫
  12. ErrorPolicy       → DefaultErrorPolicy(tool_retry, backoff, ...)
  13. Hooks            → SubprocessHookRunner(config_hooks + plugin_hooks)
  14. ToolExecutor     → ConcurrentToolExecutor(strategy, max_concurrency)
  15. LoopStrategy     → ReActStrategy(max_turns)
  16. SystemPrompt     → DefaultSystemPromptProvider.build()
  17. $INVENTORY       → 从 MCP 工具清单填充
  18. $MEMORY          → 从 memory.md 加载
  19. Model Calls      → _inject_model_calls(config):
       ├─ ModelRegistry → ModelAdapter 列表
       ├─ ModelDegrader → 有序降级包装
       └─ ModelCallProtector → TokenBucket + CircuitBreaker 包装
      → set_call_model / set_stream_model 注入引擎
```

---

## 3. 结构化 State 数据

### 3.1 AgentState (TypedDict, total=False)

**定义位置:** `arf/core/state.py`

#### 公开字段

| 字段 | 类型 | 设置者 | 说明 |
|------|------|--------|------|
| `session_id` | `str` | BaseAgent.chat() | 会话唯一标识符 |
| `agent_name` | `str` | BaseAgent.chat() | 当前活跃 Agent 名称 |
| `messages` | `list[dict]` | 引擎每步追加 | OpenAI 格式消息列表 (role+content±tool_calls) |
| `current_model` | `str` | 初始化 + Router | 当前使用的模型名称 |
| `current_turn` | `int` | 引擎循环 | 当前轮次计数 (每次 model call 递增) |
| `interaction_round` | `int` | 引擎循环 | 单调递增的交互轮次 (可能跨 Agent) |
| `context_summary` | `str` | Compaction | 被压缩掉的历史消息摘要 |
| `tool_results` | `dict[str, dict]` | 工具执行后 | tool_call_id → ToolResult 映射 |
| `plan` | `dict \| None` | Planner | 执行计划 (reserved) |
| `metadata` | `dict` | App/用户 | 自由扩展元数据 |

#### 引擎内部字段 (动态注入)

| 字段 | 类型 | 设置位置 | 说明 |
|------|------|----------|------|
| `last_token_usage` | `int` | `_step_call_model:1278` | API 返回的 total_tokens, 压缩判断阈值 |
| `_compaction_cooldown` | `int` | 压缩系统 | 递减计数器 (初始2), 防止连续触发压缩 |
| `_recovery_state` | `dict` | `_apply_recovery` | `{continuation_attempts, compact_attempts, transport_attempts}` |
| `_pending_tool_calls` | `list[dict]` | `_step_call_model` | 跨步骤暂存: 待执行的 tool_calls |
| `active_pipeline` | `dict` | Skill 系统 | `{step_name: status}`, 流水线依赖跟踪 |
| `session_active` | `bool` | BaseAgent | 会话是否活跃 |
| `_tool_failures` | `int` | `_step_execute_tools` | 连续工具失败计数 (断路器, 阈值5) |
| `_retry_call_model` | `bool` | `_step_call_model` | 400 修复后重试标记 |
| `_retry_after_repair` | `bool` | `_try_repair_400` | 消息修复后重试标记 |

### 3.2 关联数据结构

#### RoundTransaction (轮次检查点)
```
round_id: str           # UUID
round_num: int          # 轮次编号
state_snapshot: AgentState  # deepcopy 的完整状态
workspace_snapshot_dir: str  # data/checkpoints/{round_num}/
created_at: float       # 时间戳
agent_trace: list[str]  # 经过的 agent 名称列表
closed: bool            # 是否已关闭
```

#### TurnContext (传递给审批系统)
```
session_id: str
agent_name: str
turn: int
current_model: str
available_models: list[str]
last_user_message: str
last_tool_calls: list[dict]  # [{name, params}]
```

#### RecoveryDecision (恢复决策)
```
kind: "continue" | "compact" | "backoff" | "fail"
reason: str
```

### 3.3 State 持久化与生命周期

```
创建:
  BaseAgent.chat() → 新 AgentState 或 FileStateStore.get(session_id)

快照:
  RoundManager.begin_round() → deepcopy → data/checkpoints/{round}/state.json

更新:
  引擎循环中每一处 state[key] = value (就地修改)
  + StateStore.put(session_id, state) 在关键检查点:
    - 纯文本响应后
    - 工具执行完成后
    - 异常/取消时

清理:
  StateStore.put() 自动 strip "tool_results" 键 (瞬态数据不持久化)

恢复:
  引擎启动时 RoundManager 从 data/checkpoints/rounds.json 重建滚动窗口
  崩溃恢复: BaseAgent 扫描 data/state/ 找活跃但未跟踪的 session
```

---

## 4. Hook 节点全景

### 4.1 9 个生命周期 Hook (按执行顺序)

| # | Hook 类型 | 触发文件:行号 | 上下文数据 | 运行器 | 设计用途 |
|---|-----------|---------------|-----------|--------|----------|
| 1 | `session_start` | `agent/base.py:779,861` | `{session_id}` | Subprocess | 会话初始化 (日志、资源分配) |
| 2 | `round_start` | `agent/base.py:784,866` | `{session_id, round}` | Subprocess | 轮次初始化 |
| 3 | `pre_model_call` | `engine/graph.py:1153` | `{messages, model, messages_count, session_id}` | Subprocess | 模型路由、提示词注入、消息拦截 |
| 4 | `post_model_call` | `engine/graph.py:1303` | `{response: {...}}` | Subprocess | 响应处理、内容过滤、日志 |
| 5 | `post_permission` | `engine/graph.py:1384,1396` | `{tool_calls, denied, session_id, turn}` | **Subprocess + InProcess** | **HumanLoop 挂载点** (审批集成) |
| 6 | `pre_tool_exec` | `engine/graph.py:1406` | `{tool_calls, turn}` | Subprocess | 工具调用拦截、参数修改 |
| 7 | `post_tool_exec` | `engine/graph.py:1491` | `{tool_calls, results, turn}` | Subprocess | 结果后处理、数据管道 |
| 8 | `sandbox_persist` | `engine/graph.py:1511,1517` | `{session_id, turn}` | **Subprocess + InProcess** | **UNDO/Checkpoint 挂载点** |
| 9 | `round_end` | `engine/graph.py:1559,1631,1637` | `{session_id, round}` | **Subprocess + InProcess** | **Memory 提取/Compaction 挂载点** |

| 0 | `session_end` | `agent/base.py:705,728,810` | `{session_id, reason}` | Subprocess | 关闭/崩溃恢复/取消清理 |

### 4.2 双层 Hook 运行器架构

```
SubprocessHookRunner (arf/hooks/runner.py)
  ├─ 注册: 构造时按 HookDefinition.type 索引
  ├─ 执行: 每个 hook 的 run 命令列表 → asyncio.create_subprocess_shell
  ├─ 并行: 所有同类型 hook 通过 asyncio.gather 并行执行
  ├─ 链式: 命令列表中前一个成功才继续 (exit=0继续, !=0停止)
  ├─ 注入: exit=2 + stdout 非空 → HookResult.injected_message
  ├─ 超时: 默认 30s, 支持 s/m/h 后缀
  ├─ 环境变量:
  │   ARF_RUNTIME (完整运行时 JSON), ARF_SESSION_ID, ARF_ROUND,
  │   ARF_MEMORY_DIR, ARF_WORKSPACE, ARF_TRACE_DIR, ARF_SYSTEM_MODEL
  └─ 排序: set_order(event_type, hook_names) 控制执行顺序

InProcessHookRunner (arf/hooks/in_process_runner.py)
  ├─ 注册: register(plugin) 按 plugin.hooks 列表索引
  ├─ 执行: plugin.on_hook(event_type, PluginContext)
  ├─ 顺序: 按注册顺序调用
  └─ 隔离: 单个 plugin 异常被捕获抑制, 不中断其他
```

### 4.3 引擎中的双重触发

引擎同时持有两个运行器实例:

| 属性 | 类型 | 触发的 Hook 点 |
|------|------|---------------|
| `self.hook_runner` | SubprocessHookRunner | 全部 9 个点 |
| `self.plugin_runner` | InProcessHookRunner | `post_permission`, `sandbox_persist`, `round_end` (3 个额外触发) |

对于 `post_permission` 和 `sandbox_persist`, 两个运行器**先后触发**:
```
hook_runner.fire("post_permission", ...)  → 外部子进程
plugin_runner.fire("post_permission", ...) → 进程内 plugin
```

### 4.4 当前已实现的 Plugin 及其挂载点

| Plugin | 文件 | 挂载点 | 功能 |
|--------|------|--------|------|
| `CheckpointPlugin` | `arf/plugins/checkpoint/plugin.py` | `round_end`, `session_end` | 自动存档状态快照到 checkpoints 目录 |
| `CompactionPlugin` | `arf/plugins/compaction/plugin.py` | `round_end` | 轮次结束时压缩历史消息 |
| `MemoryPlugin` | `arf/plugins/memory/hooks/round_end.py` | `round_end` (每 N 轮) | LLM 提取记忆 → memory.md |
| `UNDO Plugin` | `arf/plugins/undo/` | `sandbox_persist` | 工具执行后保存工作区快照供撤销 |

### 4.5 AgentEvent 事件类型全集 (28)

**定义位置:** `arf/core/events.py`

```
Session:    session_start, session_end
User:       user_input
Model:      thinking_delta, model_call_start, model_call_end
Tool:       tool_call_start, tool_call_end, tool_call_result
Compaction: compaction_start, compaction_end
Approval:   approval_required, approval_resolved
Guards:     guard_block, guard_pass
Hooks:      hook_start, hook_end
Recovery:   undo_executed, rollback_executed
Protection: rate_limited, circuit_opened, circuit_half_open,
            circuit_closed, breaker_blocked
Plugins:    pre_model_call, post_permission, sandbox_persist
Error:      error
```

---

## 5. 关键文件索引

| 模块 | 文件路径 | 核心职责 |
|------|----------|----------|
| 引擎主循环 | `arf/engine/graph.py` | GraphEngine - 全部执行逻辑 |
| 轮次管理 | `arf/engine/round_manager.py` | RoundManager - 检查点/撤销 |
| 工具执行 | `arf/engine/tool_executor.py` | ConcurrentToolExecutor |
| Agent 组装 | `arf/agent/base.py` | BaseAgent - DI 装配 + 公共 API |
| 配置模型 | `arf/agent/config.py` | AgentConfig + AdvancedConfig |
| State 定义 | `arf/core/state.py` | AgentState + TurnContext |
| 事件类型 | `arf/core/events.py` | AgentEvent + EventType |
| 结果类型 | `arf/core/results.py` | GuardResult, ToolResult, HookResult |
| 配置基类 | `arf/core/config_base.py` | 全部子配置 Pydantic 模型 |
| Hook 运行器 | `arf/hooks/runner.py` | SubprocessHookRunner |
| 进程内 Hook | `arf/hooks/in_process_runner.py` | InProcessHookRunner |
| 守卫运行器 | `arf/guardrails/runner.py` | DefaultGuardRunner |
| 路径守卫 | `arf/guardrails/path_check.py` | PathCheckToolGuard |
| 内容守卫 | `arf/guardrails/content_guard.py` | ContentGuard |
| 错误策略 | `arf/errors/retry.py` | DefaultErrorPolicy |
| 模型适配器 | `arf/core/model_adapter.py` | ModelAdapter |
| 模型降级 | `arf/core/model_degrader.py` | ModelDegrader |
| API 保护 | `arf/protection/protector.py` | ModelCallProtector |
| 压缩 | `arf/compaction/sliding_window.py` | SlidingWindowCompactor |
| 内存存储 | `arf/memory/file_store.py` | FileMemoryStore |
| 事件总线 | `arf/event_bus.py` | InMemoryEventBus |
| 可观测性 | `arf/observability/file_trace.py` | FileTraceStore |
| 用量追踪 | `arf/observability/usage_tracker.py` | UsageTracker |
| 沙箱管理 | `arf/sandbox/sandbox_manager.py` | SandboxManager |
| 目录边界 | `arf/sandbox/directory_boundary.py` | DirectoryBoundary |
| 会话模式 | `arf/session/mode_manager.py` | SessionModeManager |
| 权限注册 | `arf/session/permissions.py` | PermissionRegistry |
| App 上下文 | `arf/agent/app_context.py` | AppContext - 路径推导 |
| 回滚管理 | `arf/action_runner/rollback.py` | RollbackManager |
| 审批 | `arf/human_loop/approval_points.py` | ToolNameAllowlist |
| 循环策略 | `arf/engine/loop_strategies/react.py` | ReActStrategy |
| 状态存储 | `arf/engine/checkpoint.py` | FileStateStore, InMemoryStateStore |

---

## 6. 设计约束与演进方向

### 当前约束
- State 是可变 dict (非不可变), 就地修改, 无并发保护
- TwoTierRouter 已移除, 路由现由 ModelDegrader 降级链替代
- HandoffManager (多 Agent 切换) 已移除, `arf/engine/handoff.py` 已删除
- `arf/concurrency/` (SequentialScheduler) 和 `arf/skills/` (SkillPipeline) 已删除
- `arf/communication/` 的协议 (AgentBus, PeerAgent, Supervisor 等) 仅有接口定义, 无实现
- ApprovalPoint/ApprovalChannel 协议存在但 HumanLoop 未通过 Hook 系统连接 (集成在引擎内联)

### 演进方向
- State 不可变性 + 并发安全 (多 Agent 并行时需要)
- SkillPipeline 重新引入 (通过 MCP 资源系统)
- A2A 通讯协议实现 (InMemoryAgentBus → 可能 gRPC/Redis)
- HumanLoop 插件化 (通过 `post_permission` Hook 替代内联)
- 压缩与内存提取统一到 Plugin 层 (减少引擎内联逻辑)
