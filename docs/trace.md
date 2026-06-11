# Trace & Observability

> **原则：引擎负责注入数据，TracePlugin 负责消费落盘，EventBus 只做实时推送。三者各司其职。**

ARF 将 Trace 作为一等框架能力。TracePlugin 作为 side plugin 挂载在所有 hook 点，引擎通过 `ctx.inject_engine_event()` 注入事件到 `hook_data`，TracePlugin 在 `round_start` 和 `post_action` 展平写入 JSONL。每条 session 对应一个 `{session_id}.jsonl` 文件，可支撑调试回放、测评数据集构建和调优。

---

## 1. OS 方案演进

> 本章描述 OS 如何实现系统监控与事件日志，作为 ARF 设计思路的参考。非严格技术对标。

### 1.1 系统监控的演进

**syslog（1980s）** — Unix 的标准日志协议。进程调用 `syslog()` 写入带 facility 和 severity 的消息，`syslogd` 守护进程按配置将消息路由到文件、控制台或远程服务器。问题：非结构化文本，难以查询和分析。

**Windows Event Log（1993）** — 结构化事件（Event ID + 分类 + 描述），集中存储在二进制文件中，提供 API 查询。比 syslog 更结构化但平台绑定。

**systemd-journal（2011）** — Linux 的结构化日志替代方案。日志条目是键值对而非自由文本。支持按任意字段索引查询（`journalctl PRIORITY=3`）。二进制存储保证了写入原子性。

**eBPF（2014+）** — 在内核中安全地运行沙箱化的程序，无需重新编译内核。可以观测内核中的任何函数调用、网络包、系统调用。类似 ARF 的 EventBus 机制：生产者 emit 事件，消费者按需订阅。

### 1.2 分布式追踪

Google Dapper（2010）论文引发了分布式追踪浪潮。OpenTelemetry（2019+）统一了 OpenTracing 和 OpenCensus，成为 CNCF 标准。核心概念：

- **Span**：一个操作单元（如一次 RPC 调用）
- **Trace**：由多个 span 组成的有向无环图
- **Context Propagation**：trace_id + span_id 在服务间传递，串联整个调用链

### 1.3 对 ARF 的启发

eBPF 的事件驱动模型影响了 ARF 的 EventBus 设计——生产者 emit 事件，消费者按需订阅（SSE、streaming）。systemd-journal 的键值对存储影响了 trace 事件的 flat dict 设计——每行 JSON 自包含，O(1) 追加。OpenTelemetry 的 span 模型影响了事件的分层结构（session → round → turn）。

---

## 2. ARF 当前实现

### 2.1 架构总览

```
ControlPlane
    │
    ├── ctx.inject_engine_event("user_input", {...})          ← round_start 前
    ├── ctx.inject_engine_event("model_call_start", {...})    ← _action_call_model 开头
    ├── ctx.inject_engine_event("model_call_end", {...})      ← _action_call_model 结尾
    ├── ctx.inject_engine_event("tool_call_start", {...})     ← _action_execute_tools 开头
    ├── ctx.inject_engine_event("tool_call_end", {...})       ← _action_execute_tools 结尾
    │
    ├── _fire_side("round_start", ctx)  ──→ TracePlugin.on_hook()   ← 展平 user_input
    └── _fire_side("post_action", ctx)  ──→ TracePlugin.on_hook()   ← 展平 model_* + tool_*
                                                      │
                                                      ▼
                                          {trace_dir}/{session_id}.jsonl
                                                    +
                                          {trace_dir}/snapshots/{hash}.xml


EventBus（独立通道，不落盘）
    │
    ├── _make_event("model_call_end", ...) ──→ SSE 实时推送给 CLI/Web
    ├── _make_event("thinking_delta", ...) ──→ SSE 流式文本
    └── ...

```

**职责分离：**

- **引擎**：通过 `ctx.inject_engine_event()` 注入事件到 `hook_data._engine_events`。引擎不知道 Trace 何时消费、如何落盘
- **TracePlugin**：作为 side hook 挂载。在 `round_start` 展平 `user_input`，在 `post_action` 展平 `model_call_*` 和 `tool_call_*`。异步消费、异步落盘，不阻塞主循环
- **EventBus**：纯实时推送通道。`_make_event()` emit 到 Bus，SSE/streaming 消费者订阅。非持久化，不落盘

### 2.2 TracePlugin

`arf/plugins/trace/plugin.py`。Mount 为 8 个 lifecycle hook 的 side plugin。

**配置（plugin.yaml）：**

```yaml
name: trace
hooks:
  - session_start
  - session_end
  - round_start
  - round_end
  - turn_start
  - turn_end
  - pre_action
  - post_action
enabled: true
config:
  trace_dir: ./data/traces
  plugins_root: ./arf/plugins          # 扫描框架内置插件配置
  config_files:                        # agent 级配置文件
    - ./agent.yaml
    - ./pyproject.toml
  extra_roots:                         # 扫描 app 级 tools/ skills/
    - .
```

**公开 API：**

- `plugin.read_trace(session_id) → list[dict]` — 读取指定 session 的完整轨迹
- `plugin.list_sessions() → list[str]` — 列出所有已记录的 session ID

### 2.3 事件注入

引擎在关键节点通过 `PluginContext.inject_engine_event()` 将内部事件注入 `hook_data._engine_events`：

| 注入点 | 事件类型 | 包含字段 |
|--------|---------|---------|
| `_execute()` round 循环开始 | `user_input` | content |
| `_action_call_model()` 开始 | `model_call_start` | model, turn |
| `_action_call_model()` 结束 | `model_call_end` | model, turn, content, reasoning, tool_calls[], usage |
| `_action_execute_tools()` 开始 | `tool_call_start` | tool_name, id, arguments, turn |
| `_action_execute_tools()` 结束 | `tool_call_end` | tool_name, id, params, turn, success, result, error, duration_ms |
| `ToolGuardPlugin`（deny） | `tool_call_start` + `tool_call_end` | tool_name, id, arguments, success=false, blocked=true, error |
| `ApprovalPlugin`（denied/timeout） | `tool_call_start` + `tool_call_end` | tool_name, id, arguments, success=false, blocked=true, error |

注入的事件在 `round_start`（user_input）和 `post_action`（model/tool 事件）时被 TracePlugin 展平为独立 JSONL 行。被安全策略阻止的工具调用（guard deny、approval denied/timeout）也通过 `inject_engine_event` 注入，确保 trace 和 benchmark 中不丢失。

### 2.4 会话生命周期

```
首次 astream():
  _execute() → session_start（仅一次，_session_opened 门控）
  round_start → + user_input 展平
  turn loop → post_action → + model_* + tool_* 展平
  _execute() 返回（不 emit session_end）

后续 astream():
  _execute() → session_start 跳过（_session_opened=True 已持久化）
  ...同上...

CLI /exit → BaseAgent.stop():
  engine.close(state) → session_end + hooks + 落盘
  invoke() 自动 close（one-shot session）
```

`session_start` 通过 `state["_session_opened"]` 标记确保整段对话只触发一次。`session_end` 通过 `ControlPlane.close()` 显式触发，`invoke()` 自动调用，`astream()` 需要 App 通过 `stop()` 触发。

### 2.5 Trajectory JSONL 格式

每条 JSONL 记录是独立的顶层事件，展平后无嵌套：

```jsonl
{"type": "session_start", "turn": 0, "timestamp": 1718000000.0, "config_hash": "a1b2c3d4", "session_id": "abc123", "data": {}}
{"type": "user_input", "turn": 1, "timestamp": 1718000000.1, "config_hash": "a1b2c3d4", "session_id": "abc123", "data": {"content": "帮我读一下 README.md"}}
{"type": "round_start", "turn": 1, "timestamp": 1718000000.2, "config_hash": "a1b2c3d4", "session_id": "abc123", "data": {}}
{"type": "model_call_start", "turn": 1, "timestamp": 1718000001.0, "config_hash": "a1b2c3d4", "session_id": "abc123", "data": {"model": "deepseek-v3", "turn": 1}}
{"type": "model_call_end", "turn": 1, "timestamp": 1718000002.0, "config_hash": "a1b2c3d4", "session_id": "abc123", "data": {"model": "deepseek-v3", "content": "好的，我来读取", "reasoning": "", "tool_calls": [{"name": "read", "params": {"path": "README.md"}}], "usage": {"total_tokens": 150}}}
{"type": "tool_call_start", "turn": 1, "timestamp": 1718000002.1, "config_hash": "a1b2c3d4", "session_id": "abc123", "data": {"tool_name": "read", "id": "call_abc", "arguments": "{\"path\": \"README.md\"}", "turn": 1}}
{"type": "tool_call_end", "turn": 1, "timestamp": 1718000002.5, "config_hash": "a1b2c3d4", "session_id": "abc123", "data": {"tool_name": "read", "id": "call_abc", "params": {"path": "README.md"}, "success": true, "result": "# ARF Framework...", "duration_ms": 150}}
{"type": "post_action", "turn": 1, "timestamp": 1718000002.6, "config_hash": "a1b2c3d4", "session_id": "abc123", "data": {}}
{"type": "session_end", "turn": 1, "timestamp": 1718000030.0, "config_hash": "a1b2c3d4", "session_id": "abc123", "data": {}}
```

**每帧保证：**
- 扁平——每行都是独立的顶层事件，无 `_engine_events` 嵌套
- 独立可解析（JSONL 逐行），按 `type` 过滤
- `config_hash` 关联到 `snapshots/{hash}.xml` 配置快照
- `model_call_end.data.tool_calls` 包含完整的工具调用参数 → 可构建 function-calling 样本
- `tool_call_end.data.result` 包含工具返回值（截断至 2000 字符）

### 2.6 配置快照

`EnvSnapshotBuilder`（`arf/plugins/trace/snapshot.py`）在首次 `_write_event()` 调用时懒加载构建：

1. 扫描 `plugins_root` 下的 plugin.yaml、tool.yaml、function.py、skill.yaml
2. 扫描 `config_files` 中指定的 agent 级配置文件（如 `agent.yaml`、`pyproject.toml`）
3. 扫描 `extra_roots` 目录下的 `tools/` 和 `skills/` 子目录（适用于 app 级资源）
4. 生成语义分组的 XML，SHA256 前 12 位 hex 作为 hash
5. 去重存储：`snapshots/{hash}.xml`，同配置复用

每条 trace 事件自动注入 `config_hash` 字段，确保轨迹与配置版本永不错配。

### 2.7 事件类型

| 事件 | 触发时机 | 来源 |
|------|----------|------|
| `session_start` / `session_end` | 会话生命周期 | `_execute()` gate / `close()` |
| `user_input` | 每轮用户输入 | `_execute()` round 循环 |
| `round_start` / `round_end` | 交互轮次边界 | hook 回调 |
| `turn_start` / `turn_end` | 每次 agent 迭代 | hook 回调 |
| `pre_action` / `post_action` | 模型调用/工具执行前后 | hook 回调 |
| `model_call_start` / `model_call_end` | 模型调用 | inject_engine_event → post_action 展平 |
| `tool_call_start` / `tool_call_end` | 工具调用 | inject_engine_event → post_action 展平 |
| `thinking_delta` | 流式文本增量 | 仅 EventBus/SSE，不入磁盘 |
| `error` | 执行异常 | `_make_event` → EventBus |
| `gate_exceeded` | 超出 turn/token 预算 | `_make_event` → EventBus |

---

## 3. 演进方向

### 3.1 SQLite Trace 数据库

当前 JSONL 追加简单但查询能力弱（每次读取整个文件）。对标 systemd-journal 的结构化索引：每个 session 写入 SQLite 表，支持按时间范围、事件类型、tool_name、model 等维度查询。TracePlugin 可提供可插拔的 `TraceStore` protocol，JSONL 和 SQLite 共存。

### 3.2 OpenTelemetry 导出

将 AgentEvent 转换为 OpenTelemetry Span 导出：每个 tool_call → Span，session → Trace。通过 OTLP 导出到 Jaeger、Tempo 或 Prometheus。

### 3.3 会话回放

基于 trace 文件逐 turn 回放对话过程，用于调试和演示。`EvalRunner` 的 offline 模式已部分覆盖——读取历史 trace 对比 golden trajectory。

### 3.4 实时告警

事件流中出现连续 error 或 model_call 超时时，自动触发 Hook 或通知外部系统（Webhook）。
