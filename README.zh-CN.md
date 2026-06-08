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
- **作为 MVP**：ARF 实现了三层机械层，证明了剥离认知责任后的 Harness 足以支撑完整可用的 Agent。6 个骨架 + Plugin 体系构成了设计准则的完整、可测试的工程实体。
- **作为研究脚手架**：ARF 为论文中全部五项实验提供统一的实验台架——状态接口稳定性、零认知基准测试、在线 LoRA 记忆、身份边界鲁棒性、内部上下文压缩。

**这个 MVP 证明了什么**：一个基于 Protocol 定义的骨架构建的 Harness，将所有"智能"行为（记忆提取、任务追踪、上下文压缩）实现为可插拔的反射弧而非核心引擎逻辑，能够在严格分离*认知工作*（模型）与*机械工作*（Harness）的前提下，支撑全功能 Agent 运行。

<br/>

---

## 阅读指引

本文档分为两大部分加研究路线图：

- **第一部分 — 框架**：大脑-脊椎-身体设计模型、三层机械层、6 骨架架构、Plugin 体系
- **第二部分 — 研究路线图**：论文五项实验在 ARF 中的当前状态与待建设内容
- **底部 [TODO](#todo)**：已知问题与演进方向

新读者建议先扫一遍三层机械层理解架构主张，再看 6 骨架表格深入实现细节。

<br/>

---

## 第一部分 — 框架

### 设计理念：大脑-脊椎-身体模型

模型是裸算力——强大，但不是一台可用的计算机。它需要内存管理、进程调度、中断响应、文件系统和安全边界。ARF 提供这一切。但设计理念比 OS 类比更深一层。

**支配每一项架构决策的生物学映射**：

| 生物系统 | Agent 系统 | 职责 | 认知负载 |
|---------|-----------|------|---------|
| **大脑** | 大语言模型 | 条件反射中枢。接收编码后的状态数据包，输出行为指令。通过后训练内化专业知识与身份边界。 | **全认知** — 理解、推理、决策 |
| **脑干 / 脊椎** | Harness 核心（6 骨架） | 固定感知编码、可靠行动执行、非条件反射。多源信号时间对齐为固定 Schema 的状态数据包。解析函数调用，执行并收集反馈——不做策略判断。 | **零认知** — 格式化、对齐、执行、门控 |
| **身体** | 工具生态 | 模型操作世界的物理/虚拟效应器。文件 I/O、网页抓取、Shell 执行、代码解释。 | **零认知** — 仅机械动作 |

**协同进化律**：猴脑无法操纵人身。Harness 定义的状态 Schema 必须通过配对后训练成为模型的原生感知语言。大脑与身体协同进化，否则两者都无法工作。

操作系统的经典抽象——虚拟内存、缓存层次、系统调用、保护环——直接映射到工程实现。但架构主张是生物学的：**认知工作属于大脑；Harness 是脊髓，不是第二个大脑。**

### 三层机械层

ARF 将 Harness 实现为三个零认知层，每层映射到具体骨架。这三层对应论文第 5 节"理想 Harness 的设计准则"。

| 层级 | 设计准则 | ARF 实现 | 对应骨架 |
|------|---------|---------|---------|
| **L1: 固定感知编码器** | 零认知状态编码。固定周期、固定字段、仅做格式与时间对齐。不追问、不澄清、不理解。 | `SystemPromptProvider` 从固定模板组装结构化上下文。`ResourceResolver` + `FileWatcher` 按文件系统约定发现并热加载工具/技能/模型——不做语义解释。 | #1 Prompt 组装, #2 资源注册 |
| **L2: 可靠行动执行器** | 可逆行动执行。预演-执行-回滚机制，确保执行损伤可恢复。支持并行执行与依赖排序。 | `SandboxManager` 提供每会话隔离工作区。`ConcurrentToolExecutor` 并行执行无依赖工具。`FunctionBackend` 支持可选 `rollback()`。`SkillPipeline` 强制执行依赖 DAG。 | #5 执行器（沙箱）, Skill Pipeline |
| **L3: 非条件反射层** | 硬编码安全。权限门控、缩手反射、节律性存档——独立于模型决策，模型不可绕过。 | `PathCheckToolGuard` 阻断路径穿越与绝对路径。`ContentGuard` 执行前/后内容筛查。`SessionModeManager` + `PermissionRegistry` 强制执行 deny→ask→allow。`RoundManager` 维护滚动快照支持 undo。每轮检查取消令牌。 | #3 权限控制, #4 安全审核, 中断回滚 |
| **跨切面** | 进程调度器 + 生命周期信号。调度三层机械层的控制平面。 | `GraphEngine` 统一执行路径 + 9 个 Hook 注入点。`LoopStrategy` ReAct 模式 + TODO 追踪。状态管理含 checkpoint/restore。 | #6 控制平面 |

**设计准则**：每层都是*机械性的*——它转换、路由、门控、记录。没有一层执行*理解*。当框架需要"智能"（记忆提取、上下文摘要），它通过 Plugin 调用模型——绝不通过核心引擎逻辑。

### Harness 即内核——6 骨架架构

> **Model + Harness = Agent。CPU + Kernel = Computer。**
>
> Token 是指令。Agent 会话是进程。工具调用是系统调用。

ARF 建立在 **6 个骨架**之上——最小可运行框架。每个骨架对应一个 Protocol。框架可以只用这 6 个骨架运行 Agent；其余一切都是挂载在生命周期 Hook 点上的 **Plugin**。

*点击骨架名称可查看深度设计文档。*

| # | 骨架 | OS 类比 | 当前实现 | 演进方向 |
|---|------|--------|----------|----------|
| 1 | **[Prompt 组装](docs/prompt-assembly.md)** | 程序加载器 (execve) | `SystemPromptProvider` — prefix（role + critical_rules）+ suffix（`$INVENTORY` 模板）。`string.Template` 占位符（`$MEMORY`、`$WORKSPACE`、`$TURN_BUDGET`）。引擎每轮替换。 | 多 Agent prompt 组合；基于角色的模板分发 |
| 2 | **[资源注册 (MCP)](docs/resource-registry.md)** | 文件系统 + 注册表 | 约定优于配置：`tool.yaml`+`function.py` 每工具，`skills/*.yaml`。模型在 `agent.yaml` 中内联定义（`model_defs`）。`FileWatcher` inotify+轮询热加载。`ResourceResolver` 覆盖合并。MCP 统一接口（本地 MCP Server 子进程，stdio JSON-RPC）聚合本地与外部资源。 | 层次化覆盖合并；MCP 多源 Provider；交叉引用验证 |
| 3 | **[权限控制](docs/tool-sandbox.md)** | ACL + 能力位 | `SessionModeManager`（auto/ask/plan）+ `PermissionRegistry` deny→ask→allow 执行。Per-agent `policy` 覆盖。`deny_patterns` 正则匹配。 | OAuth 范围权限；基于角色的访问控制 |
| 4 | **[安全审核](docs/tool-sandbox.md)** | 保护环 (Ring 0-3) | `PathCheckToolGuard` — 递归扫描（..、符号链接、深度/数量配额）。`ContentGuard` — 执行前/后 + 输出前基于规则的筛查。`GuardDefaults` 三道防线。 | 逐次调用沙箱；内容感知扫描 |
| 5 | **[执行器 (沙箱)](docs/tool-sandbox.md)** | 进程隔离 (chroot/namespace) | `SandboxManager` — 每会话隔离工作区，可配置黑名单，自动销毁。`ConcurrentToolExecutor` 并行执行。`FunctionBackend` 可选 `rollback()`。 | 容器级沙箱；资源配额 |
| 6 | **[控制平面](docs/agent-execution.md)** | 进程调度器 + 信号 | `GraphEngine` 统一 `_execute` 路径。`LoopStrategy` ReAct 模式 + TODO 追踪。State 管理（运行时会话状态）。9 个 Hook 注入点（`session_start`、`round_start`、`pre_model_call`、`post_model_call`、`post_permission`、`pre_tool_exec`、`post_tool_exec`、`sandbox_persist`、`round_end`、`session_end`）。 | Plan-Execute 循环策略；暂停/恢复/检查点；多 Agent DAG |

### Plugin 体系——反射弧，非认知模块

**Plugin ≠ Tool。** Tool 是 MCP 管理的函数资源，由 Agent 调用。Plugin 是挂载在 Hook 点上的行为——在框架生命周期事件时自动触发，如同生物的反射弧。框架无 Plugin 也能运行；Plugin 添加预置或自定义能力。关键区分：当 Plugin 需要智能（记忆提取、上下文摘要），它通过标准 `_call_model` 接口调用模型——**智能来自模型，而非 Plugin**。

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

## 快速开始

需要 Python ≥ 3.11。

```bash
git clone git@gitee.com:dalaydata/open_deepseek_arf.git
cd open_deepseek_arf
pip install -e .
cd app/arf_default_assistant
python test_setup.py   # 验证环境
python cli.py start    # 启动服务
```

浏览器打开 **http://127.0.0.1:8000**，输入 API 密钥即可开始。

## 第二部分 — 研究路线图

ARF 是论文全部五项实验的统一台架。下表将每项实验映射到当前 ARF 能力及待建设内容。

| 实验 | 论文章节 | ARF 状态 | 已有基础 | 待建设 |
|------|---------|---------|---------|--------|
| **E1: 固定状态接口 vs. 自由文本提示**——任务稳定性对比 | §6.1 | **台架就绪** | Resource Registry 提供固定 schema 的状态组装。`SystemPromptProvider` 的 `$INVENTORY` 模板已展示了结构化上下文编码。 | 构建对比基准：相同任务分别用 ARF 固定 StatePacket 和传统自由文本提示运行。测量任务完成稳定性（多次运行的方差）。候选基准：AgentBench、SWE-bench。 |
| **E2: 零认知 Harness 基准测试** | §6.2 | **台架就绪** | 全部 6 骨架实现了三层机械层。完整 Agent 循环（ReAct + 工具 + guardrails）端到端运行。 | 横向对比：ARF vs. LangChain / OpenDevin 在编程/CLI 任务上的表现。指标：代码量、任务完成率、认知泄漏点数量（在模型调用之外执行语义解释的模块）。 |
| **E3: 在线 LoRA 用于长期记忆保持** | §6.3 | **扩展点** | `MemoryPlugin` 提取事实到 `memory.md`。`ModelAdapter` 提供模型调用抽象。`FileMemoryStore` 作为数据源。 | 在 `ModelAdapter` 上接入 LoRA 权重更新接口。使用 `memory.md` 条目作为在线 LoRA 微调的训练信号。对比记忆保持质量 vs. 外部记忆注入。 |
| **E4: 后训练身份边界鲁棒性** | §6.4 | **扩展点** | `Guardrails` 层含 `deny_patterns` 正则匹配。`SessionModeManager` 强制执行权限边界。`PathCheckToolGuard` 阻断路径穿越。 | 扩展 guardrails 支持对抗性提示测试。构建越狱基准测试集。测量身份边界在提示注入、角色覆盖、少样本操纵攻击下的保持率。 |
| **E5: 架构内上下文压缩（记忆令牌）** | §6.5 | **扩展点** | `CompactionPlugin` 提供 token 感知滑动窗口 + LLM 摘要。Token 计数基础设施已存在。 | 原型验证基于记忆令牌的压缩：替代外部摘要，训练模型将上下文内部压缩为记忆令牌。对比压缩保真度 vs. 当前 LLM 摘要方案。 |

**运行实验**：每项实验设计为在同一 ARF 台架上运行。框架的 `EvalPlugin` + `TracePlugin` 提供统一的数据采集与指标计算。参见 [回归测评文档](docs/eval-benchmark.md) 了解评估基础设施。

**研究日志**：`docs/paper/`（待创建）将包含论文框架、实验方案和阶段性结果。

<br/>

---

## TODO

### 已知代码问题 (2026-05-26 事实校验)

| # | 标题 | 代码路径 | 功能域 | 类型 | 详情 |
|---|------|---------|--------|------|------|
| 1 | ~~Engine `invoke`/`astream` 代码重复~~ → **已修复** | `arf/engine/graph.py` | 进程调度 | 框架 | ~~~400 行几乎相同的 Agent Loop 逻辑在两处~~ → 提取 `_execute()` + `_step_call_model()` + `_step_execute_tools()` 统一路径，invoke/astream 简化为薄包装。 |
| 2 | ~~`BaseAgent.__init__` 巨型构造~~ → **已修复** | `arf/agent/base.py` | 进程创建 | 框架 | ~~构造函数内直接实例化 20+ 个实现~~ → 提取工厂方法。 |
| 3 | ~~`server.py` 单文件混杂~~ → **已修复** | `app/arf_default_assistant/routers/` | 用户界面 | App | ~~REST 路由、WebSocket、SSE 流、CORS、文件服务、状态管理、配置 API 全在一个文件。~~ → 拆分为 `routers/`。 |
| 4 | ~~`SnapshotRollback` 状态快照为空~~ → **已修复** | `arf/resources/backends/function.py` | 故障恢复 | 框架 | 改为 `FunctionBackend` 内联回滚。 |
| 5 | ~~`EvalRunner` 指标空转~~ → **已修复** | `arf/evaluation/runner.py` | 质量保证 | 框架 | 重写：通过 `EventBus.events_since()` 采集真实 trace，4 个 metric 在真实数据上计算。 |
| 6 | ~~全局状态 `registry._agent`~~ → **已修复** | `arf/agent/registry.py` 已删除 | 进程隔离 | 框架 | 已删除。Engine + state_store 通过工具执行器参数注入。 |
| 7 | ~~`PromptBasedPlanner` 返回空计划~~ → **已修复** | `arf/plugins/planner/` | 任务规划 | 框架 | 由插件系统取代。 |
| 8 | ~~SSE 监听器泄漏~~ → **已修复** | `arf/streaming/adapters/sse.py` | 通信协议 | 框架 | 改为 `@asynccontextmanager`。 |
| 9 | ~~代码规范不统一~~ → **已修复** | 13 文件 + `graph.py` + `planner.py` | 文档系统 | 框架 | 全部文件已加模块 docstring，核心签名用 `dict[str, Any]`。 |
| 10 | ~~无 Rate Limiting / Circuit Breaker~~ → **已修复** | `arf/protection/` | 进程调度 | 框架 | `ModelCallProtector` 组合 `TokenBucket` + `CircuitBreaker`。 |
| 11 | 开源基建缺失 | — | 打包分发 | 框架 | 无 `CONTRIBUTING.md`、PR/Issue 模板、`CHANGELOG.md`、版本发布流程。 |

**Plugins** — 挂载在 Hook 点上的能力包。每个 Plugin 包含 `plugin.yaml`（name + hooks + config）和 `plugin.py`（PluginProtocol 实现）。`PluginLoader` 扫描 `arf/plugins/{name}/`。社区可贡献。Plugin ≠ Tool — Plugin 在生命周期 Hook 自动触发，Tool 是 Agent 主动调用的 MCP 资源。

| # | Plugin | 状态 | Hook | 描述 |
|---|--------|------|------|------|
| P-1 | `compaction` | DONE | `round_end` | Token 感知上下文压缩，75% 阈值 + LLM 摘要 |
| P-2 | `checkpoint` | DONE | `round_end`, `session_end` | Round 快照 + session 归档，支持恢复 |
| P-3 | `trace` | DONE | 全部 9 个 hook | JSONL 事件记录，用于调试、回放、评估 |
| P-4 | `eval` | DONE | 离线 | Trace 回放 + 指标计算 + diff 报告 |
| P-5 | `memory` | DONE | `round_end` | 长期记忆提取，system model 驱动，原子写入 `memory.md` |
| P-6 | `todo` | DONE | `round_start`, `round_end` | 任务列表追踪 + 提醒注入 |
| P-7 | `undo` | DONE | `round_end`, `sandbox_persist` | Round 级状态 + 文件回滚 |
| ~~P-8~~ | ~~`model_router`~~ | **已弃用** | `pre_model_call` | TwoTierRouter 快/慢分发 — 已弃用 |
| P-9 | `human_loop` | DONE | `post_permission` | SSE 审批通道，60s 超时 |
| P-10 | `bash` | P1 | `pre_tool_exec` | Shell 执行器，注入安全审计 |
| P-11 | `code_interpreter` | P1 | `pre_tool_exec` | Python 沙箱 |

### 演进方向

参见各模块设计文档：
- [上下文管理](docs/context-management.md) — 语义单元压缩、自适应阈值、跨会话摘要复用
- [Memory 插件](docs/plugins/memory.md) — 多轮次触发、自定义 prompt 模板
- [资源注册](docs/resource-registry.md) — 层次化覆盖合并、MCP 多源 Provider
- [工具沙箱](docs/tool-sandbox.md) — Per-invocation sandbox、内容感知扫描
- [Skill Pipeline](docs/skill-pipeline.md) — 多 Agent DAG、Worktree 隔离
- [中断](docs/interrupt.md) — 暂停/重定向、空闲超时
- [Trace](docs/trace.md) — SQLite Trace DB、OpenTelemetry 导出
- [回归测评](docs/eval-benchmark.md) — CLI 集成、语义相似度指标

<br/>

---

<p align="center">
  <sub>MIT — 详见 <a href="./LICENSE">LICENSE</a></sub>
  <br/>
  <sub><a href="https://gitee.com/dalaydata/open_deepseek_arf">Gitee</a> &nbsp;·&nbsp; <a href="https://github.com/Wang-hubber/open_deepseek_arf">GitHub</a></sub>
</p>
