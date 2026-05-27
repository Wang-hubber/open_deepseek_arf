# Round-Level Checkpoint & Undo — Design Spec

## 目标

将检查点/Undo 机制从 per-agent 栈重构为 **round 级事务**，保证：
1. 一个用户交互轮次（跨多次 handoff）作为原子 undo 单元
2. 多 Agent team 模式下的一致性
3. Trace 不可篡改但可标记 undo 边界
4. 为持久化检查点提供数据模型基础

---

## 1. 当前问题

### 1.1 检查点碎片化

```
主 Agent → handoff → sub-agent
  │                       │
  ├─ _checkpoints = [...]  │
  └─ _agent_checkpoints    ├─ _checkpoints = deque(maxlen=3)  ← 全新的空栈
     ["main"] = [...]      │
                           └─ push_checkpoint → [cp_sub_0]
```

- `push_checkpoint` 只在 `BaseAgent.chat/astream` 入口调用一次
- handoff 时主 Agent 的检查点存入 `_agent_checkpoints`，sub-agent 用空栈
- undo 只操作 `self._checkpoints`，无法跨 agent 边界回滚

### 1.2 状态分散

```
GraphEngine 实例变量：
  self._checkpoints: deque[dict]           ← per-agent 栈
  self._agent_states: dict[str, AgentState]
  self._agent_checkpoints: dict[str, deque]
  self._handoff_checkpoint: dict | None    ← 单一 handoff 上下文
  self._interaction_round: int
```

同一个 round 的状态散落在 5 个变量中，没有统一的生命周期管理。

### 1.3 Trace 盲区

undo 不产生任何事件。Trace 是只增不改的审计日志，但缺少 `undo_executed` 标记，看 trace 的人无法判断哪些回合后来被撤销了。

---

## 2. 核心抽象：`RoundManager`

### 2.1 概念

```
RoundManager
  ├─ rounds: deque[RoundTransaction]  (maxlen=3)
  ├─ active_round: RoundTransaction | None
  └─ persistent_store: Path | None  (预留)
```

一个 `RoundTransaction` 代表一个用户交互轮次的完整状态快照，无论该轮次涉及多少个 agent 周转。

### 2.2 生命周期

```
begin_round(state)          ← 用户消息进入时
    │
    ├─ 主 Agent turns
    │     │
    │     └─ handoff → sub-agent
    │           │
    │           ├─ sub-agent turns
    │           └─ handoff → 主 Agent (回传)
    │
    ├─ 主 Agent turns (继续)
    │
close_round(state)          ← 返回最终回复前 (no-op，留钩子)
    │
    ▼
undo(steps=1)               ← 任何时机，恢复到 round-1 状态
```

### 2.3 数据模型

```python
@dataclass
class RoundTransaction:
    """一个用户交互轮次的完整状态快照。"""

    round_id: str                         # "session_id/round_num"
    round_num: int                        # 自增序号，重启不归零

    # 主快照：begin_round 时的状态
    state_snapshot: dict                  # deepcopy(AgentState)
    workspace_snapshot_dir: str | None    # memory/checkpoints/{round_num}/

    # 元数据
    created_at: float                     # time.time()
    agent_trace: list[str]                # ["main", "sys_agent", "main"] — 参与顺序
    handoff_count: int                    # 该 round 内发生了几次 handoff
    closed: bool                          # close_round() 调用过

    # 增量：撤销时的附加操作
    dirty_files: dict[str, str] | None    # {rel_path: original_content} — 大文件优化（预留）
```

### 2.4 RoundManager

```python
class RoundManager:
    def __init__(self, max_undo_depth: int = 3):
        self._rounds: deque[RoundTransaction] = deque(maxlen=max_undo_depth)
        self._active: RoundTransaction | None = None
        self._current_round: int = 0
        self._participants: list[str] = []  # 当前 round 参与的 agent 名

    def begin_round(self, state: AgentState, workspace_dir: str) -> RoundTransaction:
        """Start a new round. Push snapshot of current state."""
        self._current_round += 1
        tx = RoundTransaction(
            round_id=f"{state['session_id']}/{self._current_round}",
            round_num=self._current_round,
            state_snapshot=copy.deepcopy(dict(state)),
            created_at=time.time(),
            agent_trace=[state.get("active_agent") or state.get("agent_name", "main")],
            handoff_count=0,
            closed=False,
        )
        # Snapshot workspace files
        tx.workspace_snapshot_dir = self._snapshot_workspace(workspace_dir, self._current_round)

        self._rounds.append(tx)
        self._active = tx
        self._participants = list(tx.agent_trace)
        return tx

    def record_handoff(self, from_agent: str, to_agent: str) -> None:
        """Record agent switch within the active round."""
        if self._active:
            self._active.agent_trace.append(to_agent)
            self._active.handoff_count += 1
            self._participants.append(to_agent)

    def close_round(self) -> None:
        """Mark the active round as complete."""
        if self._active:
            self._active.closed = True
            self._active = None
            self._participants = []

    def undo(self, steps: int, workspace_dir: str) -> AgentState | None:
        """Pop N rounds, restore to round_num - steps state.
        Returns the state snapshot of the round BEFORE the undone range,
        or None if insufficient rounds.
        """
        if steps < 1 or steps > len(self._rounds):
            return None

        # Discard the most recent N rounds
        target = None
        for _ in range(steps):
            target = self._rounds.pop()

        if target is None:
            return None

        # Restore files from target snapshot
        self._restore_workspace(target, workspace_dir)

        # Clean up checkpoint directories >= target round
        self._cleanup_checkpoint_dirs(target.round_num, workspace_dir)

        self._active = None
        self._participants = []

        return copy.deepcopy(target.state_snapshot)

    def count(self) -> int:
        return len(self._rounds)

    def active_agents(self) -> list[str]:
        return list(self._participants)
```

---

## 3. GraphEngine 集成

### 3.1 变更点

```diff
class GraphEngine:
    def __init__(self, ...):
-       self._checkpoints: deque[dict] = deque(maxlen=3)
-       self._agent_checkpoints: dict[str, deque] = {}
-       self._agent_states: dict[str, AgentState] = {}
-       self._handoff_checkpoint: dict | None = None
+       self._rounds = RoundManager(max_undo_depth=3)
```

### 3.2 push_checkpoint → begin_round

```python
# base.py — chat/astream 入口
# 当前:
engine.push_checkpoint(state)

# 新:
engine.rounds.begin_round(state, workspace_dir)
```

### 3.3 handoff 集成

```python
# graph.py — _execute_handoff
async def _execute_handoff(self, state, handoff_data, current_model):
    from_agent = state.get("active_agent") or "main"
    to_agent = await self._handoff_manager.resolve(from_agent, handoff_data)

    # Round 继续，不切换检查点栈
    self._rounds.record_handoff(from_agent, to_agent)

    # Agent 状态保存（用于 sub-agent 上下文恢复）
    await self.state_store.put(f"{session_id}/{from_agent}", state)

    # 构建 target context（逻辑不变）
    ...

    # 切换到目标 agent
    state["active_agent"] = to_agent
    return state
```

关键变化：handoff 时**不再切换 checkpoint 栈**，只记录 `agent_trace`。

### 3.4 undo 方法

```python
# graph.py
def undo(self, steps: int = 1, workspace_dir: str = "") -> AgentState | None:
    restored = self._rounds.undo(steps, workspace_dir)
    if restored:
        self._emit("undo_executed", {
            "from_round": restored.get("interaction_round", 0) + steps,
            "to_round": restored.get("interaction_round", 0),
            "steps": steps,
        })
    return restored

def checkpoint_count(self) -> int:
    return self._rounds.count()
```

### 3.5 undo 工具 / API 端点

```python
# undo/function.py — 不变
# server.py undo 端点 — 不变
# 两者都调用 engine.undo()，新实现自动处理多 agent 场景
```

---

## 4. Trace 集成

### 4.1 新增事件

```python
EventType = Literal[
    ...
    "undo_executed",  # 新增
]
```

### 4.2 事件结构

```json
{
    "type": "undo_executed",
    "data": {
        "from_round": 5,
        "to_round": 3,
        "steps": 2,
        "agent_trace": ["main", "sys_agent", "main"],
        "timestamp": 1717000000.0
    },
    "turn": 0,
    "session_id": "default"
}
```

### 4.3 FileTraceStore 行为

- **不删除**任何已写入的 trace 事件
- `undo_executed` 像普通事件一样追加到 trace 文件
- 前端解析 trace 时：遇到 `undo_executed` 知道 `from_round → to_round` 之间的回合已被撤销，渲染时加删除线或折叠

### 4.4 前端 TraceView 适配（实施计划阶段细化）

```
Round 0 → Round 1 → Round 2 ──── undo_executed(from=2, to=0) ──── Round 2' → Round 3
                                  │
                                  Round 1 和 Round 2 的 UI 渲染：
                                  ─ 折叠/灰度显示
                                  ─ tooltip: "已通过 Undo 撤销"
```

---

## 5. 多 Agent Team 模式预留

### 5.1 Round 内多 Agent 的两种模式

```
// Sequential (handoff 链) — 当前支持
Main → sub_1 → sub_2 → Main

// Parallel (team 模式) — 未来
Main ┬─ sub_1 ─┐
     └─ sub_2 ─┴─ Main (merge)
```

### 5.2 当前设计对 Team 模式的兼容性

`RoundTransaction.agent_trace` 当前是线性的 `list[str]`。Team 模式下可扩展为：

```python
# 未来扩展
@dataclass
class RoundTransaction:
    ...
    agent_trace: list[str]                     # 线性的，handoff 用
    parallel_groups: list[list[str]] | None    # 并行的，team 用
```

并行时 `record_handoff` 不适用——改用 `record_fork(agents)` / `record_join()`。但 `begin_round → undo` 的核心抽象不变：**一个 round = 一个检查点**。

---

## 6. 迁移计划

### 阶段 1：RoundManager 实现（本次）
1. 新增 `arf/engine/round_manager.py` — `RoundTransaction` + `RoundManager`
2. 更新 `GraphEngine.__init__` — 用 `RoundManager` 替换 5 个分散变量
3. 更新 `BaseAgent.chat/astream` — `push_checkpoint` → `rounds.begin_round`
4. 更新 `_execute_handoff` — 移除 checkpoint 栈切换，调用 `rounds.record_handoff`
5. 新增 `undo_executed` 事件，在 `undo()` 中 emit
6. 移除 `_agent_checkpoints`、`_agent_states` 等变量（agent 状态持久化走 `state_store`）

### 阶段 2：持久化（后续）
1. `RoundTransaction` 序列化到 `memory/checkpoints/rounds.json`
2. `RoundManager.__init__` 启动时加载已有 rounds
3. 重启后 undo 可用

### 阶段 3：Team 模式扩展（后续）
1. `agent_trace` → 支持 fork/join 结构
2. `parallel_groups` 字段
3. undo 时恢复所有并行 agent 的状态到 fork 点

---

## 7. 向后兼容

- `GraphEngine.undo()` 签名不变
- `GraphEngine.checkpoint_count()` 签名不变
- `BaseAgent.chat/astream` 调用方式不变
- `undo` 工具 function.py 不变
- server.py `/api/chat/undo` 端点不变
- agent.yaml 配置不变

唯一破坏性变更：`push_checkpoint()` 方法被 `rounds.begin_round()` 替代。如果有外部代码直接调用 `push_checkpoint()`，需要适配。

---

## 8. 测试要点

| 测试场景 | 预期行为 |
|----------|----------|
| 单 Agent, undo 1 步 | 恢复到上一轮用户消息前 |
| 主→sub→主, undo 1 步 | 恢复到 handoff 前的用户消息 |
| Undo 后继续对话 | 状态正确，无 400 错误 |
| Undo 步数超出 | 返回 None，不崩溃 |
| Undo 后 Trace | `undo_executed` 事件在 trace 尾部 |
| Undo 后文件 | workspace 文件恢复到目标 round 状态 |
| 3 次 round 后 undo 到最早 | oldest cp 被正确恢复，无残留文件 |
| 频繁 undo + 继续对话 | 无内存泄漏，checkpoint 数量正确 |
