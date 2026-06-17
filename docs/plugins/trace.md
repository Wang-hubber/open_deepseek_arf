# Trace Plugin — 跨切面事件记录与回放

挂载全部 9 个 hook 点（全部 `side`），将引擎事件追加写入 session 级 JSONL 文件。同时生成内容寻址的配置快照，支持跨部署复现。

---

## 1. 文件结构

```
data/
└── {session_id}/
    └── traces/
        └── {session_id}.jsonl    # 追加写入，每行自包含 JSON

data/
└── snapshots/
    └── {hash}.xml                # 配置快照，同配置复用
```

`data_dir` 由 BaseAgent 注入 `set_data_dir()`，默认 `./data`。

---

## 2. 记录格式

每条 JSONL 行：

```json
{
  "type": "post_action",
  "round": 3,
  "turn": 12,
  "timestamp": 1718000000.123,
  "data": { ... },
  "session_id": "abc123",
  "config_hash": "a1b2c3d4e5f6"
}
```

`config_hash` 在首次写入时自动生成——内容寻址，相同配置产生相同 hash。

---

## 3. 事件注入机制

引擎通过 `ctx.inject_engine_event()` 将诊断事件注入 `hook_data._engine_events`。TracePlugin 在**每次 hook 回调**时排空该列表，将每个事件扁平化为独立 JSONL 行。

这意味着即使循环在 `post_action` 前中断（如 break / error），已注入的事件也不会丢失。

```
ctx.inject_engine_event("model_call_start", {model: "v4", tokens: 4500})
ctx.inject_engine_event("model_call_end",   {output: "...", latency: 2.3})

→ hook_data._engine_events = [event1, event2]
→ TracePlugin.on_hook() flattens both → 2 JSONL rows
```

---

## 4. 事件类型

`AgentEvent.type` 定义在 `arf/core/events.py`，共 29 种：

| 类别 | 事件 |
|------|------|
| 会话边界 | `session_start`, `session_end` |
| 交互边界 | `user_input` |
| Round/Turn | `round_start`, `round_end`, `turn_start`, `turn_end` |
| 调度 | `pre_action`, `post_action` |
| 模型调用 | `model_call_start`, `model_call_end`, `thinking_delta` |
| 工具调用 | `tool_call_start`, `tool_call_end`, `tool_call_result` |
| Hook | `hook_start`, `hook_end` |
| 压缩 | `compaction_start`, `compaction_end` |
| 截断 | `truncation_start`, `truncation_end`, `safeguard_triggered` |
| 安全 | `guard_block`, `guard_pass` |
| 审批 | `approval_required`, `approval_resolved` |
| 回滚 | `undo_executed`, `rollback_executed` |
| 模式 | `session_policy_switch` |
| 保护 | `rate_limited`, `circuit_opened`, `circuit_half_open`, `circuit_closed`, `breaker_blocked` |
| 错误 | `error` |
| 反馈 | `user_annotation` |

---

## 5. 配置快照

`EnvSnapshotBuilder` 在首次 trace 写入时（lazy）扫描：

- `plugins_root` 下所有 `plugin.yaml`、`tool.yaml`、`function.py`、skill yaml
- `extra_files`（如 `agent.yaml`）
- `extra_roots` 下的 `tools/` 和 `skills/` 目录

生成确定性 XML → SHA256 取前 12 位 → 写入 `data/snapshots/{hash}.xml`。内容寻址：相同配置复用同一快照文件，可跨部署复现。

XML 结构：

```xml
<snapshot created_at="..." hash="a1b2c3d4e5f6">
  <agent>
    <config src="agent.yaml">...</config>
  </agent>
  <resources root=".">
    <tools>...</tools>
    <skills>...</skills>
  </resources>
  <plugins root="./arf/plugins">
    <plugin name="memory">
      <config src="plugin.yaml">...</config>
      <tools>...</tools>
    </plugin>
    ...
  </plugins>
</snapshot>
```

---

## 6. 数据清洗

非 JSON 可序列化值自动转换：Exception → `"TypeName: message"`，其他不可序列化类型 → `str()`。保证写入不抛异常。

---

## 7. 公共 API

```python
plugin = TracePlugin({"data_dir": "./data"})

# 读 session 完整 trace
events = plugin.read_trace("session_123")   # → list[dict]

# 枚举所有已记录 session
sessions = plugin.list_sessions()           # → list[str]
```

---

## 8. 配置

```yaml
plugins:
  - trace

plugins_config:
  trace:
    data_dir: ./data             # 数据根目录（默认 ./data）
    enabled: true                # 是否启用（默认 true）
    plugins_root: ./arf/plugins  # 插件扫描根（默认 ./arf/plugins）
    config_files:                # 额外配置文件加入快照
      - ./agent.yaml
    extra_roots:                 # 额外扫描根（app 级 tools/skills）
      - .
```

框架通过 `set_data_dir()` 自动注入 computed `data_dir`，通常无需手动配置。`plugins_root`、`config_files`、`extra_roots` 仅用于快照生成，不影响 trace 写入路径。
