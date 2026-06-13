# Undo Plugin — Round 级 Checkpoint + 回滚

Round 级别的对话回滚能力。每轮开始时自动快照状态和工作区文件，支持 N 步撤销。

---

## 架构

```
round_start → UndoPlugin.on_hook() → RoundManager.begin_round()
                                        ├── deepcopy(AgentState)
                                        └── 快照 workspace → data/checkpoints/{N}/

round_end   → UndoPlugin.on_hook() → RoundManager.close_round()
                                        └── 标记 round 完成

undo tool   → _engine.undo(N) → UndoPlugin.undo() → RoundManager.undo(N)
                                                      ├── pop N rounds
                                                      ├── 恢复 state snapshot
                                                      └── 恢复 workspace 文件
```

## 配置

```yaml
plugins:
  - undo

advanced:
  max_undo_depth: 3   # 最大回滚步数（滑动窗口）
```

## 持久化

RoundManager 将 checkpoint 持久化到 `data/checkpoints/`：
- `rounds.json` — round 索引（round_id、round_num、agent_trace）
- `data/checkpoints/{N}/state.json` — 完整状态快照
- `data/checkpoints/{N}/` — 工作区文件副本

框架重启后自动从磁盘恢复，undo 立即可用。

## 工具

`undo` 工具 — Agent 可自行调用的回滚接口：

- `steps`（int, 默认 1）：回滚 N 轮
- 返回：`{ok, steps, messages_restored, remaining_checkpoints}`

## 安全

- `.git` 目录在快照和恢复中均被跳过
- 深拷贝确保快照不受后续状态变更影响
- 超过 `max_undo_depth` 的旧 round 自动淘汰
