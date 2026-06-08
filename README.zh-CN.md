<p align="center">
  <h1 align="center">ARF — Agent Resources & RunTime FrameWork</h1>
  <p align="center"><em>大脑-脊椎-身体架构的研究脚手架与 Harness MVP</em></p>
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
  <a href="./pyproject.toml"><img src="https://img.shields.io/badge/python-3.11+-5fa04e?style=flat-square&labelColor=161b22&logo=python&logoColor=white" alt="Python 3.11+"/></a>
  <a href="./LICENSE"><img src="https://img.shields.io/badge/license-MIT-8b949e?style=flat-square&labelColor=161b22" alt="license"/></a>
  <a href="https://github.com/Wang-hubber/open_deepseek_arf/stargazers"><img src="https://img.shields.io/github/stars/Wang-hubber/open_deepseek_arf.svg?style=flat-square&color=dbab09&labelColor=161b22&logo=github&logoColor=white" alt="GitHub stars"/></a>
</p>

<br/>

<h3 align="center">Harness = 操作系统内核。Model = CPU。Agent = 计算机。</h3>
<p align="center">模型是大脑。Harness 是脑干、脊椎与身体。</p>
<p align="center">本地优先 · 约定大于配置 · 全程可追溯 · 自我演进</p>

<br/>

> **本项目由 DeepSeek V4 Pro 与 Claude Code 协作完成。** 作者仅提供设计思路与代码审核，未手写任何一行代码。

<br/>

## 研究背景

ARF 是研究论文 **《寻找 Agent 系统的脊椎——大模型与 Harness 的严格分工与协同进化》** 的工程配套项目。

**核心命题**：当前 Agent 系统普遍存在 *Harness 膨胀*——协调层承担了大量本该属于模型的认知功能：RAG 知识注入、系统提示词身份赋予、上下文摘要、外部记忆管理。这种膨胀不是实现缺陷，而是根本性的角色混淆。Harness 本应是**零认知的机械层**，如同生物的脑干、脊椎与身体：编码感知、执行动作、运转硬连线反射。它不应思考。

**ARF 的双重角色**：
- **作为 MVP**：ARF 实现了三层基于规则的简单反射，证明了剥离认知责任后的 Harness 足以支撑完整可用的 Agent。5 个骨架 + 控制平面 + Plugin 体系构成了设计准则的完整、可测试的工程实体。
- **作为研究脚手架**：ARF 为论文中全部五项实验提供统一的实验台架——状态接口稳定性、零认知基准测试、在线 LoRA 记忆、身份边界鲁棒性、内部上下文压缩。

**这个 MVP 证明了什么**：一个基于 Protocol 定义的骨架构建的 Harness，将所有"智能"行为（记忆提取、任务追踪、上下文压缩）实现为可插拔的反射弧而非核心引擎逻辑，能够在严格分离*认知工作*（模型）与*机械工作*（Harness）的前提下，支撑全功能 Agent 运行。

**配套教学项目 — [ARF App](https://gitee.com/dalaydata/arf_app_021)**：7 单元渐进式教程，覆盖从零构建 ARF 应用到生产级 Agent 的完整链路——Hello ARF → 会话管理 → 工具系统 → 工具审批 → Guardrails 安全 → 长期记忆 → Agent 调优。每单元含可运行代码快照。教程同时作为框架的用户验收测试，在真实使用中验证 API 设计的完备性。

<br/>

---

## 阅读指引

**研究者** — 关注架构主张和实验路线：

- [设计理念](#设计理念大脑-脊椎-身体模型) → [三层基于规则的简单反射](#三层基于规则的简单反射) → [控制平面](#控制平面--结构化-state--生命周期) → [第二部分 — 研究路线图](#第二部分--研究路线图)

**框架使用者** — 想基于 ARF 构建应用：

- [5 骨架架构](#harness-即内核5-骨架架构) → [Plugin 体系](#plugin-体系反射弧非认知模块) → 然后移步 [ARF App 教学项目](https://gitee.com/dalaydata/arf_app_021)，7 单元渐进式教程，从零到生产级 Agent

<br/>

---

## 第一部分 — 框架

### 设计理念：大脑-脊椎-身体模型

模型是裸算力——强大，但不是一台可用的计算机。它需要内存管理、进程调度、中断响应、文件系统和安全边界。ARF 提供这一切。但设计理念比 OS 类比更深一层。

**支配每一项架构决策的生物学映射**：

| 生物系统 | Agent 系统 | 职责 | 认知负载 |
|---------|-----------|------|---------|
| **大脑** | 大语言模型 | 条件反射中枢。接收编码后的状态数据包，输出行为指令。通过后训练内化专业知识与身份边界。 | **全认知** — 理解、推理、决策 |
| **脑干 / 脊椎** | Harness 核心（5 骨架 + 控制平面） | 固定感知编码、可靠行动执行、非条件反射。多源信号时间对齐为固定 Schema 的状态数据包。解析函数调用，执行并收集反馈——不做策略判断。 | **零认知** — 格式化、对齐、执行、门控 |
| **身体** | 工具生态 | 模型操作世界的物理/虚拟效应器。文件 I/O、网页抓取、Shell 执行、代码解释。 | **零认知** — 仅机械动作 |

**协同进化律**：猴脑无法操纵人身。Harness 定义的状态 Schema 必须通过配对后训练成为模型的原生感知语言。大脑与身体协同进化，否则两者都无法工作。

操作系统的经典抽象——虚拟内存、缓存层次、系统调用、保护环——直接映射到工程实现。但架构主张是生物学的：**认知工作属于大脑；Harness 是脊髓，不是第二个大脑。**

### 三层基于规则的简单反射

ARF 将 Harness 实现为三层基于规则的简单反射——固定规则、确定性执行、零认知。如同生物的膝跳反射、缩手反射，每层通过固定规则将输入转换为输出，不做语义理解。这三层对应论文第 5 节"理想 Harness 的设计准则"。

| 层级 | 设计准则 | ARF 实现 | 对应骨架 |
|------|---------|---------|---------|
| **L1: 固定感知编码器** | 零认知状态编码。固定周期、固定字段、仅做格式与时间对齐。不追问、不澄清、不理解。 | `SystemPromptProvider` 从固定模板组装结构化上下文。`ResourceResolver` + `FileWatcher` 按文件系统约定发现并热加载工具/技能/模型——不做语义解释。 | [#1 Prompt 组装](docs/prompt-assembly.md), [#2 资源注册](docs/resource-registry.md) |
| **L2: 可靠行动执行器** | 可逆行动执行。预演-执行-回滚机制，确保执行损伤可恢复。支持并行执行与依赖排序。 | `SandboxManager` 提供每会话隔离工作区。`ConcurrentToolExecutor` 并行执行无依赖工具。`FunctionBackend` 支持可选 `rollback()`。 | [#5 执行器](docs/tool-sandbox.md) |
| **L3: 非条件反射层** | 硬编码安全。权限门控、缩手反射、节律性存档——独立于模型决策，模型不可绕过。 | `PathCheckToolGuard` 阻断路径穿越与绝对路径。`ContentGuard` 执行前/后内容筛查。`SessionModeManager` + `PermissionRegistry` 强制执行 deny→ask→allow。`RoundManager` 维护滚动快照支持 undo。每轮检查取消令牌。 | [#3 权限控制](docs/tool-sandbox.md), [#4 安全审核](docs/tool-sandbox.md), [中断回滚](docs/interrupt.md) |

**设计准则**：每层都是*规则驱动的*——通过固定规则转换、路由、门控、记录。没有一层执行*理解*。当框架需要"智能"（记忆提取、上下文摘要），它通过 Plugin 调用模型——绝不通过核心引擎逻辑。

### 控制平面 — 结构化 State & 生命周期

三层并非孤立漂浮。**[控制平面](docs/agent-execution.md)** 是它们共同交汇的调度面——相当于脊髓，在脑和身体之间路由信号。

| 方面 | 实现 |
|------|------|
| **执行引擎** | `ControlPlane` 统一 `_execute` 路径——三层在此交汇。`LoopStrategy` ReAct 模式 + TODO 追踪 |
| **结构化 State** | 每轮组装固定 Schema 的 State Packet。`RoundManager` 维护 3 个滚动快照支持 checkpoint/restore。会话生命周期：create → resume → archive |
| **Hook 挂载面** | 9 个注入点（`session_start`、`round_start`、`pre_model_call`、`post_model_call`、`post_permission`、`pre_tool_exec`、`post_tool_exec`、`sandbox_persist`、`round_end`、`session_end`）——Plugin 挂载的 9 个触点 |

如果说三层是反射弧，控制平面就是脊髓——协调每个反射的触发时机，在三层之间路由状态，并为所有 Plugin 提供挂载面。

### Harness 即内核——5 骨架架构

> **Model + Harness = Agent。CPU + Kernel = Computer。**
>
> Token 是指令。Agent 会话是进程。工具调用是系统调用。

ARF 建立在 **5 个骨架**之上——最小可运行框架。每个骨架对应一个 Protocol。5 骨架 + [控制平面](#控制平面--结构化-state--生命周期) 构成完整 Harness 核心。其余一切都是挂载在生命周期 Hook 点上的 **Plugin**。

*点击骨架名称可查看深度设计文档。*

| # | 骨架 | OS 类比 | 当前实现 | 演进方向 |
|---|------|--------|----------|----------|
| 1 | **[Prompt 组装](docs/prompt-assembly.md)** | 程序加载器 (execve) | `SystemPromptProvider` — prefix（role + critical_rules）+ suffix（`$INVENTORY` 模板）。`string.Template` 占位符（`$MEMORY`、`$WORKSPACE`、`$TURN_BUDGET`）。引擎每轮替换。 | 多 Agent prompt 组合；基于角色的模板分发 |
| 2 | **[资源注册 (MCP)](docs/resource-registry.md)** | 文件系统 + 注册表 | 约定优于配置：`tool.yaml`+`function.py` 每工具，`skills/*.yaml`。模型在 `agent.yaml` 中内联定义（`model_defs`）。`FileWatcher` inotify+轮询热加载。`ResourceResolver` 覆盖合并。MCP 统一接口（本地 MCP Server 子进程，stdio JSON-RPC）聚合本地与外部资源。 | 层次化覆盖合并；MCP 多源 Provider；交叉引用验证 |
| 3 | **[权限控制](docs/tool-sandbox.md)** | ACL + 能力位 | `SessionModeManager`（auto/ask/plan）+ `PermissionRegistry` deny→ask→allow 执行。Per-agent `policy` 覆盖。`deny_patterns` 正则匹配。 | OAuth 范围权限；基于角色的访问控制 |
| 4 | **[安全审核](docs/tool-sandbox.md)** | 保护环 (Ring 0-3) | `PathCheckToolGuard` — 递归扫描（..、符号链接、深度/数量配额）。`ContentGuard` — 执行前/后 + 输出前基于规则的筛查。`GuardDefaults` 三道防线。 | 逐次调用沙箱；内容感知扫描 |
| 5 | **[执行器 (沙箱)](docs/tool-sandbox.md)** | 进程隔离 (chroot/namespace) | `SandboxManager` — 每会话隔离工作区，可配置黑名单，自动销毁。`ConcurrentToolExecutor` 并行执行。`FunctionBackend` 可选 `rollback()`。 | 容器级沙箱；资源配额 |

### Plugin 体系——反射弧，非认知模块

**Plugin ≠ Tool。** Tool 是 MCP 管理的函数资源，由 Agent 调用。Plugin 是挂载在 Hook 点上的行为——在框架生命周期事件时自动触发，如同生物的反射弧。框架无 Plugin 也能运行；Plugin 添加预置或自定义能力。关键区分：当 Plugin 需要智能（记忆提取、上下文摘要），它通过标准 `_call_model` 接口调用模型——**智能来自模型，而非 Plugin**。详见 [Plugin 总览](docs/plugins/overview.md)。

| Plugin | Hook | 状态 | 描述 |
|--------|------|------|------|
| **Memory** | `round_end` | DONE | 长期记忆提取，system model 驱动，原子写入 `memory.md` |
| **TODO** | `round_start`, `round_end` | DONE | 任务列表追踪 + 提醒注入 |
| **UNDO** | `round_end`, `sandbox_persist` | DONE | Round 级状态 + 文件回滚 |
| ~~**Model Routing**~~ | `pre_model_call` | **已弃用** | `TwoTierRouter` — 廉价 LLM 分类：简单→flash，复杂→pro。已弃用，改用直接模型配置。 |
| **Human Loop** | `post_permission`, `pre_tool_exec` | DONE | SSE 审批通道，60s 超时 |
| **Compaction** | `round_end` | DONE | `CompactionPlugin` — token 感知，75% 阈值，保留 8 条 + LLM 摘要 |
| **Checkpoint** | `round_end`, `session_end` | DONE | `CheckpointPlugin` — round 快照 + session 归档，支持 undo/restore |
| **Trace** | 全部 hook（跨切面） | DONE | `TracePlugin` — JSONL 事件记录，用于调试、回放、评估 |
| **Evaluation** | 离线 | DONE | `EvalPlugin` — 重放 trace、计算指标、diff 报告 |
| Planner | (延后) | P1 | 任务分解，system model 驱动 |
| bash | (延后) | P1 | Shell 执行器，注入安全审计 |
| code_interpreter | (延后) | P1 | Python 沙箱 |

### 弃用/延后

| 模块 | 处理 | 原因 |
|------|------|------|
| A2A 通信 (`arf/communication/`) | 弃用 | 先聚焦 agent+subagent |
| TaskScheduler (`arf/concurrency/`) | 弃用 | 仅单 Agent 执行 |
| Plan-Execute 策略 | 延后 | ReAct + TODO 当前足够 |

## 第二部分 — 研究路线图

ARF 是论文全部五项实验的统一台架。下表将每项实验映射到当前 ARF 能力及待建设内容。

| 实验 | 论文章节 | ARF 状态 | 已有基础 | 待建设 |
|------|---------|---------|---------|--------|
| **E1: 固定状态接口 vs. 自由文本提示**——任务稳定性对比 | §6.1 | **台架就绪** | Resource Registry 提供固定 schema 的状态组装。`SystemPromptProvider` 的 `$INVENTORY` 模板已展示了结构化上下文编码。 | 构建对比基准：相同任务分别用 ARF 固定 StatePacket 和传统自由文本提示运行。测量任务完成稳定性（多次运行的方差）。候选基准：AgentBench、SWE-bench。 |
| **E2: 零认知 Harness 基准测试** | §6.2 | **台架就绪** | 5 骨架 + 控制平面实现了三层基于规则的简单反射。完整 Agent 循环（ReAct + 工具 + guardrails）端到端运行。 | 横向对比：ARF vs. LangChain / OpenDevin 在编程/CLI 任务上的表现。指标：代码量、任务完成率、认知泄漏点数量（在模型调用之外执行语义解释的模块）。 |
| **E3: 在线 LoRA 用于长期记忆保持** | §6.3 | **扩展点** | `MemoryPlugin` 提取事实到 `memory.md`。`ModelAdapter` 提供模型调用抽象。`FileMemoryStore` 作为数据源。 | 在 `ModelAdapter` 上接入 LoRA 权重更新接口。使用 `memory.md` 条目作为在线 LoRA 微调的训练信号。对比记忆保持质量 vs. 外部记忆注入。 |
| **E4: 后训练身份边界鲁棒性** | §6.4 | **扩展点** | `Guardrails` 层含 `deny_patterns` 正则匹配。`SessionModeManager` 强制执行权限边界。`PathCheckToolGuard` 阻断路径穿越。 | 扩展 guardrails 支持对抗性提示测试。构建越狱基准测试集。测量身份边界在提示注入、角色覆盖、少样本操纵攻击下的保持率。 |
| **E5: 架构内上下文压缩（记忆令牌）** | §6.5 | **扩展点** | `CompactionPlugin` 提供 token 感知滑动窗口 + LLM 摘要。Token 计数基础设施已存在。 | 原型验证基于记忆令牌的压缩：替代外部摘要，训练模型将上下文内部压缩为记忆令牌。对比压缩保真度 vs. 当前 LLM 摘要方案。 |

**运行实验**：每项实验设计为在同一 ARF 台架上运行。框架的 `EvalPlugin` + `TracePlugin` 提供统一的数据采集与指标计算。参见 [回归测评文档](docs/eval-benchmark.md) 了解评估基础设施。

**研究日志**：[`docs/paper/`](docs/paper/) 包含论文框架、阅读笔记和阶段性研究进展。

<br/>

---

<p align="center">
  <sub>MIT — 详见 <a href="./LICENSE">LICENSE</a></sub>
  <br/>
  <sub><a href="https://gitee.com/dalaydata/open_deepseek_arf">Gitee</a> &nbsp;·&nbsp; <a href="https://github.com/Wang-hubber/open_deepseek_arf">GitHub</a></sub>
</p>
