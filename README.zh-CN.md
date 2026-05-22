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

<h3 align="center">模型与用户之间的一切——资源系统、智能体引擎、可观测基础设施——<br/>封装在一个自托管的目录中。</h3>
<p align="center">本地优先 · 基于文件系统 · 约定大于配置 · 全程可追溯 · 自我演进</p>

<br/>

## 设计理念

模型本身不是智能体。它需要工具、记忆、技能——需要一个运行时来编排推理、行动和验证。ARF 提供这一层。它是一个**智能体框架**，建立在一个核心信念之上：文件系统是智能体资源管理的正确抽象。

### 本地优先，基于文件系统

一切都在一个工作区目录中。模型、工具、技能、记忆、会话归档——全是磁盘上的文件。没有云服务，没有托管数据库，没有遥测。配置是 YAML，版本控制是 Git，历史记录可 grep。

```
my_workspace/
├── arf_agent.yaml          # 智能体配置
├── models/                 # 模型定义（端点、凭证、参数）
├── tools/                  # 自定义工具
├── skills/                 # 可复用的提示词与工具编排模板
├── memory/
│   ├── session.md          # 短期上下文
│   ├── long_term.md        # 持久用户画像与事实
│   └── sessions/           # 已归档会话，含完整 Trace
└── .hooks.json             # 生命周期 Hook 定义
```

### 约定大于配置

四种实体类型——**model**、**tool**、**skill**、**hook**——每种遵循可预期的目录约定。框架自动发现，无需手动注册。没有装饰器，没有基类，没有 `__init__.py`。一个工具就是两个文件：`tool.yaml` 定义 Schema，`function.py` 承载逻辑。这就是全部的接口面。

### 渐进式披露

智能体不会把所有能力塞进每次 API 调用。九个内核工具（约 800 tokens）始终激活，其余按需通过 `resource_loader` 加载、执行、停用。长工具结果存盘，上下文仅保留摘要。智能体只为实际使用的能力付费。这是**上下文工程**的系统性实践——不是补丁，是架构。

### 可追踪可回溯

每一次模型调用、每一次工具执行、每一次 Hook 触发——全部记录。Trace 是一等子系统，不是事后挂载的日志文件。

- **6 表 SQLite 追踪数据库**——会话生命周期、模型调用（tokens、延迟、输入输出片段）、工具 I/O、Hook 退出码、提示词快照、图节点状态变迁
- **瀑布图可视化**——每一轮对话渲染为时间比例级联：classify → compact → call_model → execute_tools → respond
- **会话归档**——完整对话 + Trace + 用量统计，持久化为可移植的 JSON

Trace 回答那些真正重要的问题：*智能体做了什么？为什么选择这个模型？每一步花了多长时间？消耗了多少 tokens？*

### 单用户自持的双智能体

User Agent 处理你的任务。System Agent 负责内部操作——记忆提取、标题生成、错误恢复。独立执行，共享工作区。用户看到一个连贯的助手；双智能体架构是实现细节，提升可靠性而不增加认知负担。

<br/>

## 愿景

ARF 是**自成长智能体的孵化场**。每一个在这个框架下诞生的 Agent，都是在任务场景内自主成长的智能个体。它们共享同一底层能力——**资源的感知与利用**——却因各自深耕的领域，演化出不同的任务技巧和行为侧重。

每个任务场景都运行在一个闭环中：

```
感知 → 思考 → 行动 → 验证 → 感知 → ...
```

Agent 在此回路中持续迭代，收敛于各自领域的**局部最优解**。当一群这样的专家协作博弈时，系统整体涌现出超越个体的能力。**涌现发生。泛化随之而来。**

这就是我们的路径：不是一个庞大的超级模型，而是一个**自成长智能体的社会**——从专业能力逐步垒叠，通向通用智能。ARF 为这个社会的形成提供运行时、资源系统和 Trace 基础设施。

<br/>

## 框架 vs. 应用

ARF 是一个**框架**——一套约定、一个资源系统、一个图引擎、一层可观测基础设施。当前发布的是基于框架构建的**参考应用**：一个带 Vue 3 前端的单用户对话助手。

| 层级 | 范畴 | 举例 |
|------|------|------|
| **框架** | 约定、引擎、资源系统、Trace 基础设施 | `ResourceRegistry`、`GraphEngine`、双源资源加载、Hook 退出码契约、SQLite Trace Schema、提示词管线 |
| **参考应用** | 基于框架构建的具体智能体 | Vue 3 前端、会话侧边栏、模型路由、`session_archiver`、`title_generator` |
| **用户工作区** | 你在框架之上的构建 | 模型配置、自定义工具、`long_term.md`、工作区 YAML |

参考应用展示框架能力。你完全可以构建完全不同的东西——CLI 工具、无头自动化智能体、代码审查机器人——使用同样的约定，完全不需要前端。

**当前参考应用的多会话侧边栏是参考实现的细节**，而非框架约束。框架提供 `SessionManager` 作为构建块，不做进一步限定。

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

浏览器打开 **http://localhost:5173**——输入 API 密钥即可开始。

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
| `ARF_DB_NAME` | `arf.db` | SQLite Trace 数据库 |
| `ARF_CORS_ORIGINS` | `localhost:5173` | CORS 允许来源 |
| `ARF_IDLE_TIMEOUT` | `600` | 会话空闲超时（秒） |
| `ARF_CLASSIFIER_ENABLED` | `0` | 自动模型路由（设为 `1` 激活） |

模型配置：`models/<name>/config.yaml`——`base_url`、`api_key`、`model_name`、`temperature` 等。

<br/>

## 架构

ARF 将**做什么**（控制）与**怎么做**（执行）解耦。文件系统是两者之间的桥梁。

```
┌─────────────────────────────────────────────────────┐
│              控制层（声明式，What）                      │
│  models/    tools/    skills/    arf_agent.yaml      │
│  （YAML——人类可读，Git 可追踪）                          │
└──────────────────────┬──────────────────────────────┘
                       │  文件系统自动发现
┌──────────────────────┴──────────────────────────────┐
│              执行层（LangGraph，How）                   │
│  ResourceRegistry → UserAgent/SysAgent → GraphEngine │
│  classify → compact → call_model → execute_tools     │
│  FastAPI + WebSocket + SSE → Vue 3 前端              │
└─────────────────────────────────────────────────────┘
```

核心工程选择：

- **双源资源体系**——系统资源随框架发布（只读，18 工具 + 18 技能）。用户资源在工作区中（可读写，按名称覆盖系统版本）。框架升级永不会覆盖你的定制。
- **自演进**——智能体可在运行时创建、编写、注册新工具和技能。一次对话即可产生永久能力。
- **子进程 Hook**——六个生命周期事件，退出码契约（0 = 继续，1 = 阻断，2 = 注入）。Hook 作为独立进程运行，拥有独立的超时和故障域。崩溃的 Hook 不会拖垮智能体。
- **热重载**——文件监听器检测资源变更，注册表无重启更新。

<br/>

## Trace 系统

| Trace 事件 | 记录内容 |
|------------|----------|
| `lifecycle.session_start` | 工作区、传输方式、时间戳 |
| `lifecycle.session_end` | 消息数、持续时长、触发方式 |
| `lifecycle.prompt_snapshot` | 提示词哈希、长度、活跃工具列表 |
| `lifecycle.hook_execution` | Hook 名称、事件、退出码、stdout/stderr |
| `lifecycle.init` | 注册表计数、智能体构建参数 |
| `lifecycle.config` | 智能体重建触发原因 |

每轮、每节点的 Trace：

| 节点 | 记录内容 |
|------|----------|
| `classify` | 分类结果（`medium`/`complex`）、命中的模型 |
| `call_model` | 模型、tokens（prompt/completion/total）、输入输出片段、延迟 |
| `execute_tools` | 工具名称、分类、输入输出片段、延迟 |
| `hook` | 事件类型、退出状态、Hook 名称 |
| `respond` | 回复摘要、截断标记 |

瀑布图查看器（`/traces`）：每轮对话渲染为时间比例的块序列，可展开查看 token 计数、输入输出片段和工具执行细节。

<br/>

## 路线图

| 优先级 | 项目 |
|--------|------|
| **P0** | **无限上下文单会话体验**——废除多会话侧边栏。用户永远看不到会话列表。对话持续流转，归档和压缩在后台透明完成。 |
| **P0** | 工具执行 SandBox 运行时 |
| **P1** | `arf chat`——交互式 CLI |
| **P1** | `arf run`——无头批量执行 |
| **P2** | 插件/扩展系统 |
| **P2** | MCP（Model Context Protocol）支持 |
| **P3** | 工具审批流程——敏感操作需人工介入 |

<br/>

## 参与贡献

详见 [贡献者须知.md](./贡献者须知.md)。

```bash
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
