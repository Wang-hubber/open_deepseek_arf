# ARF Default Assistant 设计

## 目标

在 `app/arf_default_assistant/` 下用新 `arf/` 框架搭建单用户助手应用，验证框架接口可用性和"约定大于配置"的完善程度。

## 目录结构

```
app/arf_default_assistant/
├── agent.yaml            # 单 Agent 配置（身份 + 4 核心资源）
├── models.yaml           # 模型清单（可独立引用）
├── tools/                # 6 个基础工具
│   ├── file_reader/
│   │   ├── tool.yaml
│   │   └── function.py
│   ├── file_writer/
│   │   ├── tool.yaml
│   │   └── function.py
│   ├── file_deleter/
│   │   ├── tool.yaml
│   │   └── function.py
│   ├── web_search/
│   │   ├── tool.yaml
│   │   └── function.py
│   ├── web_fetch/
│   │   ├── tool.yaml
│   │   └── function.py
│   └── python_exec/
│       ├── tool.yaml
│       └── function.py
├── skills/               # 2 个技能
│   ├── code_review.yaml
│   └── file_ops.yaml
├── hooks/                # 钩子脚本
│   ├── log_start.sh
│   └── archive_session.py
├── cli.py                # arf-assistant init / chat / start
├── server.py             # 精简 FastAPI (~200 行)
└── memory/               # 文件记忆存储（自动创建）
    └── sessions/         # trace 文件存放
```

## CLI 命令

### `arf-assistant init`
创建骨架目录，写入默认 `agent.yaml`:
```
app/arf_default_assistant/
├── tools/   (空)
├── skills/  (空)
├── hooks/   (空)
└── agent.yaml (包含默认 template)
```

### `arf-assistant chat "message"`
- 加载当前目录 `agent.yaml` → `create_agent()` → `BaseAgent`
- 调用 `agent.chat(message)` → 打印响应

### `arf-assistant start`
- 初始化 Agent
- 启动 uvicorn (FastAPI backend)
- 如果 `app/web/` 存在，同时启动 vite frontend
- 注册 SIGINT/SIGTERM 优雅关闭

## Server API

### `POST /chat`
```json
Request:  {"message": "hello", "session_id": "default"}
Response: {"content": "Hello! How can I help?"}
```

### `GET /chat/stream?message=hello&session_id=default`
SSE 流式推送，事件类型: `thinking_delta`, `model_call_end`, `tool_call_start`, `tool_call_result`, `error`。以 `data: [DONE]` 结尾。

### `GET /trace/{session_id}`
返回指定 session 的完整事件轨迹 JSON。

### `GET /trace/stream`
实时 SSE 推送 EventBus 事件，供前端 TraceView 渲染。

## 数据流

```
CLI                    Server                  Framework
───                    ──────                  ─────────
arf-assistant chat  →  POST /chat          →  agent.chat(msg)
        │                    │                    │
        │                    │              GraphEngine.invoke()
        │                    │                ├─ guard_runner.check_input()
        │                    │                ├─ memory_retriever.retrieve()
        │                    │                ├─ call_model()  [deepseek API]
        │                    │                ├─ guard_runner.check_output()
        │                    │                ├─ tool_executor.execute()
        │                    │                ├─ memory_writer.extract_and_write()
        │                    │                └─ state_store.put()
        │                    │                    │
        │              SSE   │              EventBus.emit()
        │              ←─────│                ├─ TuiDashboard.consume()
        │                    │                ├─ FileTraceStore._append()
        │                    │                └─ OtelTracer.consume()
        ▼                    ▼
      终端输出          前端 StreamView
```

## 框架自动推导的行为

用户 `agent.yaml` 只声明 name + description + models + skills + tools + hooks，框架自动推导:

| 策略 | 推导值 | 原因 |
|---|---|---|
| loop_strategy | react | 默认 |
| routing | two_tier | models > 1 |
| compaction | sliding_window 75% | 默认 |
| memory | file_store + recent_first + rule_writer | 默认 |
| guardrails | none_input + regex_output + path_tool | 默认 |
| errors | 2次重试 + 指数退避 + 5xx降级 | 默认 |
| streaming | SSE 全事件 | 默认 |
| human_loop | auto_approve | 默认 |
| trace | FileTraceStore → ./memory/sessions/ | 默认 |

## 与旧应用对比

| 维度 | 旧 `src/arf/` | 新 `app/arf_default_assistant/` |
|---|---|---|
| Agent 数量 | 双代理 (User+Sys) | 单 Agent |
| Agent 配置 | ~300 行 YAML | ~80 行 YAML |
| CLI 命令 | 10 个 | 3 个 |
| Server 代码 | ~2000 行 | ~200 行 |
| Trace 存储 | SQLite 6 表 | JSON 文件 per session |
| Session 管理 | SessionManager 类 | BaseAgent 内置 |
| 工具数量 | 19 | 6 |
| 技能数量 | 18 | 2 |
| 用户配置项 | 20+ | 4 核心 + advanced opt-in |
