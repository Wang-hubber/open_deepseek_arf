# Interrupt & Rollback

ARF 提供两种保障对话连续性的机制：**中断/恢复**（遭遇异常后快速继续对话）和**回滚**（撤销操作回到安全状态）。

---

## 1. OS 方案演进

> 本章描述 OS 如何处理中断与检查点，作为 ARF 设计思路的参考。非严格技术对标。

### 1.1 硬件中断的演进

**问题**：CPU 如何响应外部设备的异步事件而不轮询浪费 CPU？

**8259 PIC** — 中断到达时向 CPU 发送 INT 信号，CPU 保存现场（EFLAGS、CS、EIP 压栈），通过中断向量表（IDT）跳转到中断服务例程（ISR），执行完用 IRET 恢复现场。

**APIC** — 多核环境下的中断路由，支持中断优先级和向量重定向。

**信号（Signal）** — Unix 的"软件中断"。SIGINT（Ctrl+C）、SIGTERM、SIGKILL。进程收到信号后执行注册的 handler 或默认动作。类比 ARF：`cancel_event.set()` 类似 SIGINT — 用户按下"停止"按钮。

### 1.2 进程检查点与恢复

长时间运行的进程如何在崩溃后恢复而不丢失中间结果？

OS 用检查点保存进程状态快照（CRIU、BLCR）。ARF 的 `RoundManager` 直接对应检查点+恢复模型：每个用户交互轮次推入检查点，undo 时恢复到目标检查点，文件和状态双回滚。

### 1.3 对 ARF 的启发

| OS 概念 | ARF 对应 |
|---------|----------|
| 硬件中断 → 保存现场 → ISR → 恢复 | `cancel_event.set()` → break → `FileStateStore` 持久化 → 下次对话恢复 |
| 信号（SIGINT） | `POST /api/chat/cancel`，`AbortController.abort()` |
| 检查点（CRIU） | `RoundManager.begin_round()` → `undo(steps)` |
| 守护进程 / 看门狗 | Tool `rollback()` — execute 失败时 Framework 自动调用 rollback 清理副作用 |

---

## 2. 中断与恢复

当对话被中断（用户主动停止、网络断开、服务异常），ARF 通过 `FileStateStore` 将状态持久化到磁盘。下次启动时自动恢复，用户无感知。

### 2.1 中断场景

| 场景 | 触发方式 | 引擎行为 |
|------|---------|---------|
| **用户主动中断** | App 层 inject `cancel_event.set()`（如 Stop 按钮） | `cancel_event.set()` → 循环边界检测 `_cancelled()` → break → 状态落盘 |
| **网络异常/超时** | 客户端断开 → `asyncio.CancelledError` → App 层调用 `cancel_event.set()` | 同上 — 引擎响应 cancel_event 信号 |
| **服务异常** | 进程崩溃、OOM、kill | 最后一次 `state_store.put()` 的状态可用（每 turn 结束时写入） |

### 2.2 取消信号传递

```
用户点击 Stop / 客户端断开
    │
    ▼
cancel_event.set()  ← asyncio.Event（非阻塞标志）
    │
    ▼
GraphEngine 循环边界检查 _cancelled()
    ├─ True  → emit session_end(reason="cancelled") → break
    └─ False → 继续执行
```

**引擎侧**（`GraphEngine`）：

```python
def _cancelled(self) -> bool:
    return self._cancel_event is not None and self._cancel_event.is_set()
```

`asyncio.Event` 是非阻塞检查——取消信号到达后，引擎在当前循环边界响应，类似硬件中断在当前指令边界响应。

App 层通过 `engine.set_cancel_event()` 注入事件，监听 `session_end(reason="cancelled")` 事件通知前端。具体集成方式见 App 开发者指南。

### 2.3 状态持久化与恢复

**引擎侧**：`FileStateStore` 在每个 turn 结束、工具执行前后、`human_loop` 暂停前自动调用 `put()`。状态以 JSON 格式写入 `memory/state/{session_id}.json`。

**服务端**：启动时从 `FileStateStore` 加载状态，存在则恢复对话历史、当前模型、上下文摘要等。`api/chat` 和 `astream` 入口读取 `state_store.get("default")` 拿到已有状态后追加新消息。

```python
# server.py lifespan — startup
state = await _agent.state_store.get("default")
if state:
    logger.info(f"Restored state: {len(state['messages'])} messages")
```

**当前限制**：多 Agent Team 并行模式的检查点恢复待 `RoundTransaction` 扩展支持

---

## 3. 错误处理

在回滚之前，框架先通过错误处理策略决定如何响应异常。`ErrorPolicy` 协议定义三类异常的处理动作。

### 3.0 协议

`ErrorPolicy` 协议（`arf/core/protocols/errors.py`）：

```python
class ErrorPolicy(Protocol):
    def on_tool_error(self, error: Exception, tool_name: str, attempt: int) -> ErrorAction: ...
    def on_model_error(self, error: Exception, model_name: str, attempt: int) -> ErrorAction: ...
    def on_guardrail_block(self, result: GuardResult, context: TurnContext) -> ErrorAction: ...
```

`DefaultErrorPolicy`（`arf/errors/retry.py`）是该协议的唯一实现：

- **工具错误**：指数退避重试（2^attempt × 1.0s），超出 `tool_retry` 后 abort
- **模型 5xx**：根据 `model_5xx_action` 决定 fallback（切换到备用模型）、retry 或 abort。引擎级重试已移除，瞬时重试由 protection 层处理
- **护栏拦截**：根据 `guardrail_block_action` 决定 abort 或 ask_user

---

## 4. 回滚

### 4.1 回滚场景

| 场景 | 触发方式 | 行为 |
|------|---------|------|
| **用户主动撤销** | `POST /api/chat/undo?steps=N`，对话内 `undo` 工具 | 状态 + 文件恢复到 N 轮之前 |
| **检查点损坏/不可用** | `undo()` 返回 `None`，或 `checkpoint_count()` < steps | 拒绝回滚，返回错误信息 |

### 4.2 RoundManager 检查点

`RoundManager`（`arf/engine/round_manager.py`）维护可配数量的 `RoundTransaction` 滚动窗口。每个 round 代表一次用户交互，可跨多次 agent handoff：

```python
class RoundManager:
    def __init__(self, max_undo_depth: int = 3):
        self._rounds: deque[RoundTransaction] = deque(maxlen=max_undo_depth)
```

**检查点创建**：`BaseAgent.chat/astream` 入口调用 `rounds.begin_round(state)`，同时保存：

1. 对话状态深拷贝（messages、current_model、context_summary）
2. 工作区文件快照（复制到 `memory/checkpoints/{round_num}/`，排除 `.git`）
3. Round 元数据持久化到 `memory/checkpoints/rounds.json`

**Handoff 与检查点**：Agent 切换时 `rounds.record_handoff(from, to)` 仅记录参与顺序，**不创建新检查点**。一个 round 内无论多少次 handoff，undo 都回退整个 round。

### 4.3 Undo 过程

```
Round 0: hello.txt(v1) → begin_round → memory/checkpoints/0/hello.txt
Round 1: hello.txt(v2) → begin_round → memory/checkpoints/1/hello.txt
  └─ handoff → sys_agent (record_handoff, no new checkpoint)
Round 2: hello.txt(v3)  ← 改坏了
    │
    ▼ undo(steps=1)
    │
    ├─ 恢复对话状态到 Round 1 开始前
    ├─ 删除当前工作区文件
    ├─ 从 memory/checkpoints/1/ 恢复文件 → hello.txt(v2)
    ├─ emit undo_executed(from=2, to=1) → Trace 可标记回滚边界
    └─ 清理 >= round 2 的检查点目录
```

**对话内 Undo 工具**：框架提供 `undo` 工具（kernel 级别激活），LLM 可在对话中直接调用。用户说"撤回"即可触发，无需 API。

**文件回滚范围**：所有 agent 共享同一个 `workspace` 目录，`RoundManager` 快照覆盖 workspace 内全部文件（排除 `.git`），任何 agent 在 workspace 内的文件变更都可以被回滚。

**Trace 集成**：undo 时不删除已有 trace 事件（审计日志不可篡改），而是追加 `undo_executed` 事件。前端可根据 `from_round` / `to_round` 折叠或灰度显示被撤销的轮次。

### 4.4 检查点不可用时的行为

当 `checkpoint_count() < steps` 或检查点数据损坏时：

- API 端点返回 `{"status": "insufficient_checkpoints", "available": N, "requested": steps}`
- 对话内 `undo` 工具返回 `{"ok": false, "error": "Only N checkpoints available"}`
- 不会部分回滚，不会损坏现有状态

### 4.5 Tool 级回滚 — FunctionBackend 内联回滚

RoundManager 的 undo 是**跨轮次**的状态回退。ARF 也提供**单 Tool 执行失败时**的副作用清理机制。

**机制**：涉及数据写入的 Tool 在 `function.py` 中可选导出 `rollback()` 函数。`FunctionBackend.execute_with_fn` 在 `execute()` 抛出异常后自动调用 `rollback()`，并将结果打包进 `ToolResult`。

```python
# tools/my_writer/function.py

async def execute(path: str, content: str) -> dict:
    p = WORKSPACE / path
    p.write_text(content)
    return {"ok": True, "path": str(p)}

async def rollback(path: str, content: str) -> dict:
    """execute 失败时自动调用，清理副作用"""
    p = WORKSPACE / path
    p.unlink(missing_ok=True)
    return {"ok": True, "action": "deleted", "path": str(p)}
```

**执行流程**：
```
execute() 抛异常
    │
    ├─ ToolProvider 提供了 rollback_fn？
    │   ├─ 是 → 调用 rollback_fn(**params)
    │   │   ├─ 成功 → ToolResult(rolled_back=True, rollback_error=None)
    │   │   └─ 失败 → ToolResult(rolled_back=True, rollback_error="...")
    │   └─ 否 → ToolResult(success=False, rolled_back=False)
    │
    ▼
emit rollback_executed trace 事件（如有回滚发生）
```

**与 RoundManager undo 的对比**：

| | RoundManager undo | FunctionBackend rollback |
|---|---|---|
| 粒度 | 用户交互轮次（Round） | 单次 Tool 执行 |
| 触发方式 | 用户主动调用 / API | execute() 异常时自动 |
| 回滚内容 | 状态 + 工作区文件 | Tool 自身副作用 |
| 提供方 | 框架内置 | Tool 开发者可选提供 |
| 非强制 | 否（框架保证） | 是（约定规范） |

---

## 5. 配置

```yaml
advanced:
  # 中断与回滚
  max_undo_depth: 3             # 最大 undo 步数（RoundManager 滚动窗口大小）

  # 错误处理（ErrorConfig）
  errors:
    tool_retry: 2                         # 工具失败最大重试次数（默认 2）
    tool_backoff: exponential             # 退避策略（exponential | linear | none）
    model_5xx_action: fallback            # 模型 5xx 处理（fallback | retry | abort）
    guardrail_block_action: abort         # 护栏拦截处理（abort | ask_user）

tools:
  - name: undo
    description: 撤销最近的对话轮次（支持跨 handoff 回退）
    parameters: {type: object, properties: {steps: {type: integer, default: 1}}}
    activation: kernel
```

- **`tool_retry`**：工具执行失败时的最大重试次数，默认 2。超出后 abort
- **`tool_backoff`**：重试退避策略。exponential 为 2^attempt × 1.0s 延迟
- **`model_5xx_action`**：模型返回 500/502/503/504 时的行为。`fallback` 切换备用模型，`retry`/`abort` 直接终止（引擎级重试已移除，瞬时重试由 protection 层处理）
- **`guardrail_block_action`**：护栏拦截时的行为。`abort` 终止执行，`ask_user` 推送审批

---

## 6. 演进方向

### 6.1 暂停/恢复

当前取消是"终止型"的——一旦取消，整个 Agent 循环退出。可以支持更细粒度的干预：

- **暂停/恢复**：类似 SIGSTOP/SIGCONT。用户暂停 Agent 后，可在稍后恢复继续。需将完整 engine 状态序列化，支持跨进程恢复
- **重定向**：类似信号 handler。用户不停止 Agent，而是注入新的指令，Agent 在当前 turn 结束后切换任务

### 6.2 探索性方向

- **空闲超时**：Agent 长时间无交互时自动暂停，释放资源
- **中断优先级**：区分"紧急中断"（强制停止）和"软中断"（LLM 自行决定是否采纳），紧急中断在循环边界立即响应
