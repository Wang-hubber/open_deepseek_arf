# ARF Fact-Check Report — 2026-05-28 — Interrupt

## Summary
- **Total tests**: 101
- **Passed**: 97
- **Failed**: 4 (automated findings)
- **Findings**: 5 (2 Warning, 3 Info)

## Findings

### Warning — State snapshot not restored on undo (§2.x)

**Doc 声称**: `undo(steps=N)` restores state from the target round, returning the prior state snapshot.

**代码实际**: `RoundManager.undo()` returns the current/latest state rather than the restored prior state.

**测试**: `test_undo_restores_state_snapshot`, `test_undo_multiple_steps`

**根因**: `undo()` 的 snapshot 回退逻辑未正确实现，或返回的是 pop 后的最新状态而非目标 snapshot。

### Warning — `state_store.put()` not called in engine invoke/astream (§2.x)

**Doc 声称**: GraphEngine 在每轮 turn 结束后调用 `state_store.put()` 持久化状态（checkpoint）。

**代码实际**: `InMemoryStateStore.put.call_count == 0` — invoke 和 astream 路径中均未调用 `state_store.put()`。

**测试**: `test_invoke_calls_state_store_put_at_turn_end`, `test_astream_calls_state_store_put`

**根因**: 可能是 `BUG-001` 修复（"GraphEngine break bypassed checkpoint"）的回归，或测试中 engine 配置不完整导致未走到 put() 分支。

### Info — RoundManager round counting needs review

`begin_round()` 和 `count()` 的行为在测试中表现不一致——某些边界条件下计数不匹配预期。

## Verified Claims

### 文件存在性
- [x] `arf/errors/policy.py` — DefaultErrorPolicy
- [x] `arf/errors/actions.py` — error actions
- [x] `arf/engine/round_manager.py` — RoundManager, RoundTransaction
- [x] `arf/engine/checkpoint.py` — checkpoint logic
- [x] `arf/engine/graph.py` — invoke/astream with cancel/undo
- [x] `arf/resources/backends/function.py` — FunctionBackend rollback

### Cancel/Interrupt 机制
- [x] GraphEngine 有 `cancel_event` 属性（`asyncio.Event | None`）
- [x] `set_cancel_event()` 注入 cancel 事件
- [x] `cancelled()` 方法匹配文档签名
- [x] cancel event 为 None 时 `cancelled` 返回 False
- [x] invoke 在 cancelled 时 break
- [x] astream 在 cancelled 时 break
- [x] `_close_tool_calls()` 覆盖异常退出场景

### State/Checkpoint
- [x] `FileStateStore` 有 `put()` 和 `get()` 方法
- [x] `FileStateStore` 默认目录 `./memory/state`
- [x] `FileStateStore` 持久化 JSON + 原子写入
- [x] `FileStateStore.get()` 恢复状态
- [x] `FileStateStore.delete()` 删除文件
- [x] `InMemoryStateStore` 有 `put()/get()` + snapshot/reset 支持

### RoundManager
- [x] `RoundManager(max_undo_depth=5)` 使用 `deque(maxlen=5)`
- [x] `RoundTransaction` dataclass 字段完整
- [x] `begin_round()` 递增计数
- [x] `record_handoff()` 记录 handoff 信息
- [x] `close_round()` 标记已关闭

### Error Policy
- [x] `DefaultErrorPolicy` 构造函数参数完整
- [x] `tool_retry` / `model_5xx_action` 字段存在
- [x] `FunctionBackend` rollback_fn 支持 + rolled_back 标记

### Tool Provider Rollback
- [x] ToolProvider 有 `_kernel_rollbacks` 和 `_rollbacks` dict
- [x] ToolResult 有 `rolled_back` 和 `rollback_error` 字段

### 引擎集成
- [x] BaseAgent 从 `AdvancedConfig.errors` wiring error policy
- [x] HandoffManager 记录 round 信息

## Test Suite
- **文件**: `tests/fact_check/test_interrupt.py`
- **结构**: 20 个 TestClass，101 个测试方法
- **覆盖**: 文档全部章节，focus on cancel/undo/rollback/checkpoint
