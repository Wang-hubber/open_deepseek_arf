# Park/Resume — 等待与唤醒机制

ARF 提供通用的 Park/Resume 原语，允许 Agent 在任意生命周期节点注册等待条件，由外部事件驱动唤醒。HITL、Subagent、Peer Agent 三种模式共享同一套机制。

## OS 类比：进程阻塞原语

```
操作系统                           ARF
─────────                         ───
进程状态 (READY/BLOCKED)   →      harness park / wakeup
wait_queue                 →      state.waiting[hook_name]
wake_up(event)             →      resolve_wait(wait_id)
信号量 / futex             →      asyncio.Event (park_event)

关键区别:
- OS 进程被内核阻塞，不消耗 CPU → Agent harness 被 asyncio.Event 阻塞，不占用 worker
- OS 信号量支持多个等待者 → ARF 支持同一 checkpoint 上多个 wait
- OS wake_up 唤醒单个/全部 → ARF resolve_wait 唤醒一个 + 允许部分唤醒
```

## 核心概念

### WaitItem

```python
@dataclass
class WaitItem:
    wait_id: str        # 唯一标识
    hook_name: str      # 目标 checkpoint，如 "before_round"
    reason: str         # 人类可读的原因，如 "hitl"、"subagent:task_1"
    created_at: float   # 创建时间戳
    resume_key: str     # 会话恢复标识（非空时插件需重建后台监听）
```

### 三个原语

| 原语 | 位置 | 作用 | 调用者 |
|------|------|------|--------|
| `agent.wait(hook_name, reason)` | agent | 注册等待项，同步返回 WaitItem | 工具函数、插件 |
| `agent.finish_wait(wait_id)` | agent | 移除等待项 | harness.resolve_wait 内部调用 |
| `harness.resolve_wait(wait_id, inject_message?)` | harness | 完成等待 + 可选注入消息 + 唤醒 harness | 外部事件源（CLI、runner、bus） |

### 两个框架注入

Engine 在 `before_tools` 对所有工具注入两个闭包，让任意工具都能注册等待和发出事件，无需直接持有 agent/harness 引用：

```python
# engine.py: 对所有 pending_tool_calls 注入
tc["params"]["_register_wait"] = lambda hook, reason, rk="": agent.wait(hook, reason, resume_key=rk)
tc["params"]["_emit"]            = ctx.emit
```

工具只需声明 `_register_wait=None, _emit=None` 参数即可接收。

## 流程图

```
Round N:
  ┌─────────────────────────────────────────────────────────┐
  │ turn K: model_call                                       │
  │   → tool_calls [ask_user / delegate_task / send_peer]    │
  │   → _register_wait("before_round", reason, resume_key)  │ ← 注册 wait
  │   → engine 检测 state.waiting 非空                       │
  │   → _round_restart = True, break turn loop              │ ← 立即结束 round
  └─────────────────────────────────────────────────────────┘
  │                                                          │
  │   after_round → round_end → _round_restart → continue    │
  │                                                          │
  ▼                                                          │
Round N+1 (新 round):                                        │
  ┌── before_round ─────────────────────────────────────────┐
  │ → state.waiting["before_round"] 有 wait                  │
  │ → _messages_injected == False → park                     │
  │   harness._do_park(): _park_event.wait()                 │ ← 阻塞
  │                                                          │
  │   ... 外部事件到达 ...                                    │
  │   resolve_wait(wait_id, inject_message=msg)               │
  │     → agent.input(msg)                                   │
  │     → agent.finish_wait(wait_id)                         │
  │     → _messages_injected = True                          │
  │     → _parked = False, _park_event.set()                 │ → 唤醒
  │                                                          │
  │ 回到 before_round checkpoint:                             │
  │   → 如果还有 wait 且 _messages_injected:                  │
  │       → 清 _messages_injected，不 park，进入 round        │
  │       → model 处理已注入的消息                             │
  │   → 如果还有 wait 且 NOT _messages_injected:              │
  │       → 重新 park（没有新消息，继续等）                     │
  │   → 如果没有 wait:                                       │
  │       → 进入 round（正常流程）                             │
  └──────────────────────────────────────────────────────────┘
```

**关键设计决策**：

- **立即 break round**。工具注册 wait 后，engine 检测 `state.waiting` 非空 → `_round_restart = True` + `break` turn loop。这防止模型在同一个 round 里继续调工具（如轮询 `queue_status`），强制进入 park。
- **只有注入消息时才跳过 park**。只有 `inject_message` 不为空时 `_messages_injected` 才为 True。这样确保了"没有任何新信息就不浪费 round"。
- **剩余 wait 在下一轮自动 park**。处理完 A 的结果后，下一轮 before_round 会检测到 B 的 wait 还在，自然 park。

## 用户打断非 HITL Park

当 harness 因 `delegate_task` 或 `send_peer_message` park 时（`reason="subagent:*"` / `reason="peer_wait:*"`），用户可以发送消息打断等待，立即得到回复：

```
Harness parked (delegate_task wait, before_round):

用户: "任务进展如何？"
  → 新 run() 存储用户消息
  → 检测 self._parked → _user_interrupted = True
  → before_round: has_waiting=True, 无 hitl wait, _user_interrupted=True
  → 跳过 park → 进入 round → 模型立即回复用户
  → round 结束 → 回到 before_round
  → _user_interrupted=False → PARK 继续等子Agent结果

子Agent 完成 → wake_parent → resolve_wait → resume → 模型看到结果
```

**规则**：
- **非 HITL wait**（`subagent:` / `peer_wait:`）→ 用户可打断，立刻得到回复
- **HITL wait**（`hitl`）→ 不可打断，必须用户明确回答（`provide_hitl_response`）
- 打断后 wait 不丢失，round 结束后自动重新 park

## 框架参数注入与过滤

Engine 在 `before_tools` 注入 `_register_wait` 和 `_emit` 到所有 `_pending_tool_calls`。这些参数需要两层过滤：

### 1. Trace 输出过滤

```python
# engine.py — tool_call_start 事件中过滤框架参数
_framework_params = {"_register_wait", "_emit"}
clean_args = {k: v for k, v in params.items() if k not in _framework_params}
```

### 2. 工具函数签名检查

`function_backend` 和 `plugin_provider` 在执行 `fn(**params)` 前检查函数签名，只向声明了 `_register_wait` / `_emit` 的工具（如 `delegate_task`、`ask_user`、`send_peer_message`）传递这些参数。其他工具（如 `queue_status`、`write_file`）不会收到：

```python
# plugin_provider.py — execute_plugin_tool 前检查签名
sig = inspect.signature(fn)
for reserved in ("_register_wait", "_emit"):
    if reserved not in sig.parameters:
        params.pop(reserved, None)
```

### 3. JSON 序列化保护

`tool_guard` 在 `json.dumps(params)` 时使用 `default=str` fallback，防止函数对象导致序列化崩溃。

## Multi-Wait：同时等多事件

```
Agent 同时调了 delegate_task(A) + send_peer_message(B):

Round 1:
  turn K: → 注册 wait_A, wait_B（都在 before_round）
         → 继续走完 round

Round 2:
  before_round → 2 个 wait → park
  A 先完成 → resolve_wait(wait_A, msg_A) → 唤醒
  before_round → 有 wait_B 但 _messages_injected=True
               → 清 flag，进入 round，model 处理 msg_A
  round_end

Round 3:
  before_round → 有 wait_B → park
  B 完成 → resolve_wait(wait_B, msg_B) → 唤醒
  before_round → 无 wait → 进入 round，model 处理 msg_B
```

## 会话持久化与恢复

等待状态通过 `_save_state` 持久化到 FileStateStore：

```python
# 持久化 waiting 结构
waiting_serialized = {
    "before_round": [
        {"wait_id": "...", "hook_name": "before_round", "reason": "peer_wait:abc",
         "created_at": 1700000000.0, "resume_key": "peer_wait:abc"}
    ]
}
```

会话恢复时 (`is_new_session=False`)：
1. 从 persisted state 重建 `state.waiting`
2. `_rebuild_wait_tasks(ctx)` 将有 `resume_key` 的 wait 写入 `ctx.hook_data["_pending_resume"]`
3. 触发 `session_start` checkpoint，插件检查 `_pending_resume` 重建后台监听
   - **Peer**：`_on_init` 为 `peer_wait:*` 重建 `_peer_wait_loop`
   - **Subagent**：`_on_init` 为 `subagent:*` 重建 `_parent_wait_ids`
   - **HITL**：`resume_key=""`，无需后台监听，直接 park 等 CLI 调用 `provide_hitl_response`
4. 下一轮 `before_round` 正常 park，等待外部事件

## 三种模式的使用方式

### HITL — 人工审批

```
ask_user 工具:
  wi = _register_wait("before_round", "hitl")
  _emit("need_human_input", {question, options, ...})
  return {ok: true, pending: true, wait_id: wi.wait_id}

CLI 收到 need_human_input → 展示 UI → 用户回答:
  harness.provide_hitl_response(session_id, answer)
    → 遍历 state.waiting["before_round"] 找 reason="hitl"
    → resolve_wait(wait_id, inject_message={role:"user", content:answer})
```

### Subagent — 父等子

```
delegate_task 工具:
  创建 runner → delegator.dispatch(parent_sid, task, runner)
  wi = _register_wait("before_round", f"subagent:{task_id}",
                       resume_key=f"subagent:{task_id}")
  return {ok: true, task_id, session_id, dispatched: true}
  → engine 检测 state.waiting 非空 → _round_restart, break turn loop
  → after_round → 回到 before_round → PARK

子任务完成:
  runner → delegator.complete(parent_sid, task_id, result)
  _wake_parent(registry, parent_sid)
    → delegator.get_pending(parent_sid) 取结果
    → 注入消息含: 截断内容(500字符) + file_changes + result_file 路径
    → result_file 在 data/{parent_sid}/subagent_results/task_N.md
    → resolve_wait(wait_id, inject_message=formatted_result)
    → 父 harness 唤醒，结果已在 messages 中

等待期间用户打断:
  → 新 run() 检测 self._parked → _user_interrupted = True
  → before_round 跳过 park（无 hitl wait）→ 模型立即回复用户
  → round 结束 → 重新 park 等子Agent
```

### Peer Agent — 对等通讯

```
send_peer_message 工具:
  构建 JRPC envelope → target_bus.send(msg)
  注册 pending_reply
  wi = _register_wait("before_round", f"peer_wait:{corr_id}",
                       resume_key=f"peer_wait:{corr_id}")
  return {ok: true, correlation_id, pending: true}

Peer 回复到达:
  _peer_wait_loop (后台 asyncio task, 30s 轮询)
    → bus.wait_for_message(inbox_key)
    → resolve_wait(wait_id, inject_message=packaged_messages)
    → harness 唤醒，消息在 messages 中
```

## 时间线规则

框架不限制 `wait()` 在哪个 hook 注册——可以在任意时刻调用。实际效果由 checkpoint 顺序决定：

```
wait("before_round") in tool_call
  → 当前 checkpoint = before_tools / after_tools（已过 before_round）
  → 下一轮 before_round 才 park（自然延迟）

wait("after_tools") in before_tools
  → target_hook (after_tools) 在当前 checkpoint (before_tools) 之后
  → 本轮内 after_tools 处 park（同步等待）
```

**引擎内置 round break 原语**。工具注册 wait 后 engine 检测 `state.waiting` 自动 break turn loop，立即结束当前 round 进入 park。不再需要等到下一轮才 park——这一轮调了 `delegate_task`，这一轮就会结束。

## 与现有 checkpoint 的关系

```
checkpoint 序列:
  before_round → before_model → after_model → before_tools → after_tools → after_round → before_break
      ↑                                                                                       │
      └─────────────────────────── _round_restart ────────────────────────────────────────────┘

所有外部 park 统一在 before_round:
  - HITL:       wait("before_round", "hitl")
  - Subagent:   wait("before_round", "subagent:...")
  - Peer send:  wait("before_round", "peer_wait:...")
  - Peer idle:  wait("before_round", "peer_idle:...")（插件主动注册）

保留在非 before_round 节点的 park:
  - Approval:    wait("before_tools", ...) — 原地阻塞，阻止工具执行
  - validate:    wait("before_break", ...) — 输出校验，强制重试（0.1s 自唤醒）
```

## 配置与使用

框架层面无需配置。三个模式需要插件启用：

```yaml
# agent.yaml
plugins:
  - a2a_subagents   # subagent 模式（delegate_task 工具）
  - a2a_teammates   # peer 模式（send_peer_message 工具）
```

HITL 是框架内置的 kernel tool（`ask_user`），无需插件。

## 演进方向

- **跨进程 Park/Resume**：当前 `asyncio.Event` 仅限单进程。未来可通过 Redis pub/sub 或消息队列实现跨进程的 park/wakeup，支持 Agent 分布在多个 worker 上
- **Wait 超时与降级**：框架当前不设超时——超时由插件的 `_peer_wait_loop` 自行处理（如 600s idle timeout）。可以将超时策略统一到框架层，`wait()` 增加 `timeout` 参数
- **Wait 优先级与取消传播**：多个 wait 之间没有优先级。未来可支持优先级排序，以及级联取消时按优先级传播
- **Wait 可观测性**：当前 park/wakeup 通过 `parked` event emit 到 trace。可以增加 wait 生命周期事件（registered → parked → resolved → timeout），方便调试和监控
