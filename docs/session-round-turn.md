# ARF 会话层级模型：Session > Round > Turn

> 框架会话生命周期的唯一权威术语定义。所有模块、事件、trace、eval 均以此为准。

## 1. 层级定义

```
session  >  round  >  turn
```

### session（会话）

一次完整的多轮对话，有独立的 `state_store`、`event_bus` 和 trace 文件。由 `session_id` 标识，跨多次 `agent.chat()` 调用。

### round（轮）

一次 `user_input` 到 `final_output` 的完整交互。对应引擎外层 `while not aborted` 循环的一次迭代。

- 引擎变量：`_interaction_round`（`control_plane.py:62`）
- session 内全局递增，跨 `chat()` 不重置
- 一个 round = 一个 eval case

### turn（步）

Agent ReAct 循环中的一次原子步骤：一次 `model_call` + 可选的 `tool_calls`。

- 引擎变量：`current_turn`（`control_plane.py:138`）
- session 内全局递增，跨 round 不重置
- 一个 round 包含 1~N 个 turn
- 最后一个 turn 只有 `model_call`（无 `tool_calls`），模型返回纯文本后 turn loop 退出

## 2. 各层命名规范

### 汇总表

| 层级 | 控制平面变量 | Trace JSONL 字段 | AgentEvent 字段 | PluginContext | Eval 概念 |
|------|-------------|-----------------|-----------------|---------------|-----------|
| session | `session_id` | `session_id` | `session_id` | `session_id` | `EvalCase.session_id` |
| round | `_interaction_round` | `round` | `data["round"]` | `interaction_round` | case 边界过滤，Runner `_last_round` |
| turn | `current_turn` | `turn` | `event.turn`, `data["turn"]` | `turn` | `TurnEfficiencyMetric`, `max_turns`, `golden_trajectory.turns` — eval 评估的就是 ReAct 步数 |

### 控制平面（`arf/engine/control_plane.py`）

```
while not aborted:                      # ← round loop
    _interaction_round += 1
    # round_start hooks
    while True:                         # ← turn loop
        current_turn += 1
        # model_call → [tool_calls → model_call → ...] → text → break
    # round_end hooks + exit check
```

### Trace（`arf/plugins/trace/plugin.py`）

每条事件记录：

```json
{
  "type": "...",
  "session_id": "...",
  "round": 1,
  "turn": 3,
  "data": {...}
}
```

- `round` ← `context.interaction_round`
- `turn` ← `context.turn`（即 `current_turn`）

### EventBus（`arf/core/events.py`）

```
AgentEvent:
  .turn          ← current_turn
  .data["turn"]  ← current_turn
  .data["round"] ← _interaction_round
```

### Eval（`arf/plugins/eval/`）

Eval 平面评估的是 **case → turn** 关系——一次 user_input 到 final_output 中用了多少步 ReAct。因此：

- `EvalCase.max_turns` — 期望最大 ReAct 步数（**保留原名，不改为 max_rounds**）
- `TurnEfficiencyMetric` / `turn_efficiency` — 越少越强（**保留原名**）
- `golden_trajectory.turns` — 每步 model_call 是一条 golden turn（**保留原名**）
- Runner `_last_round` — 跨 case 跟踪 round 边界，用 trace `"round"` 字段

## 3. 关键规则

1. 一次 `chat()` = 一个 round = 一个 eval case（gate 未超限时）
2. 一个 round 包含 1~N 个 turn（tool 调用循环时 `current_turn` 递增）
3. round 编号 session 内全局递增，跨 `chat()` 不重置
4. turn 编号 session 内全局递增，跨 round 不重置
5. Eval 跨 case 共享 session：同源 `session_id` 复用 eval session，`round > prev_round` 过滤隔离当前 case

## 4. 数据流

```
Engine:
  _interaction_round += 1        # round++
  while True:
      current_turn += 1          # turn++
      _make_event():
          event.turn = current_turn
          data["turn"] = current_turn
          data["round"] = _interaction_round
      → AgentEvent → EventBus

TracePlugin (side hook):
  record["round"] = context.interaction_round
  record["turn"] = context.turn
  → JSONL trace file

Eval Runner:
  读 trace["round"] 过滤 case 边界
  _last_round 跟踪上一个 case 的 max round
```

## 5. 实施记录

已实施（2026-06-11），计划见 `docs/superpowers/plans/2026-06-11-session-round-turn-terminology.md`。

### 5.1 已改

| 文件 | 改动 |
|------|------|
| `arf/plugins/trace/plugin.py` | 事件记录 `"round"` = `interaction_round`，`"turn"` = `current_turn` |
| `arf/plugins/eval/runner.py` | case 边界过滤用 `e.get("round", e.get("turn", 0))` fallback |
| `tests/test_trace_plugin.py` | 断言更新为 `"round"` 字段 |

### 5.2 不改的（eval 层命名本就正确）

| 保持不变 | 原因 |
|----------|------|
| `EvalCase.max_turns` | Eval 评估 ReAct 步数，不是 round 数 |
| `TurnEfficiencyMetric`, `turn_efficiency` | 衡量 agent 用几步完成，越少越强 |
| `golden_trajectory.turns` | 每步 model_call 是一条 golden turn |
| `_build_golden_turns()` | trace 修好后 `"turn"` 是真正的 current_turn |
| `EvalSummary.avg_turns` | 同上 |
| `_interaction_round`, `current_turn` | 引擎内部已是正确命名 |

### 5.3 兼容策略

- **Trace 读取**：`e.get("round", e.get("turn", 0))` — 新 trace 有 `"round"`，旧 trace `"turn"` 里存的是 round
- **Builder/Metrics**：读 `e.get("turn")` 即可——新 trace 存的是真正的 current_turn，旧 trace 存的是 interaction_round（只有 1 个值），行为向后兼容
