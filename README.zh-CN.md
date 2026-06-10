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

**核心命题**：《Attention Is All You Need》发表近十年后，Agent 系统陷入了另一种"一切皆在 X"——一切皆在上下文。Harness 硬编码循环策略决定"怎么想"，System Prompt 定义身份，RAG 管线注入知识，memory.md 承载长期记忆，自然语言文本充当 Agent 间通信协议。In-Context Learning 成为了万能锤子。论文主张一种范式迁移：行为策略、身份、记忆、知识、通信应从上下文窗口迁移至模型参数——通过 LoRA 适配器在运行时热插拔、组合叠加、渐进更新。**Parameter Is All You Need.**

**Harness 在新范式中的角色**：如果参数承载认知信号，Harness 还剩什么？两条并行线：**硬线**（零认知，永不变更——安全门控、存档、追踪、Action 执行、Hook 挂载）和**软线**（策略/身份/知识/记忆/通信信号从 ICL 渐进迁移至 LoRA MOE）。Harness 是脊椎——不思考，但它是学习发生的载体。

**ARF 的双重角色**：
- **作为 MVP**：ARF 实现了完整硬线——安全、错误恢复、存档、追踪、测评、Action 执行、Hook 体系、Agent 编排。软线（LoRA MOE 路由 + 在线 SFT 管线）为实验一至四的建设目标。
- **作为研究脚手架**：ARF 为全部五项实验提供统一台架——五个单维度验证（策略、身份、记忆、知识、TFlow 通信）加终局对比（热机 LoRA MOE vs 冷启动纯 ICL Harness）。

**配套教学项目 — [ARF App](https://gitee.com/dalaydata/arf_app_021)**：7 单元渐进式教程，覆盖从零构建 ARF 应用到生产级 Agent 的完整链路——Hello ARF → 会话管理 → 工具系统 → 工具审批 → Guardrails 安全 → 长期记忆 → Agent 调优。每单元含可运行代码快照。教程同时作为框架的用户验收测试，在真实使用中验证 API 设计的完备性。

<br/>

---

## 阅读指引

**研究者** — 关注架构主张和实验路线：

- [研究路线图](#第一部分--研究路线图) → [设计理念](#第二部分--框架) → [三层基于规则的简单反射](#三层基于规则的简单反射) → [控制平面](#控制平面--结构化-state--生命周期)

**框架使用者** — 想基于 ARF 构建应用：

- [研究路线图](#第一部分--研究路线图) → [框架骨架](#harness-即内核框架骨架) → [控制平面](#控制平面--结构化-state--生命周期) → [Plugin 体系](#plugin-体系反射弧非认知模块) → 然后移步 [ARF App 教学项目](https://gitee.com/dalaydata/arf_app_021)，7 单元渐进式教程，从零到生产级 Agent

<br/>

---

## 第一部分 — 研究路线图

ARF 是论文全部五项实验的统一台架。实验按 §2 相关工作的五个问题域组织——按认知递进排列（从模型内部到模型之间），每个域定义旧 Harness 的越位与新 Harness 的职责边界。

| 问题域 | 论文 § | 研究问题 | 旧 Harness | 新 Harness | 实验 |
|--------|---------|---------|-----------|-----------|------|
| **行为策略** | §2.1 · §5（框架） | 模型应自主选择推理策略，而非由 Harness 硬编码 ReAct/Plan-Solve？ | Harness 在引擎代码中硬编码循环策略——替模型决定"怎么想" | Harness 采集并提供训练数据；策略选择逐步内生于模型 | —（基线）综述：[behavior-strategy](docs/paper/reading_summary/2.1-behavior-strategy/) |
| **身份** | §2.2 · §5.2 | Identity LoRA 固化人格在对抗攻击下是否比 System Prompt 注入更难突破？ | System Prompt 定义角色边界——"我是谁、行为准则、能力边界"等任务无关的抽象准则 | Harness 提供身份切换；身份准则通过参数化固定为模型行为基准 | **E2: 身份边界鲁棒性** 综述：[identity](docs/paper/reading_summary/2.2-identity/) |
| **记忆** | §2.3 · §5.1, §5.3 | 参数化记忆（在线 SFT → LoRA B 矩阵）在保持率、更新能力、抗干扰、上下文占用上是否优于外部注入？ | memory.md + 向量 DB 外部注入偏好、环境变化、任务场景内的抽象化准则 | Harness 提供记忆抽取和注入的触发点；记忆内生，通过参数化注入 | **E1: 在线 LoRA 长期记忆保持** · **E3: 参数化上下文压缩** 综述：[memory](docs/paper/reading_summary/2.3-memory/) |
| **知识** | §2.4 · §5.3 | Knowledge LoRA 是否优于 RAG（稳定领域知识），同时为动态高频数据保留 ICL 通道？ | RAG 将事实、场景固定准则反复注入上下文窗口——每次推理重新注入 | Harness 提供知识注入的触发点；动态高频更新数据保留 ICL 通道 | **E3: 参数化上下文压缩** 综述：[knowledge](docs/paper/reading_summary/2.4-knowledge/) |
| **A2A 通讯** | §2.5 · §5.4 | 权重空间扰动（TFlow）在延迟和带宽上是否优于 NL 文本——Agent 间应走 parameter 层级管道？ | Agent 间交换 NL 文本——稀疏的人类面向输出，串行经过 LLM 推理 | Harness 提供 parameter 层级的通讯管道——in-memory object 传递，非文本生成 | **E4: TFlow 权重空间通信** 综述：[communication](docs/paper/reading_summary/2.5-agent-communication/) |
| **终局** | §5.5 | "Parameter Is All You Need" 能否击败 "Context Is All You Need"？ | 同一 ARF 硬线，两组软线：A 组纯 ICL（五个维度上下文注入），B 组五个 LoRA 适配器全激活 | **E5: 热机 LoRA MOE vs 冷启动 ICL Harness** — 同任务、同基座、六维评分 |

**运行实验**：每项实验在同一 ARF 台架上运行。`EvalPlugin` + `TracePlugin` 提供统一的数据采集与指标计算。参见[回归测评文档](docs/eval-benchmark.md)。

**研究日志**：[`docs/paper/`](docs/paper/) — 论文框架、按域组织的阅读笔记、阶段性研究进展。

**纲领**：随着大模型越来越强，需要通过 ICL 注入的信息就越来越少——Harness 越来越薄。与此同时，从 ICL 走向 Parameter 的过程，也是大模型和 Harness 的绑定关系越来越紧密的过程：Harness 提供的训练数据、注入触发点、身份切换、参数通讯管道，都深度依赖于特定模型的架构和后训练接口。薄的是认知负担，紧的是工程耦合。

<br/>

---

## 第二部分 — 框架

### Harness 即内核——框架骨架

> **Model + Harness = Agent。CPU + Kernel = Computer。**
>
> Token 是指令。Agent 会话是进程。工具调用是系统调用。

无论软线如何演进——无论身份、知识、记忆、通信以 ICL 还是 LoRA MOE 方式注入——Harness 硬线必须提供六项跨范式能力。其中四项直接影响 Agent 运行时行为（控制项），两项是范式无关的企业级基础设施（非控制项——不影响运行，但决定框架能否落地）。

**控制项（运行时骨架）**

| # | 骨架 | 职责 | ARF 实现 | 演进方向 |
|---|------|------|---------|----------|
| 1 | **[Prompt 组装](docs/prompt-assembly.md)** | 将系统指令、任务描述、工具清单、记忆摘要等组装为结构化 Prompt。即便大段身份提示词不再需要，临时性修正、运行态状态仍需注入 | `SystemPromptProvider` — prefix + suffix（`$INVENTORY` 模板）。`string.Template` 占位符。引擎每轮替换 | 多 Agent prompt 组合；基于角色的模板分发 |
| 2 | **[资源发现与注册](docs/resource-registry.md)** | 发现并热加载 Tool 和 Skill。Tool 和 Skill 是跨模型的——无论模型如何演进，工具生态需要框架管理 | 约定优于配置：`tool.yaml`+`function.py`。`FileWatcher` 热加载。MCP Server 子进程 stdio JSON-RPC 聚合本地与外部资源 | 层次化覆盖合并；MCP 多源 Provider |
| 3 | **[动作执行](docs/tool-sandbox.md)** | 工具调用、错误恢复、断连重试、权限门控（deny→ask→allow）、人工审批、沙箱隔离、安全审查。机械化执行不占用大模型——规则判断，零认知 | `SessionModeManager` + `PermissionRegistry`。`PathCheckToolGuard` + `ContentGuard`。`SandboxManager` 每会话隔离。`FunctionBackend` + `rollback()` | 容器级沙箱；OAuth 范围权限；内容感知扫描 |
| 4 | **[Hook 挂载面](docs/agent-execution.md)** | 生命周期事件驱动的可扩展挂载点。框架能力随实验需求动态扩展——新 Plugin 不修改核心代码 | 9 个生命周期 Hook 点（`session_start` ~ `error`）。`pre_action`/`post_action` 包裹每次模型调用和工具执行 | 动态 Hook 注册；优先级排序 |

**非控制项（企业级基础设施——不影响运行，影响落地）**

| # | 能力 | 职责 | ARF 实现 |
|---|------|------|---------|
| 5 | **Trace** | 全链路追踪：每轮 Prompt、每个 Action、每次模型调用的输入输出。可观测性独立于认知——无论模型多强，运行记录必须完整、可回放 | `FileTraceStore` + `TracePlugin` |
| 6 | **测评** | 回归测评台架：A/B 对比、多维指标、会话回放。评估基础设施不关心被测对象是 ICL 还是 LoRA——只关心可比较性 | `EvalRunner` + `BenchmarkBuilder` + `EvalComparator` |

### 控制平面 — 结构化 State & 生命周期

**[控制平面](docs/agent-execution.md)** 是四项控制骨架的共同调度面——统一 `_execute` 路径，协调 Plugin 挂载，管理会话生命周期。

| 方面 | 实现 |
|------|------|
| **执行引擎** | `ControlPlane` 统一 `_execute` 路径。`LoopStrategy` ReAct 模式 + TODO 追踪 |
| **结构化 State** | 每轮组装固定 Schema 的 State Packet。`RoundManager` 维护 3 个滚动快照支持 checkpoint/restore。会话生命周期：create → resume → archive |
| **Hook 挂载面** | 9 个生命周期点（`session_start`、`round_start`、`turn_start`、`pre_action`、`post_action`、`turn_end`、`round_end`、`session_end`、`error`）——Plugin 挂载的 9 个触点。`pre_action`/`post_action` 包裹每一次模型调用和工具执行 |

### Plugin 体系——反射弧，非认知模块

**Plugin ≠ Tool。** Tool 是 MCP 管理的函数资源，由 Agent 调用。Plugin 是挂载在 Hook 点上的行为——在框架生命周期事件时自动触发，如同生物的反射弧。框架无 Plugin 也能运行；Plugin 添加预置或自定义能力。关键区分：当 Plugin 需要智能（记忆提取、上下文摘要），它通过标准 `_call_model` 接口调用模型——**智能来自模型，而非 Plugin**。详见 [Plugin 总览](docs/plugins/overview.md)。

<br/>

---

## 演进方向

### 短期：论文阅读与理论校验

算力受限，聚焦五维软线的文献调研：

- **行为策略**：ReAct、PS Prompting、AutoGen、MetaGPT — 策略选择能否内生于模型？
- **身份**：Character-LLM、Neeko、RoleLLM — LoRA 固化人格的对抗鲁棒性
- **记忆**：PEAM、TMEM — 扩展至 Memorizing Transformer、Unlimiformer、MemGPT
- **知识**：P-RAG、MEGa — RAG vs Fine-tuning 系统对比；动态 ICL 通道保留
- **通信**：TFlow — 权重扰动叠加在大规模 Agent 群中的稳定性
- **消融实验设计**：完善 E1/E2/E3 变量控制、基准和指标，算力就绪后快速启动

详见 [阅读笔记](docs/paper/reading_summary/)。

### 中远期：LoRA MOE + 在线 SFT 管线

算力就绪后启动：

- LoRA MOE 路由器 — 按功能域（策略/身份/知识/记忆/通信）热插拔适配器
- 在线 SFT 管线 — 双缓冲 LoRA B 矩阵，异步非阻塞更新
- E5 终局实验 — 热机 LoRA MOE Harness vs 冷启动 ICL Harness，六维评分

---

<p align="center">
  <sub>MIT — 详见 <a href="./LICENSE">LICENSE</a></sub>
  <br/>
  <sub><a href="https://gitee.com/dalaydata/open_deepseek_arf">Gitee</a> &nbsp;·&nbsp; <a href="https://github.com/Wang-hubber/open_deepseek_arf">GitHub</a></sub>
</p>
