# Approval Plugin — 人机审批通道

`pre_action` hook（`execute_tools` 阶段）对 `ask_list` 中的工具暂停执行，等待人工确认。仅 `ask` 模式激活——`auto` 模式全放行，`plan` 模式由 tool_guard 已拦截副作用工具。

---

## 1. 审批流程

```
execute_tools 阶段
  → effective_mode == "ask"?
    → 遍历 _pending_tool_calls
      → 工具名 (namespaced) ∈ ask_list?
        → 发射 approval_required
        ├─ chat() + on_approval handler → 同步回调批准/拒绝
        ├─ astream() → asyncio.Event.wait(60s) → approve() 外部调用
        └─ 超时 → 视为拒绝
      → 全部批准? → 放行执行
      → 有拒绝? → 注入 blocked tool 消息 + raise ApprovalDenied
```

---

## 2. 两种调用路径

### chat() — 内联 handler

```python
def my_handler(tool_name: str, params: dict) -> bool:
    return input(f"Approve {tool_name}? (y/n): ").lower() == "y"

agent.chat("delete old files", on_approval=my_handler)
```

handler 可以是同步或 async 函数。不传 handler 且命中审批 → 直接抛 `RuntimeError`。

### astream() — 外部 approve()

```python
async for event in agent.astream("delete old files"):
    if event.type == "approval_required":
        decision_id = event.data["decision_id"]
        agent.approval_plugin.approve(decision_id, approved=True)
```

`approve()` 可在任意线程/协程调用，线程安全（`asyncio.Event` + dict）。

---

## 3. 拒绝处理

工具被拒绝/超时后，ApprovalPlugin 注入合成事件以维护 API 消息格式合约（tool_calls 必须后跟 tool 消息）：

```
拒绝 → 发射 tool_call_start (blocked)
     → 发射 tool_call_end   (success=false, blocked=true)
     → 注入 tool 消息到 messages: "[blocked] user denied"
     → raise ApprovalDenied → error_handler 捕获
```

---

## 4. 名称解析

`set_name_resolver()` 在 BaseAgent init 时注入，将配置中的裸名转换为 namespaced 名：

```python
# 配置: ask_list: [write_file]
# 解析后: ask_list: {"user::write_file"}
```

这确保 `plugins_config` 中的工具名与实际调用的 namespaced 名匹配。

---

## 5. 异常类

| 异常 | 触发条件 |
|------|---------|
| `ApprovalTimeout` | `asyncio.Event.wait()` 超时（默认 60s） |
| `ApprovalDenied` | 用户拒绝或超时（汇总后统一抛） |
| `RuntimeError` | `chat()` 无 handler 但命中审批 |

---

## 6. 配置

```yaml
plugins:
  - approval

plugins_config:
  approval:
    timeout: 60               # 审批超时秒数（默认 60）
    ask_list:                 # 需要审批的工具（裸名）
      - write_file
      - delete_file
      - move_file
```

---

## 7. 事件

| 事件 | 触发时机 | data 字段 |
|------|---------|----------|
| `approval_required` | 工具等待审批 | decision_id, tool_name, params |
| `approval_resolved` | 审批完成 | decision_id, approved[, reason] |
| `tool_call_start` | 拒绝时合成 | tool_name, id, arguments |
| `tool_call_end` | 拒绝时合成 | tool_name, id, success=false, blocked=true |

---

## 8. 公共 API

```python
plugin.approve(decision_id: str, approved: bool = True) -> bool
# 返回 True 表示找到对应 pending 请求并已通知
# 返回 False 表示 decision_id 已过期或不存在（幂等安全）
```
