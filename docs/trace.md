# Trace & Observability

ARF 将 Trace 作为一等框架能力：每次模型调用、工具执行、Hook 触发、压缩操作都记录为结构化事件，通过文件持久化、API 查询和前端瀑布流可视化。

---

## 1. OS 方案演进

> 本章描述 OS 如何实现系统监控与事件日志，作为 ARF 设计思路的参考。非严格技术对标。

### 1.1 系统监控的演进

**syslog（1980s）** — Unix 的标准日志协议。进程调用 `syslog()` 写入带 facility 和 severity 的消息，`syslogd` 守护进程按配置将消息路由到文件、控制台或远程服务器。问题：非结构化文本，难以查询和分析。

**Windows Event Log（1993）** — 结构化事件（Event ID + 分类 + 描述），集中存储在二进制文件中，提供 API 查询。比 syslog 更结构化但平台绑定。

**systemd-journal（2011）** — Linux 的结构化日志替代方案。日志条目是键值对而非自由文本。支持按任意字段索引查询（`journalctl PRIORITY=3`）。二进制存储保证了写入原子性。

**eBPF（2014+）** — 革命性改变。在内核中安全地运行沙箱化的程序，无需重新编译内核。可以观测内核中的任何函数调用、网络包、系统调用——本质上是内核级的"事件总线"。类似 ARF 的 `EventBus` 机制：生产者 emit 事件，消费者按需订阅。

### 1.2 分布式追踪

Google Dapper（2010）论文引发了分布式追踪浪潮。OpenTelemetry（2019+）统一了 OpenTracing 和 OpenCensus，成为 CNCF 标准。核心概念：

- **Span**：一个操作单元（如一次 RPC 调用）
- **Trace**：由多个 span 组成的有向无环图
- **Context Propagation**：trace_id + span_id 在服务间传递，串联整个调用链

### 1.3 对 ARF 的启发

eBPF 的事件驱动模型影响了 ARF 的 EventBus 设计——emit 和 subscribe 解耦。OpenTelemetry 的 span 模型影响了事件的分层结构（session → round → turn → tool_call）。Windows Event Log 的结构化事件（类型码 + 数据字典）直接映射为 ARF 的事件类型体系。

---

## 2. ARF 当前实现

### 2.1 架构总览

```
GraphEngine._emit() / _make_event()
    │  自动注入 data.round（用户交互轮次）
    ▼
EventBus.emit(AgentEvent)
    │
    ├─ FileTraceStore → memory/traces/{session_id}.json
    │   （跳过 session_start, session_end, thinking_delta, tool_call_chunk；
    │    guard_block, guard_pass, approval_required, approval_resolved 落盘）
    │
    ├─ UsageTracker → memory/usage.json
    │   （按模型累加 model_call_end.usage.total_tokens）
    │
    └─ 消费者（App 层按需构建）
        （SSE 流、WebSocket、Trace Viewer 等，
         通过 EventBus.subscribe() 获取事件流）
```

### 2.2 事件模型

`AgentEvent`（`arf/core/events.py`）：`type` + `data` + `timestamp` + `trace_id` + `span_id` + `parent_span_id` + `session_id` + `agent_name` + `turn`。

**事件类型定义**（`EventType` Literal）：

| 事件 | 触发时机 | 关键 data 字段 |
|------|----------|---------------|
| `session_start` | 会话开始 | session_id |
| `session_end` | 会话结束/取消 | session_id, reason |
| `user_input` | 用户发送消息 | content, turn |
| `model_call_start` | 模型调用开始 | model, turn |
| `model_call_end` | 模型调用结束 | model, usage, content |
| `thinking_delta` | 流式文本增量 | content, reasoning *(仅 SSE，不入磁盘)* |
| `tool_call_chunk` | 流式工具调用增量 | name, arguments, delta *(仅 SSE，不入磁盘)* |
| `tool_call_start` | 工具调用开始 | tool_name, arguments |
| `tool_call_end` | 工具调用结束 | tool_name, success, result, error, duration_ms |
| `tool_call_result` | 回放时工具结果产出 | tool_name, result *(仅 ReplayController 使用)* |
| `compaction_start` | 压缩开始 | msg_count, model |
| `compaction_end` | 压缩结束 | msg_count, summary_len |
| `guard_block` | 路径沙箱或权限检查拒绝 | tool_name, guard *(path_check\|permission)*, reason |
| `guard_pass` | 所有 guard 检查通过 | tool_name |
| `approval_required` | 审批通道：等待用户确认 | decision_id, tool_name, params |
| `approval_resolved` | 审批通道：用户已决定 | decision_id, tool_name, approved, reason |
| `agent_switch` | Agent 切换 (handoff) | from_agent, to_agent |
| `undo_executed` | 撤销操作完成 | undo_count, agent_trace |
| `rollback_executed` | 工具回滚完成 | rolled_back |
| `hook_start` | Hook 执行开始 | event (hook 名称) |
| `hook_end` | Hook 执行结束 | event, passed, failed |
| `error` | 执行错误 | detail, code |

保护事件（`rate_limited`, `circuit_opened`, `circuit_half_open`, `circuit_closed`, `breaker_blocked`）已在 EventType 中预留，由 `arf/observability/protection.py` 在运行时使用。

### 2.3 FileTraceStore

`arf/observability/file_trace.py`。通过 `asyncio.create_task` 订阅 EventBus，异步消费事件流写入 `memory/traces/{session_id}.json`（默认 `dir=./memory/traces`，可通过 `ObservabilityConfig.trace_dir` 配置）。过滤规则：`session_start`、`session_end`、`thinking_delta`、`tool_call_chunk` 不入磁盘——`model_call_end` 已包含完整响应，流式中间片段（文本增量、工具调用增量）仅通过 SSE 推送。`guard_block`、`guard_pass`、`approval_required`、`approval_resolved` 全部落盘，保证安全决策可回溯。过滤后文件体积减少约 75%（实测 4000+ → 7 条核心事件）。

`BaseAgent` 在构造时自动创建 `FileTraceStore` 并订阅 `EventBus`。App 层可通过 `agent.trace_store` 属性访问实例，通过 `ObservabilityConfig.trace_dir` 配置存储路径（或通过 `AppContext.trace_dir` 自动推导）。

### 2.4 UsageTracker

`arf/observability/usage_tracker.py`。订阅 `model_call_end` 事件，按模型累加 `prompt_tokens`、`completion_tokens`、`total_tokens`、`calls`。持久化到 `memory/usage.json`，启动时加载历史数据（重启不丢失）。`BaseAgent` 自动创建，应用层无需手动初始化。

### 2.5 交互轮次分组

`round` 字段由引擎自动注入到每条事件（`arf/engine/graph.py`）。值来自 `AgentState.interaction_round`，每轮用户消息 +1。App 层可通过 `round` 字段将事件按交互轮次分组展示：

```
Round 0 (3 次内部迭代)
├── user_input: "我是谁"
├── 迭代 1
│   ├── model_call_end: "让我看看工作区文件"
│   ├── guard_pass (file_reader)          ← 路径沙箱 + 权限检查通过
│   ├── tool_call_end (file_reader)
│   ├── hook_start / hook_end (pre_tool_exec)
│   └── hook_start / hook_end (post_tool_exec)
├── 迭代 2
│   ├── model_call_end: "帮你写一个文件"
│   ├── approval_required (file_writer)   ← 权限 ask，需审批
│   ├── approval_resolved (approved)      ← 用户确认
│   ├── guard_pass (file_writer)          ← 审批通过后 guard 放行
│   └── tool_call_end (file_writer)
└── 迭代 3
    └── model_call_end: "你是 ARF 框架的创造者"
```

### 2.6 框架级查询 API

`FileTraceStore` 提供三个查询方法，App 层可通过 `agent.trace_store` 调用：

- `store.load(session_id) → list[dict]` — 加载指定 session 的完整轨迹
- `store.list_sessions() → list[str]` — 列出所有已记录的 session ID
- `store._append(session_id, event)` — 追加单条事件（内部使用）

`UsageTracker` 提供 `summary() → dict` 返回 `{total_tokens, total_calls, by_model}`。

`EventBus.subscribe(event_types=None) → AsyncIterator[AgentEvent]` 逐事件流式消费，支持按类型过滤。App 层可基于此构建 SSE 流或 WebSocket 推送。

### 2.7 Trace Viewer（App 层）

`/trace-viewer` — 框架提供 `arf/observability/trace_viewer.html` 单文件 HTML，零外部依赖。App 层通过路由挂载该文件即可使用。支持折叠/展开、时间筛选、token 统计、guard/approval 事件渲染。

### 2.8 回放控制器

`FileReplayController`（`arf/observability/replay.py`）提供录制和确定性回放能力。录制时将每轮模型输出和工具结果序列化为 JSON。回放时按 turn 顺序 yield `AgentEvent`，支持起始 turn 和断点（用于调试）。用于评估和回归测试——同一组输入的多次回放应产生完全一致的输出。

### 2.9 OpenTelemetry 模块

`arf/observability/otel.py` 文件存在但当前仅包含框架代码，未接入 EventBus。是预留的 OTLP 导出扩展点——未来可将 AgentEvent 转换为 OpenTelemetry Span 并导出到 Jaeger/Tempo/Prometheus。

### 2.10 配置

`ObservabilityConfig`（`arf/core/config_base.py`）定义可观测性全部配置项，`BaseAgent` 自动根据配置或 `AppContext` 创建 `FileTraceStore` 和 `UsageTracker`。

```yaml
# agent.yaml (全部字段可选，有合理默认值)
advanced:
  observability:
    trace_dir: ./memory/traces     # FileTraceStore 存储路径
    usage_dir: ./memory            # UsageTracker 存储路径
    trace_enabled: true            # 是否启用 FileTraceStore
    otel_exporter: none            # none | console | otlp
```

`interaction_round` 通过 `FileStateStore`（`agent.state_store`）持久化，每轮用户消息自动 +1，重启不丢失。

---

## 3. 演进方向

### 3.1 对标 OS 最佳实践：SQLite Trace 数据库

当前 `FileTraceStore` 是 JSON 文件追加——简单但查询能力弱（每次读取整个文件）。对标 systemd-journal 的结构化索引：

**SQLite Trace 数据库**：每个 session 的 events 写入 SQLite 表。支持按时间范围、事件类型、tool_name、model 等维度查询。聚合查询（"过去一周的 token 总消耗"）不需要加载所有 JSON 文件。`FileTraceStore` 和 `SQLiteTraceStore` 可通过统一协议共存，用户按需选择。

### 3.2 OpenTelemetry 导出

将 `otel.py` 模块接入 EventBus，将 AgentEvent 转换：每个 tool_call → Span（parent 为 model_call span），session → Trace。通过 OTLP 导出到 Jaeger（本地）、Tempo（生产）或 Prometheus（指标）。使 ARF 的可观测性融入现有基础设施，而非独立体系。

### 3.3 探索性方向

**实时告警**：类似 systemd-journal 的 watchdog。事件流中出现连续 error 事件或 model_call 超时时，自动触发 Hook 或通知外部系统（Webhook）。

**会话回放 UI**：基于 `FileReplayController` 的录制数据，在 Trace Viewer 中逐 turn 回放对话过程，用于调试和演示。

**性能剖面**：从 `tool_call_end.duration_ms` 和 `model_call_end` 累计延迟数据生成性能热力图，定位慢工具或慢模型。
