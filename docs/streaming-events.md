# Streaming & Event Communication — 框架通讯层

ARF 采用三层事件架构：引擎层产生结构化事件，流适配器层做传输格式转换，消费端按需接入。三层解耦，每层独立演进。

---

## 1. OS 方案演进

> 本章描述 OS 如何解决进程输出与事件通知问题，作为 ARF 设计思路的参考。非严格技术对标。

### 1.1 标准输出 — 最朴素的流

**问题**：进程如何向外界报告自己的状态？

**Unix 管道**（Unix V1，1971）：进程写入 stdout/stderr，另一个进程从管道读取。这是最原始的"流式输出"——单向、顺序、文本行分割。`tail -f` 实时追踪日志，`grep` 过滤感兴趣的行。管道足够简单，但格式全靠约定，没有结构语义。

**问题**：管道无法区分"这次工具调用的结果"和"系统日志消息"——全是字节流。

### 1.2 事件日志 — 结构化通知

**systemd-journal（2011）**：每个日志条目是键值对（`MESSAGE=`, `PRIORITY=3`, `_PID=1234`），而非自由文本。支持按字段索引查询。ARF 的 `AgentEvent` 采用同一思路——每个事件是 typed dataclass，有 `type`、`data`、`timestamp`、`session_id` 等结构化字段。

**syslog protocol（RFC 5424）**：`<PRI>VERSION TIMESTAMP HOSTNAME APP-NAME PROCID MSGID [STRUCTURED-DATA] MSG`。结构化数据段（SD-ID）是可选的键值对。ARF 的 `EventBus.emit()` + `FileTraceStore` 架构类比 syslogd——事件发射后由 store 持久化，无需调用方关心存储细节。

### 1.3 Server-Sent Events — HTTP 原生推送

**HTTP/1.1 长连接**：早期 Web 通过轮询（polling）获取更新——客户端定时发起 HTTP 请求，服务端返回最新数据。每次请求都有完整的 HTTP 头部开销，延迟等于轮询间隔。

**Server-Sent Events（W3C，2012）**：单向、文本流、标准 HTTP。服务端响应 `Content-Type: text/event-stream`，连接保持打开，事件以 `data: <payload>\n\n` 格式持续推送。浏览器原生支持 `EventSource` API，自动重连。ARF 的 `SSEStreamAdapter` 就是将 `astream()` 的 Python 对象流翻译为这种标准格式。

**NDJSON（Newline-Delimited JSON）**：每行一个 JSON 对象，适合命令行管道（`| jq`）或浏览器 `fetch()` 的 `ReadableStream`。比 SSE 更轻量——无 `data:` 前缀、无空行分隔、无 `event:` 字段。

---

## 2. 当前实现

### 2.1 三层架构

```
┌─────────────────────────────────────────────────────────────────┐
│                      Consumer Layer                             │
│  CLI agent.astream()  │  Web EventSource  │  WebSocket  │  SDK  │
│  (Python原生迭代器)    │  (SSE标准格式)     │  (双向)     │  (多语言) │
├─────────────────────────────────────────────────────────────────┤
│                     Streaming Adapters                          │
│  SSEStreamAdapter     │  NDJSONStreamAdapter    │  (future…)    │
│  text/event-stream    │  application/x-ndjson    │               │
├─────────────────────────────────────────────────────────────────┤
│                        Engine Layer                             │
│  ControlPlane.astream() → AsyncIterator[AgentEvent]             │
│  ControlPlane.invoke()  → AgentState                           │
│  EventBus.emit()        → FileTraceStore (持久化)               │
└─────────────────────────────────────────────────────────────────┘
```

**Engine Layer** — 唯一的事件来源。`astream()` 产生 `AgentEvent` 的 async generator，`invoke()` 收集完整状态后返回。`EventBus` 是旁路持久化通道——所有事件 emit 到 `EventBus`，由 `FileTraceStore` 写入 JSON 文件，不影响主执行流。

**Streaming Adapters** — 纯格式转换。接收 `AgentEvent` 对象，输出 `bytes`（SSE 或 NDJSON）。无状态、无网络依赖、无框架绑定。调用方自行处理 HTTP 传输（FastAPI `StreamingResponse` / Starlette `EventSourceResponse`）。

**Consumer Layer** — 多种消费方式：
- **CLI**：直接 `async for event in agent.astream()` — Python 原生对象，零序列化
- **Web 前端**：`EventSource("/stream")` — 标准 SSE，浏览器原生重连
- **日志管道**：`curl -N /stream | jq` — NDJSON 适合命令行消费

### 2.2 Streaming Adapters

```python
# SSE — 适用于浏览器 EventSource
from arf.streaming import SSEStreamAdapter

@app.get("/stream")
async def stream():
    async def gen():
        async for chunk in SSEStreamAdapter(agent).stream("hello"):
            yield chunk
    return StreamingResponse(gen(), media_type="text/event-stream")

# NDJSON — 适用于 CLI 管道或日志系统
from arf.streaming import NDJSONStreamAdapter

async for chunk in NDJSONStreamAdapter(agent).stream("hello"):
    sys.stdout.buffer.write(chunk)
```

两个 adapter 的唯一区别是输出格式：
- SSE：`data: {"type":"text","data":{"content":"hello"}}\n\n`
- NDJSON：`{"type":"text","data":{"content":"hello"}}\n`

### 2.3 AgentEvent 类型枚举

引擎产生的事件类型覆盖完整生命周期：

```
session_start → model_call_start → tool_call_start → tool_call_end
  → model_call_end → text → ... → session_end
```

以及防护事件（`guard_block`、`guard_pass`）、压缩事件（`compaction_start`、`compaction_end`）、审批事件（`approval_required`、`approval_resolved`）、错误事件（`error`）、回滚事件（`undo_executed`、`rollback_executed`）。完整列表见 `arf/core/events.py:EventType`。

### 2.4 与 A2A 通讯的关系

`streaming/` 是**外部传输层**，解决"如何把事件送给使用者"。`A2A Communication`（`docs/a2a-communication.md`）是**内部通讯层**，解决"Agent 之间如何对话和协作"。两者分层：

```
外部: streaming/ → SSE/NDJSON → 前端 / CLI / SDK / 外部系统
内部: AgentBus / HandoffManager / PeerAgent → Agent 间通讯
```

---

## 3. 演进方向

### 3.1 短期 — 更多 adapter 格式

- **WebSocket adapter**：双向通道，支持前端主动取消或修改参数
- **gRPC stream adapter**：适合微服务间通讯，protobuf 序列化比 JSON 紧凑
- **`event:` 字段**：SSE 支持 `event: tool_call_end\ndata: ...\n\n`，前端可按事件类型注册 handler，无需解析 JSON 判断类型

### 3.2 中期 — 流式可靠性

- **背压（backpressure）**：当消费端处理速度低于生产端时，事件队列如何管理？当前 `EventBus` 队列满时静默丢事件（`QueueFull → pass`），需要丢弃策略 + 告警
- **断线重连 + 增量恢复**：SSE 自带 `Last-Event-ID`，但需要服务端支持 `?since=<event_id>` 查询。`FileTraceStore` 的 append-only 结构天然适合按序列号增量读取
- **心跳（heartbeat）**：长连接需要定期 `: heartbeat\n\n`（SSE 注释行）保持连接，防止 proxy 超时断开

### 3.3 长期 — 分布式追踪

- **trace_id / span_id / parent_span_id**：当前 `AgentEvent` 已预留三个字段但从不填充。在 A2A 场景中，一个用户请求可能跨越多个 Agent 进程——需要端到端的 trace ID 传播
- **W3C Trace Context**：标准化 `traceparent` header 格式，已经在 `agent/trace_context.py` 中实现 propagation 逻辑，但尚未与 `AgentEvent` 集成
