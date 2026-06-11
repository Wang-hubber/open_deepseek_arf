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
| round | `_interaction_round` | `round` | `data["round"]` | `interaction_round` | case 边界, `max_rounds`, `golden_trajectory.rounds` |
| turn | `current_turn` | `turn` | `event.turn`, `data["turn"]` | `turn` | round 内的 ReAct steps |

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

- `EvalCase.max_rounds` — 期望最大 round 数
- `golden_trajectory.rounds` — 每个元素是一个 round
- `RoundEfficiencyMetric` — 按 round 统计效率
- Runner `_last_round` — 跨 case 跟踪 round 边界

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

## 5. 迁移计划

### 5.1 待改文件清单

| 文件 | 改动 |
|------|------|
| `arf/plugins/trace/plugin.py` | 事件记录：`"turn"` → `"round"`，新增 `"turn"` = `context.turn` |
| `arf/plugins/eval/models.py` | `max_turns` → `max_rounds`，`avg_turns` → `avg_rounds` |
| `arf/plugins/eval/builder.py` | `_build_golden_turns()` → `_build_golden_rounds()`，trajectory 中 `turns` → `rounds` |
| `arf/plugins/eval/runner.py` | `_extract_trace_stats` 中 `turns` → `rounds` |
| `arf/plugins/eval/metrics.py` | `TurnEfficiencyMetric` → `RoundEfficiencyMetric` |
| `arf/plugins/eval/comparator.py` | 如有 turn 引用一并修正 |

### 5.2 兼容策略

- **Trace 读取**：优先读 `round`，fallback 到旧 `turn` 字段（兼容已有 trace 文件）
- **Benchmark JSON**：`from_json` 同时接受 `max_turns`/`max_rounds`，`to_json` 只写 `max_rounds`

### 5.3 不改的（控制平面内部已是正确命名）

- `_interaction_round` — 已是 round 的正确名称
- `current_turn` — 已是 turn 的正确名称
- `AgentEvent.turn` / `data["turn"]` / `data["round"]` — 已正确对应
