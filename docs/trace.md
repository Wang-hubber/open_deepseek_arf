# Trace — 可追溯、结构化、人类友好的观测系统

ARF 将 Trace 作为一等框架能力。每次模型调用、工具执行、Hook 触发、压缩操作都被记录为结构化事件，通过文件持久化、API 查询和前端瀑布流可视化。

## Architecture

```
GraphEngine._emit / _make_event
    │  自动注入 data.round（用户交互轮次）
    ▼
EventBus.emit(AgentEvent)
    │
    ├─ FileTraceStore → memory/sessions/{session_id}.json
    │   (跳过 thinking_delta — model_call_end 已含完整响应)
    │
    ├─ UsageTracker → memory/usage.json (token 统计)
    │
    ├─ SSE stream → /api/trace/stream (实时前端推送)
    │
    └─ 前端 TraceView → 按交互轮次分组的瀑布流
```

## 事件类型

每条事件包含 `type`, `data`(含 `round`), `turn`, `timestamp`：

| 事件 | 触发时机 | 关键字段 |
|------|----------|----------|
| `session_start` | 会话开始 | session_id |
| `session_end` | 会话结束/取消 | session_id, reason |
| `user_input` | 用户发送消息 | content, turn |
| `model_call_start` | 模型调用开始 | model, turn |
| `model_call_end` | 模型调用结束 | model, usage, content |
| `thinking_delta` | 流式思考增量 | content, reasoning *(仅 SSE，不入磁盘)* |
| `tool_call_start` | 工具调用开始 | tool_name, arguments |
| `tool_call_end` | 工具调用结束 | tool_name, success, result, error, duration_ms |
| `compaction_start` | 压缩开始 | msg_count, model |
| `compaction_end` | 压缩结束 | msg_count, summary_len |
| `hook_start` | Hook 执行开始 | event (hook name) |
| `hook_end` | Hook 执行结束 | event, passed, failed |
| `error` | 执行错误 | detail, code |

## 交互轮次分组

`round` 字段由引擎自动注入到每条事件（`_emit` 和 `_make_event` 均已覆盖）。值来自 `AgentState.interaction_round`，每轮用户消息 +1。

```
Round 0 (3 内部迭代)
├── 用户输入: "我是谁"
├── 迭代 1 · T7
│   ├── 🤖 模型响应: "让我看看工作区文件"
│   ├── 🔧 file_reader (list .)
│   │   ├── 调用参数: {"operation": "list", "path": "."}
│   │   └── 运行结果: {'items': [...], 'count': 5}
│   ├── 🪝 工具前钩子 (pre_tool_exec)
│   └── 🪝 工具后钩子 (post_tool_exec)
├── 迭代 2 · T8
│   └── 🤖 模型响应: "再读一个文件"
├── 迭代 3 · T9
│   └── ✅ 最终回复: "你是 ARF 框架的创造者王协"
```

## 前端瀑布流

`/traces` 页面 — 三级层级展开：

- **Round**（用户交互轮次）：折叠显示输入摘要 + token 统计
- **Iteration**（内部迭代）：每次 model_call + 关联 tool calls + hooks
- **Detail**：工具参数/结果、推理内容、Hook 退出码

## API

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/trace` | GET | 全量事件 |
| `/api/traces/sessions` | GET | Session 列表 |
| `/api/traces/sessions/{id}` | GET | Session 详情 |
| `/api/traces/summary` | GET | 统计（events, tokens, turns） |
| `/api/traces/resource-stats` | GET | 工具/模型调用统计 |
| `/api/traces/export` | GET | 原始 JSON 下载 |
| `/api/trace/stream` | GET | SSE 实时推送 |

## 存储

- **FileTraceStore**：`memory/sessions/{session_id}.json`，过滤 `thinking_delta`（75% 噪音消除），`session_start`/`session_end` 不入库
- **UsageTracker**：`memory/usage.json`，累加 `model_call_end.usage.total_tokens`

## 独立 Trace Viewer

`/trace-viewer` — 单文件 HTML，零依赖：折叠/展开、时间筛选、token 统计。

## 配置

```python
FileTraceStore(_agent.event_bus, dir="./memory/sessions")  # server.py 启动时
UsageTracker(event_bus)   # BaseAgent 自动创建
archive.json              # 持久化 interaction_round，重启不丢失
```
