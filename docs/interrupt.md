# External Interrupt & User Intervention

ARF 参照 OS 硬件中断模型：用户可随时中止流式响应，Hook 可注入消息到对话流，undo 支持状态+文件双回滚。

---

## 1. OS 方案演进

> 本章描述 OS 如何处理外部中断与进程间通信，作为 ARF 设计思路的参考。非严格技术对标。

### 1.1 硬件中断的演进

**问题**：CPU 如何响应外部设备的异步事件（键盘按下、网络包到达、定时器到期）而不轮询浪费 CPU？

**8259 PIC（可编程中断控制器）** — IBM PC/AT 引入。两片级联支持 15 个中断源（IRQ 0-15）。中断到达时，PIC 向 CPU 发送 INT 信号，CPU 保存现场（EFLAGS、CS、EIP 压栈），通过中断向量表（IDT）跳转到对应的中断服务例程（ISR）。ISR 执行完用 IRET 恢复现场。

**APIC（高级可编程中断控制器）** — Pentium Pro 引入。支持多核环境下的中断路由。Local APIC（每核一个）处理本地中断和 IPI（核间中断）。IO APIC 替代 8259 管理外部设备中断。支持中断优先级和向量重定向。

**MSI/MSI-X（消息信号中断）** — PCIe 引入。设备不通过物理中断引脚，而是写特定内存地址（MSI address register）来发送中断。消除了 IRQ 共享冲突，支持更多向量（最多 2048 个）。类比 ARF 的 Hook 退出码 2：不阻断流程，而是写入一条消息（中断信号）供接收方（LLM）在下一轮处理。

### 1.2 信号 — 用户态"软件中断"

Unix 信号（SIGINT、SIGTERM、SIGKILL 等）是用户态的异步通知机制。进程收到信号后执行注册的 handler（ISR 的用户态等价），或执行默认动作（终止/忽略/core dump）。Ctrl+C 是 SIGINT——用户在终端按下的"停止"按钮。

### 1.3 进程检查点与恢复

**问题**：长时间运行的进程如何在崩溃或中断后恢复，而不丢失所有中间结果？

OS 用检查点保存进程状态快照——内存页、文件描述符、寄存器。恢复时重建全部状态继续执行。典型实现有 CRIU（Checkpoint/Restore In Userspace）和 BLCR。ARF 的 undo 机制直接对应检查点+恢复模型，但以对话轮次为粒度。

### 1.4 对 ARF 的启发

硬件中断的"保存现场→ISR→恢复"对应了 cancel 的"捕获取消信号→break 清理→下轮正常"。信号的异步通知对应了 Hook 退出码 2 的消息注入——不打断流程但插入信息。检查点模型直接影响了 undo 机制的设计。

---

## 2. ARF 当前实现

### 2.1 架构总览

```
用户点击 Stop / 客户端断开
    │
    ▼
POST /api/chat/cancel 或 asyncio.CancelledError
    │
    ▼
cancel_event.set()  ← asyncio.Event（非阻塞标志）
    │
    ▼
GraphEngine 循环边界检查 _cancelled()
    ├─ True  → emit session_end(reason="cancelled") → break
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
LLM 下一轮可见注入消息
```

### 2.2 取消机制

**引擎侧**（`graph.py:146-148`）：

```python
def _cancelled(self) -> bool:
    return self._cancel_event is not None and self._cancel_event.is_set()
```

在 `invoke()` 和 `astream()` 的 while 循环开始处检查（`graph.py:249-251`, `501-505`）。`asyncio.Event` 是非阻塞检查——取消信号到达后，引擎在下一个循环边界响应，类似硬件中断在当前指令边界响应。

**服务端**（`server.py:131-189`）：

- `POST /api/chat/cancel`：设置 `cancel_event`，引擎在下一轮检测到并退出
- 客户端断开：`asyncio.CancelledError` 被 SSE 生成器捕获 → 设置 `cancel_event` → 引擎退出
- 取消后 SSE 推送 `{"type": "cancelled"}` 事件，前端可据此更新 UI

### 2.3 Hook 消息注入

Hook 退出码 2 的消息被注入对话历史（`graph.py:217-225`）：

```python
def _inject_hook_messages(self, results, state):
    for r in results:
        if r.exit_code == 2 and r.injected_message:
            state["messages"].append({
                "role": "system",
                "content": f"[Hook: {r.hook_name}] {r.injected_message}"
            })
```

所有六个生命周期事件（`session_start`, `pre_model_call`, `post_model_call`, `pre_tool_exec`, `post_tool_exec`, `session_end`）均已接入注入逻辑。类似 MSI 中断——不打断主流程，而是在消息队列（对话历史）中插入信息。

### 2.4 Undo — 状态 + 文件双回滚

`GraphEngine` 维护 3 个检查点的滚动窗口（`graph.py:62`）：

```python
self._checkpoints: deque[dict] = deque(maxlen=3)
```

**检查点创建**（`graph.py:73-98`）：每轮用户交互前（`base.py:369,402`），`push_checkpoint()` 同时保存：
1. 对话状态深拷贝（messages、current_model、context_summary）
2. 工作区文件快照（复制到 `memory/checkpoints/{round}/`，排除 `.git`）

**Undo 过程**（`graph.py:100-141`）：

```
Round 0: hello.txt(v1) → push_checkpoint → memory/checkpoints/0/hello.txt
Round 1: hello.txt(v2) → push_checkpoint → memory/checkpoints/1/hello.txt
Round 2: hello.txt(v3)  ← 改坏了
    │
    ▼ undo(steps=1)
    │
    ├─ 恢复对话状态到 Round 1
    ├─ 删除当前工作区文件
    ├─ 从 memory/checkpoints/1/ 恢复文件 → hello.txt(v2)
    └─ 清理 Round 1 及之后的检查点目录
```

**对话内 Undo 工具**：框架提供 `undo` 工具（kernel 级别激活），LLM 可在对话中直接调用。用户说"撤回"即可触发，无需 API。

**当前限制**：
- 快照上限 3 个（deque maxlen=3），用户只能 undo 最近 1-3 步
- 检查点在内存中（deque），重启丢失
- 文件快照仅覆盖 `workspace_dir`（默认 `workspaces/default`），不覆盖其他目录

### 2.5 配置

```yaml
# 无显式配置项。取消通过 API / SSE 生命周期自动管理
# undo 通过工具声明启用（框架内置）
tools:
  - name: undo
    description: 撤销最近的对话轮次
    parameters: {type: object, properties: {steps: {type: integer, default: 1}}}
    activation: kernel
```

---

## 3. 演进方向

### 3.1 对标 OS 最佳实践：暂停/重定向向量

当前取消是"终止型"的——一旦取消，整个 Agent 循环退出。对标 OS 的信号处理（可注册 handler 而非只有 default kill），可以支持更细粒度的干预：

- **暂停/恢复**：类似 SIGSTOP/SIGCONT。用户暂停 Agent 后，可在稍后恢复继续。需要将完整 engine 状态序列化，支持跨进程恢复
- **重定向**：类似信号 handler。用户不停止 Agent，而是注入新的指令（"别管那个了，先做这个"），Agent 在当前 turn 结束后切换任务

### 3.2 持久化检查点

对标 CRIU 的进程检查点机制：将检查点从内存 deque 移到持久化存储（`memory/checkpoints/`），支持重启后 undo。结合 `archive.json`（已实现会话持久化），实现完整的"会话可恢复性"。

### 3.3 探索性方向

**空闲超时**：Agent 长时间等待用户输入或工具响应时自动暂停，释放资源。类似 OS 将进程换出到 swap 等待唤醒。

**中断优先级**：区分"紧急中断"（用户强制停止）和"软中断"（Hook 注入的消息建议 LLM 调整方向但不强制终止）。紧急中断在引擎循环边界立即响应；软中断由 LLM 在自己的判断中决定是否采纳。
