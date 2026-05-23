# ARF Default Assistant 设计（修订版）

## 目标

在 `app/arf_default_assistant/` 下用新 `arf/` 框架实现旧 ARF 的全部功能：双 Agent 自主演进、单无限对话、lazy 持久化。验证框架接口可用性和"约定大于配置"的完善程度。

## 核心改动 vs 旧 ARF

| 旧 ARF | 新 ARF Default Assistant |
|---|---|
| 多 session 存档 + 恢复 | **单无限对话**，无"新建会话"概念 |
| SessionManager + SQLite | **lazy 持久化**: shutdown 时 archive 到文件，startup 时恢复 |
| 双 Agent (User + Sys) + handoff | **同**，但用新框架 `AgentConfig.agents` + `HandoverConfig` |
| 自演进 skills (tool_generator 等) | **同**，对话中动态创建 tools/skills，通过 ToolResolver 热加载 |
| 前端 8 views | **同**，复用 `app/web/` 现有 Vue3 前端 |

## 单无限对话模型

```
Server 启动 → 加载 archive.json（如存在）→ 恢复 Agent 状态 → 进入对话循环
    ↓
  所有用户消息追加到同一 context，compaction 自动管理窗口
    ↓
  对话中 agent 可能创建新 tools/skills → ToolResolver 自动重载
    ↓
  Server 收到 SIGTERM → StateStore.put() → 整个对话 archive 到 memory/archive.json
    ↓
  下次 Server 启动 → 从 archive.json 恢复 → 继续
```

## 目录结构

```
app/arf_default_assistant/
├── agent.yaml            # 双 Agent 配置（主 Agent + SysAgent 子定义）
├── cli.py                # arf-assistant: 10 个命令
├── server.py             # FastAPI ~400 行（无 session CRUD）
├── tools/                # 19 个工具（全部移植）
├── skills/               # 18 个技能（含自演进全套）
├── hooks/                # 1 个生命周期 hook（自演进触发）
├── memory/               # archive.json（唯一持久化文件）
│   └── archive.json
└── workspaces/           # Agent 工作目录（文件工具的作用域）
```

## CLI 命令（10 个）

| 命令 | 功能 | 说明 |
|---|---|---|
| `init` | 创建骨架目录 + 默认 agent.yaml | 同旧 `arf init` |
| `start` | 启动 backend + frontend | 同旧 `arf start` |
| `stop` | 发送 SIGTERM → lazy archive → 关闭 | 同旧 `arf stop` |
| `reload` | stop + start | 同旧 `arf reload` |
| `chat` | 终端对话 | 连接已有 server |
| `web` | 仅启动 backend | 同旧 `arf web` |
| `run <skill>` | 运行指定 skill | 同旧 `arf run` |
| `list [type]` | 列出注册的 tools/skills/models | 同旧 `arf list` |
| `validate` | 校验资源完整性 | 同旧 `arf validate` |
| `clone <type> <name>` | 复制系统资源到用户工作区 | 同旧 `arf clone` |

## Server API（去掉 session CRUD）

| 端点 | 功能 |
|---|---|
| `POST /chat` | 发送消息，返回完整响应 |
| `GET /chat/stream` | SSE 流式推送 |
| `GET /config/status` | 当前配置摘要（Agent 名、模型、工具/技能数、上下文长度） |
| `GET /trace` | 当前会话完整事件轨迹 |
| `GET /trace/stream` | SSE 实时事件推送 |
| `GET /resources/{type}` | 列出 tools / skills / models |
| `POST /tools` | 动态创建工具（agent 自演进用） |
| `POST /skills` | 动态创建技能（agent 自演进用） |
| `GET /archive` | 下载 archive.json |
| `POST /save` | 手动触发 archive |
| `POST /feedback` | 用户反馈 |

## 双 Agent 配置

```yaml
# arf_version: 1.0
name: arf_assistant
description: >
  可自我演进的 AI 助手。支持代码编写、文件管理、网络搜索、工具/技能创建。
  作为主 Agent (User) 与用户交互，必要时将复杂操作移交给 SysAgent。

models:
  - name: quick
    api_type: openai
    model: deepseek-v4-flash
    api_base: https://api.deepseek.com
    api_key_env: DEEPSEEK_API_KEY
    kwargs:
      reasoning_effort: high

  - name: deep
    api_type: openai
    model: deepseek-v4-pro
    api_base: https://api.deepseek.com
    api_key_env: DEEPSEEK_API_KEY
    kwargs:
      reasoning_effort: max

tools:
  # 全部 19 个工具（file_reader, file_writer, file_deleter, file_download,
  # web_search, web_fetch, python_exec, handoff_to_sys, resource_loader,
  # resource_registrar, resource_scaffold, memory_store, manage_hooks,
  # model_switch, image_understanding, ocr, speech_output,
  # speech_understanding, video_understanding）
  # ... 完整定义见工具清单

skills:
  # 全部 18 个技能（code_review, debug, file_ops, error_handler,
  # memory_extract, memory_compress, memory_management, db_operator,
  # rag_operator, model_configurator, model_switch, resource_clone,
  # resource_scaffold, tool_generator, tool_manager, skill_generator,
  # skill_manager, validate_skill, validate_tool）
  # ... 完整定义见技能清单

hooks:
  - name: self_evolve
    type: post_tool_exec
    run: ["python ./hooks/self_evolve.py"]
    # 检测 tools/skills/ 目录变化 → 触发 ToolResolver 重载

agents:
  - name: sys_agent
    description: 系统工程师——处理资源创建、模型配置等系统级操作
    models:
      - name: deep
        api_type: openai
        model: deepseek-v4-pro
        api_base: https://api.deepseek.com
        api_key_env: DEEPSEEK_API_KEY
        kwargs:
          reasoning_effort: max
    tools:
      - resource_loader
      - resource_registrar
      - resource_scaffold
      - model_manager
      - file_writer
      - file_deleter
    skills:
      - resource_scaffold
      - tool_generator
      - skill_generator
      - validate_tool
      - validate_skill
    hooks: []

handover:
  rules:
    - from: arf_assistant
      to: sys_agent
      trigger: "创建或修改 tools/skills/models 资源"
    - from: sys_agent
      to: arf_assistant
      trigger: "资源操作完成"
```

## 自演进数据流

```
用户: "帮我写个天气查询工具"
  ↓
UserAgent 分析任务 → 需要创建新 tool
  ↓ handoff → SysAgent
SysAgent 调用 resource_scaffold + tool_generator
  ↓ 在 tools/weather_query/ 下写入 tool.yaml + function.py
  ↓ sys_agent: "已创建 weather_query 工具"
  ↓ handoff → UserAgent
UserAgent 调用 resource_loader → ToolResolver 检测到新 tool → 自动激活
  ↓
UserAgent: "天气查询工具已就绪，让我查一下上海的天气"
  ↓ 调用 weather_query
```

## Lazy 持久化数据流

```
启动:
  server.py startup
    → 检查 memory/archive.json 是否存在
    → 存在 → StateStore.put(last_state)
    → 不存在 → 空 state 开始

关闭:
  server.py shutdown (SIGTERM)
    → StateStore.get() → AgentState
    → 序列化到 memory/archive.json:
       {
         "messages": [...],       # 完整对话历史
         "context_summary": "...",# compaction 摘要
         "tool_results": {...},   # 最近工具调用结果
         "plan": null,            # Planner 状态（如有）
         "memory": {...},         # 长期记忆（MemoryStore dump）
         "timestamp": 1234567890
       }

恢复:
  下次启动 → FileMemoryStore 加载记忆 → MemoryRetriever 可用
  → context 携带上次对话的 compaction 摘要
  → Agent 无缝继续
```

## 与旧应用对比

| 维度 | 旧 `src/arf/` | 新 `app/arf_default_assistant/` |
|---|---|---|
| 会话模型 | 多 session 管理 | 单无限对话 + lazy archive |
| Agent | UserAgent + SysAgent (YAML 子类) | AgentConfig.agents + HandoverConfig |
| 自演进 | resource_scaffold + tool_generator | 同，ToolResolver 自动热重载 |
| Server 代码 | ~2000 行 | ~400 行 |
| 持久化 | SQLite 6 表 | 单个 archive.json |
| CLI | 10 命令 | 10 命令（全部保留） |
| 前端 | Vue3 全家桶 | 复用 |
