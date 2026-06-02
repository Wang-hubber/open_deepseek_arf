# APP 开发者指南 — 从 0 到 1 构建你的 ARF Agent 应用

> 本文档中的代码示例基于 `app/arf_default_assistant/` 版本。示例为教学目的可能有简化，完整代码请参考源文件。

ARF 提供 Agent 运行所需的全部基础设施——资源发现与热加载、内存管理、模型调度、安全沙箱、可观测性。你只需要关注四件事：**用哪些模型、给什么工具、怎么组织技能、何时触发 Hook**。四种实体按约定的目录结构放置，框架自动发现。

---

## 目录

1. [快速开始](#1-快速开始) — 跑通参考应用
2. [最小可运行 App](#2-最小可运行-app) — 三个文件即对话
3. [目录结构与 AppContext](#3-目录结构与-appcontext) — 框架约定的布局
4. [配置 Agent — agent.yaml 深度解析](#4-配置-agent--agentyaml-深度解析) — 每一段都干什么
5. [编写工具](#5-编写工具) — 四种模式的完整示例
6. [定义技能](#6-定义技能) — 工具组合与渐进式披露
7. [生命周期 Hook](#7-生命周期-hook) — 六个事件点与退出码
8. [定制 Server](#8-定制-server) — 从最小封装到生产级 FastAPI
9. [搭建前端](#9-搭建前端) — Vue 3 SPA + SSE 客户端
10. [双 Agent 架构](#10-双-agent-架构) — User Agent + System Agent
11. [CLI 工具](#11-cli-工具) — 命令行管理界面
12. [进阶主题](#12-进阶主题) — 热加载、配置生成、框架模块、回归测评

---

## 1. 快速开始

跑通参考应用，确认环境正常。

```bash
git clone git@gitee.com:dalaydata/open_deepseek_arf.git
cd open_deepseek_arf
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
export DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxx
cd app/arf_default_assistant
python cli.py validate  # 环境验证（检查依赖、配置、目录结构）
python cli.py start     # 启动 server + 前端
```

浏览器打开 `http://127.0.0.1:8000`，注册 API Key 后即可对话。

参考实现目录：`app/arf_default_assistant/`。下文所有示例均来自此目录。

---

## 2. 最小可运行 App

只需要三个文件，就能拥有一个能对话的 Agent。

模型定义在 `models/` 目录（文件系统是真相源），`agent.yaml` 不内联模型配置。

**models/quick.yaml** — 模型源定义：

```yaml
type: quick
api_type: openai
model: deepseek-v4-flash
api_base: https://api.deepseek.com
api_key_env: DEEPSEEK_API_KEY
context_window: 800000
activation: kernel
```

**agent.yaml** — 只写 Agent 自身信息，不写模型：

```yaml
name: my_agent
description: 我的第一个 ARF Agent
system_prompt:
  prefix:
    role: |
      You are my_agent, a helpful assistant.
    critical_rules: |
      ### R1: Verify, then answer
      Never state file contents from memory. Call the relevant tool first.
  suffix: |
    $INVENTORY
```

**server.py** — 最小 FastAPI 封装：

```python
from arf.agent.factory import create_agent
from arf.agent.config import AgentConfig
from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI()
cfg = AgentConfig.from_yaml("agent.yaml")
agent = create_agent(config=cfg)

@app.post("/api/chat")
async def chat(req: dict):
    result = await agent.chat(req["message"])
    return JSONResponse({"content": result})
```

启动：

```bash
export DEEPSEEK_API_KEY=sk-xxx
uvicorn server:app --host 127.0.0.1 --port 8000
curl -X POST http://127.0.0.1:8000/api/chat -H 'Content-Type: application/json' -d '{"message":"你好"}'
```

**关键点**：`AgentConfig.from_yaml("agent.yaml")` 自动从 `models/` 目录加载模型定义，`agent.yaml` 无需内联模型配置。只有在需要覆盖特定字段（如 `temperature: 0.3`）时，才在 `agent.yaml` 中按 name 引用：

```yaml
# agent.yaml — 可选覆盖（不需要就不写）
models:
  - type: quick
    temperature: 0.3   # 只写要覆盖的字段，其余从 models/quick.yaml 继承
```

框架自动完成模型适配器注入、EventBus 创建、状态管理、上下文压缩——app 层不需要管这些。参考 app 在此基础上增加了 SSE streaming、undo、trace API、双 Agent 路由、权限审批等。

---

## 3. 目录结构与 AppContext

### 3.1 约定的目录布局

```
my_app/
├── agent.yaml          # Agent 配置（唯一必建文件）
├── agent_main.py       # AppContext 声明（server 和 cli 共享引用）
├── server.py           # FastAPI 入口
├── cli.py              # CLI 管理工具（可选）
├── models/             # 模型声明文件（推荐放在这，也可在 agent.yaml 内联）
│   ├── deep.yaml
│   └── quick.yaml
├── tools/              # 自定义工具（每个工具一个子目录）
│   └── my_tool/
│       ├── tool.yaml   # Schema 定义
│       └── function.py # 实现
├── skills/             # 技能定义 YAML
│   └── my_skill.yaml
├── hooks/              # Hook 脚本
└── memory/             # 运行时数据：状态持久化、Trace 记录、文件操作根目录
```

### 3.1b 框架 Plugins

`arf/plugins/` 提供框架内置的能力包，通过 `agent.yaml` 的 `plugins:` 字段激活：

```yaml
plugins:
  - planner    # 任务规划
  - todo       # 任务追踪
  - undo       # 对话回退
```

Plugin 内部结构与 App 层工具/技能约定一致（`tools/` + `skills/` 子目录）。
框架启动时 `PluginProvider` 扫描激活的 plugin 目录，`ResourceResolver` 自动合并到工具/技能列表。
App 层同名工具覆盖 plugin 版本（app > plugin）。

社区贡献的 plugin 放在 `arf/plugins/` 目录下即可被框架发现。

### 3.2 AppContext — 单一路径源

`agent_main.py` 声明 App 根目录，框架由此推导所有标准路径。`server.py` 和 `cli.py` 共用此模块，避免散落路径字符串。

```python
"""agent_main.py — App 声明根目录，框架推导所有标准路径。"""
from pathlib import Path
from arf.agent import AppContext

APP_ROOT = Path(__file__).parent.resolve()
app_context = AppContext(root=APP_ROOT)
```

`AppContext` 自动推导的路径：

| 属性 | 值 | 用途 |
|------|-----|------|
| `config_path` | `{root}/agent.yaml` | 配置文件路径 |
| `tools_dir` | `{root}/tools` | 工具目录 |
| `skills_dir` | `{root}/skills` | 技能目录 |
| `models_dir` | `{root}/models` | 模型目录 |
| `hooks_dir` | `{root}/hooks` | Hook 脚本目录 |
| `workspace_dir` | `{root}/memory` | Memory store 与状态持久化根目录 |
| `state_dir` | `{root}/memory/state` | 会话状态持久化 |
| `trace_dir` | `{root}/memory/traces` | Trace 事件存储 |
| `logs_dir` | `{root}/logs` | 日志目录 |

Server 端消费：`from agent_main import app_context`。

---

## 4. 配置 Agent — agent.yaml 深度解析

`agent.yaml` 是 App 的唯一配置文件。框架从它出发，组装 BaseAgent、注入所有子系统、启动引擎。以下逐段拆解参考 App 的完整配置。

### 4.1 基础字段

```yaml
name: arf_assistant
role: 主助手
task: 与用户对话、调用工具完成任务、工具不足时交接给 SysAgent
description: >
  可自我演进的 AI 助手。擅长代码编写、文件管理、网络搜索、资源创建。
  作为主 Agent 与用户交互，资源创建等系统操作移交给 SysAgent。
```

### 4.2 System Prompt 配置

系统提示词采用 **prefix/suffix 分层结构**。Framework 使用 `SystemPromptProvider` 组装，`string.Template` 渲染占位符（`$VARIABLE` 语法）。

| 字段 | 作用 | 缓存策略 |
|------|------|----------|
| `prefix.role` | 角色定义 | 极稳定 → 命中 API 缓存 |
| `prefix.critical_rules` | 硬规则 | 极稳定 → 命中 API 缓存 |
| `suffix` | 动态内容模板 | 包含 `$INVENTORY` 等可变占位符 |

**prefix 内顺序由框架保证**：`role` → `critical_rules`，用户无需手动写占位符。

**per-turn 占位符**（`$MEMORY`、`$WORKSPACE`、`$TURN_BUDGET`）由引擎在每轮运行时替换，不在 Provider 编译期处理。

```yaml
system_prompt:
  prefix:
    role: |
      You are my_agent, an AI assistant.

      ## Capabilities
      You help users accomplish tasks through natural language conversation.
      You can read and write files, browse the web, manage memory, and process documents.
    critical_rules: |
      ### R1: Verify, then answer
      Never state file contents, current model, or any runtime state from memory.
      Call the relevant tool FIRST, then answer from the tool result.

      ### R2: Tool calls produce action, not text
      Saying "switched to X" or "created Y" without calling the tool is a violation.

      ### R3: Verify after action
      After calling a tool that changes state, verify the result.

      ### R4: Handoff for privileged operations
      Call `handoff` when the user asks to create/modify/delete a tool,
      skill, or model, or needs to write to tools/, skills/, models/ paths.

      ### R5: Progressive skill loading
      Skills are loaded on demand. When a skill matches intent, read its
      full instructions via `file_reader` before executing.
  suffix: |
    $INVENTORY
```

**关键变更**（v1.0 → v1.1）：
- `template` + `critical_rules` 字段合并为 `prefix`（`role` + `critical_rules`）+ `suffix`
- 占位符语法从 `{{PLACEHOLDER}}` 迁移为 `$PLACEHOLDER`（Python `string.Template`）
- 删除 `pipeline` 字段（`PipelineSection` 未使用）
- `SystemPromptProvider` Protocol 支持依赖注入覆盖

### 4.3 模型声明

推荐放在 `models/*.yaml`，文件系统是真相源。`agent.yaml` 中只写需要覆盖的字段。

**models/deep.yaml** — 深度推理模型：

```yaml
type: deep
api_type: openai
model: deepseek-v4-pro
api_base: https://api.deepseek.com
api_key_env: DEEPSEEK_API_KEY
context_window: 1000000
activation: kernel
kwargs:
  reasoning_effort: max
```

**models/quick.yaml** — 快速廉价模型：

```yaml
type: quick
api_type: openai
model: deepseek-v4-flash
api_base: https://api.deepseek.com
api_key_env: DEEPSEEK_API_KEY
context_window: 800000
activation: kernel
kwargs:
  reasoning_effort: high
  temperature: 0.7
```

合并优先级：**agent.yaml 覆盖 > 文件系统字段 > Pydantic 默认值**。`activation: kernel` 表示框架初始化时加载且之后不可变，`discoverable` 可热重载。

> 深入阅读：[`docs/app/models.md`](docs/app/models.md)

### 4.4 MCP 统一资源接口

ARF 使用 MCP（Model Context Protocol）统一管理工具和技能的发现与执行。架构：

```
Agent → McpClientManager (stdio) → Local MCP Server (子进程)
                                       ├── ToolProvider + SkillProvider (本地)
                                       └── McpRemoteClient × N (外部)
```

**工具命名空间**：所有工具带 `{source}__` 前缀，`arf__` 为本地/插件，外部以 `{server_name}__` 标识。

**配置外部 MCP**（`agent.yaml`）：

```yaml
mcp_servers:
  - name: search
    transport: sse
    url: http://localhost:9000/sse
  - name: ci
    transport: http
    url: http://localhost:9001
    api_key_env: MCP_CI_KEY
```

**SystemPromptProvider 简化**：Provider 只组装 prefix（role + critical_rules），suffix 中的 `$INVENTORY` 由 MCP 在启动时填充并缓存，工具变更时通过 `resources/updated` 通知触发刷新。

### 4.5 工具声明

agent.yaml 中只需按 name 引用工具，框架从 `tools/<name>/tool.yaml` 自动发现完整 Schema。

```yaml
# agent.yaml — 仅引用工具，框架从 tools/<name>/tool.yaml 自动发现参数
tools:
  - name: file_reader
    activation: kernel
  - name: file_writer
    activation: kernel
  - name: web_search
    activation: kernel
  - name: python_exec
    activation: discoverable

# 框架合并优先级：agent.yaml 覆盖 > tool.yaml 基础定义 > Pydantic 默认值。
# tool.yaml 提供完整 Schema（description + parameters），agent.yaml 仅覆盖
# 需要微调的字段（如 activation）。
```

`activation: kernel` 的工具始终在 LLM 工具列表中；`discoverable` 的工具按需加载——LLM 知道它们存在但不主动加载，匹配时才激活。

以下为各工具的完整 Schema 参考（完整定义在 `tools/<name>/tool.yaml` 中）：

| 工具 | description | 关键参数 | activation |
|------|-------------|---------|-----------|
| `file_reader` | 读取文件内容或列出目录 | `path`(required), `operation`(read\|list) | kernel |
| `file_writer` | 写入文件（创建或覆盖） | `path`(required), `content`(required) | kernel |
| `web_search` | 搜索互联网（DuckDuckGo） | `query`(required) | kernel |
| `handoff` | 将资源创建/修改操作移交给 SysAgent | `task`(required), `context` | kernel |
| `python_exec` | 执行 Python 代码片段 | `code`(required) | discoverable |

> 深入阅读：[`docs/app/tools.md`](docs/app/tools.md)

### 4.6 高级配置 — advanced 段

`advanced:` 控制框架所有子系统的行为。以下为参考 App 的完整配置，逐段解释。

```yaml
advanced:
  max_turns: 50
  # 系统后台模型 — 记忆抽取/检索、路由分类、上下文压缩共用
  # 应选廉价快速模型 (flash, thinking disabled, low temp)
  system_model: quick
```

**Guardrails — 权限与安全**：

```yaml
  guardrails:
    permissions:
      allow:
        - file_reader
        - web_search
        - web_fetch
        - resource_loader
        - resource_registrar
        - model_switch
        - handoff
      ask:
        - file_writer
        - file_deleter
        - python_exec
      deny: []
      deny_patterns:
        - "rm -rf"
        - "sudo"
        - "chmod 777"
```

每次工具调用前，框架执行 deny → ask → allow 检查。`ask` 列表中的工具需要人工审批（前端弹出确认框）。`deny_patterns` 硬阻断危险命令模式。

**Human Loop — 审批通道**：

> `approval_required` 事件通过 EventBus 发射（astream 路径同时 `yield` 给 SSE）。
> App 层可通过 `event_bus.subscribe()` 自行推送到前端（WebSocket/轮询），
> 收到审批后调用 `engine.approve()` 解除 60s 阻塞。SSE 路径已内置支持。

```yaml
  human_loop:
    approval_points: tool_name_allowlist
    allowlist:
      - file_writer
      - file_deleter
      - python_exec
    channel: websocket
    timeout: 60s
```

**Memory — 长期记忆**：

```yaml
  memory:
    store: file                 # file | sqlite | none
    workspace: ./memory
    retriever: llm              # llm | recent_first
    writer: llm                 # llm | rule
    max_tokens: 2000
    top_k: 5                    # 每次检索记忆条数
```

`retriever: llm` — 用 system_model 判断哪些记忆与当前 query 相关。
`writer: llm` — 用 system_model 从对话中提取事实/偏好/决策。

**Routing — 模型路由**：

```yaml
  routing:
    strategy: two_tier          # two_tier | static
    default: quick
    classify:
      medium: quick             # 简单任务 → 廉价模型
      complex: deep             # 复杂任务 → 深度模型
    fallback:
      deep: quick               # deep 不可用时回退
```

每轮对话开始前，用 system_model 分类用户 query，路由到对应模型。`strategy: static` 则始终用默认模型。

**Compaction — 上下文压缩**：

```yaml
  compaction:
    strategy: sliding_window    # sliding_window | none
    threshold: 0.75             # 触发阈值（占 context_window 的比例）
```

Token 用量超过模型上下文窗口的 75% 时，旧轮次被压缩为结构化摘要，保留最近 4 条消息。

> 深入阅读：[`docs/app/advanced.md`](docs/app/advanced.md)

---

## 5. 编写工具

工具由两个文件组成，放在 `tools/<name>/` 目录下。框架自动发现，无需手动注册。以下展示四种典型模式。

### 5.1 模式一：最简工具 — text_to_upper

**tools/text_to_upper/tool.yaml**：

```yaml
name: text_to_upper
description: Convert input text to uppercase
parameters:
  type: object
  properties:
    text:
      type: string
      description: The text to convert to uppercase
  required:
    - text
execution:
  sandbox: inherit
  timeout: 30s
activation: discoverable
```

**tools/text_to_upper/function.py**：

```python
async def execute(text: str) -> dict:
    try:
        result = text.upper()
        return {"ok": True, "original": text, "result": result, "length": len(result)}
    except Exception as e:
        return {"error": str(e)}
```

关键约定：
- 函数名必须是 `execute`
- 参数名和类型必须与 `tool.yaml` 的 `properties` 一一对应
- 返回值 `dict`，无固定格式——整个 dict 作为工具结果传给 LLM
- 使用 `async def`（即使函数内部是同步操作）

### 5.2 模式二：文件/路径操作 — file_reader

**tools/file_reader/tool.yaml**：

```yaml
name: file_reader
description: Read file contents or list directory entries
parameters:
  type: object
  properties:
    operation:
      type: string
      enum: [read, list]
      description: "read (file contents), list (directory listing)"
    path:
      type: string
      description: "File or directory path relative to workspace root"
  required:
    - operation
    - path
execution:
  sandbox: inherit
  timeout: 30s
activation: kernel
```

**tools/file_reader/function.py**：

```python
from pathlib import Path

WORKSPACE = Path("memory/")

async def execute(operation: str, path: str) -> dict:
    p = WORKSPACE / path
    try:
        if operation == "read":
            if not p.exists():
                return {"error": f"File not found: {path}"}
            if p.is_dir():
                return {"error": f"Path is a directory, use list instead: {path}"}
            text = p.read_text(encoding="utf-8")
            return {"content": text, "size": len(text)}
        elif operation == "list":
            if not p.exists():
                return {"error": f"Directory not found: {path}"}
            if not p.is_dir():
                return {"error": f"Not a directory: {path}"}
            items = []
            for child in sorted(p.iterdir()):
                items.append({
                    "name": child.name,
                    "type": "dir" if child.is_dir() else "file",
                    "size": child.stat().st_size if child.is_file() else 0,
                })
            return {"items": items, "count": len(items)}
        else:
            return {"error": f"Unknown operation: {operation}"}
    except Exception as e:
        return {"error": str(e)}
```

要点：所有文件路径相对于 `memory//`。框架的 `PathCheckToolGuard` 在每次工具调用前自动阻断 `..` 路径穿越和绝对路径——工具本身不需要额外做路径校验。

### 5.3 模式三：HTTP 请求 — web_search

**tools/web_search/tool.yaml**：

```yaml
name: web_search
description: Search the internet using DuckDuckGo, returns titles, snippets, and URLs
parameters:
  type: object
  properties:
    query:
      type: string
      description: "Search query, supports Chinese and English"
    max_results:
      type: integer
      description: "Maximum results to return, default 10, max 20"
  required:
    - query
execution:
  sandbox: inherit
  timeout: 30s
activation: kernel
```

**tools/web_search/function.py**（核心逻辑）：

```python
import html
import re
import urllib.parse
import urllib.request

DUCKDUCKGO_HTML = "https://html.duckduckgo.com/html/"

async def execute(query: str, max_results: int = 10) -> dict:
    try:
        if not query or not isinstance(query, str) or not query.strip():
            return {"error": "query must be a non-empty string"}
        max_results = min(max_results, 20)

        data = urllib.parse.urlencode({"q": query}).encode("utf-8")
        req = urllib.request.Request(
            DUCKDUCKGO_HTML, data=data,
            headers={
                "User-Agent": "Mozilla/5.0 ... Chrome/120.0.0.0 Safari/537.36",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode(resp.headers.get_content_charset() or "utf-8", errors="replace")

        results = _parse_results(raw, max_results)
        return {"ok": True, "query": query, "results": results, "count": len(results)}
    except Exception as exc:
        return {"error": str(exc), "detail": type(exc).__name__}

def _parse_results(html_text: str, max_results: int) -> list:
    """Extract titles, URLs, snippets from DuckDuckGo HTML."""
    results = []
    blocks = re.split(r'<div class="[^"]*result[^"]*">', html_text)[1:]
    for block in blocks:
        if len(results) >= max_results:
            break
        link_match = re.search(
            r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            block, re.DOTALL,
        )
        if not link_match:
            continue
        url = html.unescape(link_match.group(1).strip())
        title = _strip_html(link_match.group(2)).strip()
        snippet_match = re.search(
            r'<[^>]*class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</(?:a|div|span)>',
            block, re.DOTALL,
        )
        snippet = _strip_html(snippet_match.group(1)).strip() if snippet_match else ""
        if title and url:
            results.append({"title": title, "url": url, "snippet": snippet})
    return results

def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text
```

要点：纯 stdlib，无外部依赖。`async def` 配合同步 `urllib`——框架的 `FunctionBackend` 自动处理。

### 5.4 模式四：框架集成 — undo

`undo` 工具调用引擎的 checkpoint 机制回滚对话状态。

**tools/undo/tool.yaml**：

```yaml
name: undo
description: 撤销最近的对话轮次（状态 + 文件双回滚）
parameters:
  type: object
  properties:
    steps:
      type: integer
      description: "回退步数（默认 1）"
      default: 1
execution:
  sandbox: inherit
  timeout: 30s
activation: kernel
```

**tools/undo/function.py**：

```python
from arf.agent.registry import get_agent

async def execute(steps: int = 1) -> dict:
    try:
        agent = get_agent()
        if agent is None:
            return {"ok": False, "error": "Agent not initialized yet"}

        engine = agent._engine
        effective_steps = steps + 1  # +1 跳过 undo 本轮自身刚 push 的 checkpoint
        available = engine.checkpoint_count()
        if available < effective_steps:
            return {"ok": False, "error": f"Only {available} checkpoints available"}

        restored = engine.undo(steps + 1)
        if restored is None:
            return {"ok": False, "error": "No checkpoints available"}

        await agent.state_store.put("default", restored)
        msg_count = len(restored.get("messages", []))
        return {
            "ok": True, "steps": steps,
            "messages_restored": msg_count,
            "remaining_checkpoints": engine.checkpoint_count(),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}
```

要点：通过 `get_agent()` 获取全局 agent 实例，访问 `_engine` 内部能力。+1 checkpoint 偏移是因为 undo 调用本身会 push 一个 checkpoint——需要跳过它。

### 5.5 要点总结

| 模式 | 代表工具 | 关键技术点 |
|------|---------|-----------|
| 最简工具 | text_to_upper | 参数校验 + try/except |
| 路径操作 | file_reader | WORKSPACE 隔离 + PathCheckToolGuard |
| HTTP 请求 | web_search | async def + urllib + HTML 解析 |
| 框架集成 | undo | get_agent() + engine.checkpoint 操作 |

- 文件系统是真相源：放好文件，FileWatcher 自动检测，下一轮对话即可用
- 错误返回推荐 `{"error": "..."}` 格式，非强制
- `activation: kernel` = 始终可用；`discoverable` = 按需加载

### 5.6 数据修改工具的 Rollback 规范

涉及数据写入的 Tool（如 `file_writer`、`file_deleter`、`resource_scaffold`）
**应该**在 `function.py` 中同时导出 `rollback` 函数，与 `execute` 并列。

框架在 `execute` 抛出异常后自动检查是否存在 `rollback` 函数：
- 有 → 调用 `rollback(**params)`，`ToolResult.rolled_back = True`
- rollback 成功 → 副作用被消除
- rollback 失败 → 错误信息通过 `ToolResult.rollback_error` 携带
- 无 → 不回滚，由模型根据错误信息自行处理

```python
# tools/my_writer/function.py

async def execute(path: str, content: str) -> dict:
    p = WORKSPACE / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return {"ok": True, "path": str(p), "bytes": len(content)}

async def rollback(path: str, content: str) -> dict:
    """撤销文件写入：删除创建的/覆盖的文件."""
    p = WORKSPACE / path
    try:
        p.unlink(missing_ok=True)
        return {"ok": True, "action": "deleted", "path": str(p)}
    except Exception as e:
        return {"ok": False, "error": str(e)}
```

**规范要点**：
- `rollback` 签名与 `execute` 完全一致，接收相同的参数
- 返回值 `{"ok": True, "action": "..."}` 或 `{"ok": False, "error": "..."}`
- 是非强制约定 — 有则回滚，无则跳过
- 只读工具（`file_reader`、`web_search`、`web_fetch`）无需提供 rollback

> 深入阅读：[`docs/app/tools.md`](docs/app/tools.md)

---

## 6. 定义技能

Skill 将多个工具组合为一个可被 LLM 发现和加载的能力。一个 YAML 文件 = 一段提示词 + 工具列表 + 可选的执行依赖。

### 6.1 基础 Skill — file_ops

```yaml
# skills/file_ops.yaml
name: file_ops
description: Comprehensive file operations — read, write, list, delete, download
prompt: |
  # File Operations
  You have access to file manipulation tools. Follow these patterns:

  ## Reading Files
  - Always use file_reader with operation="read" to read file contents
  - Before editing a file, read it first

  ## Writing Files
  - Use file_writer to create or overwrite files
  - For tools/skills/models paths, use handoff

  ## Path Rules
  - All paths are relative to the workspace root
  - Path traversal (..) is blocked
tools:
  - file_reader
  - file_writer
  - file_deleter
  - file_download
activation: kernel
```

`activation: kernel` 的 skill 始终在 LLM 可见范围内。LLM 收到 system prompt 时直接获得 `prompt` 内容，无需额外加载。

### 6.2 渐进式披露 — code_review

```yaml
# skills/code_review.yaml
name: code_review
description: Review code changes for correctness, style, and potential bugs
prompt: |
  # Code Review
  Review the code or changes presented. Perform these checks:

  ## 1. Correctness — does the code do what it claims to do?
  ## 2. Security — injection vulnerabilities? secrets hardcoded?
  ## 3. Style — standard conventions? clear naming?
  ## 4. Robustness — error handling? timeouts?

  Report issues in priority order: CRITICAL, MAJOR, MINOR.
tools:
  - file_reader
activation: discoverable
```

`activation: discoverable` 的 skill **不在**初始工具列表中。LLM 只知道它的存在和描述。当用户意图匹配时，LLM 调用 `file_reader` 读取 `skills/code_review.yaml` 获取完整指令。这就是 ARF 的**渐进式披露**机制：只为活跃能力消耗 token，不活跃的能力零成本驻留。

### 6.3 Pipeline — 执行依赖

当 Skill 中的工具必须按特定顺序执行时，声明 `pipeline`：

```yaml
# skills/resource_scaffold.yaml
name: resource_scaffold
description: Generate a new Tool or Skill resource from requirements
prompt: |
  You are generating a new ARF resource. Produce the complete file contents.

  ## If generating a TOOL:
  **tool.yaml** — use this exact structure: ...
  **function.py** — EVERY generated function.py MUST include error handling.

  ## After creation:
  Use resource_loader to activate the new resource.
tools:
  - file_writer
  - resource_loader
activation: kernel
pipeline:
  - tool: file_writer
    description: 创建 tool.yaml 和 function.py
  - tool: resource_loader
    depends_on:
      - file_writer
    description: 激活新创建的资源
```

引擎在每次工具调用前检查 pipeline 约束：`resource_loader` 依赖 `file_writer` 先成功执行。依赖未满足时，引擎 emit 错误事件并阻断调用。

> 深入阅读：[`docs/app/skills.md`](docs/app/skills.md)

---

## 7. 生命周期 Hook

Hook 是独立子进程脚本，在 Agent 的八个生命周期事件点触发。通过 `SubprocessHookRunner` 以 `asyncio.create_subprocess_shell` 并行启动。

### 7.1 八个事件点

| 事件 | 触发时机 | 典型用途 |
|------|---------|---------|
| `session_start` | 会话开始时 | 初始化日志、加载外部配置 |
| `round_start` | 每轮用户交互开始时 | 轮次计数、上下文准备 |
| `pre_model_call` | 每次调用模型前 | 消息预处理、敏感词过滤 |
| `post_model_call` | 每次模型响应后 | 响应审计、内容归档 |
| `pre_tool_exec` | 工具执行前 | 参数校验、权限二次检查 |
| `post_tool_exec` | 工具执行后 | 工具调用日志、结果归档 |
| `round_end` | 每轮用户交互结束时 | 记忆提取、状态持久化 |
| `session_end` | 会话结束时 | 清理临时文件、发送通知 |

### 7.2 配置与退出码

```yaml
# agent.yaml
hooks:
  - name: log_tool_calls
    type: post_tool_exec
    run: ["python", "./hooks/log_tool_calls.py"]
    timeout: 5s
    env:                          # 可选
      MY_VAR: "value"
```

**退出码约定**：

| 退出码 | 行为 |
|--------|------|
| `0` | 正常执行，继续 Agent 流程 |
| `1` | 阻断当前操作 |
| `2` | 将 stdout 作为 system 消息注入对话流，LLM 下一轮可见 |

退出码 2 是最强大的机制——Hook 可以向对话插入修正或上下文：

```python
# hooks/remind_user.py
import sys
print("注意：用户之前提到过偏好 dark mode 界面风格。")
sys.exit(2)
```

### 7.3 运行时环境变量

所有 hook 子进程自动获得完整的运行时上下文环境变量：

| 变量 | 说明 |
|------|------|
| `ARF_RUNTIME` | 完整运行时上下文 JSON |
| `ARF_SESSION_ID` | 当前会话 ID |
| `ARF_ROUND` | 当前交互轮次 |
| `ARF_MEMORY_DIR` | memory 目录绝对路径 |
| `ARF_WORKSPACE` | workspace 目录绝对路径 |

### 7.4 注意事项

- Hook 是独立子进程，不能直接访问 Agent 内存状态
- 同一事件类型的多个 Hook 通过 `asyncio.gather` 并行执行，顺序不保证
- 超时 Hook 被 SIGKILL 强制终止
- 所有 hook 子进程自动获得 `ARF_RUNTIME`、`ARF_SESSION_ID`、`ARF_ROUND`、`ARF_MEMORY_DIR`、`ARF_WORKSPACE` 环境变量

> 深入阅读：[`docs/app/hooks.md`](docs/app/hooks.md)

---

## 8. 定制 Server

参考 App 的 `server.py` 是框架在生产环境中的完整用法。以下逐层展示从最简单到功能完备的演进。

### 8.1 最小 Server

回顾 Part 2 的 10 行版本：

```python
from arf.agent.factory import create_agent
from arf.agent.config import AgentConfig
from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI()
cfg = AgentConfig.from_yaml("agent.yaml")
agent = create_agent(config=cfg)

@app.post("/api/chat")
async def chat(req: dict):
    result = await agent.chat(req["message"])
    return JSONResponse({"content": result})
```

### 8.2 引入 AppContext + Lifespan

生产级 Server 使用 FastAPI 的 `lifespan` context manager 管理 Agent 生命周期（跨平台，不依赖 signal）：

```python
from contextlib import asynccontextmanager
from agent_main import app_context
from arf.agent.factory import create_agent
from arf.agent.config import AgentConfig
from arf.agent.registry import set_agent

_agent = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _agent
    # ---- STARTUP ----
    _load_dotenv()                                                      # 加载 .env
    cfg = AgentConfig.from_yaml(str(app_context.config_path))           # 解析配置
    _agent = create_agent(config=cfg, app_context=app_context)          # 组装 Agent
    set_agent(_agent)                                                   # 注册全局实例

    # 恢复会话状态
    state = await _agent.state_store.get("default")
    if state:
        logger.info(f"Restored state: {len(state.get('messages', []))} messages")

    await _agent.start()
    yield
    # ---- SHUTDOWN ----
    await _agent.stop()

app = FastAPI(title="ARF Assistant", lifespan=lifespan)
```

`_load_dotenv()` 是一个简易版 `.env` 解析器（不依赖 python-dotenv）：

```python
def _load_dotenv() -> None:
    env_path = app_context.root / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if key and key not in os.environ:
                os.environ[key] = value
```

> BaseAgent 自动创建 FileTraceStore 和 UsageTracker，App 无需手动初始化。
> 参考 `arf/agent/base.py:449-455`。

> **框架自动注入的能力（App 无需编写）：**
> FileTraceStore（事件追踪）、UsageTracker（用量统计）、
> call_model / stream_model（模型 API 注入）、
> SlidingWindowCompactor（上下文压缩）、TwoTierRouter（模型调度）。
> App 层只需关注 agent.yaml 配置 + server 胶水代码。

### 8.3 SSE 流式响应

核心：将 `_agent.astream()` 产生的 `AgentEvent` 翻译为前端可消费的 SSE 事件。参考 `server.py` 的 `_sse_chat` 生成器：

```python
async def _sse_chat(message: str):
    cancel_evt = asyncio.Event()
    _agent.engine.set_cancel_event(cancel_evt)

    # Chrome 缓冲对策：填充 2KB 空字符，强制浏览器 flush ReadableStream
    yield ":" + " " * 2048 + "\n\n"

    async for event in _agent.astream(message):
        t = event.type
        if t == "thinking_delta":
            yield f"data: {json.dumps({'type': 'chunk', 'content': event.data.get('content', '')}, ensure_ascii=False)}\n\n"
        elif t == "tool_call_start":
            yield f"data: {json.dumps({'type': 'tool_call', 'name': event.data.get('tool_name', ''), 'arguments': event.data.get('arguments', '{}'), 'id': event.data.get('id', 'call_0')}, ensure_ascii=False)}\n\n"
        elif t == "tool_call_end":
            success = event.data.get("success", False)
            yield f"data: {json.dumps({'type': 'tool_result', 'id': event.data.get('id', ''), 'result': 'success' if success else 'error', 'content': event.data.get('result', '') if success else ''}, ensure_ascii=False)}\n\n"
        elif t == "approval_required":
            yield f"data: {json.dumps({'type': 'approval_required', 'decision_id': event.data.get('decision_id', ''), 'tool_name': event.data.get('tool_name', ''), 'params': event.data.get('params', {})}, ensure_ascii=False)}\n\n"
        elif t == "guard_block":
            yield f"data: {json.dumps({'type': 'guard_block', 'tool_name': event.data.get('tool_name', ''), 'reason': event.data.get('reason', '')}, ensure_ascii=False)}\n\n"
        elif t == "error":
            yield f"data: {json.dumps({'type': 'error', 'detail': event.data.get('detail', '')}, ensure_ascii=False)}\n\n"
            return

    # 发送完整历史供前端 renderFromHistory
    state = await _agent.state_store.get("default")
    history = state.get("messages", []) if state else []
    yield f"data: {json.dumps({'type': 'done', 'history': history, 'session_id': 'default'}, ensure_ascii=False)}\n\n"
```

事件类型映射：

| Framework Event | SSE type | 前端含义 |
|----------------|----------|---------|
| `thinking_delta` | `chunk` | 流式文本增量 |
| `tool_call_start` | `tool_call` | 工具调用开始 |
| `tool_call_end` | `tool_result` | 工具执行结果 |
| `approval_required` | `approval_required` | 需要人工审批 |
| `approval_resolved` | `approval_resolved` | 审批结果 |
| `guard_block` | `guard_block` | 安全守卫阻断 |
| `error` | `error` | 异常 |
| `session_end` | `done` / `cancelled` | 对话完成/取消 |

### 8.4 关键 API 端点

参考 App 除 `/api/chat` 外还提供了以下端点：

**取消与撤销**：

```python
@app.post("/api/chat/cancel")
async def cancel_chat():
    """取消正在进行的 streaming——设置 asyncio.Event 让引擎在下一轮循环时退出。"""
    evt = _active_cancel_events.get("default")
    if evt and not evt.is_set():
        evt.set()
        return JSONResponse({"status": "cancelled"})
    return JSONResponse({"status": "no_active_chat"})

@app.post("/api/chat/undo")
async def undo_chat(steps: int = 1):
    """回退 N 轮对话——调用 engine.undo() 后写回 state_store。"""
    restored = _agent.engine.undo(steps + 1)
    await _agent.state_store.put("default", restored)
    return JSONResponse({"status": "undone", "steps": steps})
```

**审批**：

```python
@app.post("/api/chat/approve")
async def approve_tool_call(req: dict):
    """前端审批弹窗确认后调用——engine.approve()。"""
    decision_id = req.get("decision_id", "")
    approved = req.get("approved", False)
    ok = _agent.engine.approve(decision_id, approved)
    return JSONResponse({"status": "ok" if ok else "not_found"})
```

**配置与 API Key 管理**：

```python
@app.post("/api/config/register-deepseek")
async def config_register_deepseek(req: dict):
    """接受 API Key，持久化到 .env，重建 Agent（让 ModelAdapter 拿到新 Key）。"""
    api_key = req.get("api_key", "").strip()
    _save_api_key(api_key)
    os.environ["DEEPSEEK_API_KEY"] = api_key
    # 必须重建 Agent，因为 ModelAdapter 在 BaseAgent.__init__ 中初始化
    cfg = AgentConfig.from_yaml(str(app_context.config_path))
    _agent = create_agent(config=cfg, app_context=app_context)
    set_agent(_agent)
    return JSONResponse({"ok": True, "models": [m.type for m in _agent.config.models]})
```

**SPA fallback**：

```python
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

frontend_dir = app_context.root.parent / "web" / "dist"
if frontend_dir.exists():
    app.mount("/assets", StaticFiles(directory=str(frontend_dir / "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        """所有非 /api/ 请求返回 index.html，Vue Router 接管路由。"""
        return FileResponse(frontend_dir / "index.html")
```

### 8.5 CORS 配置

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000", "http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)
```

---

## 9. 搭建前端

参考 App 的前端位于 `app/web/`，基于 **Vue 3 + TypeScript + Vite + Pinia + Vue Router**。

### 9.1 项目结构

```
app/web/
├── package.json              # vue, pinia, vue-router, echarts, vite
├── vite.config.ts            # proxy /api -> localhost:8000
├── index.html
└── src/
    ├── main.ts               # createApp + use pinia + use router
    ├── App.vue               # 根据 appStore.currentPage 路由到 loading/welcome/chat
    ├── router/index.ts       # /, /welcome, /config, /usage, /traces, /resource-stats
    ├── stores/
    │   ├── app.ts            # configStatus, language, currentPage
    │   └── chat.ts           # chatHistory, displayMessages, renderFromHistory()
    ├── composables/
    │   ├── useChat.ts        # SSE streaming 客户端
    │   ├── useApi.ts         # HTTP client
    │   ├── useI18n.ts        # 中英文切换
    │   └── useTrace.ts       # Trace 数据获取
    ├── components/
    │   ├── ChatPanel.vue     # 聊天主面板
    │   ├── MessageBubble.vue # 消息气泡
    │   ├── ToolCard.vue      # 工具调用卡片
    │   ├── StatusBar.vue     # 顶部状态栏
    │   └── ResourcePanel.vue # 资源侧边栏
    └── views/
        ├── WelcomePage.vue   # 欢迎页（"Veiled Cosmos" 视觉）
        └── ConfigPage.vue    # DeepSeek / OpenAI 兼容配置
```

### 9.2 SSE 客户端 — useChat

核心是读取 `fetch` 的 `ReadableStream`，逐行解析 SSE 事件：

```typescript
// composables/useChat.ts — 核心流读取逻辑
async function readStream(reader: ReadableStreamDefaultReader<Uint8Array>) {
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop()!

    for (const line of lines) {
      if (line.startsWith('data: ')) {
        try { handleEvent(JSON.parse(line.slice(6))) } catch { /* ignore */ }
      }
    }
  }
}

function handleEvent(evt: SSEEvent) {
  if (evt.type === 'chunk') {
    if (evt.reasoning) streamingReasoning.value += evt.reasoning
    if (evt.content) streamingText.value += evt.content
  } else if (evt.type === 'tool_call') {
    toolCalls.value.push({ id: evt.id, name: evt.name, args: evt.arguments, status: 'executing' })
  } else if (evt.type === 'tool_result') {
    // 更新 toolCalls 中的状态和结果
  } else if (evt.type === 'approval_required') {
    pendingApproval.value = { decision_id: evt.decision_id, tool_name: evt.tool_name, params: evt.params }
  } else if (evt.type === 'done') {
    chatStore.setHistory(evt.history || [])   // SSR 渲染完整历史
  } else if (evt.type === 'error') {
    streamError.value = evt.detail || 'Unknown error'
  }
}
```

发送消息和取消：

```typescript
async function sendMessage(text: string) {
  abortController = new AbortController()
  const res = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message: text, stream: true }),
    signal: abortController.signal,
  })
  streamReader = res.body!.getReader()
  await readStream(streamReader)
}

function abort() {
  abortController?.abort()    // 取消 HTTP 请求
  streamReader?.cancel()      // 关闭 ReadableStream
}
```

### 9.3 构建与部署

```bash
cd app/web
npm install
npm run build          # 输出到 dist/
```

Vite 开发模式下，`/api` 和 `/ws` proxy 到后端 8000 端口（`vite.config.ts`）：

```typescript
export default defineConfig({
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
      '/ws': { target: 'ws://localhost:8000', ws: true },
    }
  }
})
```

生产模式下，FastAPI 直接 mount `dist/` 为静态文件，SPA fallback 路由确保 Vue Router 的 history mode 正常工作。

---

## 10. 双 Agent 架构

ARF 支持 User Agent + System Agent 双 Agent 架构。User Agent 处理用户对话，System Agent 负责系统操作（资源创建、工具生成、配置管理）。用户看到的是一个连贯的助手，双 Agent 是实现细节。

### 10.1 为什么需要双 Agent

- **Token 隔离**：系统操作不污染用户对话上下文
- **权限分离**：创建/修改资源需要 System Agent 权限
- **模型分离**：系统操作用深度推理模型（V4 Pro），用户对话可在廉价模型上运行

### 10.2 System Agent 配置

System Agent 在 `agent.yaml` 的 `agents:` 段定义，拥有独立的 system_prompt、模型、工具和技能：

```yaml
agents:
  - name: sys_agent
    role: 系统工程师
    task: 资源创建、模型配置、工具/技能生成等系统级操作
    description: 处理资源创建、模型配置、工具/技能生成等系统级操作
    system_prompt:
      prefix:
        role: |
          You are the ARF System Engineer.
        critical_rules: |
          ### Gate 1 — Design
          先设计方案，等待用户确认（"go ahead"、"yes"、"确认"）
          ### Gate 2 — Write
          确认后才调用 file_writer 创建文件
          ### Gate 3 — Validate
          读取验证规范并检查
          ### Gate 4 — Activate
          调用 resource_loader 激活新资源
      suffix: |
        $INVENTORY

    models:
      - type: deep
        model: deepseek-v4-pro
        api_base: https://api.deepseek.com
        api_key_env: DEEPSEEK_API_KEY
        context_window: 1000000
        kwargs:
          reasoning_effort: max

    tools:
      - name: file_reader
        activation: kernel
      - name: file_writer
        activation: kernel
      - name: resource_loader
        activation: kernel
      - name: handoff
        activation: kernel

    skills:
      - name: resource_scaffold
        description: Generate a new Tool or Skill resource from requirements
        tools: [file_writer, resource_loader]
        activation: kernel
      - name: validate_tool
        description: Validate tool completeness
        tools: [file_reader]
        activation: discoverable

    advanced:
      max_turns: 15
      routing:
        strategy: static        # 始终用 deep，不需要路由
        default: deep
```

### 10.3 Handoff 交接

User Agent 调用 `handoff` 工具将任务移交给 System Agent：

```yaml
# tools/handoff/tool.yaml
name: handoff
description: 将资源创建/修改操作移交给 SysAgent
parameters:
  type: object
  properties:
    task:
      type: string
      description: 任务描述
    context:
      type: string
      description: 任务上下文
  required: [task]
activation: kernel
```

交接规则在 `handover:` 段声明：

```yaml
handover:
  rules:
    - from_agent: arf_assistant
      to_agent: sys_agent
      trigger: "创建或修改 resources(tools/skills/models) 目录下的资源文件"
      context:
        raw_turns: 5        # 携带最近 5 轮对话作为上下文
        task_summary: true   # 用 system_model 生成任务摘要
    - from_agent: sys_agent
      to_agent: arf_assistant
      trigger: "资源操作完成或需要用户确认"
      context:
        raw_turns: 0        # 返回时不携带对话上下文
        task_summary: true
```

### 10.4 `_agent_mode` 路径权限分离

`file_writer` 和 `file_deleter` 根据 `_agent_mode` 参数区分权限。User Agent 模式下禁止写入 `tools/`、`skills/`、`models/` 路径——需要调用 `handoff` 交接：

```python
USER_RESTRICTED_PREFIXES = ("/tools/", "/skills/", "/models/")

async def execute(path: str, content: str, _agent_mode: str = "sys") -> dict:
    if _agent_mode == "user":
        for prefix in USER_RESTRICTED_PREFIXES:
            if prefix in path:
                return {"error": "需要 System Agent 权限，请调用 handoff"}
    # 正常写入逻辑...
```

### 10.5 Handoff 流程详解

**正向交接（User Agent → System Agent）**：

1. User Agent 调用 `handoff(task="...", context="...")` → 函数返回 `{"handoff": True, "task": ..., "context": ...}`
2. 引擎在每次工具执行后调用 `HandoffManager.detect()` 扫描 tool_results，发现 `{"handoff": True}` 信号
3. 保存当前 User Agent 状态到 `state_store`（key: `{session_id}/{from_agent}`）
4. `HandoffManager.resolve()` 根据 `handover.rules` 解析目标 Agent
5. `HandoffManager.build_target_context()` 构建 System Agent 的初始消息：target system prompt + raw_turns 上下文 + task summary + handoff user message
6. 设置 `state["active_agent"] = "sys_agent"`，后续工具调用自动携带 `_agent_mode` 参数
7. 下一轮循环使用 System Agent 的独立配置（system_prompt、tools、skills、max_turns）

**反向交接（System Agent → User Agent）**：

System Agent 完成任务后再次调用 `handoff`，引擎检测到 handoff 信号后解析回 `arf_assistant`，从 state_store 恢复正向交接时保存的 User Agent 状态。子 Agent 的最后一条 assistant 消息作为 handoff 结果注入原对话，用户感知不到切换。

**`_agent_mode` 传递链路**：

```
graph.py:state["active_agent"] → tool_executor.execute(agent_mode=...)
→ function.py:params["_agent_mode"] = agent_mode
```

`file_writer` / `file_deleter` 据此区分权限：`_agent_mode == "user"` 时禁止写入 `tools/`、`skills/`、`models/` 路径。

**注意事项**：

- System Agent 每次被调用创建独立上下文（build_target_context 构建全新 messages），不污染 User Agent 对话历史
- User Agent 的状态在 handoff 前持久化，返回时完整恢复
- System Agent 创建的资源（tools/skills/models）由 FileWatcher 自动检测，User Agent 无需重启即可使用
- `trigger` 字段在当前单规则配置下不会被使用（`len(candidates) == 1` 直接返回），但框架已为多目标 handoff 预留了 LLM 匹配 + 关键词 fallback 能力（`HandoffManager.resolve()`）

> 深入阅读：[`docs/app/dual-agent.md`](docs/app/dual-agent.md)

---

## 11. CLI 工具

参考 App 的 `cli.py` 提供常用命令集合。架构模式：

```python
import argparse
from agent_main import app_context   # 共用 AppContext

def cmd_init(args): ...
def cmd_start(args): ...
def cmd_chat(args): ...

def main():
    parser = argparse.ArgumentParser(description="ARF Default Assistant CLI")
    sub = parser.add_subparsers(dest="command")

    p_init = sub.add_parser("init", help="Create skeleton directories")
    p_init.set_defaults(func=cmd_init)
    # ... 更多子命令

    args = parser.parse_args()
    args.func(args)    # 分派到 cmd_xxx
```

### 关键命令

| 命令 | 用途 |
|------|------|
| `init` | 创建 tools/, skills/, hooks/, memory/ 骨架目录 |
| `start` | 构建前端 → 启动 uvicorn → 前台运行（Ctrl+C 停止） |
| `stop` | kill 8000 端口进程 |
| `chat "hello"` | POST /api/chat，打印响应 |
| `list tools` | 列出已注册工具（tools/skills/models） |
| `validate` | 校验 agent.yaml 中声明的工具/技能/钩子是否存在于文件系统 |
| `config generate` | 扫描 filesystem → 输出完整 agent.yaml |

### CLI 作为 HTTP 客户端

大部分命令是对 Server API 的 HTTP 调用：

```python
def _httpx_get(path: str) -> dict | None:
    import httpx
    resp = httpx.get(f"http://127.0.0.1:8000{path}", timeout=10)
    resp.raise_for_status()
    return resp.json()

def _httpx_post(path: str, data: dict | None = None) -> dict | None:
    import httpx
    resp = httpx.post(f"http://127.0.0.1:8000{path}", json=data, timeout=10)
    resp.raise_for_status()
    return resp.json()
```

`config generate` 命令直接使用框架的 Provider 和 Resolver 扫描文件系统：

```python
from arf.resources.providers.tool_provider import ToolProvider
from arf.resources.providers.skill_provider import SkillProvider
from arf.resources.providers.model_provider import ModelProvider
from arf.resources.resolver import ResourceResolver

async def _run():
    tp = ToolProvider(APP_DIR / "tools")
    sp = SkillProvider(APP_DIR / "skills")
    mp = ModelProvider(APP_DIR / "models")
    resolver = ResourceResolver(tp, sp, mp)
    return await resolver.generate_config()

config = asyncio.run(_run())
print(yaml.dump(config, allow_unicode=True, default_flow_style=False))
```

---

## 12. 进阶主题

### 12.1 资源热加载

FileWatcher 在 Linux 上使用 inotify（亚秒级响应），在其他平台使用轮询。检测到 `tools/`、`skills/`、`models/` 目录下文件变更后自动清除缓存，下一轮对话即可使用新资源。

```yaml
advanced:
  reload:
    watch: true             # 启用 FileWatcher（默认 true）
    poll_interval: 5        # 轮询间隔（非 Linux 平台）

  protection:
    enabled: true
    rate_limit:
      requests_per_second: 5       # 每 API 端点每秒请求数
      max_burst: 10                 # 桶容量（允许瞬时突发）
    circuit_breaker:
      failure_threshold: 3          # 连续失败 N 次 → 熔断
      base_cooldown: 10s            # 首次熔断冷却时间
      cooldown_multiplier: 2        # 每次重开后冷却翻倍
      max_cooldown: 300s            # 冷却时间上限
      half_open_max_requests: 1     # 半开状态探测请求数
```

> 深入阅读：[`docs/api-protection.md`](docs/api-protection.md)

### 12.2 动态配置生成

`cli.py config generate` 扫描 tools/skills/models 目录，通过 ResourceResolver 生成完整 agent.yaml。适合用来快速搭建新 App 的骨架配置——先从文件系统了解全部资源，再精简为实际需要的。

### 12.3 框架模块概览

了解框架模块有助于理解运行时行为，通常不需要修改：

```
arf/
├── agent/          # BaseAgent — 组装所有协议实现，自动注入 call_model
├── engine/         # GraphEngine — 主循环：memory→route→compact→call→guard→execute
├── resources/      # 资源系统：三个 Provider + ResourceResolver + FileWatcher
├── memory/         # FileMemoryStore、LLMMemoryWriter、LLMMemoryRetriever
├── compaction/     # SlidingWindowCompactor — token 感知窗口压缩
├── routing/        # TwoTierRouter — 快慢模型调度
├── guardrails/     # PathCheckToolGuard、ToolPermissionChecker
├── hooks/          # SubprocessHookRunner — 六个生命周期事件
├── sandbox/        # PathSandbox — 路径合法性校验
├── observability/  # FileTraceStore、UsageTracker、trace_viewer.html
├── skills/         # SkillPipeline — 工具依赖执行时序
├── human_loop/     # ApprovalPoint、ConsoleChannel — 人机审批
├── evaluation/     # EvalRunner、BenchmarkBuilder、EvalComparator — 回归测评
├── streaming/      # SseStream — Server-Sent Events 传输
├── communication/  # InMemoryAgentBus、PeerAgent — 多 Agent 通信
├── errors/         # DefaultErrorPolicy
├── concurrency/    # SequentialScheduler
├── evaluation/     # EvalRunner、Metrics
├── testing/        # InMemory* test doubles
└── core/           # 协议定义、Pydantic 配置模型、事件类型、ModelAdapter
```

### 框架自动化能力

以下能力由 BaseAgent 自动注入，App 只需在 agent.yaml 中配置即可：

| 能力 | 机制 | agent.yaml 字段 |
|------|------|----------------|
| 工具/技能/模型发现 | FileWatcher + Provider + ResourceResolver | `tools:`, `models/`, `skills/` 目录约定 |
| 状态持久化 | FileStateStore | 自动，每轮 engine turn 后 put() |
| Trace 记录 | FileTraceStore | `advanced.observability.trace_dir` |
| Token 用量追踪 | UsageTracker | 自动 |
| 上下文压缩 | SlidingWindowCompactor | `advanced.compaction` |
| 模型路由 | TwoTierRouter | `advanced.routing` |
| Plugin 注入 | PluginProvider | `plugins:` |
| 模型 API 注入 | ModelAdapter + call_model | `models/*.yaml` |

### 12.4 回归测评

ARF 内置会话回放与回归检测机制。从真实对话 trace 创建 benchmark，通过 EventBus 采集执行轨迹重放，跨配置/模型切换对比运行报告。

```python
from arf.evaluation import BenchmarkBuilder, EvalRunner, EvalComparator

store = FileTraceStore(agent.event_bus, dir="./memory/traces")

# 从真实对话创建 benchmark
builder = BenchmarkBuilder(store)
benchmark = builder.build(session_id="default", name="regression_v1")
benchmark.to_json("benchmarks/regression_v1.json")

# 重放并采集真实 trace
runner = EvalRunner(agent, agent.event_bus)
report = await runner.run(benchmark)
report.to_json("reports/regression_v1_baseline.json")
```

4 个内置指标：成功率、工具准确率、轮次效率、输出关键词匹配。

> 深入阅读：[`docs/eval-benchmark.md`](docs/eval-benchmark.md)

---

## 进一步阅读

| 文档 | 内容 |
|------|------|
| [配置模型](docs/app/models.md) | model.yaml 完整字段、多模型路由、activation 语义 |
| [编写工具](docs/app/tools.md) | tool.yaml 完整字段、工作区隔离、现有工具参考表 |
| [技能与流水线](docs/app/skills.md) | Skill YAML 格式、渐进式披露、pipeline 依赖 |
| [生命周期钩子](docs/app/hooks.md) | 六个事件点详解、退出码约定、配置字段 |
| [双 Agent 架构](docs/app/dual-agent.md) | handoff 机制、路径权限分离、System Agent 设计 |
| [高级配置](docs/app/advanced.md) | Memory、Routing、Compaction、Guardrails 全部字段 |
| [内存管理](docs/memory-management.md) | 压缩管道、记忆抽取与检索、OS 演进类比 |
| [模型路由](docs/model-routing.md) | TwoTierRouter、system model、KV cache |
| [工具沙箱](docs/tool-sandbox.md) | PathSandbox、权限分级、审批通道 |
| [资源注册](docs/resource-registry.md) | 文件系统真相源、Provider、FileWatcher |
| [中断机制](docs/interrupt.md) | 取消、undo、Hook 消息注入 |
| [Trace 可观测性](docs/trace.md) | 事件系统、FileTraceStore、TraceViewer |
| [回归测评](docs/eval-benchmark.md) | 会话回放、Benchmark 创建、指标计算、回归对比 |
