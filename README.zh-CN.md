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

<h3 align="center">基于文件系统的、可自演进的 AI 智能体框架。</h3>
<p align="center">执行层与控制层深度解耦。约定大于配置。渐进式能力披露。开箱即用，随时演进。</p>

<br/>

> [!TIP]
> ARF 将本地文件系统作为唯一真相来源。工具、技能、模型就是包含 YAML 配置的目录——无需数据库迁移、无需 Web 控制台、无供应商锁定。一切皆是文件：`ls` 查看，`git` 版本管理，`rsync` 共享。智能体甚至可以在运行时创建和修改自己的资源——自演进是第一等设计概念。

> [!NOTE]
> **LangGraph 引擎（默认）：** 结构化多节点智能体图，SQLite Trace 追踪，分类器驱动的模型路由，SSE 流式事件——全部通过单个 FastAPI 进程提供服务。系统提示词仅约 800 tokens，始终激活的内核工具仅 9 个——其余全部按需加载。

<br/>

## 快速开始

需要 Python ≥ 3.10 和 Node.js ≥ 18。

```bash
# 克隆并安装
git clone git@gitee.com:dalaydata/open_deepseek_arf.git
cd open_deepseek_arf
pip install -e .
cd frontend && npm install && cd ..

# 创建工作区并启动（后端 + 前端一起启动）
arf init my_workspace
arf start --workspace my_workspace
```

浏览器打开 **http://localhost:5173** ——输入 DeepSeek API 密钥即可开始对话。密钥保存在工作区的 `models/<name>/config.yaml` 中。

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

## 设计哲学

ARF 建立在五个原则之上，每一个设计决策都源于此。

### 1. 执行层与控制层——高度解耦

文件系统是"做什么"与"怎么做"之间的桥梁。

- **控制层** — YAML 配置（`tool.yaml`、`skill.yaml`、`config.yaml`）定义了存在哪些资源、它们的 Schema、以及何时使用它们。这一层是人类可读的、Git 可追踪的、无需看代码即可理解的。
- **执行层** — LangGraph 引擎在运行时动态加载和调用工具函数。它通过构造函数注入接收所有依赖，对具体资源一无所知。
- **智能体作为编排者** — `ARFAgent` 从文件系统注册表读取信息，向执行引擎派发任务。对控制文件的更改（编辑工具的 YAML、添加新技能）通过热重载即时生效——执行层无需重启即可适配。

这种解耦意味着你可以通过阅读 YAML 文件来理解智能体行为，通过添加目录来扩展能力。

### 2. 基于文件系统的自演进智能体

ARF 不仅可配置——它还能自我演进。

- **无需数据库迁移。** `arf init` 创建目录。`git push` 共享配置。`ls` 查看状态。
- **运行时自演进。** 智能体可以在运行时使用 `resource_scaffold` + `file_writer` 创建、修改、注册新的工具和技能。一次对话就能产生一个永久能力。
- **记忆即文件。** 会话上下文（`session.md`）、长期记忆（`long_term.md`）、归档会话（`sessions/*.json`）都是磁盘上的文件——可 grep、可备份、完全透明。
- **双源资源。** 系统资源随框架发布（只读，通过 `pip install --upgrade` 更新）。用户资源存放在工作区中（可读写，按名称覆盖系统版本）。你的自定义永远不会被框架更新覆盖。

### 3. 高内聚低耦合——约定大于配置

每种资源都遵循相同的最小约定。框架自动发现，你无需注册。

- **工具** 就是一个包含 `tool.yaml` + `function.py` 的目录。仅此而已——没有装饰器、没有基类、没有 `__init__.py`、没有注册钩子。
- **技能** 就是一个包含 `skill.yaml` 的目录。工具通过名称引用，不通过导入路径。
- **模型** 就是一个包含 `config.yaml` 的目录。适配器在调用时解析配置。
- 位于约定路径的资源目录会被自动扫描和索引。添加一个工具就是创建正确的目录结构——仅此而已。

```
models/deep_thinking/config.yaml    →  注册为 "deep_thinking"
tools/web_fetch/tool.yaml           →  注册为 "web_fetch"
skills/error_handler/skill.yaml     →  注册为 "error_handler"
```

### 4. 每种功能提供且只提供一种默认实现

面向非开发者用户，ARF 为每种能力提供恰好一种实现。

- 一个 `web_fetch`——不做三个 HTTP 客户端。
- 一个 `memory_store`——不做五个存储后端。
- 一个 `model_adapter`——OpenAI 兼容 API，不做多供应商抽象层。

这是刻意的：减少选择疲劳、降低维护负担、缩小 Bug 面。如果你需要不同的实现，用 `arf clone` 将默认实现复制到工作区自行定制。

### 5. 渐进式披露

系统提示词约 800 tokens。始终激活的内核工具仅 9 个。

- **内核工具**（始终激活）：`file_reader`、`file_writer`、`file_deleter`、`resource_loader`、`memory_store`、`model_manager`、`model_switch`、`resource_registrar`、`manage_hooks`
- **其余全部按需加载：** 智能体读取引用某工具的技能时，通过 `resource_loader` 激活该工具，使用完毕后停用。
- 智能体只为其真正使用的上下文付费——不会把 50+ 个工具定义塞进每次提示。

### 6. 如无必要，不增实体

完整的扩展分类就是：**model · skill · tool · hook**。四个实体。没有插件、没有中间件链、没有 Provider 接口、没有组件注册表、没有抽象工厂。如果某个特性无法用这四个实体之一表达，那它可能不属于框架层。

<br/>

## 架构

```
┌──────────────────────────────────────────────────────────┐
│                   控制层（WHAT）                            │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  │
│  │tool.yaml │  │skill.yaml│  │config.yaml│  │arf_agent │  │
│  │(Schema,  │  │(提示词,  │  │(模型,    │  │ .yaml    │  │
│  │ 描述)    │  │ 工具)    │  │ 参数)    │  │(工作区)  │  │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘  │
│       │              │              │              │       │
│  ┌────┴──────────────┴──────────────┴──────────────┴───┐  │
│  │         文件系统（桥梁）                                │  │
│  │  system/ (只读，随包分发)                              │  │
│  │  user/   (可读写，在工作区中)                           │  │
│  └─────────────────────────┬────────────────────────────┘  │
└────────────────────────────┼───────────────────────────────┘
                             │
┌────────────────────────────┼───────────────────────────────┐
│                   执行层（HOW）                              │
│  ┌─────────────────────────┴───────────────────────────┐   │
│  │        ResourceRegistry（双源加载器）                  │   │
│  │   扫描 YAML → 按名称索引 → 追踪来源                      │   │
│  └─────────────────────────┬───────────────────────────┘   │
│                            ▼                               │
│  ┌─────────────────────────────────────────────────────┐   │
│  │           ARFAgent（编排者）                          │   │
│  │  提示词管线：工作区 → 记忆 → 身份 →                     │   │
│  │  资源清单（活跃工具 + 技能） → 语言                     │   │
│  └──────────────────────┬──────────────────────────────┘   │
│                         ▼                                   │
│  ┌──────────────────────────────────────────────────────┐  │
│  │        LangGraph 引擎（StateGraph）                   │  │
│  │  ┌──────────┐  ┌──────────┐  ┌───────────────────┐  │  │
│  │  │ classify │→│call_model│→│execute_tools/      │  │  │
│  │  │ (可选)   │  │          │  │respond            │  │  │
│  │  └──────────┘  └────┬─────┘  └───────────────────┘  │  │
│  │                     │恢复/错误处理                     │  │
│  │                     └────────┐                        │  │
│  │              ┌───────────────┘                        │  │
│  │              ▼                                        │  │
│  │  ┌────────────────────────────────────────────────┐   │  │
│  │  │  FastAPI 服务器 + WebSocket + SSE 流式传输      │   │  │
│  │  │  REST /api/* | WS /ws | SessionManager         │   │  │
│  │  │  SQLite 追踪 | 热重载文件监听                     │   │  │
│  │  └────────────────────────────────────────────────┘   │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

控制层（目录中的 YAML）定义 WHAT（存在哪些资源）。执行层（LangGraph 引擎）决定 HOW（如何运行它们）。文件系统作为桥梁连接两者——`编辑、保存、即时生效`。

<br/>

## 实现状态

### CLI 命令

| 命令 | 状态 |
|------|------|
| `arf init` · `arf web` · `arf start` · `arf stop` · `arf reload` | ✅ 已实现 |
| `arf list` · `arf validate` · `arf clone` · `arf vault *` | ✅ 已实现 |
| `arf chat` · `arf run` | 🚧 骨架（打印 "not yet implemented"） |

### 引擎

全部节点已实现：`classify` → `call_model` → `execute_tools` / `respond`，含 `recovery` 处理 max_tokens 续写和 API 错误恢复。分类器驱动的三级模型路由（`quick_no_thinking` → `quick_thinking` → `deep_thinking`），支持自动降级。

### 工具（共 17 个）

| 状态 | 数量 | 工具 |
|------|------|------|
| **内核工具**（始终激活） | 9 | `file_reader` · `file_writer` · `file_deleter` · `resource_loader` · `memory_store` · `model_manager` · `model_switch` · `resource_registrar` · `manage_hooks` |
| **可发现工具** — 已实现 | 2 | `web_fetch` · `git_pusher` |
| **可发现工具** — 骨架 | 6 | `web_search` · `image_understanding` · `ocr` · `speech_output` · `speech_understanding` · `video_understanding` |

内核工具始终存在于系统提示词中（约 800 tokens）。可发现工具通过 `resource_loader` 按需激活。骨架工具仅有 `config_default.yaml`，无 `function.py`——端点已预留，实现待补充。

### 模型（共 9 种类型）

| 状态 | 数量 | 类型 |
|------|------|------|
| **推理模型** — 可配置 | 3 | `deep_thinking` · `quick_thinking` · `quick_no_thinking` |
| **多模态模型** — 骨架 | 6 | `embedding` · `rerank` · `vision` · `vlm` · `tts` · `stt` |

推理模型完全可配置，兼容 DeepSeek API。多模态模型配置仅作为占位符存在（尚无集成实现）。

### 技能（共 14 个，均有 `skill.yaml`）

| 分类 | 技能 |
|------|------|
| **记忆** | `memory_extract` · `memory_compress` · `memory_management` |
| **模型** | `model_switch` · `model_manager` · `model_configurator` |
| **工具** | `tool_generator` · `tool_manager` · `validate_tool` |
| **技能** | `skill_generator` · `skill_manager` |
| **基础设施** | `resource_scaffold` · `error_handler` · `db_operator` |

注：`rag_operator` 仅有 `config_default.yaml`（部分实现）。

### 服务器与基础设施（全部已实现）

| 组件 | 状态 |
|------|------|
| FastAPI + WebSocket + SSE 流式传输 | ✅ |
| SQLite Trace 追踪（6 张表） | ✅ |
| 会话管理（CRUD、归档、空闲锁定） | ✅ |
| 热重载文件监听（资源变更） | ✅ |
| 子进程 Hook 引擎（4 个内置 Hook） | ✅ |
| AES-256-GCM 加密保险库 | ✅ |
| 多阶段 Docker 构建 + docker-compose | ✅ |
| CI/CD（GitHub Actions + Gitee CI） | ✅ |

### 前端

| | 详情 |
|---|---|
| **技术** | Vue 3 + TypeScript + Vite |
| **规模** | 6400+ 行（8 视图、13 组件、8 组合式函数、3 Store、Pinia + Vue Router） |
| **开发** | `npm run dev` 带 HMR，端口 5173，API 代理至后端 |
| **生产** | `npm run build` 输出至 `server/static/`，由 FastAPI 托管 |

### 框架层 TODO

| 项目 | 状态 |
|------|------|
| **SandBox 运行时安全隔离** | 🔴 规划中——工具当前在进程内执行 |
| `arf chat` 交互式 CLI | 🟡 骨架 |
| `arf run` 无头执行 | 🟡 骨架 |
| 多用户模式 | 🟡 `cli.py` 中已注释 |
| 多模态工具实现 | 🟡 6 个骨架 |

<br/>

## CLI 命令参考

| 命令 | 用途 |
|------|------|
| `arf init <name>` | 创建新工作区。**从这里开始。** |
| `arf start` | 同时启动后端 + 前端（推荐）。 |
| `arf web` | 仅启动 Web 服务器（FastAPI + WebSocket + SSE）。 |
| `arf stop` | 停止运行中的后端和前端进程。 |
| `arf reload` | 停止 + 重启，保留运行配置。 |
| `arf list [tools\|skills\|models]` | 列出已注册资源。`[sys]` = 框架内置。 |
| `arf validate` | 检查工作区资源完整性。 |
| `arf clone <type> <name>` | 将系统资源克隆到用户空间以便自定义。 |
| `arf vault init` | 创建加密保险库（独立键值存储）。 |
| `arf vault unlock` · `lock` · `status` | 管理保险库生命周期。 |

<br/>

## 内置资源

### 内核工具（始终激活，约 800 tokens）

| 工具 | 用途 |
|------|------|
| `file_reader` · `file_writer` · `file_deleter` | 文件系统读写删操作 |
| `resource_loader` | 按需激活/停用工具 |
| `memory_store` | 长期记忆读写，自动备份轮转 |
| `model_manager` | 模型增删改查、连接测试、激活切换 |
| `model_switch` | 运行时三级模型热切换，自动降级 |
| `resource_registrar` | 查询资源配置状态 |
| `manage_hooks` | 查看和切换运行时 Hook 定义 |

### 可发现工具（按需加载）

| 工具 | 状态 | 用途 |
|------|------|------|
| `web_fetch` | ✅ | 获取并处理网页内容 |
| `git_pusher` | ✅ | 暂存、提交、推送文件 |
| `web_search` | 🚧 | 网络搜索（仅配置） |
| `image_understanding` | 🚧 | 图像分析（仅配置） |
| `ocr` | 🚧 | 光学字符识别（仅配置） |
| `speech_output` | 🚧 | 文本转语音（仅配置） |
| `speech_understanding` | 🚧 | 语音转文本（仅配置） |
| `video_understanding` | 🚧 | 视频分析（仅配置） |

### 技能（14 个内置，按类别分组）

| 分类 | 技能 | 用途 |
|------|------|------|
| **记忆** | `memory_extract` | 从对话中提取长期记忆 |
| | `memory_compress` | 压缩长期记忆（超过 700 KB 触发） |
| | `memory_management` | 检查和管理记忆文件 |
| **模型** | `model_switch` | 运行时切换模型层级 |
| | `model_manager` | 模型全生命周期管理 |
| | `model_configurator` | 交互式模型配置 |
| **工具** | `tool_generator` | 从对话生成新工具 |
| | `tool_manager` | 工具生命周期管理 |
| | `validate_tool` | 验证工具 YAML 和 function.py |
| **技能** | `skill_generator` | 从对话生成新技能 |
| | `skill_manager` | 技能生命周期管理 |
| **基础设施** | `resource_scaffold` | 脚手架创建资源目录结构 |
| | `error_handler` | 结构化错误恢复流程 |
| | `db_operator` | SQLite 数据库查询和检查 |

<br/>

## 横向对比

| | ARF | LangChain | Dify | 裸 FastAPI + SDK |
|---|---|---|---|---|
| **方法论** | 工作区即代码 | 类库 | 低代码平台 | 自己动手 |
| **Agent 引擎** | LangGraph StateGraph | LangGraph | 自研 | 自己构建 |
| **资源模型** | **文件系统目录** | Python 类 | Web UI 表单 | 无 |
| **自演进** | **支持——Agent 可创建资源** | 手动 | 部分（插件商店） | 手动 |
| **热重载** | **支持（内置）** | 手动 | 部分支持 | 手动 |
| **渐进式披露** | **支持——9 内核工具，约 800 tokens** | 否（全量工具列表） | 部分 | 手动 |
| **前端** | Vue 3 + TypeScript | 无（LangServe） | React | 自己构建 |
| **Trace 追踪** | SQLite + 瀑布图 | LangSmith（付费） | 内置 | 自己构建 |
| **沙箱隔离** | 规划中 | 可选 | 部分支持 | 手动 |
| **自托管** | 是，单进程 | 是 | 是（Docker） | 是 |
| **供应商锁定** | 无——Git 原生 | LangChain 生态 | Dify 平台 | 无 |
| **开源协议** | MIT | MIT | Apache 2 | 不适用 |

<br/>

## 工作区结构

```
my_workspace/
├── arf_agent.yaml          # 工作区配置（Agent 名称、模型、最大轮次、预加载）
├── .arf/                   # 运行时状态（PID 文件、运行配置）
├── models/                 # 用户模型配置
│   └── deep_thinking/
│       └── config.yaml     # base_url、api_key、model_name、temperature...
├── tools/                  # 用户自定义工具
├── skills/                 # 用户自定义技能
├── memory/                 # 记忆系统
│   ├── session.md          # 短期会话上下文
│   ├── long_term.md        # 长期用户画像和重要事实
│   └── sessions/           # 已归档会话 JSON（含 trace）
└── .git/                   # 自行初始化：git init && git add -A
```

启用分类器，实现自动模型路由：

```yaml
# arf_agent.yaml
agent:
  name: "我的工作区"
  model: "quick_no_thinking"
  max_turns: 10
  classifier_enabled: true       # 按任务复杂度自动路由

resources:
  preload: []                    # 会话启动时即激活的工具列表
```

<br/>

## 记忆系统

| 层级 | 存储 | 用途 |
|------|------|------|
| **短期记忆** | `memory/session.md` | 当前会话上下文，每次请求注入提示词 |
| **长期记忆** | `memory/long_term.md` | 用户画像、偏好、重要事实——跨会话持久化 |
| **会话归档** | `memory/sessions/*.json` | 已完成会话，含结构化 trace 和用量数据 |

提取和压缩全自动：`memory_extractor` Hook 在每次会话结束后运行；当长期记忆超过 700 KB（可配置）时自动触发 `memory_compress`。`memory_store` 在每次写入长期记忆前自动创建备份。

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
| `ARF_DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | DeepSeek 一键配置的 Base URL |

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

保险库（`arf vault init`）提供 AES-256-GCM 加密存储，可用于保存任意密钥。当前为独立键值存储——保险库与模型配置的自动集成尚未实现。

<br/>

## 路线图

| 时间 | 项目 |
|------|------|
| **近期** | SandBox 运行时——工具执行的安全隔离（当前为进程内执行） |
| **近期** | `arf chat`——交互式对话 CLI |
| **近期** | `arf run`——无头脚本执行 |
| **后续** | 多模态工具实现（web_search、image_understanding、ocr 等） |
| **后续** | 多用户模式——认证和独立会话 |

<br/>

## 设计决策

> [!IMPORTANT]
> ARF 是有明确取舍的。以下选择皆是刻意为之。

**约定大于配置。** 路径是可预测的，不可配置的。`models/<name>/config.yaml` 永远有效。没有路径别名，没有 `$MODEL_DIR` 环境变量。

**每种功能一种默认实现。** 一个 web fetch、一个 memory store、一个 model adapter。降低维护成本，避免选择困难，保持系统提示词整洁。

**四种实体类型。** model / skill / tool / hook。如果某个特性需要第五种，它可能不属于框架层。

**不做多供应商抽象层。** ARF 使用 OpenAI 兼容 API。配置好 `base_url` 直接用——无需供应商专用封装。

**不做云 SaaS 服务。** 自托管是默认设计。无托管服务、无遥测、无账户系统（除非启用多用户模式）。

**不做零代码构建器。** ARF 期望你写 YAML 和 Python。Web UI 用于交互，不是用于构建资源。

<br/>

## 参与贡献

详见 [贡献者须知.md](./贡献者须知.md)，包括：

- 如何添加新工具、技能、模型、Hook
- 约定大于配置的编码规范
- Pull Request 工作流和测试指南

```bash
# 开发环境搭建
git clone git@gitee.com:dalaydata/open_deepseek_arf.git
cd open_deepseek_arf
pip install -e .
cd frontend && npm install && npm run dev
```

**核心技术栈：** Python 3.10+ · FastAPI · LangGraph · Vue 3 · TypeScript · Vite · SQLite

**依赖：** uvicorn · websockets · openai · PyYAML · watchfiles · jinja2 · cryptography

<br/>

---

<p align="center">
  <sub>MIT — 详见 <a href="./LICENSE">LICENSE</a></sub>
  <br/>
  <sub>基于 LangGraph、FastAPI 和 Vue 3 构建</sub>
  <br/>
  <sub><a href="https://gitee.com/dalaydata/open_deepseek_arf">Gitee</a> &nbsp;·&nbsp; <a href="https://github.com/Wang-hubber/open_deepseek_arf">GitHub</a></sub>
</p>
