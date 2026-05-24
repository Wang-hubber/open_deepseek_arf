# External Interrupt & User Intervention

ARF 参照 OS 硬件中断模型：用户可随时中止流式响应，Hook 可注入消息到对话流，取消令牌在引擎每次循环边界检查。

## Architecture

```
用户点击 Stop / 客户端断开
    │
    ▼
POST /api/chat/cancel 或 asyncio.CancelledError
    │
    ▼
cancel_event.set()
    │
    ▼
GraphEngine 循环边界检查 _cancelled()
    ├─ True  → yield session_end(reason="cancelled") → break
    └─ False → 继续执行

Hook 退出码 2
    │
    ▼
HookResult.injected_message
    │
    ▼
_inject_hook_messages() → state["messages"].append(role="system")
    │
    ▼
LLM 下轮看到注入消息
```

## 取消机制

### 引擎侧

`GraphEngine` 接受 `cancel_event: asyncio.Event`，每次循环开始前非阻塞检查：

```python
def _cancelled(self) -> bool:
    return self._cancel_event is not None and self._cancel_event.is_set()

# 在 invoke() / astream() 的 while 循环中：
while self.loop_strategy.should_continue(state):
    if self._cancelled():
        self._emit("session_end", {"reason": "cancelled"})
        break
    # ... normal turn execution
```

### 服务端

`POST /api/chat/cancel` 设置取消事件：

```python
_active_cancel_events: dict[str, asyncio.Event] = {}

@app.post("/api/chat/cancel")
async def cancel_chat():
    evt = _active_cancel_events.get("default")
    if evt and not evt.is_set():
        evt.set()
        return {"status": "cancelled"}
    return {"status": "no_active_chat"}
```

### 客户端断开

SSE 生成器捕获 `asyncio.CancelledError`（FastAPI 在客户端断开时取消生成器），触发取消：

```python
except asyncio.CancelledError:
    cancel_evt.set()  # 通知引擎停止
finally:
    _active_cancel_events.pop("default", None)
    _agent._engine.set_cancel_event(None)  # 重置
```

## Hook 消息注入

Hook 退出码 2 的消息被注入到对话历史，LLM 在下一轮可见：

```python
def _inject_hook_messages(self, results, state):
    for r in results:
        if r.exit_code == 2 and r.injected_message:
            state["messages"].append({
                "role": "system",
                "content": f"[Hook: {r.hook_name}] {r.injected_message}"
            })
```

所有六个生命周期事件（`session_start`, `pre_model_call`, `post_model_call`, `pre_tool_exec`, `post_tool_exec`, `session_end`）均已接入注入逻辑。

## 前端事件

取消时 SSE 推送 `{"type": "cancelled"}` 事件，前端可据此更新 UI：

```python
elif t == "session_end":
    if event.data.get("reason") == "cancelled":
        yield f"data: {json.dumps({'type': 'cancelled'})}\n\n"
```

## Undo — 状态 + 文件双回滚

`push_checkpoint` 在每轮用户交互前同时快照对话状态和工作区文件：

```
Round 0: hello.txt(v1) → push_checkpoint → memory/checkpoints/0/hello.txt
Round 1: hello.txt(v2) → push_checkpoint → memory/checkpoints/1/hello.txt
Round 2: hello.txt(v3)  ← 改坏了
    │
    ▼ POST /api/chat/undo?steps=1
    │
    ├─ 恢复对话状态到 Round 1
    └─ 恢复 hello.txt → v2 ✓
```

- 滚动窗口 **3 个**快照（最老自动淘汰）
- 文件备份在 `memory/checkpoints/{round}/`，undo 时恢复
- `.git` 目录自动排除

## 验证

| 场景 | 结果 |
|------|------|
| 引擎创建时注入 cancel_event | ✅ 循环边界检查生效 |
| cancel_event.set() | ✅ `_cancelled()` 返回 True |
| 客户端断开 → 取消 | ✅ asyncio.CancelledError → cancel_evt.set() |
| Hook exit_code=2 | ✅ 注入到 state.messages |
| 3 轮文件修改后 undo 1 步 | ✅ 文件恢复到 v2 |
| 再 undo 1 步 | ✅ 文件恢复到 v1 |
| undo 超出快照数 | ✅ 返回 None |
| 滚动窗口淘汰最老快照 | ✅ 最多保留 3 个 |

## 与 OS 模式的对应

| OS 概念 | ARF 实现 |
|---------|----------|
| 硬件中断 | `cancel_event.set()` 触发异步取消 |
| 中断服务例程 (ISR) | `_cancelled()` 检查 + `break` 清理 |
| 中断向量 | Hook 退出码 0/1/2 |
| 消息信号中断 (MSI) | Hook `injected_message` 注入对话流 |
| 进程终止 | `session_end(reason="cancelled")` |
