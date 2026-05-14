# ARF — Agent Resource Framework

> **v0.7-dev** (LangGraph) | 双源资源架构 · 长期记忆 · 多用户支持 · i18n · 用量可视化 · 对话即开发 · **LangGraph 引擎 · 内建 Trace 追踪 · 反馈标注 · 自动模型路由**

像与一位随身工程师结对工作——描述任务，它立刻制造工具、组合技能、运行验证，并把你的一切能力资产有序沉淀在本地目录中。

## 核心理念

三大设计哲学贯穿始终：

| 哲学 | 核心思想 |
|------|---------|
| **约定大于配置** | 目录结构即注册表，文件名即配置，零样板代码启动 |
| **低耦合高内聚** | QueryEngine 纯回调隔离，Agent 不感知 LLM 提供商，换模型零影响 |
| **渐进式披露** | 初始系统提示仅 ~800 tokens，6 个 Kernel Tool 始终激活，其余按需加载 |

详见 [产品设计文档](docs/ARF.md)。

## v0.7 LangGraph 引擎 (opt-in)

v0.7 引入了基于 LangGraph 的 Agent 核心引擎，替代手写 `while` 循环控制平面，启用后获得：

| 特性 | 说明 |
|------|------|
| **GraphEngine** | LangGraph `StateGraph` 替代 `QueryEngine`，显式节点/边/条件路由，结构化状态管理 |
| **内建 Trace 追踪** | SQLite 存储每次调用的节点级 trace（耗时、模型、token、工具），前端 `/traces` 瀑布图可视化，支持 JSON 导出 |
| **反馈标注** | 每条 AI 回复可赞/踩，踩了弹出反馈输入框，反馈数据关联到 trace 记录 |
| **自动模型路由** | 开启 classifier 后，系统自动分析任务复杂度（simple/medium/complex）并选择最佳模型 tier |

### 启用方式

```bash
# 单用户模式 — 启用 LangGraph 引擎
ARF_USE_LANGGRAPH=1 arf web

# 同时启用自动模型路由（按任务复杂度自动切换模型）
ARF_USE_LANGGRAPH=1 ARF_CLASSIFIER_ENABLED=1 arf web
```

默认关闭，使用传统 `QueryEngine` — 可在 `arf_agent.yaml` 或环境变量中配置。
Trace 数据自动写入 SQLite，前端 `/traces` 页面查看，无需额外配置。

### 架构变化

```
传统引擎 (v0.6)               LangGraph 引擎 (v0.7)
─────────────────              ─────────────────────
while turn <= max_turns:       START → classify?
    call_model()                   ↓
    if tool_calls:            call_model → route:
        execute()                 ├─ tool_calls → execute → loop
    else:                         ├─ max_tokens → recovery → respond/loop
        break                     └─ normal → respond → END
```

### 安装依赖

```bash
pip install -e ".[langgraph]"
# 或直接安装
pip install langgraph langsmith langchain-core
```

## 快速开始

```bash
pip install arf

# 多用户模式（推荐）
ARF_MULTI_USER=1 arf serve --data-dir ./data --port 8000

# 浏览器打开 http://localhost:8000
# → 注册 → 引导页 → DeepSeek 一键配置（仅需 API Key）→ 开始对话
```

**单用户模式**：
```bash
arf init my_workspace
cd my_workspace
arf web
```

## v0.6 亮点

- **前后端分离**：Vue 3 + TypeScript + Pinia，独立 Vite 构建，三栏布局
- **多用户支持**：注册（用户名+邮箱+密码+验证码）、JWT 登录、LRU 会话池
- **i18n 国际化**：前端 UI + Agent 回复语言全链路中/英文切换
- **用量统计**：Token 消耗趋势图 + 各模型汇总表 + 费用估算（UsagePage + UsageBar）
- **长期记忆**：双层记忆体系（session + long_term），自动提取+压缩+备份轮转
- **暗色主题**：Dark tech-theme UI，玻璃拟态 + 渐变，资源面板系统/用户标签切换
- **Gated 资源创建**：Agent 生成资源 → 用户确认 → 写入 → 验证的闭环工作流
- **用户可配置 max_turns**：`arf_agent.yaml` 中设置 `agent.max_turns`，默认 10

## CLI 命令

| 命令 | 说明 |
|------|------|
| `arf init <name>` | 创建新工作区 |
| `arf web` | 启动 Web 管理界面 (FastAPI + SSE + WebSocket) |
| `arf chat` | 终端对话会话 |
| `arf list [tools\|skills\|models]` | 列出可用资源，系统资源标记 `[sys]` |
| `arf validate` | 检查工作区资源完整性 |
| `arf clone <type> <name>` | 克隆系统资源到用户空间以便自定义 |
| `arf run <skill>` | 运行已保存的技能 |
| `arf user delete <username>` | 删除用户（多用户模式） |

## 资源模型

所有能力收敛为三种资源：

| 资源 | 定义 |
|------|------|
| **Model** | LLM 配置，支持 deep_thinking / quick_thinking / quick_no_thinking / embedding / rerank / vision / tts / stt / vlm 十种类型，OpenAI 兼容接口 |
| **Tool** | 可执行功能单元，`tool.yaml` + `function.py`，封装任意能力（HTTP、文件、SQL、RAG 等） |
| **Skill** | 可复用任务流程，包含 `prompt_template`、工具链和参数定义 |

## 工作区结构

```
my_workspace/
├── arf_agent.yaml       # 工作区配置 (agent.model, agent.max_turns, resources.preload)
├── models/              # 用户模型配置
│   ├── deep_thinking/
│   │   └── config.yaml
│   ├── quick_thinking/
│   │   └── config.yaml
│   └── quick_no_thinking/
│       └── config.yaml
├── tools/               # 用户自定义工具
├── skills/              # 固化技能
├── memory/              # 记忆系统
│   ├── session.md       # 短期会话记忆（框架基础组件）
│   ├── long_term.md     # 长期记忆（跨会话持久化，≤1MB）
│   └── sessions/        # 会话归档（JSON，含 graph_traces）
│       └── 20260513_143022.json
└── .git/
```

启用 LangGraph 引擎后，每次调用的 trace 自动写入 SQLite（`trace_events` 表），会话归档 JSON 包含结构化 trace 和用量汇总：

```json
{
  "id": "20260513_143022",
  "title": "设计微服务架构",
  "messages": [...],
  "graph_traces": [
    {"node": "classify", "classification": "complex", "resolved_model": "deep_thinking", "duration_ms": 320.0},
    {"node": "call_model", "model": "deep_thinking", "turn": 1, "has_tool_calls": true, "duration_ms": 2150.0},
    {"node": "execute_tools", "tool": "file_writer", "turn": 1, "duration_ms": 45.0}
  ],
  "usage": {"prompt_tokens": 1250, "completion_tokens": 800, "total_tokens": 2050}
}
```

前端 `/traces` 页面提供会话列表 + 瀑布图可视化 + JSON 导出。每条 AI 回复可 👍👎 标注，踩了的反馈记录在 `message_feedback` 表中并与 trace 关联。

## 双源资源架构

```
┌──────────────────────────────────┐
│     Unified Resource Registry    │
│  ┌────────┐ ┌──────┐ ┌───────┐  │
│  │ Model  │ │ Tool │ │ Skill │  │
│  └────────┘ └──────┘ └───────┘  │
└─────────┬──────────────┬─────────┘
          │              │
   ┌──────┘              └──────┐
   ▼                            ▼
┌──────────┐            ┌──────────────┐
│  System  │            │     User     │
│  只读    │            │   可读写     │
│ 包内分发  │            │  Workspace 下 │
└──────────┘            └──────────────┘
```

- 系统资源随 `pip install --upgrade arf` 更新，用户 Workspace 完全不受影响
- 同名工具/技能冲突时，用户版本覆盖系统版本（支持 clone → 自定义）
- 同名模型配置自动合并（用户 api_key 覆盖系统模板）

## 预置系统资源

### Tools (9 个)

| 工具 | 说明 | 类型 |
|------|------|------|
| file_reader | 文件读取、目录列表 | Kernel |
| file_writer | 文件写入 | Kernel |
| file_deleter | 文件删除 | Kernel |
| resource_loader | 按需激活/停用工具 | Kernel |
| memory_store | 长期记忆读写，自动备份轮转，1MB 上限 | Kernel |
| model_manager | 模型资源管理（增删改查、连接测试、切换激活） | Kernel |
| model_switch | 运行时三级模型热切换，自动降级 | Kernel |
| web_fetch | HTTP 请求获取网页内容 | Discoverable |
| git_pusher | Git 仓库推送管理 | Discoverable |

### Skills (6 个)

| 技能 | 说明 |
|------|------|
| error_handler | 工具调用出错时的自愈协议 |
| model_switch | 指导 LLM 根据任务复杂度在三级模型间切换 |
| memory_extract | 对话结束后自动提取用户偏好、重要事实存入长期记忆 |
| memory_compress | 记忆压缩整理（优先使用 flash/quick_thinking 模型节省成本） |
| resource_scaffold | 根据需求描述生成 Tool 或 Skill 脚手架 |
| validate_tool | 干跑测试验证工具可用性 |

## 记忆系统

| 层级 | 存储 | 用途 |
|------|------|------|
| **短期记忆** | `memory/session.md` | 当前会话上下文，框架基础组件，每次请求注入提示词 |
| **长期记忆** | `memory/long_term.md` | 用户画像、偏好、重要事实、决策记录，跨会话持久化 |

**长期记忆工作流程：**
1. 每轮对话结束后，Agent 自动调用 `memory_extract` 技能提取值得记忆的信息
2. 记忆注入提示词 pipeline（优先级在 workspace 和 session memory 之间）
3. 记忆超过 700KB（70%）时触发压缩提醒，优先使用 flash 模型压缩
4. 每次写入前自动备份为 `long_term_{timestamp}_bak.md`

## 典型工作流

```
用户: 帮我汇总昨天的邮件并生成待办事项
  → Agent 分析：需要 email_reader 工具 + todo_summarizer 技能
  → 生成缺失资源，展示 diff 请求确认
  → 写入 workspace，干跑验证
  → 执行完整流程，返回结果
  → 固化技能，下次一句命令即可复用
```

## 配置说明

### 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ARF_MULTI_USER` | `0` | 设为 `1` 启用多用户模式（注册/登录/JWT） |
| `ARF_DATA_DIR` | `./data` | 数据目录（数据库、用户工作区、SMTP 配置） |
| `ARF_DB_NAME` | `arf.db` | SQLite 数据库文件名 |
| `ARF_DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | DeepSeek API 地址 |
| `ARF_SMTP_HOST` | `smtp.163.com` | SMTP 服务器 |
| `ARF_SMTP_PORT` | `465` | SMTP 端口 |
| `ARF_SMTP_USER` | — | SMTP 登录用户名 |
| `ARF_SMTP_PASSWORD` | — | SMTP 授权码 |
| `ARF_DEV_EMAIL` | — | 开发模式收件邮箱（SMTP 未配置时验证码打入日志） |
| `ARF_API_MAX_RETRIES` | `3` | API 调用最大重试次数 |
| `ARF_API_RETRY_BACKOFF` | `1.5` | 重试退避基数（秒） |
| `ARF_CORS_ORIGINS` | `localhost:5173` | CORS 允许来源，逗号分隔 |
| `ARF_SERVE_STATIC` | `1` | 是否托管前端静态文件，设为 `0` 禁用 |
| `ARF_IDLE_TIMEOUT` | `600` | 会话空闲超时（秒），超时自动锁定保险库 |
| `ARF_JWT_EXPIRY_HOURS` | `24` | JWT Token 过期时间（小时） |
| `ARF_USE_LANGGRAPH` | `0` | 设为 `1` 启用 LangGraph 引擎替代传统 QueryEngine |
| `ARF_CLASSIFIER_ENABLED` | `0` | 设为 `1` 启用任务复杂度自动分类与模型路由（需 LangGraph） |

### smtp.yaml

多用户模式下（`ARF_MULTI_USER=1`），放在 `{ARF_DATA_DIR}/smtp.yaml`：

```yaml
host: smtp.163.com
port: 465
user: your_email@163.com
password: your_smtp_password
dev_email: admin@example.com
```

环境变量优先级高于 smtp.yaml 文件。

### 工作区配置 (arf_agent.yaml)

```yaml
agent:
  name: "My Workspace"
  model: "quick_no_thinking"   # 会话初始模型
  max_turns: 10                # 每轮对话最大工具调用次数
  memory: "memory/session.md"

resources:
  preload: []                  # 会话启动时预加载的工具列表
```

### 模型配置 (models/{name}/config.yaml)

```yaml
name: deep_thinking
model_type: deep_thinking
config:
  base_url: "https://api.deepseek.com"
  api_key: "sk-..."
  model_name: "deepseek-v4-pro"
  temperature: 0.7
  max_tokens: 10240
  top_p: 1.0
  thinking_enabled: true       # DeepSeek: 是否启用深度思考
  reasoning_effort: "max"      # DeepSeek: high | max
  response_format: "text"      # text | json_object
```

DeepSeek 一键注册（Web UI → 配置页 → DeepSeek 选项卡）自动创建以上三个模型配置，仅需提供 API Key。

**三级模型切换**：系统内置 `model_switch` 工具，Agent 可根据任务复杂度在三档模型间手动切换。启用 `ARF_CLASSIFIER_ENABLED=1` 后，首条消息自动分类并路由到最佳模型：

| 分类 | 模型 | 典型场景 |
|------|------|---------|
| **simple** | `quick_no_thinking` | 问候、文件读取、事实查询 |
| **medium** | `quick_thinking` | 代码生成、调试、多步推理 |
| **complex** | `deep_thinking` | 系统设计、多文件重构、创意工作 |

自动路由找不到目标模型时会沿 `deep_thinking → quick_thinking → quick_no_thinking` 链降级。

## 开发

```bash
git clone git@gitee.com:dalaydata/arf.git
cd arf
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 运行测试
pytest

# 前端开发
cd frontend
npm install
npm run dev
```

**要求**: Python 3.10+ | Node.js 18+

**核心依赖**: FastAPI, uvicorn, openai, PyYAML, watchfiles, jinja2, websockets, python-multipart, cryptography, pyjwt

**可选依赖 (LangGraph 引擎)**：安装 `pip install -e ".[langgraph]"` 获得 `langgraph>=0.3.0`, `langchain-core>=0.3.0`（Trace 追踪为内建实现，零额外依赖）

**前端依赖**: Vue 3, TypeScript, Vite, Pinia, Vue Router

## 路线图

| 版本 | 核心交付 |
|------|----------|
| **v0.5** | 双源资源架构、精简资源模型、核心系统工具/技能、CLI 对话流、资源生成与验证闭环 |
| **v0.6** | 前后端分离（Vue3+TS）、多用户支持（JWT+注册）、i18n 中/英文全链路切换、用量统计与可视化、长期记忆系统、暗色主题 UI、会话生命周期管理 |
| **v0.7** | **LangGraph 引擎 (GraphEngine)** 替代手写循环、**内建 Trace 追踪 (SQLite + 瀑布图)**、**反馈标注 (赞/踩 + 反馈输入)**、**任务复杂度自动分类与模型路由**、**会话费用统计**、流式 token 级事件透传 |
| v0.8 | 向量化记忆检索、Skill 依赖关系图谱、密码重置/找回、管理员审核界面 |
| v1.0 | 开放注册（邮箱验证码）、社区 Skill 市场、声明式多 Agent 协作、企业级权限与部署 |

## 许可证

Private — Gitee: [dalaydata/arf](https://gitee.com/dalaydata/arf)
