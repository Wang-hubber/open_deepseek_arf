<p align="center">
  <h1 align="center">ARF — Agent Resources & RunTime FrameWork</h1>
  <p align="center"><em>"Parameter Is All You Need" 的研究脚手架与 Harness MVP</em></p>
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

<h3 align="center">Parameter Is All You Need — Harness 的全新范式</h3>
<p align="center">硬线（零认知，永不变更）+ 软线（ICL → LoRA MOE 渐进内化）</p>
<p align="center">本地优先 · 约定大于配置 · 全程可追溯 · 自我演进</p>

<br/>

> **本项目由 DeepSeek V4 Pro 与 Claude Code 协作完成。** 作者仅提供设计思路与代码审核，未手写任何一行代码。

<br/>

## 研究背景

ARF 是研究论文 **[《Parameter Is All You Need——Harness 的全新范式》](docs/paper/framework.md)** 的工程配套项目。

**核心命题**：《Attention Is All You Need》发表近十年后，Agent 系统陷入了另一种"一切皆在 X"——一切皆在上下文。System Prompt 定义身份，RAG 管线注入知识，memory.md 承载长期记忆，自然语言文本充当 Agent 间通信协议。In-Context Learning 成为了万能锤子。论文主张一种范式迁移：身份、知识、记忆、通信应从上下文窗口迁移至模型参数——通过 LoRA 适配器在运行时热插拔、组合叠加、渐进更新。**Parameter Is All You Need.**

**Harness 在新范式中的角色**：如果参数承载认知信号，Harness 还剩什么？两条并行线：**硬线**（零认知，永不变更——安全门控、存档、追踪、Action 执行、Hook 挂载）和**软线**（身份/知识/记忆/通信信号从 ICL 渐进迁移至 LoRA MOE）。Harness 是脊椎——不思考，但它是学习发生的载体。

**ARF 的双重角色**：
- **作为 MVP**：ARF 实现了完整硬线——安全、错误恢复、存档、追踪、测评、Action 执行、Hook 体系、Agent 编排。软线（LoRA MOE 路由 + 在线 SFT 管线）为实验一至四的建设目标。
- **作为研究脚手架**：ARF 为全部五项实验提供统一台架——四个单维度验证（记忆、身份、压缩、TFlow 通信）加终局对比（热机 LoRA MOE vs 冷启动纯 ICL Harness）。

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
| **Hook 挂载面** | 9 个生命周期点（`session_start`、`round_start`、`turn_start`、`pre_action`、`post_action`、`turn_end`、`round_end`、`session_end`、`error`）——Plugin 挂载的 9 个触点。`pre_action`/`post_action` 包裹每一次模型调用和工具执行 |

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

## 第二部分 — 研究路线图

ARF 是论文全部五项实验的统一台架。实验按 §2 相关工作的四个问题域组织——每个域对应一篇综述立场 + 一组实验验证。

| 问题域 | 论文 § | 研究问题 | ARF 基线 | 实验 | 待建设 |
|--------|---------|---------|----------|------|--------|
| **Loop Strategies** | §2.1 · §5（框架） | Harness 能否被简化为薄 ReAct 循环 + 零认知反射弧？ | `ControlPlane` + `LoopStrategy`（ReAct 参考实现）。三层基于规则的简单反射。 | —（基线） | 将 ARF 硬线文档化为"最薄可行 Harness"的参考实现。综述：[loop-strategies](docs/paper/reading_summary/2.1-loop-strategies/) |
| **RAG vs 后训练** | §2.2 · §5.2 | Identity LoRA 固化人格在对抗攻击下是否比 System Prompt 更难突破？ | `Guardrails` + `deny_patterns` + `SessionModeManager`。数据集：JailbreakBench、自建 Persona Conflict | **E2: 身份边界鲁棒性** | 从角色扮演数据训练 Identity LoRA。测量 ASR/ICS/RQ。对抗攻击下对比 System Prompt 基线。扫 LoRA 秩 r。综述：[rag-finetuning](docs/paper/reading_summary/2.2-rag-vs-finetuning/) |
| **记忆与上下文** | §2.3 · §5.1, §5.3 | 参数化记忆（在线 SFT → LoRA B 矩阵）在保持率、更新能力、抗干扰、上下文占用上是否优于外部注入（memory.md + 向量 DB）？ | `MemoryPlugin` → `memory.md` + `ModelAdapter`。`CompactionPlugin`（token 感知滑动窗口 + LLM 摘要）。数据集：LoCoMo、LongMemEval、LongBench QA | **E1: 在线 LoRA 长期记忆保持** · **E3: 参数化上下文压缩** | 接入 LoRA B 矩阵在线 SFT。以 memory.md 条目为监督。同步 SFT 改为异步双缓冲。扫 r=1–8。四维对比（保持率/更新/抗干扰/上下文占用）。综述：[memory-context](docs/paper/reading_summary/2.3-memory-context/) |
| **Agent 通信** | §2.4 · §5.4 | 权重空间扰动（TFlow）在延迟和带宽上是否优于 NL 文本通信？ | `AgentBus` + `PeerAgent` + `ControlPlane.astream()`。自建 DRSA 模拟环境 | **E4: TFlow 权重空间通信** | 实现扰动编译模块（内部激活 → ΔW）。扫发送方数量（2–32）。对比延迟/带宽 vs NL 文本。综述：[agent-communication](docs/paper/reading_summary/2.4-agent-communication/) |
| **终局** | §5.5 | "Parameter Is All You Need" 能否击败 "Context Is All You Need"？ | 同一 ARF 硬线，两组软线配置：A 组纯 ICL，B 组四个 LoRA 全激活 | **E5: 热机 LoRA MOE vs 冷启动 ICL Harness** | 同任务、同基座、四维评分。检验标题命题。 |

**运行实验**：每项实验在同一 ARF 台架上运行。`EvalPlugin` + `TracePlugin` 提供统一的数据采集与指标计算。参见[回归测评文档](docs/eval-benchmark.md)。

**研究日志**：[`docs/paper/`](docs/paper/) — 论文框架、按域组织的阅读笔记、阶段性研究进展。

<br/>

---

## 演进方向

### 短期：论文阅读与理论校验

算力受限，聚焦四维软线的文献调研：

- **记忆**：PEAM、TMEM — 扩展至 Memorizing Transformer、Unlimiformer、MemGPT
- **身份**：Character-LLM、Neeko、RoleLLM — LoRA 固化人格的对抗鲁棒性
- **知识**：P-RAG、MEGa — RAG vs Fine-tuning 系统对比
- **通信**：TFlow — 权重扰动叠加在大规模 Agent 群中的稳定性
- **消融实验设计**：完善 E1/E3 变量控制、基准和指标，算力就绪后快速启动

详见 [阅读笔记](docs/paper/reading_summary/)。

### 中远期：LoRA MOE + 在线 SFT 管线

算力就绪后启动：

- LoRA MOE 路由器 — 按功能域（身份/知识/记忆/通信）热插拔适配器
- 在线 SFT 管线 — 双缓冲 LoRA B 矩阵，异步非阻塞更新
- E5 终局实验 — 热机 LoRA MOE Harness vs 冷启动 ICL Harness，四维评分
- 异步非阻塞更新（双缓冲 LoRA B 矩阵）
- 偏好与事实解耦（自监督 vs 强监督，分离适配器）

---

<p align="center">
  <sub>MIT — 详见 <a href="./LICENSE">LICENSE</a></sub>
  <br/>
  <sub><a href="https://gitee.com/dalaydata/open_deepseek_arf">Gitee</a> &nbsp;·&nbsp; <a href="https://github.com/Wang-hubber/open_deepseek_arf">GitHub</a></sub>
</p>
