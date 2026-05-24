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

<h3 align="center">Harness = 操作系统内核。Model = CPU。Agent = 计算机。</h3>
<p align="center">本地优先 · 基于文件系统 · 约定大于配置 · 全程可追溯 · 自我演进</p>

<br/>

> **本项目由 DeepSeek V4 Pro 与 Claude Code 协作完成。** 作者仅提供设计思路与代码审核，未手写任何一行代码。如果你还在怀疑 Agent Harness 是否真的有效——这个项目本身就是回答。

<br/>

## 设计理念

模型是裸算力——强大，但不是一台可用的计算机。它需要内存管理、进程调度、中断响应、文件系统和安全边界。ARF 提供这一切。它是一个**智能体框架**，建立在一个核心架构洞察之上：**Harness 层就是 AI 原生计算的内核态**。

操作系统的经典抽象——虚拟内存、缓存层次、系统调用、保护环——直接映射到每个智能体工程师日常面对的问题。ARF 不发明新抽象，而是将经过数十年验证的 OS 模式适配到 Token 时代。

<br/>

## 愿景

ARF 是**自成长智能体的孵化场**。每一个在这个框架下诞生的 Agent，都是在任务场景内自主成长的智能个体。它们共享同一底层能力——**资源的感知与利用**——却因各自深耕的领域，演化出不同的任务技巧和行为侧重。

每个任务场景都运行在一个闭环中：

```
感知 → 思考 → 行动 → 验证 → 感知 → ...
```

Agent 在此回路中持续迭代，收敛于各自领域的**局部最优解**。当一群这样的专家协作博弈时，系统整体涌现出超越个体的能力。**涌现发生。泛化随之而来。**

这不是一个庞大的超级模型，而是一个**自成长智能体的社会**——从专业能力逐步垒叠，通向通用智能。

<br/>

## Harness 即内核——问题域架构

> **Model + Harness = Agent。CPU + Kernel = Computer。**
>
> Harness 是 AI 原生计算的内核态。Token 是指令。Agent 会话是进程。工具调用是系统调用。当前智能体工程遇到的每一项难题，在操作系统经典文献中都能找到成熟的解题框架。我们适配，而非发明。

下表按问题域组织整个 Harness 工程空间，每个问题映射到其 OS 对应方案、ARF 的当前实现，以及可预见的演进路径。

| 要解决的问题 | OS 经典方案 | 实际状态（最小可行方案） | 演进方向 |
|-------------|------------|----------------------|----------|
| **内存管理（OOM + 持久化）** | 虚拟内存 + 文件系统：页交换，持久存储 | Token 感知的滑动窗口压缩（75% 阈值）含 LLM 摘要。长工具输出落盘，上下文保留摘要。事实/偏好/决策自动抽取去重，语义检索注入。 [压缩 →](docs/compaction.md) [记忆 →](docs/memory-pipeline.md) | 细粒度缺页调入：语义单元检索；Agent 维护知识图谱索引，支持路径 + 语义双模式访问 |
| **模型路由与资源分配** | 多级缓存 (L1/L2) + big.LITTLE 异构调度 | 二级 LLM 分类器按复杂度路由用户任务（中等→quick，复杂→deep）。专用模型处理框架后台工作（记忆抽取、分类）。每 turn 动态切换。 [设计文档 →](docs/model-routing.md) | 硬件感知调度：任务复杂度 × 延迟预算 × KV Cache 占用；同会话无感切换模型 |
| **工具沙箱与安全边界** | 系统调用 + 保护环（Ring 0–3）+ ACL：内核门控每次调用，用户态受限 | 工具调用：`tool.yaml`（JSON Schema）+ `function.py`。`PathCheckToolGuard` 阻断 `..` 和绝对路径。双源隔离：框架资源只读，用户工作区读写。Hook 退出码契约（0=继续，1=阻断，2=注入）。 [设计文档 →](docs/tool-sandbox.md) | 逐工具 deny→ask→allow 权限门控。每次调用独立沙箱隔离（CPU、内存、网络配额）。最小权限自动推导。MCP 协议集成。 |
| **并发与死锁预防** | 超标量执行 + 资源分配图：依赖分析，锁层级 | 顺序 Agent 执行避免并发。Skill 声明工具流水线与显式依赖——引擎强制执行顺序。Hook 线程池并行。 [设计文档 →](docs/skill-pipeline.md) | 多 Agent 流水线：自动 DAG 分析；Worktree 隔离；动态并行；分布式锁管理器。 |
| **外部中断与用户干预** | 硬件中断：保存现场 → 执行 ISR → 恢复现场 | `cancel_event` 异步取消 + `POST /api/chat/cancel`。3 快照 undo（状态+文件双回滚），支持 API 和对话内 `undo` 工具。Hook exit-code-2 消息注入。 [设计文档 →](docs/interrupt.md) | 通用中断向量（暂停/重定向）；多模态实时打断；会话空闲超时。 |

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
- **子进程 Hook**——六个生命周期事件，退出码契约（0 = 继续，1 = 阻断，2 = 注入）。Hook 作为独立进程运行，拥有独立的超时和故障域。
- **热重载**——文件监听器检测资源变更，注册表无重启更新。

### 框架 vs. 应用

| 层级 | 范畴 | 举例 |
|------|------|------|
| **框架** | 约定、引擎、资源系统、Trace 基础设施 | `ResourceRegistry`、`GraphEngine`、双源资源加载、Hook 退出码契约、SQLite Trace Schema、提示词管线 |
| **参考应用** | 基于框架构建的具体智能体 | Vue 3 前端、会话侧边栏、模型路由、`session_archiver`、`title_generator` |
| **用户工作区** | 你在框架之上的构建 | 模型配置、自定义工具、`long_term.md`、工作区 YAML |

**当前多会话侧边栏是参考实现的细节**，而非框架约束。框架提供 `SessionManager` 作为构建块，不做进一步限定。

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

| 节点 | 记录内容 |
|------|----------|
| `classify` | 分类结果（`medium`/`complex`）、命中的模型 |
| `call_model` | 模型、tokens、输入输出片段、延迟 |
| `execute_tools` | 工具名称、分类、输入输出片段、延迟 |
| `hook` | 事件类型、退出状态、Hook 名称 |
| `respond` | 回复摘要、截断标记 |

瀑布图查看器（`/traces`）：每轮对话渲染为时间比例的块序列，可展开查看 token 计数、输入输出片段和工具执行细节。

<br/>

## 快速开始

需要 Python ≥ 3.10 和 Node.js ≥ 18。

```bash
git clone git@gitee.com:dalaydata/open_deepseek_arf.git
cd open_deepseek_arf
pip install -e .
cd app/web && npm install && cd ..

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

## 参考应用设计

以下模式是参考应用（`app/arf_default_assistant/`）的实现细节，非框架约束。

### 约定大于配置

四种实体类型——**model**、**tool**、**skill**、**hook**——每种遵循可预期的目录约定。框架自动发现，无需手动注册。没有装饰器，没有基类，没有 `__init__.py`。一个工具就是两个文件：`tool.yaml` 定义 Schema，`function.py` 承载逻辑。这就是全部的接口面。

### 渐进式披露

智能体不会把所有能力塞进每次 API 调用。九个内核工具（约 800 tokens）始终激活，其余按需通过 `resource_loader` 加载、执行、停用。长工具结果存盘，上下文仅保留摘要。智能体只为实际使用的能力付费。这是**上下文工程**的系统性实践——不是补丁，是架构。

### 可追踪可回溯

每一次模型调用、每一次工具执行、每一次 Hook 触发——全部记录。Trace 是一等子系统，不是事后挂载的日志文件。

- **6 表 SQLite 追踪数据库**——会话生命周期、模型调用（tokens、延迟、输入输出片段）、工具 I/O、Hook 退出码、提示词快照、图节点状态变迁
- **瀑布图可视化**——每一轮对话渲染为时间比例级联：classify → compact → call_model → execute_tools → respond
- **会话归档**——完整对话 + Trace + 用量统计，持久化为可移植的 JSON

### 单用户自持的双智能体

User Agent 处理你的任务。System Agent 负责内部操作——记忆提取、标题生成、错误恢复。独立执行，共享工作区。用户看到一个连贯的助手；双智能体架构是实现细节，提升可靠性而不增加认知负担。

<br/>

## 参与贡献

详见 [贡献者须知.md](./贡献者须知.md)。

```bash
git clone git@gitee.com:dalaydata/open_deepseek_arf.git
cd open_deepseek_arf
pip install -e .
cd app/web && npm install && npm run dev
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
