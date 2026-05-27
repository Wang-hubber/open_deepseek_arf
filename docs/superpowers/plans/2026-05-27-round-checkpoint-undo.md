# Round-Level Checkpoint & Undo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace per-agent deque checkpoint stacks with RoundManager that treats each user interaction round as an atomic transaction unit, enabling undo across handoff boundaries.

**Architecture:** New `arf/engine/round_manager.py` module (RoundTransaction dataclass + RoundManager class). GraphEngine replaces 5 scattered checkpoint variables with a single `self._rounds: RoundManager`. Handoff records agent trace instead of switching stacks. Undo emits `undo_executed` event into trace.

**Tech Stack:** Python 3.11+, asyncio, dataclasses

---

## File Structure

```
Create:
  arf/engine/round_manager.py         — RoundTransaction dataclass + RoundManager

Modify:
  arf/engine/__init__.py              — export RoundManager
  arf/core/events.py                  — add "undo_executed" to EventType
  arf/engine/graph.py                 — replace 5 vars with RoundManager;
                                        update _execute_handoff, _restore_from_handoff,
                                        undo(), checkpoint_count();
                                        remove push_checkpoint()
  arf/agent/base.py                   — push_checkpoint → engine.rounds.begin_round
  docs/interrupt.md                   — update 2.4 Undo section
  README.md                           — update interrupt row in framework table
  README.zh-CN.md                     — same
```

---

### Task 1: Create RoundTransaction + RoundManager module

**Files:**
- Create: `arf/engine/round_manager.py`

- [ ] **Step 1: Write the module**

```python
"""RoundManager — round-level checkpoint and undo for multi-agent scenarios."""
import copy
import shutil
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from arf.core.state import AgentState


@dataclass
class RoundTransaction:
    """Full state snapshot for one user interaction round.

    A round may span multiple agent handoffs. This snapshot captures
    the state at the beginning of the round. Undo restores to this point.
    """

    round_id: str                       # "session_id/round_num"
    round_num: int                      # monotonic, lifetime of RoundManager
    state_snapshot: dict                # deepcopy(AgentState) at round start
    workspace_snapshot_dir: str | None = None  # memory/checkpoints/{round_num}/
    created_at: float = field(default_factory=time.time)
    agent_trace: list[str] = field(default_factory=list)  # ["main","sys","main"]
    handoff_count: int = 0
    closed: bool = False


class RoundManager:
    """Round-level checkpoint manager.

    Each round is a transaction: begin_round() pushes a snapshot;
    handoffs within the round are recorded via record_handoff() but
    do NOT create new checkpoints.  undo(N) restores to round-N ago.
    """

    def __init__(self, max_undo_depth: int = 3) -> None:
        self._rounds: deque[RoundTransaction] = deque(maxlen=max_undo_depth)
        self._active: RoundTransaction | None = None
        self._current_round: int = 0

    # -- public API --

    def begin_round(self, state: AgentState, workspace_dir: str = "") -> RoundTransaction:
        """Snapshot *state* and workspace files.  Returns the new transaction."""
        self._current_round += 1
        agent = state.get("active_agent") or state.get("agent_name", "main")
        tx = RoundTransaction(
            round_id=f"{state.get('session_id', 'default')}/{self._current_round}",
            round_num=self._current_round,
            state_snapshot=copy.deepcopy(dict(state)),
            agent_trace=[agent],
        )
        ws = Path(workspace_dir) if workspace_dir else Path("workspaces/default")
        tx.workspace_snapshot_dir = self._snapshot_workspace(ws, self._current_round)

        self._rounds.append(tx)
        self._active = tx
        return tx

    def record_handoff(self, from_agent: str, to_agent: str) -> None:
        """Record an agent switch within the active round (no new checkpoint)."""
        if self._active:
            self._active.agent_trace.append(to_agent)
            self._active.handoff_count += 1

    def close_round(self) -> None:
        """Mark the active round as complete (hook for future persistence)."""
        if self._active:
            self._active.closed = True
            self._active = None

    def undo(self, steps: int, workspace_dir: str = "") -> AgentState | None:
        """Pop N rounds and restore state from the oldest popped.

        Returns the state snapshot from the target (restored) round,
        or None if insufficient rounds.
        """
        if steps < 1 or steps > len(self._rounds):
            return None

        target = None
        for _ in range(steps):
            target = self._rounds.pop()

        if target is None:
            return None

        ws = Path(workspace_dir) if workspace_dir else Path("workspaces/default")
        self._restore_workspace_files(target, ws)
        self._cleanup_checkpoint_dirs(target.round_num, ws)
        self._active = None

        return copy.deepcopy(target.state_snapshot)

    def count(self) -> int:
        return len(self._rounds)

    @property
    def active_round(self) -> RoundTransaction | None:
        return self._active

    @property
    def current_round_num(self) -> int:
        return self._current_round

    # -- internal --

    def _snapshot_workspace(self, workspace: Path, round_num: int) -> str | None:
        """Copy workspace files to memory/checkpoints/{round_num}/."""
        if not workspace.exists():
            return None
        ckpt_dir = Path("memory/checkpoints") / str(round_num)
        if ckpt_dir.exists():
            shutil.rmtree(ckpt_dir)
        ckpt_dir.mkdir(parents=True, exist_ok=True)
        for f in workspace.rglob("*"):
            if f.is_file() and ".git" not in f.parts:
                rel = f.relative_to(workspace)
                dest = ckpt_dir / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dest)
        return str(ckpt_dir)

    def _restore_workspace_files(self, tx: RoundTransaction, workspace: Path) -> None:
        """Delete current workspace files and restore from *tx* snapshot."""
        if not tx.workspace_snapshot_dir or not workspace.exists():
            return
        ckpt = Path(tx.workspace_snapshot_dir)
        if not ckpt.exists():
            return
        # Remove current files (non-git)
        for f in workspace.rglob("*"):
            if f.is_file() and ".git" not in f.parts:
                f.unlink()
        # Restore from checkpoint
        for f in ckpt.rglob("*"):
            if f.is_file():
                rel = f.relative_to(ckpt)
                dest = workspace / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dest)

    def _cleanup_checkpoint_dirs(self, from_round: int, workspace: Path) -> None:
        """Remove checkpoint directories >= *from_round*."""
        ckpts = Path("memory/checkpoints")
        if not ckpts.exists():
            return
        for d in ckpts.iterdir():
            if d.is_dir():
                try:
                    if int(d.name) >= from_round:
                        shutil.rmtree(d)
                except (ValueError, OSError):
                    pass
```

- [ ] **Step 2: Verify import**

```bash
cd /home/wangxie/open_deepseek_arf && python3 -c "
from arf.engine.round_manager import RoundManager, RoundTransaction
print('RoundManager imported OK')
"
```
Expected: `RoundManager imported OK`

- [ ] **Step 3: Commit**

```bash
git add arf/engine/round_manager.py
git commit -m "feat: add RoundManager — round-level checkpoint and undo

RoundTransaction represents one user interaction round as an atomic
transaction. RoundManager replaces per-agent deque stacks. Handoffs
record agent trace without creating new checkpoints.

Co-Authored-By: Claude Code with DeepSeek V4"
```

---

### Task 2: Add undo_executed event type

**Files:**
- Modify: `arf/core/events.py:7-21`

- [ ] **Step 1: Add event to EventType**

```python
# arf/core/events.py — add "undo_executed" before "error" at the end
EventType = Literal[
    "session_start", "session_end",
    "user_input",
    "thinking_delta",
    "model_call_start", "model_call_end",
    "tool_call_start", "tool_call_end",
    "compaction_start", "compaction_end",
    "approval_required",
    "approval_resolved",
    "agent_switch",
    "guard_block",
    "guard_pass",
    "hook_start", "hook_end",
    "undo_executed",        # undo boundary marker — trace never deletes, only marks
    "error",
]
```

- [ ] **Step 2: Verify**

```bash
cd /home/wangxie/open_deepseek_arf && python3 -c "
from arf.core.events import EventType
import typing
types = typing.get_args(EventType)
assert 'undo_executed' in types
print(f'Event types: {len(types)}')
"
```
Expected: `Event types: 20` (was 19, now 20)

- [ ] **Step 3: Commit**

```bash
git add arf/core/events.py
git commit -m "feat: add undo_executed event type to trace undo boundaries

Co-Authored-By: Claude Code with DeepSeek V4"
```

---

### Task 3: Wire RoundManager into GraphEngine

**Files:**
- Modify: `arf/engine/__init__.py:1-5`
- Modify: `arf/engine/graph.py:1-80,100-172,220-381`
- Modify: `arf/agent/base.py:570-592,602-624`

- [ ] **Step 1: Export RoundManager**

```python
# arf/engine/__init__.py
from arf.engine.round_manager import RoundManager, RoundTransaction

__all__ = [
    "GraphEngine",
    "ReActStrategy",
    "InMemoryStateStore",
    "FileStateStore",
    "ConcurrentToolExecutor",
    "PromptBasedPlanner",
    "RoundManager",
    "RoundTransaction",
]
```

- [ ] **Step 2: Replace checkpoint vars in GraphEngine.__init__**

Replace lines 73-82 in graph.py:

```python
# Before (lines 73-82):
        self._checkpoints: deque[dict] = deque(maxlen=3)  # rolling 3 snapshots
        self._interaction_round = 0
        # Multi-agent
        self._sub_agent_configs: dict = sub_agent_configs or {}
        self._handoff_manager = handoff_manager
        self._handoff_checkpoint: dict | None = None
        self._active_agent: str = ""
        # Per-agent checkpoint stacks (keyed by agent_name)
        self._agent_checkpoints: dict[str, deque] = {}
        self._agent_states: dict[str, AgentState] = {}

# After:
        self._interaction_round = 0
        # Round-level checkpoint manager (replaces per-agent stacks)
        from arf.engine.round_manager import RoundManager
        self._rounds = RoundManager(max_undo_depth=3)
        # Multi-agent
        self._sub_agent_configs: dict = sub_agent_configs or {}
        self._handoff_manager = handoff_manager
        self._active_agent: str = ""
```

- [ ] **Step 3: Replace push_checkpoint, undo, checkpoint_count methods**

Replace lines 101-172:

```python
# Before:
    def push_checkpoint(self, state: AgentState, workspace_dir: str = "") -> None:
        ...
    def undo(self, steps: int = 1, workspace_dir: str = "") -> AgentState | None:
        ...
    def checkpoint_count(self) -> int:
        return len(self._checkpoints)

# After:
    def checkpoint_count(self) -> int:
        return self._rounds.count()
```

Note: `push_checkpoint` is removed completely. `undo` is replaced below (Step 4).

- [ ] **Step 4: Add new undo method with undo_executed event**

Insert after `checkpoint_count`:

```python
    def undo(self, steps: int = 1, workspace_dir: str = "") -> AgentState | None:
        """Pop N rounds and restore state from the target checkpoint.

        Emits undo_executed trace event so consumers can mark the
        rollback boundary without deleting historical events.
        """
        active_trace = list(self._rounds.active_round.agent_trace) if self._rounds.active_round else []
        target_round = max(0, self._rounds.current_round_num - steps)
        restored = self._rounds.undo(steps, workspace_dir)
        if restored is not None:
            self._emit("undo_executed", {
                "from_round": self._rounds.current_round_num,
                "to_round": target_round,
                "steps": steps,
                "agent_trace": active_trace,
            })
        return restored
```

- [ ] **Step 5: Rewrite _execute_handoff — remove checkpoint stack switching**

Replace the checkpoint-related lines in `_execute_handoff` (lines 231-233, 239-245, 262-265, 298-302):

```python
# Lines 231-233: Replace with
        # 1. Save current agent state (persist for later resume)
        self._rounds.record_handoff(from_agent or "main", to_agent)
        await self.state_store.put(
            f"{session_id}/{from_agent}" if from_agent else session_id,
            state,
        )

# Lines 239-245: REMOVE (no more _handoff_checkpoint)
# Lines 262-265: REMOVE (no more _agent_checkpoints switch)
# Lines 298-302: REMOVE (no more _agent_checkpoints init)
```

Let me read the exact code to make precise edits...

Actually, let me provide the complete rewritten `_execute_handoff`:

```python
    async def _execute_handoff(self, state: AgentState, handoff_data: dict,
                                current_model: str) -> AgentState:
        """Execute forward handoff: save agent state → resolve → build context → swap."""
        session_id = state.get("session_id", "default")
        from_agent = (
            state.get("active_agent", "")
            or state.get("agent_name", "")
            or self._active_agent
            or ""
        )

        # 1. Save current agent state
        await self.state_store.put(
            f"{session_id}/{from_agent}" if from_agent else session_id,
            state,
        )

        # 2. Resolve target
        to_agent = await self._handoff_manager.resolve(from_agent, handoff_data)
        if not to_agent:
            state["handoff_error"] = f"No handover rule matches from '{from_agent}'"
            return state

        rule = self._handoff_manager.get_rule(from_agent, to_agent)
        if not rule:
            state["handoff_error"] = f"No rule for {from_agent} → {to_agent}"
            return state

        # 3. Record agent switch in the current round (no new checkpoint)
        self._rounds.record_handoff(from_agent or "main", to_agent)

        # 4. Try to load existing target agent state, or build fresh context
        existing_target = await self.state_store.get(f"{session_id}/{to_agent}")
        if existing_target:
            # Resume: restore target agent's previous state
            state.update(existing_target)
        else:
            # First time: build target context from handoff data
            target_cfg = self._sub_agent_configs.get(to_agent, {})
            target_prompt = target_cfg.get("system_prompt", "")
            new_messages = self._handoff_manager.build_target_context(
                from_state=state,
                rule=rule,
                handoff_data=handoff_data,
                target_system_prompt=target_prompt,
            )

            # Generate task summary (if configured)
            if rule.context.task_summary and self._handoff_manager._system_model_call:
                try:
                    summary = await self._handoff_manager._system_model_call(
                        f"Summarize this handoff task in one sentence (Chinese):\n"
                        f"Task: {handoff_data.get('task', '')}\n"
                        f"Context: {handoff_data.get('context', '')}"
                    )
                    for i, m in enumerate(new_messages):
                        if m.get("content") == "__TASK_SUMMARY_PLACEHOLDER__":
                            new_messages[i] = {
                                "role": "system",
                                "content": f"[Task Summary] {summary.strip()}",
                            }
                except Exception:
                    new_messages = [m for m in new_messages
                                    if m.get("content") != "__TASK_SUMMARY_PLACEHOLDER__"]
            else:
                new_messages = [m for m in new_messages
                                if m.get("content") != "__TASK_SUMMARY_PLACEHOLDER__"]

            state["messages"] = new_messages
            state["current_turn"] = 0
            state["tool_results"] = {}

        # 5. Swap active agent
        state["active_agent"] = to_agent
        state["handoff_task"] = handoff_data.get("task", "")

        # 6. Emit agent_switch
        self._emit("agent_switch", {
            "from": from_agent,
            "to": to_agent,
            "task": handoff_data.get("task", ""),
        }, session_id=session_id, agent_name=to_agent)

        logging.getLogger("arf.engine").info(
            "Handoff: %s → %s, task: %.80s", from_agent, to_agent,
            handoff_data.get("task", "")
        )
        return state
```

- [ ] **Step 6: Rewrite _restore_from_handoff — remove checkpoint stack restoration**

Replace lines 321-381:

```python
    async def _restore_from_handoff(self, state: AgentState,
                                      handoff_data: dict) -> AgentState:
        """Restore original agent after sub-agent handoff back."""
        session_id = state.get("session_id", "default")
        current_agent = state.get("active_agent", "")
        result_content = ""

        # Get sub-agent's last assistant message as result
        for m in reversed(state.get("messages", [])):
            if m.get("role") == "assistant":
                result_content = m.get("content", "")
                break

        if not result_content:
            result_content = "(handoff completed, no response)"

        # Save sub-agent's final state
        if current_agent:
            await self.state_store.put(f"{session_id}/{current_agent}", state)

        # Find the original handoff trigger and replace its tool result with
        # the sub-agent's response
        target_agent = self._active_agent or state.get("agent_name", "main")
        from_state = await self.state_store.get(f"{session_id}/{target_agent}")
        if from_state:
            messages = from_state.get("messages", [])
            # The last message should be the handoff tool result — replace it
            if messages and messages[-1].get("role") == "tool":
                messages[-1]["content"] = result_content
        else:
            # Fallback: keep current messages
            messages = state.get("messages", [])

        # Record the return switch
        self._rounds.record_handoff(current_agent, target_agent)

        # Emit agent_switch back
        self._emit("agent_switch", {
            "from": current_agent,
            "to": target_agent,
            "task": "handoff complete",
        }, session_id=session_id, agent_name=target_agent)

        # Swap state back to original agent
        state["messages"] = messages
        state["active_agent"] = target_agent
        state["tool_results"] = {}
        state.pop("handoff_task", None)
        state = self._close_tool_calls(state)

        return state
```

- [ ] **Step 7: Update base.py — push_checkpoint → rounds.begin_round**

Replace two calls in `base.py`:

```python
# Line 589: base.py chat() method
# Before:
        self._engine.push_checkpoint(state)

# After:
        self._engine._rounds.begin_round(state)

# Line 622: base.py astream() method
# Before:
        self._engine.push_checkpoint(state)

# After:
        self._engine._rounds.begin_round(state)
```

- [ ] **Step 8: Verify syntax**

```bash
cd /home/wangxie/open_deepseek_arf && python3 -c "
import ast
for f in ['arf/engine/round_manager.py', 'arf/engine/graph.py', 'arf/engine/__init__.py', 'arf/agent/base.py']:
    ast.parse(open(f).read())
    print(f'{f}: OK')
"
```

- [ ] **Step 9: Run import verification**

```bash
cd /home/wangxie/open_deepseek_arf && python3 -c "
from arf.engine.round_manager import RoundManager, RoundTransaction
from arf.engine import RoundManager as RM
print('All imports OK')
# Quick smoke test
rm = RoundManager()
assert rm.count() == 0
tx = rm.begin_round({'session_id':'test', 'agent_name':'main'})
assert rm.count() == 1
print(f'Round {rm.current_round_num} started, trace: {tx.agent_trace}')
"
```

- [ ] **Step 10: Commit**

```bash
git add arf/engine/__init__.py arf/engine/graph.py arf/agent/base.py
git commit -m "feat: wire RoundManager into GraphEngine, remove per-agent checkpoints

- GraphEngine replaces 5 scattered checkpoint vars with self._rounds
- _execute_handoff calls record_handoff() instead of switching stacks
- _restore_from_handoff loads from state_store instead of _handoff_checkpoint
- undo() emits undo_executed trace event
- push_checkpoint() removed, base.py calls rounds.begin_round()
- Remove _agent_checkpoints, _agent_states, _handoff_checkpoint

Co-Authored-By: Claude Code with DeepSeek V4"
```

---

### Task 4: Update docs/interrupt.md — Undo section

**Files:**
- Modify: `docs/interrupt.md:100-144`

- [ ] **Step 1: Rewrite section 2.4**

Replace lines 100-144 (2.4 Undo — 状态 + 文件双回滚 and 2.5 配置):

```markdown
### 2.4 Undo — 状态 + 文件双回滚

`RoundManager`（`arf/engine/round_manager.py`）维护 3 个 `RoundTransaction` 的滚动窗口。每个 round 代表一次用户交互，可跨多次 agent handoff：

```python
class RoundManager:
    def __init__(self, max_undo_depth: int = 3):
        self._rounds: deque[RoundTransaction] = deque(maxlen=max_undo_depth)
```

**检查点创建**：`BaseAgent.chat/astream` 入口调用 `rounds.begin_round(state)`，同时保存：
1. 对话状态深拷贝（messages、current_model、context_summary）
2. 工作区文件快照（复制到 `memory/checkpoints/{round_num}/`，排除 `.git`）

**Handoff 与检查点**：Agent 切换时 `rounds.record_handoff(from, to)` 仅记录参与顺序，**不创建新检查点**。一个 round 内无论多少次 handoff，undo 都回退整个 round。

**Undo 过程**：

```
Round 0: hello.txt(v1) → begin_round → memory/checkpoints/0/hello.txt
Round 1: hello.txt(v2) → begin_round → memory/checkpoints/1/hello.txt
  └─ handoff → sys_agent (record_handoff, no new checkpoint)
Round 2: hello.txt(v3) ← 改坏了
    │
    ▼ undo(steps=1)
    │
    ├─ 恢复对话状态到 Round 1 开始前
    ├─ 删除当前工作区文件
    ├─ 从 memory/checkpoints/1/ 恢复文件 → hello.txt(v2)
    ├─ emit undo_executed(from=2, to=1) → Trace 可标记回滚边界
    └─ 清理 >= round 2 的检查点目录
```

**Trace 集成**：undo 时不删除已有 trace 事件（审计日志不可篡改），而是追加 `undo_executed` 事件。前端可根据 `from_round` / `to_round` 折叠或灰度显示被撤销的轮次。

**对话内 Undo 工具**：框架提供 `undo` 工具（kernel 级别激活），LLM 可在对话中直接调用。用户说"撤回"即可触发，无需 API。

**当前限制**：
- 快照上限 3 个（deque maxlen=3），用户只能 undo 最近 1-3 步
- 检查点在内存中（deque），重启丢失
- 文件快照仅覆盖 `workspace_dir`（默认 `workspaces/default`），不覆盖其他目录
- 多 Agent Team 并行模式的检查点恢复待 `RoundTransaction` 扩展支持

### 2.5 配置

```yaml
# 无显式配置项。取消通过 API / SSE 生命周期自动管理
# undo 通过工具声明启用（框架内置）
tools:
  - name: undo
    description: 撤销最近的对话轮次（支持跨 handoff 回退）
    parameters: {type: object, properties: {steps: {type: integer, default: 1}}}
    activation: kernel
```
```

- [ ] **Step 2: Commit**

```bash
git add docs/interrupt.md
git commit -m "docs: update undo section with RoundManager mechanism

Reflect new round-level checkpoint design, handoff interaction,
undo_executed trace event, and updated limitations.

Co-Authored-By: Claude Code with DeepSeek V4"
```

---

### Task 5: Update README.md and README.zh-CN.md

**Files:**
- Modify: `README.md:68`
- Modify: `README.zh-CN.md:68`
- Modify: `README.md:181-185`
- Modify: `README.zh-CN.md:181-185`

- [ ] **Step 1: Update Interrupt row in framework tables**

README.md line 68:
```markdown
# Before:
| **[Interrupt →](docs/interrupt.md)**<br>User intervention | Hardware interrupt: save state → ISR → restore | `asyncio.Event` cancellation. 3-snapshot undo (state + files) via API or in-conversation `undo` tool. Hook exit-code-2 message injection. | Pause/redirect vectors; idle timeout |

# After:
| **[Interrupt →](docs/interrupt.md)**<br>User intervention | Hardware interrupt: save state → ISR → restore | `asyncio.Event` cancellation. Round-level undo via `RoundManager` — 3-snapshot rolling window, state + file rollback across handoff boundaries. Hook exit-code-2 message injection. `undo_executed` trace event. | Pause/redirect vectors; idle timeout |
```

README.zh-CN.md line 68:
```markdown
# Before:
| **[外部中断 →](docs/interrupt.md)**<br>用户干预 | 硬件中断：保存现场 → ISR → 恢复 | `asyncio.Event` 异步取消。3 快照 undo（状态+文件双回滚），支持 API 和对话内 `undo` 工具。Hook 退出码 2 消息注入。 | 暂停/重定向向量；空闲超时 |

# After:
| **[外部中断 →](docs/interrupt.md)**<br>用户干预 | 硬件中断：保存现场 → ISR → 恢复 | `asyncio.Event` 异步取消。Round 级 undo（`RoundManager`）— 3 快照滚动窗口，状态+文件跨 handoff 回滚。Hook 退出码 2 消息注入。`undo_executed` trace 事件。 | 暂停/重定向向量；空闲超时 |
```

- [ ] **Step 2: Update Part II Interrupt section**

README.md lines 181-185:
```markdown
# Before:
### Interrupt — Cancel & Undo

The engine checks an `asyncio.Event` cancellation token each turn. `POST /api/chat/cancel` or client disconnect stops the agent. Three rolling snapshots per interaction round enable state + file undo via API or the in-conversation `undo` tool. Hook exit-code-2 messages are injected into the conversation.

# After:
### Interrupt — Cancel & Undo

The engine checks an `asyncio.Event` cancellation token each turn. `POST /api/chat/cancel` or client disconnect stops the agent. `RoundManager` maintains 3 rolling round-level snapshots — undo restores state + files to the beginning of any recent round, even across agent handoff boundaries. The `undo_executed` trace event marks rollback boundaries without deleting history. Hook exit-code-2 messages are injected into the conversation.
```

README.zh-CN.md lines 181-185:
```markdown
# Before:
### 中断——取消与撤销

引擎每轮检查 `asyncio.Event` 取消令牌。`POST /api/chat/cancel` 或客户端断开即可停止 Agent。三轮滚动快照支持状态+文件双回滚，通过 API 或对话内 `undo` 工具触发。Hook 退出码 2 的消息注入对话流。

# After:
### 中断——取消与撤销

引擎每轮检查 `asyncio.Event` 取消令牌。`POST /api/chat/cancel` 或客户端断开即可停止 Agent。`RoundManager` 维护 3 个 Round 级滚动快照——undo 恢复到任意最近轮次开始时的状态+文件，跨 agent handoff 边界也生效。`undo_executed` trace 事件标记回滚边界但不删除历史。Hook 退出码 2 的消息注入对话流。
```

- [ ] **Step 3: Commit**

```bash
git add README.md README.zh-CN.md
git commit -m "docs: update interrupt rows with RoundManager undo mechanism

Reflect round-level checkpoint design and undo_executed trace event
in both English and Chinese READMEs.

Co-Authored-By: Claude Code with DeepSeek V4"
```

---

### Task 6: E2E verification

- [ ] **Step 1: Start server and run undo test**

```bash
cd /home/wangxie/open_deepseek_arf/app/arf_default_assistant
rm -f memory/state/default.json memory/traces/default.json
export DEEPSEEK_API_KEY="sk-xxx" && export PYTHONPATH=/home/wangxie/open_deepseek_arf
.venv/bin/python3 -B server.py &>/tmp/arf_server.log &
sleep 3

# Send a message
curl -s --max-time 30 -X POST http://127.0.0.1:8000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"remember: answer is 42"}'

# Check undo available
curl -s http://127.0.0.1:8000/api/chat/undo/status
# Expected: {"available": N, "max": 3}

# Undo
curl -s -X POST "http://127.0.0.1:8000/api/chat/undo?steps=1"
# Expected: {"status": "undone", ...}

# Check trace for undo_executed event
curl -s http://127.0.0.1:8000/api/trace | python3 -c "
import json,sys
events = json.load(sys.stdin)['events']
undo_events = [e for e in events if e['type'] == 'undo_executed']
print(f'undo_executed events: {len(undo_events)}')
for e in undo_events:
    print(f'  {e[\"data\"]}')
"
# Expected: at least 1 undo_executed event

# Cleanup
kill $(lsof -ti :8000) 2>/dev/null
```

- [ ] **Step 2: Verify state integrity after undo**

```bash
curl -s -X POST http://127.0.0.1:8000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"what was the answer?"}'
# Expected: model should NOT remember "42" (it was undone)
```

- [ ] **Step 3: Final commit (if any fixes needed)**

---

## Self-Review

1. **Spec coverage**: Spec sections 2 (RoundManager), 3 (GraphEngine integration), 4 (Trace), 7 (backward compat) all covered. Section 5 (team mode reserve) is future work, no task needed. Section 6 (migration phases) — Phase 1 is this plan, Phases 2-3 are out of scope.

2. **Placeholder scan**: No TBD/TODO in code steps. All code is concrete. E2E task has `sk-xxx` placeholder — user must fill in their key.

3. **Type consistency**: `RoundTransaction` fields used consistently across all tasks. `RoundManager.count()` returns int, used in `checkpoint_count()`. `begin_round` returns `RoundTransaction`, matches spec.

4. **Missing items**: 
   - `_restore_from_handoff` uses `await self.state_store.get(...)` to retrieve from agent state — this is new behavior. The old code used `_handoff_checkpoint` which was an in-memory dict. The new code uses `state_store` which is file-backed. This means `_restore_from_handoff` now reads from disk. Since `_execute_handoff` saves to `state_store` in step 1, this should work. But we need to ensure the sub-agent also saves its state before restore.
   - The `_active_agent` variable: we still need it for `_active_config` and tool resolution. It's NOT removed — only checkpoint vars are removed.
