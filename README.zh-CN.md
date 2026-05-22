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

<h3 align="center">单用户、自托管、会进化的双智能体助手。</h3>
<p align="center">本地优先 · 基于文件系统 · 约定大于配置 · 渐进式披露 · 全程可追溯</p>

<br/>

## 设计理念

ARF 是一个**单用户自托管的双智能体框架**。它运行在你的机器上，一切皆为文件，每一步都有迹可循。

### 本地优先，基于文件系统

工作区就是一个目录。模型、工具、技能、记忆、会话归档——全是文件。没有云服务、没有托管数据库、没有遥测。Git 原生：整个智能体的配置和历史都可以版本管理。

```
my_workspace/
├── arf_agent.yaml          # 智能体配置
├── models/                 # 模型定义（API 端点、凭证）
├── tools/                  # 自定义工具
├── skills/                 # 可复用的提示词 + 工具编排模板
├── memory/
│   ├── session.md          # 短期上下文
│   ├── long_term.md        # 持久化的用户画像和重要事实
│   └── sessions/           # 已归档会话，含完整 trace
└── .hooks.json             # 生命周期 Hook 定义
```

### 约定大于配置

四种实体类型统御整个框架：**model**、**tool**、**skill**、**hook**。每种实体遵循可预期的目录约定。框架自动发现，无需手动注册。没有装饰器，没有基类，没有 `__init__.py`。一个工具就是一个包含两个文件的目录——`tool.yaml`（Schema）和 `function.py`（实现）。仅此而已。

### 渐进式披露

智能体不会把全部能力塞进每一次 API 调用。9 个内核工具（约 800 tokens）始终激活，其余按需通过 `resource_loader` 加载、用完即停。长工具结果存盘，上下文只保留摘要。智能体只为其实际使用的能力付费。

### 可追踪可回溯

每一次对话、每一次模型调用、每一次工具执行、每一次 Hook 触发——全部记录。Trace 系统不是附加功能，而是一等设计特性和核心亮点。

- **6 表 SQLite 追踪数据库** 覆盖完整生命周期：会话始终、模型调用（tokens、延迟、输入输出片段）、工具执行（入参出参）、Hook 运行（退出码、stdout/stderr）、提示词快照、图节点状态变迁
- **瀑布图可视化** 在前端将每一轮对话渲染为按时间比例排列的 classify → compact → call_model → execute_tools → respond 级联块
- **会话归档** 将完整对话历史连同 trace 和用量统计持久化为 JSON 文件，可 grep、可迁移

Trace 回答那些真正重要的问题：*智能体做了什么？为什么选择了这个模型？每一步花了多长时间？消耗了多少 tokens？*

### 单用户自持的双智能体

ARF 呈现为双智能体体系：**User Agent** 直接处理你的任务；**System Agent** 负责内部操作（记忆提取、标题生成、错误恢复）。它们共享同一工作区但独立运作。用户看到的是一个连贯的助手——双智能体架构是实现细节，提升可靠性而不增加认知负担。

<br/>

## 愿景

ARF 不止是一个聊天助手。它是**自成长智能体的孵化场**。每一个在 ARF 框架下诞生的 Agent，都是在任务场景内自成长的智能个体。它们共享同样的底层能力——**资源的感知与利用**——却因各自深耕的领域，演化出不同的任务技巧和行为侧重。

每个任务场景都是一个闭环：

```
感知 → 思考 → 行动 → 验证 → 感知 → ...
```

Agent 在这个回路中持续迭代，收敛于各自领域内的**局部最优解**。当一群这样的 Agent——每个都是自身领域的专家——协作与博弈时，系统整体开始涌现出超越个体的能力。**涌现发生。泛化随之而来。**

这就是 ARF 通向 AGI 的道路：不是一个庞大的超级模型，而是一个**自成长智能体的社会**，从专业能力逐步垒叠向通用智能。

<br/>

## 框架 vs. MVP

ARF 是一个**框架**——一套约定、一个资源系统、一个图引擎、一层可观测基础设施。当前发布的是基于框架构建的 **MVP 应用**：一个带 Vue 3 前端的单用户对话助手。

| 层级 | 是什么 | 举例 |
|------|--------|------|
| **框架** | 约定、引擎、资源系统、Trace 基础设施 | `ResourceRegistry`、`GraphEngine`、双源资源加载、Hook 退出码契约、SQLite trace schema、提示词管线 |
| **MVP 应用** | 基于框架构建的具体对话应用 | Vue 3 前端、会话侧边栏、多模型路由、`session_archiver` Hook、`title_generator` Hook |
| **用户工作区** | 你的模型、工具、技能、记忆——你在框架之上的构建 | 模型配置、自定义工具、`long_term.md`、工作区 YAML |

MVP 展示了框架的能力。你完全可以构建完全不同的东西——CLI 工具、无头自动化智能体、代码审查机器人——用同样的框架约定，完全不需要前端。

**当前 MVP 的多会话侧边栏是 MVP 的实现细节，而非框架的强制要求。** 框架本身不对会话管理做任何限定——`SessionManager` 只是一个可选的构建块。

<br/>

## 快速开始

需要 Python ≥ 3.10 和 Node.js ≥ 18。

```bash
git clone git@gitee.com:dalaydata/open_deepseek_arf.git
cd open_deepseek_arf
pip install -e .
cd frontend && npm install && cd ..

arf init my_workspace
arf start --workspace my_workspace
```

浏览器打开 **http://localhost:5173**——输入 API 密钥即可开始对话。

### CLI 命令参考

| 命令 | 用途 |
|------|------|
| `arf init <name>` | 创建新工作区 |
| `arf start` | 启动后端 + 前端 |
| `arf web` | 仅启动后端（FastAPI + WebSocket + SSE） |
| `arf stop` | 停止运行中的进程 |
| `arf reload` | 停止 + 重启 |
| `arf list [tools\|skills\|models]` | 列出已注册资源 |
| `arf validate` | 检查工作区资源完整性 |
| `arf clone <type> <name>` | 将系统资源克隆到工作区以便定制 |

### 配置

| 环境变量 | 默认值 | 用途 |
|----------|--------|------|
| `ARF_SERVE_STATIC` | `1` | 后端托管前端静态文件 |
| `ARF_DB_NAME` | `arf.db` | SQLite trace 数据库文件名 |
| `ARF_CORS_ORIGINS` | `localhost:5173` | CORS 允许来源 |
| `ARF_IDLE_TIMEOUT` | `600` | 会话空闲超时（秒） |
| `ARF_CLASSIFIER_ENABLED` | `0` | 自动模型路由（设为 `1` 激活） |

模型配置位于 `models/<name>/config.yaml`——`base_url`、`api_key`、`model_name`、`temperature` 等。

<br/>

## 架构

ARF 将**做什么**（控制层）与**怎么做**（执行层）解耦。文件系统是它们之间的桥梁。

```
┌─────────────────────────────────────────────────────┐
│                控制层（声明式，What）                    │
│  models/    tools/    skills/    arf_agent.yaml      │
│  （YAML 定义——人类可读，Git 可追踪）                     │
└──────────────────────┬──────────────────────────────┘
                       │  文件系统自动发现
┌──────────────────────┴──────────────────────────────┐
│                执行层（LangGraph，How）                 │
│  ResourceRegistry → UserAgent/SysAgent → GraphEngine │
│  classify → compact → call_model → execute_tools     │
│  FastAPI + WebSocket + SSE → Vue 3 前端              │
└─────────────────────────────────────────────────────┘
```

**双源资源体系：** 系统资源随框架发布（只读，18 工具 + 18 技能）。用户资源在工作区中（可读写，按名称覆盖系统版本）。升级永不会覆盖你的定制。

**自演进：** 智能体可在运行时创建、编写、注册新工具和技能。一次对话即可产生永久能力。

**Hook 系统：** 基于子进程的生命周期 Hook（6 个事件），退出码契约（0 = 继续，1 = 阻断，2 = 注入）。崩溃的 Hook 不会拖垮智能体。

**热重载：** 文件监听器检测资源变更，注册表无重启更新。

<br/>

## Trace 系统

Trace 是 ARF 可观测性的骨干。每个生命周期事件都被捕获。

| Trace 事件 | 记录内容 |
|------------|----------|
| `lifecycle.session_start` | 工作区、传输方式、时间戳 |
| `lifecycle.session_end` | 消息数、持续时长、触发方式 |
| `lifecycle.prompt_snapshot` | 提示词哈希、长度、活跃工具列表 |
| `lifecycle.hook_execution` | Hook 名称、事件、退出码、stdout/stderr |
| `lifecycle.init` | 注册表加载计数、智能体构建参数 |
| `lifecycle.config` | 智能体重建触发原因 |

**图节点 Trace**（每轮、每节点）：

| 节点 | 记录内容 |
|------|----------|
| `classify` | 分类结果（`medium`/`complex`）、命中的模型 |
| `call_model` | 模型名称、tokens（prompt/completion/total）、输入输出片段、耗时 |
| `execute_tools` | 工具名称、分类、输入输出片段、耗时 |
| `hook` | Hook 事件类型、退出状态、Hook 名称 |
| `respond` | 回复摘要、截断标记 |

**前端 Trace 查看器**（`/traces`）：瀑布流时间线，将每轮对话渲染为时间比例的块序列，可展开查看 token 计数、模型输入输出片段和工具执行细节。

<br/>

## TODO

框架的下一个里程碑：**单用户无限上下文智能体**——MVP 应用从一个带会话管理的聊天工具演进为无缝的持续对话体验。

| 优先级 | 项目 |
|--------|------|
| **P0** | **无限上下文单会话体验** —— 废除多会话侧边栏、resume/delete 按钮和会话切换 UI。用户永远看不到"会话列表"。对话持续流转；归档和上下文压缩在后台透明完成。这是框架设计哲学的自然呈现：框架提供引擎，应用变得不可见。 |
| **P0** | SandBox 运行时 —— 工具执行的安全隔离 |
| **P1** | `arf chat` —— 交互式 CLI 对话 |
| **P1** | `arf run` —— 无头批量执行模式 |
| **P2** | 插件/扩展系统 —— 第三方资源包 |
| **P2** | MCP（Model Context Protocol）支持 |
| **P3** | 工具审批流程 —— 敏感操作需用户介入确认 |

<br/>

## 参与贡献

详见 [贡献者须知.md](./贡献者须知.md)。

```bash
# 开发环境搭建
git clone git@gitee.com:dalaydata/open_deepseek_arf.git
cd open_deepseek_arf
pip install -e .
cd frontend && npm install && npm run dev
```

**核心技术栈：** Python 3.10+ · FastAPI · LangGraph · Vue 3 · TypeScript · Vite · SQLite

<br/>

---

<p align="center">
  <sub>MIT — 详见 <a href="./LICENSE">LICENSE</a></sub>
  <br/>
  <sub>基于 LangGraph、FastAPI 和 Vue 3 构建</sub>
  <br/>
  <sub><a href="https://gitee.com/dalaydata/open_deepseek_arf">Gitee</a> &nbsp;·&nbsp; <a href="https://github.com/Wang-hubber/open_deepseek_arf">GitHub</a></sub>
</p>
