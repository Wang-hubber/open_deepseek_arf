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

这不是一个庞大的超级模型，而是一个**自成长智能体的社会**——从专业能力逐步垒叠，通向通用智能。

<br/>

## Harness 即内核——问题域架构

> **Model + Harness = Agent。CPU + Kernel = Computer。**
>
> Harness 是 AI 原生计算的内核态。Token 是指令。Agent 会话是进程。工具调用是系统调用。当前智能体工程遇到的每一项难题，在操作系统经典文献中都能找到成熟的解题框架。我们适配，而非发明。

下表按问题域组织整个 Harness 工程空间，每个问题映射到其 OS 对应方案、ARF 的当前实现，以及可预见的演进路径。

| 要解决的问题 | OS 经典方案 | 实际状态（最小可行方案） | 演进方向 |
|-------------|------------|----------------------|----------|
| **上下文窗口耗尽（OOM）** | 虚拟内存 + 页交换：冷页换出到磁盘，按需换入 | 75% 上下文水位触发滑动窗口压缩，旧轮次汇总为 `context_summary`，后续压缩在前次摘要之上累积。会话结束时写入完整归档，但运行时不从归档换入历史上下文。 | 细粒度"缺页"调入：按最小语义单元（函数签名、关键决策）精确检索注入，而非整块解压；LRU 相关性淘汰 |
| **长程记忆与状态持久化** | 文件系统：数据以文件形式持久存储，按路径索引 | 三层文件记忆：`session.md`（短期上下文）、`long_term.md`（持久画像）、`sessions/*.json`（完整归档）。`memory_extractor` Hook 在会话结束时自动提取事实，但合并到长期记忆需 Agent 复核。 | 语义文件系统：Agent 自动维护知识图谱式索引；记忆可按语义路径访问（"用户对 Python 类型标注的偏好"），而非字面路径 |
| **快慢推理调度** | 多级缓存：L1 快而小，L2 慢而大；CPU 自动判断命中 | 二级分类器：中等任务用 `quick_thinking`，复杂任务用 `deep_thinking`；`quick_no_thinking` 用于后台工作（压缩、标题生成）。分类器路径支持降级链，但直接模型调用无通用回退。 | 硬件感知的动态推理缓存：任务复杂度 × 延迟预算 × KV Cache 占用自动选择 L1/L2/L3 推理；同会话内无感切换模型，支持上下文"进程迁移" |
| **模型计算资源分配** | big.LITTLE 异构调度：轻任务用小核，重任务用大核 | 可配置 `max_turns`，在图路由中强制执行。模型按优先级解析（`quick_thinking` → `deep_thinking` → 任意已配置）。工具输出超 2000 字符时截断，完整结果存盘，上下文仅保留摘要。 | 自适应模型"核"调度器：实时监测任务难度；同会话内无感切换模型；弱模型群体协作可超越单强模型 |
| **外部能力调用与工具沙箱** | 系统调用 + 协处理器 + 进程隔离（chroot、容器）：CPU 通过中断调用加速器，每次系统调用经内核权限检查 | 工具调用：每工具 `tool.yaml`（JSON Schema）+ `function.py`，通过双源注册表加载。Hook 退出码契约（0=继续，1=阻断，2=注入）。路径沙箱防工作区逃逸。工具在 Agent 进程内执行，无 per-invocation 隔离沙箱，无 deny→ask→allow 权限管道。MCP 协议在路线图中。 | 逐工具 deny→ask→allow 权限门控。每次调用在独立沙箱中执行，带资源配额（CPU、内存、网络）。高频工具的硬件化加速与 DMA 式异步调度 |
| **任务并行与并发** | 超标量/乱序执行 + 多核：操作数无关的指令同时发射 | 顺序两阶段调度：UserAgent → SysAgent 交接执行特权操作。Hook 在线程池中以独立子进程并行执行。尚无多 Agent 并发执行。 | 多 Agent 流水线优化：自动分析任务依赖图；Worktree 隔离独立子任务；动态并行度调节；Agent 间流水线深度与吞吐优化 |
| **外部中断与用户干预** | 硬件中断：保存现场 → 执行 ISR → 恢复现场 | 用户可中止流式响应（`AbortController`）。Hook 通过退出码 2 注入消息：引擎将 `[Hook message]` 插入对话并继续循环。无流中用户注入，无会话空闲超时。 | Agent 信号标准化：定义通用中断向量（暂停/重定向/撤销/注入）；支持键盘、语音等多模态实时打断 |
| **资源死锁与竞争** | 资源分配图 + 死锁检测/规避：锁层级、超时回滚 | 顺序 Agent 执行从设计上避免并发。文件编辑通过 `file_writer` 串行化，路径沙箱防遍历。无显式死锁检测，无多 Agent 共享资源协议。 | Agent 间并发控制协议：引入分布式锁管理器（DLM）、乐观并发控制；自动检测并打破 Agent 间的循环等待 |
| **身份、权限与安全边界** | 保护环（Ring 0–3）+ 访问控制列表：内核态拥有最高权限，用户态受限 | 双源隔离：系统资源只读（Ring 0），用户工作区可读写（Ring 3）。路径沙箱防工作区逃逸。UserAgent 限制写入 `tools/`、`skills/`、`models/` 目录。无用户介入的敏感操作审批流程。 | 最小权限自动推导：Agent 仅被授予完成任务所需的最小工具集与数据视图；权限随任务阶段动态伸缩；高风险操作需人工审批 |

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
