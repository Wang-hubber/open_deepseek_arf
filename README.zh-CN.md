<p align="center">
  <h1 align="center">ARF — Agent Resource Framework</h1>
</p>

<p align="center">
  <a href="./README.md">English</a>
  &nbsp;·&nbsp;
  <strong>简体中文</strong>
  &nbsp;·&nbsp;
  <a href="https://gitee.com/dalaydata/open_deepseek_arf">Gitee</a>
  &nbsp;·&nbsp;
  <a href="https://github.com/Wang-hubber/open_deepseek_arf">GitHub</a>
</p>

<p align="center">
  <a href="./pyproject.toml"><img src="https://img.shields.io/badge/python-3.10+-5fa04e?style=flat-square&labelColor=161b22&logo=python&logoColor=white" alt="Python 3.10+"/></a>
  <a href="./LICENSE"><img src="https://img.shields.io/badge/license-MIT-8b949e?style=flat-square&labelColor=161b22" alt="license"/></a>
  <a href="https://github.com/Wang-hubber/open_deepseek_arf/stargazers"><img src="https://img.shields.io/github/stars/Wang-hubber/open_deepseek_arf.svg?style=flat-square&color=dbab09&labelColor=161b22&logo=github&logoColor=white" alt="GitHub stars"/></a>
</p>

<br/>

<h3 align="center">基于 LangGraph 的 AI 智能体框架，配备 Vue 3 前端。</h3>
<p align="center">工作区即代码 · 双源资源架构（系统 + 用户）· 热重载 · 内建 Trace 追踪 · Docker 一键部署</p>

<br/>

> [!TIP]
> ARF 将本地文件系统作为唯一真相来源。工具、技能、模型就是包含 YAML 配置的目录——无需数据库迁移、无需 Web 控制台、无供应商锁定。`git push` 即可共享你的全部智能体配置。

> [!NOTE]
> **LangGraph 引擎（默认）：** 结构化多节点智能体图，SQLite Trace 追踪，分类器驱动的模型路由，SSE 流式事件——全部通过单个 FastAPI 进程提供服务。

<br/>

## 快速开始

需要 Python ≥ 3.10 和 Node.js ≥ 18。

```bash
# 克隆并安装
git clone git@gitee.com:dalaydata/open_deepseek_arf.git
cd open_deepseek_arf
pip install -e .
cd frontend && npm install && cd ..

# 创建工作区并启动
arf init my_workspace
arf web --workspace my_workspace

# 在另一个终端中启动前端开发服务器
cd frontend && npm run dev
```

浏览器打开 **http://localhost:5173** ——配置 LLM 连接即可开始对话。

### Docker（一键启动）

```bash
docker compose up -d
# → http://localhost:8000
```

通过 GitHub Actions (ghcr.io) 和 Gitee CI (阿里云 ACR) 自动构建预置镜像。详见 [`.github/workflows/docker-publish.yml`](./.github/workflows/docker-publish.yml) 和 [`.gitee-ci.yml`](./.gitee-ci.yml)。

| 镜像仓库 | 拉取命令 |
|----------|---------|
| **GitHub (ghcr.io)** | `docker pull ghcr.io/Wang-hubber/open_deepseek_arf:latest` |
| **阿里云 ACR**（国内） | `docker pull registry.cn-hangzhou.aliyuncs.com/<ns>/arf:latest` |

<br/>

## CLI 命令

| 命令 | 用途 |
|------|------|
| `arf init <name>` | 创建新工作区。**从这里开始。** |
| `arf web` | 启动 Web 服务器（FastAPI + WebSocket + SSE）。 |
| `arf list [tools\|skills\|models]` | 列出已注册资源。`[sys]` = 框架内置。 |
| `arf validate` | 检查工作区资源完整性。 |
| `arf clone <type> <name>` | 将系统资源克隆到用户空间以便自定义。 |
| `arf vault init` | 创建加密保险库，保护 API 密钥和凭据。 |

<br/>

## ARF 的独特之处

| 支柱 | 理念 |
|------|------|
| **文件系统即注册表** | 工具 = 包含 `tool.yaml` + `function.py` 的目录。技能 = 包含 `skill.yaml` 的目录。无需数据库，无需 Web 控制台——`ls` 和 `git diff` 就能说明一切。 |
| **双源资源** | 系统资源随包分发，通过 `pip install --upgrade` 更新。用户资源存放在工作区中，按名称覆盖系统版本。你的自定义永远不会丢失。 |
| **热重载** | 编辑工具的 YAML 或 Python 文件——Agent 立即感知变化。无需重启，无需重新注册。 |
| **LangGraph 引擎** | StateGraph 显式节点（classify → call_model → route → execute_tools/respond）。结构化状态、条件边、内建 SQLite 追踪。 |
| **渐进式披露** | 初始系统提示约 800 tokens。7 个内核工具始终激活。其余按需加载——Agent 只为其真正使用的上下文付费。 |

<br/>

## 架构

```
┌─────────────────────────────────────────────────────┐
│                    FastAPI 服务器                      │
│  ┌──────────┐  ┌──────────┐  ┌───────────────────┐  │
│  │ REST API │  │ WebSocket│  │  静态文件服务       │  │
│  │ /api/*   │  │ /ws      │  │  (Vue 3 SPA)      │  │
│  └────┬─────┘  └────┬─────┘  └───────────────────┘  │
│       │             │                                 │
│  ┌────┴─────────────┴────┐                           │
│  │    SessionManager      │                           │
│  │  ┌──────────────────┐  │                           │
│  │  │  LangGraph Agent  │  │                           │
│  │  │  classify → model │  │                           │
│  │  │  → tools → respond│  │                           │
│  │  └────────┬─────────┘  │                           │
│  │           │             │                           │
│  │  ┌────────┴─────────┐  │                           │
│  │  │ ResourceRegistry  │  │                           │
│  │  │  tools · skills  │  │                           │
│  │  │  models · hooks  │  │                           │
│  │  └────────┬─────────┘  │                           │
│  └───────────┼────────────┘                           │
└──────────────┼────────────────────────────────────────┘
               │
     ┌─────────┴──────────┐
     ▼                    ▼
┌──────────┐      ┌──────────────┐
│  系统资源  │      │    用户资源    │
│  (只读)   │      │   (可读写)     │
│  随包分发  │      │  workspace/   │
└──────────┘      └──────────────┘
```

<br/>

## 内置资源

### 模型（9 种类型）

| 类型 | 用途 |
|------|------|
| `deep_thinking` | 复杂推理——系统设计、多文件重构 |
| `quick_thinking` | 中等任务——代码生成、调试 |
| `quick_no_thinking` | 简单任务——文件读取、事实查询、低延迟 |
| `embedding` | 向量嵌入，用于语义搜索 |
| `rerank` | 结果重排序 |
| `vision` · `vlm` · `tts` · `stt` | 多模态——图像、视频、语音 |

### 内核工具（始终激活）

| 工具 | 用途 |
|------|------|
| `file_reader` · `file_writer` · `file_deleter` | 文件系统操作 |
| `resource_loader` | 按需激活/停用工具 |
| `memory_store` | 长期记忆读写，自动备份轮转 |
| `model_manager` | 模型增删改查、连接测试、激活切换 |
| `model_switch` | 运行时三级模型热切换，自动降级 |

### 可发现工具（按需加载）

`web_fetch` · `web_search` · `git_pusher` · `resource_registrar` · `manage_hooks` · `image_understanding` · `ocr` · `speech_output` · `speech_understanding` · `video_understanding`

### 技能（15 个内置）

`error_handler` · `model_switch` · `model_manager` · `model_configurator` · `memory_extract` · `memory_compress` · `memory_management` · `resource_scaffold` · `tool_generator` · `tool_manager` · `skill_generator` · `skill_manager` · `validate_tool` · `db_operator` · `rag_operator`

<br/>

## 横向对比

| | ARF | LangChain | Dify | 裸 FastAPI + SDK |
|---|---|---|---|---|
| 方法论 | 工作区即代码 | 类库 | 低代码平台 | 自己动手 |
| Agent 引擎 | **LangGraph StateGraph** | LangGraph | 自研 | 自己构建 |
| 资源模型 | **文件系统目录** | Python 类 | Web UI 表单 | 无 |
| 热重载 | **支持（内置）** | 手动 | 部分支持 | 手动 |
| 前端 | **Vue 3 + TypeScript** | 无（LangServe） | React | 自己构建 |
| Trace 追踪 | **SQLite + 瀑布图** | LangSmith（付费） | 内置 | 自己构建 |
| 自托管 | **是，单进程** | 是 | 是（Docker） | 是 |
| 供应商锁定 | 无 | LangChain 生态 | Dify 平台 | 无 |
| 开源协议 | **MIT** | MIT | Apache 2 | 不适用 |

<br/>

## 工作区结构

```
my_workspace/
├── arf_agent.yaml          # 工作区配置（Agent 名称、模型、最大轮次）
├── models/                 # 用户模型配置
│   └── deep_thinking/
│       └── config.yaml     # base_url、api_key、model_name、temperature...
├── tools/                  # 用户自定义工具
├── skills/                 # 用户自定义技能
├── memory/                 # 记忆系统
│   ├── session.md          # 短期会话上下文
│   ├── long_term.md        # 长期用户画像和重要事实
│   └── sessions/           # 已归档会话 JSON（含 trace）
└── .git/
```

启用分类器，实现自动模型路由：

```yaml
# arf_agent.yaml
agent:
  name: "我的工作区"
  model: "quick_no_thinking"
  max_turns: 10
  classifier_enabled: true       # 按任务复杂度自动路由
```

<br/>

## 记忆系统

| 层级 | 存储 | 用途 |
|------|------|------|
| **短期记忆** | `memory/session.md` | 当前会话上下文，每次请求注入提示词 |
| **长期记忆** | `memory/long_term.md` | 用户画像、偏好、重要事实——跨会话持久化 |
| **会话归档** | `memory/sessions/*.json` | 已完成会话，含结构化 trace 和用量数据 |

提取和压缩全自动：Agent 在每次会话结束后运行 `memory_extract`，当长期记忆超过 700 KB 时触发 `memory_compress`。

<br/>

## 开发

```bash
git clone git@gitee.com:dalaydata/open_deepseek_arf.git
cd open_deepseek_arf
pip install -e .
cd frontend && npm install && npm run dev
```

**核心技术栈：** Python 3.10+ · FastAPI · LangGraph · Vue 3 · TypeScript · Vite · SQLite

**依赖：** uvicorn · websockets · openai · PyYAML · watchfiles · jinja2 · cryptography

<br/>

## 配置

### 环境变量

| 变量 | 默认值 | 用途 |
|------|--------|------|
| `ARF_SERVE_STATIC` | `1` | 后端托管前端静态文件。设为 `0` 用于开发代理。 |
| `ARF_DB_NAME` | `arf.db` | SQLite 数据库文件名 |
| `ARF_CORS_ORIGINS` | `localhost:5173` | CORS 允许来源，逗号分隔 |
| `ARF_IDLE_TIMEOUT` | `600` | 会话空闲超时（秒） |
| `ARF_API_MAX_RETRIES` | `3` | API 调用最大重试次数 |
| `ARF_API_RETRY_BACKOFF` | `1.5` | 重试退避基数（秒） |
| `ARF_WORKSPACE` | — | 工作区目录路径（Docker 模式） |

### 模型配置 (`models/<name>/config.yaml`)

```yaml
name: deep_thinking
model_type: deep_thinking
config:
  base_url: "https://api.deepseek.com"
  api_key: "sk-..."
  model_name: "deepseek-chat"
  temperature: 0.7
  max_tokens: 10240
  thinking_enabled: true
  reasoning_effort: "max"
```

<br/>

## 不做什么

> [!IMPORTANT]
> ARF 是有明确取舍的。以下事情它是故意不做的。

- **多供应商抽象层。** ARF 使用 OpenAI 兼容 API。它不会为每个供应商封装统一接口——配置好 base_url 直接用。
- **无代码/低代码平台。** ARF 期望你写 YAML 和 Python。Web UI 用于交互，不是用于构建资源。
- **云 SaaS 服务。** 自托管是默认设计。无托管服务、无遥测、无账户系统（除非你启用多用户模式）。
- **LangChain 平替。** ARF 内部使用了 LangGraph，但将其封装为面向工作区的框架，拥有自己的资源模型。

<br/>

---

<p align="center">
  <sub>MIT — 详见 <a href="./LICENSE">LICENSE</a></sub>
  <br/>
  <sub>基于 LangGraph、FastAPI 和 Vue 3 构建</sub>
  <br/>
  <sub><a href="https://gitee.com/dalaydata/open_deepseek_arf">Gitee</a> &nbsp;·&nbsp; <a href="https://github.com/Wang-hubber/open_deepseek_arf">GitHub</a></sub>
</p>
