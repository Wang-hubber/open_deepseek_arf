<p align="center">
  <h1 align="center">ARF — Agent Resources & RunTime FrameWork</h1>
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

## 设计哲学

ARF 不是又一个在抽象之上堆叠抽象的 AI 智能体框架。它建立在一个核心理念之上：**文件系统是智能体资源管理的理想媒介。** 目录即命名空间，YAML 即声明，Python 即实现——不需要数据库，不需要注册中心，不需要 UI 配置向导。

### 核心原则

**1. 执行层与控制层——通过文件系统深度解耦**

控制层（WHAT）是纯粹的声明：YAML 文件描述存在哪些资源、它们的 Schema、以及何时使用。执行层（HOW）是纯粹的逻辑：LangGraph 引擎在运行时动态加载和调用函数。文件系统作为桥梁连接两者——编辑 YAML，保存，即刻生效。无需重启，无需重新编译，无需部署。

- **控制层** — 人类可读、Git 可追踪的 YAML 配置（`tool.yaml`、`skill.yaml`、`config.yaml`）
- **执行层** — LangGraph StateGraph 引擎，通过依赖注入接收组件，对具体资源完全无感
- **ARFAgent 编排器** — 从文件系统注册表读取信息，向引擎派发任务，通过热重载即时适配变更

**2. 基于文件系统的自演进智能体**

ARF 不仅可配置——它能在运行时自我演进。

- `arf init` 创建目录。永不需要数据库迁移。
- 智能体在对话中使用 `resource_scaffold` + `file_writer` 创建新的工具和技能。一次聊天即可产生永久能力。
- 记忆即文件：`session.md`（短期上下文）、`long_term.md`（持久画像）、`sessions/*.json`（归档）。可 grep、可备份、完全透明。
- 双源资源体系：系统资源随框架发布（只读，通过 `pip install --upgrade` 更新），用户资源在工作区中（可读写，按名称覆盖系统版本）。升级永不会覆盖你的定制。

**3. 约定大于配置——四种实体，一种约定**

每种资源遵循相同的最小约定。框架自动发现，无需手动注册。

| 实体 | 定义 | 用途 |
|------|------|------|
| **Model** | `models/<name>/config.yaml` | API 端点、凭证、推理参数 |
| **Tool** | `tools/<name>/tool.yaml` + `function.py` | 可调用的能力，附 JSON Schema |
| **Skill** | `skills/<name>/skill.yaml` | 可复用的提示词 + 工具编排模板 |
| **Hook** | `.hooks.json` → 子进程脚本 | 生命周期事件拦截器 |

没有装饰器。没有基类。没有 `__init__.py`。没有注册钩子。一个工具就是一个包含两个文件的目录。一个模型就是一个包含一个文件的目录。这就是全部的分类体系。

**4. 渐进式能力披露——约 800 Token 系统提示词**

智能体只为其实际使用的上下文付费。

- **内核层**（9 个工具，始终激活）：文件操作、资源加载、记忆管理、模型管理、Hook 管理
- **可发现层**（其余所有）：通过 `resource_loader` 按需激活，用完即停
- **技能层**：编排工具的提示词模板——在智能体或用户调用时加载

不会把 50+ 工具定义塞进每次 API 调用。初始系统提示词约 800 tokens。

**5. 每种功能一种默认实现**

一个 `web_fetch`，不做三个 HTTP 客户端。一个 `memory_store`，不做五个存储后端。一个 model adapter（OpenAI 兼容 API），不做多供应商抽象层。这减少了选择疲劳、维护负担和 Bug 面。需要不同的实现？`arf clone` 默认实现到工作区，自行定制。

**6. 自托管、Git 原生、无供应商锁定**

无云 SaaS。无托管服务。无遥测。你的工作区就是一个目录。你的配置就是 YAML。你的版本控制就是 Git。部署到任何能跑 Python 的地方——单进程、Docker、或你自己的基础设施。

### 功能总览

| 能力 | 实现 |
|------|------|
| **智能体引擎** | LangGraph StateGraph，compact → classify → call_model → execute_tools/respond → recovery |
| **模型路由** | 二级分类器（medium/complex）：quick_thinking ↔ deep_thinking 自动切换 |
| **上下文压缩** | 上下文超 75% 窗口时自动滑动窗口 + 摘要压缩 |
| **渐进式披露** | 长工具结果存盘，上下文中显示摘要 + 文件路径 |
| **API 服务器** | FastAPI + WebSocket + SSE 流式传输 |
| **前端** | Vue 3 + TypeScript + Vite（6400+ 行，8 视图、13 组件） |
| **可观测性** | SQLite Trace 数据库（6 张表），瀑布图可视化 |
| **记忆系统** | 三层：会话 → 长期 → 归档，自动提取与压缩 |
| **Hook 引擎** | 基于子进程的生命周期 Hook（6 事件、4 内置），退出码契约 |
| **自演进** | 智能体可在运行时创建、编写、注册新工具和技能 |
| **热重载** | 文件监听器检测资源变更，注册表无重启更新 |
| **Docker 支持** | 多阶段 Dockerfile + docker-compose |
| **跨平台** | Windows + Linux 支持，UTF-8 文件 I/O，Vite 自动安装 |

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

浏览器打开 **http://localhost:5173**——输入 DeepSeek API 密钥即可开始对话。密钥保存在工作区的 `models/<name>/config.yaml` 中。

### CLI 命令参考

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
| `arf chat` | 交互式对话 CLI（骨架）。 |
| `arf run` | 无头脚本执行（骨架）。 |

### 工作区结构

```
my_workspace/
├── arf_agent.yaml          # 工作区配置（Agent 名称、模型、最大轮次、预加载）
├── .arf/                   # 运行时状态（PID 文件、运行配置）
├── .hooks.json             # 生命周期 Hook 定义
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

### 配置

**环境变量：**

| 变量 | 默认值 | 用途 |
|------|--------|------|
| `ARF_SERVE_STATIC` | `1` | 后端托管前端静态文件。设为 `0` 用于开发代理。 |
| `ARF_DB_NAME` | `arf.db` | SQLite 数据库文件名 |
| `ARF_CORS_ORIGINS` | `localhost:5173` | CORS 允许来源，逗号分隔 |
| `ARF_IDLE_TIMEOUT` | `600` | 会话空闲超时（秒） |
| `ARF_API_MAX_RETRIES` | `3` | API 调用最大重试次数 |
| `ARF_API_RETRY_BACKOFF` | `1.5` | 重试退避基数（秒） |

| `ARF_CLASSIFIER_ENABLED` | `0` | 启用自动模型路由（设为 `1` 激活） |
| `ARF_DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | DeepSeek 一键配置的 Base URL |

**模型配置 (`models/<name>/config.yaml`)：**

```yaml
name: deep_thinking
model_type: deep_thinking
context_window: 1048576   # 1M tokens
config:
  base_url: "https://api.deepseek.com"
  api_key: "sk-..."
  model_name: "deepseek-v4-pro"
  temperature: 0.7
  max_tokens: 100000
  thinking_enabled: true
  reasoning_effort: "max"
```

**分类器配置 (`arf_agent.yaml`)：**

```yaml
agent:
  name: "我的工作区"
  model: "quick_thinking"
  max_turns: 10
  max_tool_result_chars: 2000  # 工具输出截断阈值
  classifier_enabled: true       # 按任务复杂度自动路由

resources:
  preload: []                    # 会话启动时即激活的工具列表
```

<br/>

## 横向对比

ARF 在 AI 智能体领域占据独特的生态位：它既不是类库，也不是低代码平台，而是一个**工作区即代码的框架**，以文件系统为唯一事实来源。

| | ARF | LangChain | Dify | 裸 FastAPI + SDK |
|---|---|---|---|---|
| **方法论** | 工作区即代码 | 类库 | 低代码平台 | 自己动手 |
| **Agent 引擎** | LangGraph StateGraph | LangGraph | 自研 | 自己构建 |
| **资源模型** | 文件系统目录 | Python 类 | Web UI 表单 | 无 |
| **自演进** | 支持——Agent 可在运行时创建资源 | 手动 | 部分（插件商店） | 手动 |
| **热重载** | 支持（内置文件监听） | 手动 | 部分支持 | 手动 |
| **渐进式披露** | 支持——9 内核工具，约 800 tokens | 否（全量工具列表） | 部分 | 手动 |
| **上下文效率** | 按需激活/停用工具 | 所有工具始终在提示词中 | 有限支持 | 视实现而定 |
| **前端** | Vue 3 + TypeScript（内置） | 无（LangServe） | React | 自己构建 |
| **Trace 追踪** | SQLite + 瀑布图 | LangSmith（付费） | 内置 | 自己构建 |
| **记忆系统** | 三层文件记忆（会话/长期/归档） | 有限 | 有限 | 自己构建 |
| **Hook 系统** | 子进程 Hook，6 个生命周期事件 | Callbacks | 有限 | 自己构建 |
| **供应商锁定** | 无——Git 原生 | LangChain 生态 | Dify 平台 | 无 |
| **自托管** | 是，单进程 | 是 | 是（Docker） | 是 |
| **开源协议** | MIT | MIT | Apache 2 | 不适用 |

### 何时选择 ARF

- 你需要一个能够**自我演进**的智能体——从对话中创建工具和技能
- 你重视 **Git 原生的工作流**——配置即代码，而非点击式操作
- 你需要**透明的、基于文件的状态**——没有黑盒数据库
- 你需要**渐进式能力披露**——上下文高效的智能体，不污染每次提示
- 你偏好**约定大于配置**——可预测的路径，最少的样板代码

### 何时选择其他方案

- 你需要**可视化工作流构建器** → Dify 或 n8n
- 你需要带认证、限流、计费的**生产级多租户** → Dify 或基于 LangChain 自建
- 你在构建**一次性原型**，需要最大的库生态 → LangChain
- 你需要**细粒度控制**且有专门团队 → 裸 FastAPI + SDK

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
│                         ▼                                  │
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

### 数据流

1. **用户消息** 通过 WebSocket 或 REST API 到达
2. **SessionManager** 拼装系统提示词（工作区 → 记忆 → 身份 → 资源清单 → 语言）
3. **压缩节点** 检查上下文用量 —— 若超过 75% 窗口则压缩旧轮次为结构化摘要
4. **分类器**（可选）分析复杂度 → 路由到 `quick_thinking` / `deep_thinking`
5. **LangGraph 引擎** 执行智能体循环：compact → 调用模型 → 解析响应 → 执行工具或直接回复
6. **Hook 运行器** 在生命周期事件触发子进程（PreModelCall、PostToolUse、SessionEnd 等）
7. **Trace 追踪** 将每一步记录到 SQLite，用于可观测性分析
8. **SSE 流** 将 token、工具调用和用量数据实时推送到前端

### 记忆架构

| 层级 | 存储 | 触发时机 | 用途 |
|------|------|----------|------|
| **会话记忆** | `memory/session.md` | 每次请求 | 当前会话上下文，注入系统提示词 |
| **长期记忆** | `memory/long_term.md` | SessionEnd 自动提取 | 用户画像、偏好、事实——跨会话持久化 |
| **会话归档** | `memory/sessions/*.json` | SessionEnd 自动归档 | 结构化会话数据，含 trace 和用量统计 |

记忆提取和压缩全自动：`memory_extractor` Hook 在每次会话结束后运行；`memory_compress` 技能在长期记忆超过 700 KB（可配置）时自动触发。每次长期记忆写入前自动创建备份。

### Hook 系统

Hook 是在 6 个生命周期事件上触发的子进程脚本，通过退出码契约通信：

| 退出码 | 含义 |
|--------|------|
| `0` | 继续（stdout 可包含 JSON 数据） |
| `1` | 阻止当前动作（stderr = 原因） |
| `2` | 注入消息（stderr = 消息文本） |

| 事件 | 触发时机 | 内置 Hook |
|------|----------|-----------|
| `SessionStart` | 新会话开始 | `system_log` |
| `PreModelCall` | 每次 API 调用前 | `system_log` |
| `PostModelCall` | 每次 API 响应后 | `system_log` |
| `PreToolUse` | 工具执行前 | `system_log` |
| `PostToolUse` | 工具执行后 | `system_log` |
| `SessionEnd` | 会话终止 | `session_archiver`、`memory_extractor`、`system_log` |

Hook 通过线程池并行执行，每个 Hook 有独立的超时时间。配置定义在 `.hooks.json` 中，可通过 `manage_hooks` 工具在运行时管理。上下文通过环境变量（小负载）和 stdin JSON（大负载，如对话历史）传递给 Hook 进程。

<br/>

## 系统资源——实现状态

系统资源随框架发布在 `src/arf/resources/system/` 下，作为默认实现和用户自定义模板（通过 `arf clone` 使用）。每种资源遵循统一约定：以资源名称命名的目录，包含 YAML 定义，工具另附 `function.py`。

### Models（共 9 种）

模型定义 API 端点、凭证和推理参数。每个模型是 `models/<name>/` 下的一个目录，包含 `config_default.yaml`（用户凭证可选含 `config.yaml`）。

| 模型 | 类型 | 状态 | 说明 |
|------|------|------|------|
| `deep_thinking` | 推理 | ✅ 已实现 | 最大深度推理，适合复杂任务（架构设计、重构、创意工作） |
| `quick_thinking` | 推理 | ✅ 已实现 | 均衡推理，适合中等任务（代码生成、调试、多步骤） |
| `quick_no_thinking` | 推理 | ✅ 已实现 | 保留给后台任务（压缩、摘要、标题生成） |
| `embedding` | 多模态 | 🚧 仅配置 | 文本嵌入向量生成 |
| `rerank` | 多模态 | 🚧 仅配置 | 搜索结果重排序 |
| `vision` | 多模态 | 🚧 仅配置 | 图像理解与分析 |
| `vlm` | 多模态 | 🚧 仅配置 | 视觉语言模型，多模态推理 |
| `tts` | 多模态 | 🚧 仅配置 | 文本转语音合成 |
| `stt` | 多模态 | 🚧 仅配置 | 语音转文本转录 |

三种推理模型支撑分类器驱动的路由系统：任务被分类为 simple/medium/complex，路由到对应的模型层级。目标模型类型不可用时，系统自动降级到次优可用层级。

### Tools（共 16 个）

工具是可调用的能力。每个工具是 `tools/<name>/` 下的一个目录，包含 `tool.yaml`（JSON Schema 参数定义）、`config_default.yaml`（元数据），可选含 `function.py`（实现）。

**内核工具（始终激活，约 800 tokens）：**

| 工具 | 状态 | 说明 |
|------|------|------|
| `file_reader` | ✅ 已实现 | 读取文件内容，支持行范围 |
| `file_writer` | ✅ 已实现 | 写入或覆盖文件内容 |
| `file_deleter` | ✅ 已实现 | 删除文件，需确认 |
| `resource_loader` | ✅ 已实现 | 按需激活和停用工具 |
| `memory_store` | ✅ 已实现 | 长期记忆读写，自动备份轮转 |
| `model_manager` | ✅ 已实现 | 模型增删改查、连接测试、激活切换 |
| `model_switch` | ✅ 已实现 | 运行时三级模型热切换，自动降级 |
| `resource_registrar` | ✅ 已实现 | 查询资源配置状态和依赖关系 |
| `manage_hooks` | ✅ 已实现 | 运行时查看、启用、禁用、添加、移除 Hook 定义 |

**可发现工具（通过 `resource_loader` 按需加载）：**

| 工具 | 状态 | 说明 |
|------|------|------|
| `web_fetch` | ✅ 已实现 | 获取并处理网页内容 |
| `web_search` | 🚧 仅配置 | 网络搜索集成（端点已预留） |
| `image_understanding` | 🚧 仅配置 | 图像分析与描述（端点已预留） |
| `ocr` | 🚧 仅配置 | 光学字符识别（端点已预留） |
| `speech_output` | 🚧 仅配置 | 文本转语音输出（端点已预留） |
| `speech_understanding` | 🚧 仅配置 | 语音转文本输入（端点已预留） |
| `video_understanding` | 🚧 仅配置 | 视频内容分析（端点已预留） |

### Skills（共 14 个）

技能是可复用的提示词模板，为特定工作流编排工具。每个技能是 `skills/<name>/` 下的一个目录，包含 `skill.yaml`（提示词模板 + 工具引用），可选含 `config_default.yaml`（元数据）。

**记忆类技能：**

| 技能 | 状态 | 说明 |
|------|------|------|
| `memory_extract` | ✅ 已实现 | 从对话历史中提取长期记忆 |
| `memory_compress` | ✅ 已实现 | 压缩长期记忆（超过 700 KB 阈值时触发） |
| `memory_management` | ✅ 已实现 | 检查、搜索和管理记忆文件 |

**模型类技能：**

| 技能 | 状态 | 说明 |
|------|------|------|
| `model_switch` | ✅ 已实现 | 根据任务需求运行时切换模型层级 |
| `model_manager` | ✅ 已实现 | 模型全生命周期管理（增删改查、测试、配置） |
| `model_configurator` | ✅ 已实现 | 交互式逐步模型配置向导 |

**工具类技能：**

| 技能 | 状态 | 说明 |
|------|------|------|
| `tool_generator` | ✅ 已实现 | 从对话上下文生成新工具 |
| `tool_manager` | ✅ 已实现 | 管理工具生命周期（激活、停用、检查） |
| `validate_tool` | ✅ 已实现 | 验证工具 YAML Schema 和 function.py 正确性 |

**技能类技能：**

| 技能 | 状态 | 说明 |
|------|------|------|
| `skill_generator` | ✅ 已实现 | 从对话上下文生成新技能 |
| `skill_manager` | ✅ 已实现 | 管理技能生命周期和依赖关系 |

**基础设施类技能：**

| 技能 | 状态 | 说明 |
|------|------|------|
| `resource_scaffold` | ✅ 已实现 | 为新资源搭建规范的目录结构 |
| `error_handler` | ✅ 已实现 | 结构化错误恢复，含重试和降级流程 |
| `db_operator` | ✅ 已实现 | SQLite 数据库查询、检查和 Schema 探索 |
| `rag_operator` | 🚧 仅配置 | RAG（检索增强生成）操作（端点已预留） |

### Hooks（4 个内置）

Hook 是会话生命周期事件中触发的子进程脚本。每个 Hook 是 `src/arf/hooks/` 下的独立 Python 模块。

| Hook | 事件 | 说明 |
|------|------|------|
| `system_log` | 全部 6 个事件 | 所有生命周期事件的结构化 JSON 日志，写入 `memory/hook_events.log` |
| `session_archiver` | SessionEnd | 将已完成的会话含完整 trace 和用量数据归档到 `memory/sessions/*.json` |
| `memory_extractor` | SessionEnd | 从对话中提取关键事实、偏好和决策，写入 `long_term.md` |
| `title_generator` | SessionStart | 基于首条用户消息生成描述性会话标题（通过 API 调用） |

Hook 定义在 `.hooks.json` 中，可通过 `manage_hooks` 内核工具或 REST API 在运行时管理。用户可通过编写脚本并注册到配置中来添加自定义 Hook。

### 服务器与基础设施（全部已实现）

| 组件 | 说明 |
|------|------|
| **FastAPI 服务器** | REST API（`/api/*`）+ WebSocket（`/ws`）+ SSE 流式传输 |
| **SQLite 追踪** | 6 表可观测性数据库，瀑布图可视化 |
| **会话管理器** | CRUD、归档、空闲锁定（可配置超时）、标题生成 |
| **热重载** | 基于 `watchfiles` 的文件监听，覆盖资源、Hook 和 Agent 配置 |
| **Hook 引擎** | 子进程执行器，支持并行运行、超时和退出码契约 |
| **Docker** | 多阶段 Dockerfile + docker-compose，面向生产部署 |
| **CI/CD** | GitHub Actions + Gitee CI 流水线 |

### 前端

| | 详情 |
|---|---|
| **技术栈** | Vue 3 + TypeScript + Vite |
| **规模** | 6400+ 行：8 视图、13 组件、8 组合式函数、3 Pinia Store、Vue Router |
| **视图** | WelcomePage、ChatLayout、ConfigPage、ResourceDetailView、ResourceStatsView、TraceView、UsagePage |
| **开发** | `npm run dev` 带 HMR，端口 5173，API 代理至后端 |
| **生产** | `npm run build` → `server/static/`，由 FastAPI 直接托管 |

<br/>

## TODO

### 短期
| **P0** | 上下文压缩 —— 超 75% 上下文窗口时自动压缩旧轮次 | ✅ 已完成 |
| **P0** | 渐进式工具结果披露 —— 长输出存盘、上下文仅显示摘要 | ✅ 已完成 |

| 优先级 | 项目 | 状态 |
|--------|------|------|
| **P0** | SandBox 运行时——工具执行的安全隔离（当前为进程内执行） | 🔴 规划中 |
| **P1** | `arf chat`——交互式 CLI 对话，含完整 Agent 循环 | 🟡 骨架 |
| **P1** | `arf run`——无头脚本/批量执行模式 | 🟡 骨架 |
| **P1** | `web_search` 工具——网络搜索集成，可配置后端 | 🟡 仅配置 |

### 中期

| 优先级 | 项目 | 状态 |
|--------|------|------|
| **P2** | 多模态工具实现：`image_understanding`、`ocr`、`speech_output`、`speech_understanding`、`video_understanding` | 🟡 仅配置 |
| **P2** | 多模态模型集成：`vision`、`vlm`、`tts`、`stt`、`embedding`、`rerank` | 🟡 仅配置 |
| **P2** | `rag_operator` 技能——完整 RAG 流水线实现 | 🟡 仅配置 |


### 长期

| 优先级 | 项目 | 状态 |
|--------|------|------|
| **P3** | 工具审批流程——敏感工具调用需用户介入确认 | 🔴 规划中 |
| **P3** | Runtime 权限控制模块 | 🔴 规划中 |
| **P3** | 插件/扩展系统——可通过 pip 安装的第三方资源包 | 🔴 规划中 |
| **P3** | MCP（Model Context Protocol）支持——将外部 MCP 服务器作为工具接入 | 🔴 规划中 |

<br/>

## 设计决策

ARF 是有明确取舍的。以下选择皆是刻意为之。

**约定大于配置。** 路径是可预测的，不可配置的。`models/<name>/config.yaml` 永远有效。没有路径别名，没有 `$MODEL_DIR` 环境变量。

**每种功能一种默认实现。** 一个 web fetch、一个 memory store、一个 model adapter。降低维护成本，避免选择困难，保持系统提示词整洁。

**四种实体类型。** model / skill / tool / hook。如果某个特性需要第五种，它可能不属于框架层。

**不做多供应商抽象层。** ARF 使用 OpenAI 兼容 API。配置好 `base_url` 直接用——无需供应商专用封装、无需适配器模式、无需插件注册表。

**不做云 SaaS 服务。** 自托管是默认设计。无托管服务、无遥测、无账户系统。



**子进程 Hook，而非进程内回调。** Hook 作为独立进程运行，有各自独立的超时、环境和故障域。崩溃的 Hook 不会拖垮 Agent。退出码契约（0/1/2）是语言无关的——你可以用 Python、bash 或任何可执行文件编写 Hook。

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

**依赖：** uvicorn · websockets · openai · PyYAML · watchfiles · jinja2 · cryptography · python-multipart · langchain-core

<br/>

---

<p align="center">
  <sub>MIT — 详见 <a href="./LICENSE">LICENSE</a></sub>
  <br/>
  <sub>基于 LangGraph、FastAPI 和 Vue 3 构建</sub>
  <br/>
  <sub><a href="https://gitee.com/dalaydata/open_deepseek_arf">Gitee</a> &nbsp;·&nbsp; <a href="https://github.com/Wang-hubber/open_deepseek_arf">GitHub</a></sub>
</p>
