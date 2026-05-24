# Trace — 可追溯、结构化、人类友好的观测系统

ARF 将 Trace 作为一等框架能力。每次模型调用、工具执行、Hook 触发、压缩操作都被记录为结构化事件，通过文件持久化、API 查询和前端瀑布流可视化。

## Architecture

```
GraphEngine 每步操作
    │
    ▼
EventBus.emit(AgentEvent)
    │
    ├─ FileTraceStore → memory/sessions/{session_id}.json
    │
    ├─ UsageTracker → memory/usage.json (token 统计)
    │
    ├─ SSE stream → /api/trace/stream (实时前端推送)
    │
    └─ 前端 TraceView → 瀑布流时间线
```

## 事件类型

15 种结构化事件，每条事件包含 `type`、`data`、`turn`、`timestamp`、`interaction_round`：

| 事件 | 触发时机 | 关键字段 |
|------|----------|----------|
| `session_start` | 会话开始 | session_id |
| `session_end` | 会话结束 / 取消 | session_id, reason |
| `user_input` | 用户发送消息 | content, turn |
| `model_call_start` | 模型调用开始 | model, turn |
| `model_call_end` | 模型调用结束 | model, usage, content |
| `thinking_delta` | 流式思考增量 | content, reasoning |
| `tool_call_start` | 工具调用开始 | tool_name, arguments |
| `tool_call_end` | 工具调用结束 | tool_name, success, result, error, duration_ms |
| `compaction_start` | 压缩开始 | msg_count, model |
| `compaction_end` | 压缩结束 | msg_count, summary_len |
| `hook_start` | Hook 执行开始 | event (hook name) |
| `hook_end` | Hook 执行结束 | event, passed, failed |
| `error` | 执行错误 | detail, code |

## 存储

### FileTraceStore

`arf/observability/file_trace.py` — JSON 文件持久化。

- 每个 session 一个文件 `memory/sessions/{session_id}.json`
- 每条事件一行 JSON record（append-only 语义，当前全量读写）
- 订阅 EventBus，自动记录
- 过滤 session_start/session_end，避免重复

### UsageTracker

`arf/observability/usage_tracker.py` — Token 用量统计。

- 订阅 `model_call_end` 事件，累加 token 计数
- 持久化到 `memory/usage.json`
- 提供 `summary()` 接口供 API 查询

## API

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/trace` | GET | 全量事件（所有 session） |
| `/api/traces/sessions` | GET | Session 列表 |
| `/api/traces/sessions/{id}` | GET | Session 详情（含按 turn 分组） |
| `/api/traces/summary` | GET | 统计摘要 |
| `/api/traces/export` | GET | 原始 JSON 下载 |
| `/api/trace/stream` | GET | SSE 实时推送 |

## 前端瀑布流

`/traces` 页面 — 时间比例瀑布流可视化。

- **按交互轮次分组**：`interaction_round` 字段区分用户交互 vs 引擎内部迭代
- **层级展开**：Turn → Iteration → ToolCall / Reasoning / Hook
- **工具调用卡片**：参数、结果、耗时、成功/失败
- **思考过程**：展开查看模型推理内容
- **Hook 状态**：退出码徽章（0=通过 / 1=失败 / 2=注入）

## 交互轮次 vs 内部迭代

```
用户交互轮次 (interaction_round: 3)
├── 内部迭代 1 (turn: 15): model_call → tool_call(file_reader) → 继续
├── 内部迭代 2 (turn: 16): model_call → tool_call(file_writer) → 继续
└── 内部迭代 3 (turn: 17): model_call → 文本响应 → break
```

前端默认按轮次折叠显示，可展开查看内部迭代详情。

## 配置

```python
# server.py 启动时自动挂载
FileTraceStore(_agent.event_bus, dir="./memory/sessions")
UsageTracker(event_bus)  # BaseAgent 自动创建
```

## 独立 Trace Viewer

框架默认提供一个单文件 HTML trace 查看器，零依赖，浏览器直接打开：

- **访问**：`/trace-viewer`（开发模式）
- **能力**：按交互轮次折叠/展开、时间范围筛选、token 统计、工具调用详情
- **数据源**：可从文件选择器加载 JSON，或从 API URL 拉取

## 当前限制

- JSON 文件存储（非 SQLite），大 session 全量加载
- 无 trace 搜索/过滤
- 无自动清理/归档
- OpenTelemetry 导出为 stub
- `trace_id`/`span_id` 字段已预留但未填充
