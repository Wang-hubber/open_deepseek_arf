# Trace Plugin — 跨切面事件记录

挂载在全部 9 个 hook 点（全部 `side` 模式），将引擎事件记录为 JSONL 文件。

---

## 事件类型

记录所有 `AgentEvent` 类型（共 26 种），按 session 分文件：

```
{state_dir}/traces/{session_id}.jsonl
```

## 记录内容

每个 hook 触发时写入该 hook 对应的引擎事件：
- `session_start` / `session_end` — 会话边界
- `round_start` / `round_end` — Round 边界
- `turn_start` / `turn_end` — Turn 边界
- `pre_action` / `post_action` — 调度前后
- `model_call_start` / `model_call_end` — 模型调用
- `tool_call_start` / `tool_call_end` / `tool_call_result` — 工具执行
- `error` — 异常事件
- `session_policy_switch` — 模式切换
- `approval_required` / `approval_resolved` — 审批事件
- `guard_block` / `guard_pass` — 安全事件
- `undo_executed` / `rollback_executed` — 回滚事件
- `compaction_start` / `compaction_end` — 压缩事件

## 配置快照

`EnvSnapshotBuilder` 在 session_start 时生成配置快照：
- 扫描 `plugins_root` 获取所有工具/技能/插件配置
- 生成确定性 XML → SHA256 哈希
- 内容寻址：相同配置产生相同标识符，可跨部署复现

## 公共 API

- `read_trace(session_id)` — 读取指定会话的完整 JSONL trace
- `list_sessions()` — 枚举所有已记录的会话

## 配置快照

首次 trace 写入时自动生成配置快照，保存到 `{trace_dir}/snapshots/{hash}.xml`。内容寻址——相同配置复用同一快照文件。

```yaml
plugins:
  - trace

plugins_config:
  trace:
    trace_dir: ./data/traces      # 可手动覆盖，默认随 data_path
```

框架启动时自动注入 computed `trace_dir`（来自 `data_path`），无需手动配置。仅在需要独立路径时才覆盖。
