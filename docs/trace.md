# Trace & Observability

> **原则：Engine 只负责执行。Trace 通过 hook 挂载，是观测的唯一通路。**

ARF 将 Trace 作为一等框架能力。TracePlugin 作为 side plugin 挂载在所有 hook 点上，同时订阅 EventBus 捕获细粒度引擎事件，产出 trajectory 级别的 JSONL 轨迹文件。每条 session 对应一个 `{session_id}.jsonl` 文件，可支撑调试回放、测评数据集构建和调优。

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

eBPF 的事件驱动模型影响了 ARF 的 EventBus 设计。OpenTelemetry 的 span 模型影响了事件的分层结构（session → round → turn）。systemd-journal 的键值对存储影响了 trace 事件的 flat dict 设计——每行 JSON 自包含，O(1) 追加。

---

## 2. ARF 当前实现

### 2.1 架构总览

```
ControlPlane
    │
    ├── _fire_side("session_start", ctx) ──→ TracePlugin.on_hook()
    ├── _fire_side("pre_action", ctx)    ──→ TracePlugin.on_hook()
    ├── _action_call_model()
    │       └── ctx.inject_engine_event("model_call", {...})
    ├── _action_execute_tools()
    │       └── ctx.inject_engine_event("tool_call", {...})
    ├── _fire_side("post_action", ctx)   ──→ TracePlugin.on_hook()
    │
    └── _make_event(...) ──→ EventBus ──→ TracePlugin._consume_eventbus()
                                                  │
                                                  ▼
                                      {trace_dir}/{session_id}.jsonl
                                                +
                                      {trace_dir}/snapshots/{hash}.xml
```

**TracePlugin 使用两条输入源：**

1. **Hook 回调（on_hook）** — 记录 hook 边界事件 + `ctx.hook_data`（包含 engine 事件、子进程 hook 结果等）
2. **EventBus 订阅（_consume_eventbus）** — 后台 asyncio task 捕获细粒度引擎事件（`model_call_start/end`、`tool_call_start/end`、`error` 等）

两者统一写入同一个 JSONL 文件。过滤掉高频噪声：`thinking_delta`（仅 SSE 流）、`session_start/end`（hook 回调已覆盖）。

### 2.2 TracePlugin

`arf/plugins/trace/plugin.py`。Mount 为 8 个 lifecre hook 的 side plugin，BaseAgent 通过 `PluginProvider` 自动发现。

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
  plugins_root: ./arf/plugins
  config_files: []
```

**公开 API：**

- `plugin.set_event_bus(bus)` — BaseAgent 在 init 阶段调用，启动 EventBus 订阅后台 task
- `plugin.read_trace(session_id) → list[dict]` — 读取指定 session 的完整轨迹
- `plugin.list_sessions() → list[str]` — 列出所有已记录的 session ID
- `plugin.shutdown()` — BaseAgent.stop() 时调用，取消后台 task

### 2.3 事件注入

Engine 在关键节点通过 `PluginContext.inject_engine_event()` 将内部事件注入 `hook_data`：

| 注入点 | 事件类型 | 包含字段 |
|--------|---------|---------|
| `_action_call_model()` 结束 | `model_call` | model, input_tokens, output_tokens, content, tool_calls |
| `_action_execute_tools()` 结束 | `tool_call` | tool_name, params, success, result, error, duration_ms |

子进程 hook 的执行结果（stdout/stderr/exit_code）由 `SubprocessHookRunner` 写入 `hook_data["_subprocess_results"]`，TracePlugin 通过 hook 回调记录。

### 2.4 Trajectory JSONL 格式

每条 JSONL 记录是一帧，自包含且可独立解析：

```jsonl
{"type": "session_start", "turn": 0, "timestamp": 1718000000.0, "config_hash": "a1b2c3d4", "session_id": "abc123", "data": {...}}
{"type": "round_start", "turn": 1, "timestamp": 1718000000.1, "config_hash": "a1b2c3d4", "session_id": "abc123", "data": {}}
{"type": "model_call", "turn": 1, "timestamp": 1718000002.0, "config_hash": "a1b2c3d4", "session_id": "abc123", "data": {"model": "deepseek-v3", "input_tokens": 1234, "output_tokens": 200, "content": "...", "tool_calls": [...]}}
{"type": "tool_call", "turn": 1, "timestamp": 1718000002.5, "config_hash": "a1b2c3d4", "session_id": "abc123", "data": {"tool_name": "read", "params": {...}, "success": true, "result": "...", "duration_ms": 150}}
{"type": "post_action", "turn": 1, "timestamp": 1718000002.6, "config_hash": "a1b2c3d4", "session_id": "abc123", "data": {"_engine_events": [...], "_subprocess_results": [...]}}
{"type": "session_end", "turn": 5, "timestamp": 1718000030.0, "config_hash": "a1b2c3d4", "session_id": "abc123", "data": {"reason": "completed"}}
```

**每帧保证：**
- 独立可解析（JSONL 逐行）
- 可按 `turn` 分段、按 `type` 过滤
- `config_hash` 关联到 `snapshots/{hash}.xml` 配置快照
- 模型输入输出完整 → 可构建 SFT 样本
- 工具调用参数/结果完整 → 可构建 function-calling 样本
- 子进程 hook 结果可见

### 2.5 配置快照

`EnvSnapshotBuilder`（`arf/plugins/trace/snapshot.py`）在首次 `_write_event()` 调用时懒加载构建：

1. 扫描 `plugins_root` 下的 plugin.yaml、tool.yaml、function.py、skill.yaml
2. 包含 `config_files` 中指定的额外文件（如 agent.yaml）
3. 生成语义分组的 XML，SHA256 前 12 位 hex 作为 hash
4. 去重存储：`snapshots/{hash}.xml`，同配置复用

每条 trace 事件自动注入 `config_hash` 字段，确保轨迹与配置版本永不错配。

### 2.6 事件类型

`AgentEvent`（`arf/core/events.py`）：

| 事件 | 触发时机 |
|------|----------|
| `session_start` / `session_end` | 会话生命周期 |
| `round_start` / `round_end` | 交互轮次边界 |
| `turn_start` / `turn_end` | 每次 agent 迭代 |
| `pre_action` / `post_action` | 模型调用/工具执行前后 |
| `model_call_start` / `model_call_end` | 模型调用 |
| `tool_call_start` / `tool_call_end` | 工具调用 |
| `thinking_delta` | 流式文本增量（仅 SSE，不入磁盘） |
| `error` | 执行异常 |
| `gate_exceeded` | 超出 turn/token 预算 |

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
