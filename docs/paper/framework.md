# Parameter Is All You Need

> Harness 的全新范式
>
> 研究路线图，非完成稿。标注了 ARF 工程支撑与待完成工作。

---

## 论文结构总览

### 1 引言

**1.1 背景：一切皆在上下文**

2017 年，《Attention Is All You Need》将"注意力"确立为序列建模的核心原语。近十年后，Agent 系统的构建似乎陷入了另一种"一切皆在 X"——一切皆在上下文。System Prompt 定义身份，RAG 管线和向量数据库注入知识，memory.md 文件承载长期记忆，自然语言文本充当 Agent 间的通信协议。In-Context Learning（ICL）成为了 Harness 设计者的万能锤子。

部分研究者开始质疑这一默认假设。Hu 等人提出的 LoRA 表明，微调不需要触及全部参数——低秩矩阵足以编码复杂行为。PEAM 和 TMEM 等近期工作在记忆领域进一步验证了"参数化"路线的可行性。如果身份、知识、记忆乃至通信都可以被参数化，那么 Harness 的职责或许需要重新定义。

部分研究者对近年框架演进做过阶段性划分，将 2023–2026 年的发展归纳为三个时期（详见 [Harness 演进调查报告](reading_summary/harness-evolution-survey.md)）：

| 阶段 | 时间 | 标志 | 特征 |
|------|------|------|------|
| 奠基与探索期 | 2023–2024 | LangChain、AutoGPT | 模块化组件（LLM/Prompt/Chain/Tool/Memory），极端自主性探索 |
| 协作与专精期 | 2024–2025 | CrewAI、AutoGen、OpenDevin | 多 Agent 角色分工协同，垂直领域深耕 |
| Harness 系统化 | 2025–2026 | Claude Code、OpenClaw、LangGraph | 系统级运行环境：LLM = CPU，框架 = SDK，Harness = OS |

对现有框架的横向分析揭示了一套趋同的功能集。以下八项能力在 2026 年的主流 Harness 实现中反复出现：

1. 任务生命周期管理与编排引擎 — 动态规划、多 Agent 协同调度、状态机执行
2. 高级工具与技能管理系统 — 声明式注册、标准化调用、CLI-as-Tool
3. 精细化记忆与上下文工程 — 分层记忆、上下文压缩、KV-Cache 优化
4. 企业级安全与治理框架 — 沙盒执行、细粒度权限、DLP、成本管控
5. 全链路可观测性与调试套件 — Trace 追踪、结构化日志、执行回放
6. 系统化评估与基准测试平台 — 多维指标、过程性评分、A/B 比对
7. 高可扩展的插件化与中间件架构 — 统一插件系统、Hook 机制、模型路由
8. 标准化与互操作性接口层 — 通信协议、工具描述标准（OpenAPI）、开放基准

> **本节状态**：框架综述已覆盖 LangChain、AutoGPT、CrewAI、OpenDevin、Claude Code、OpenClaw。待补充：将调查报告中的引用转化为学术格式。

---

### 2 相关工作

| 子方向 | 要点 | 与 ARF 的关联 |
|--------|------|-------------|
| 2.1 Agent 架构 | 综述 ReAct、Plan-Execute、Multi-Agent 范式；指出 Harness 定义模糊 | ARF `ControlPlane` + `LoopStrategy` 为 ReAct 参考实现 |
| 2.2 RAG vs 后训练 | RAG 临时注入 vs LoRA/RLHF 永久固化 | 已有：PEAM、TMEM 参数化记忆方向；[范式迁移报告](reading_summary/control-paradigm-migration.md) 覆盖 P-RAG、MEGa。**待补**：RAG vs Fine-tuning 系统对比综述（如《RAG vs Fine-tuning: Pipelines, Tradeoffs》）、LoRA MoE 路由机制文献 |
| 2.3 记忆与上下文 | 外部记忆（向量 DB、MemGPT）vs 参数化记忆（LoRA 权重增量） | 已有：[参数化记忆笔记](reading_summary/parameterized-memory.md) 覆盖 PEAM/TMEM 两条路径。**待补**：MemGPT、Memotron 等外部记忆系统综述；向量 DB vs 参数化存储的定量对比文献 |
| 2.4 具身智能 | 机器人学的感知编码、状态空间、身体图式 | 对应 §4.1 生物映射中"身体"层的固定感知编码设计 |

> **本节 TODO**：检索各小节 2023–2026 代表性论文；重点确认是否有工作已提出过类似的"大脑-身体"分工或批判 Harness 功能过载。

---

### 3 问题分析：Harness 的角色混淆

**3.1 典型数据流**

> **TODO**：绘制 Agent 架构图，标注 RAG、提示词构造、记忆读取、摘要压缩在 Harness 层的分布位置，标识ICL 注入点。

**3.2 四类误置**

对当前主流 Agent 系统的审视揭示了一种模式：多种本应由模型自身承担的功能，被外置到 Harness 层，通过工程手段"补偿"实现。这一模式在四个维度上均有体现：

| 误置类型 | 现象 | 可能的正确位置 | ARF 已有支撑 |
|---------|------|-------------|-------------|
| **知识误置** | 专业知识通过 RAG 外部注入 | 后训练内化 | 论文论点层面，ARF 尚无直接实验 |
| **身份误置** | 系统提示词临时赋予角色边界 | 模型权重固有自我认知 | `SystemPromptProvider` 已模块化，便于后续替换为 LoRA 方案 |
| **记忆误置** | 上下文压缩与长期记忆由外部模块处理 | 模型内部机制 | `MemoryPlugin` + `CompactionPlugin` 为当前基线；实验一与实验三将探索替代方案 |
| **通信误置** | 多 Agent 间通过自然语言文本串行交互 | 权重空间直接信息交换（TFlow） | `AgentBus` 协议已定义；TFlow 为实验四的理论基础 |

**3.2.1 统一根源：外部信息引入模型的范式选择**

四类误置或许指向同一组根本张力：外部信息——不仅是知识，还包括身份定义、记忆持久化和 Agent 间通信——应当以何种方式引入模型。部分研究者在 PEAM 和 TMEM 等工作中探讨过类似的参数化替代方案。

| 维度 | 当前范式（In-Context Learning） | 参数化范式（Parameterization） |
|------|-------------------------------|------------------------------|
| **知识** | RAG 检索后拼入提示词 | LoRA 适配器编码领域知识，MoE 路由分发 |
| **身份** | System Prompt 注入角色描述 | 后训练将身份边界写入权重 |
| **记忆** | memory.md / 向量库摘要后注入上下文 | 在线 SFT 将记忆写入 LoRA B 矩阵 |
| **通信** | Agent 间 NL 文本串行交互 | 权重空间扰动融合（TFlow） |
| **共性** | 每次推理重新注入，占用上下文窗口 | 一次训练/编译，不占用上下文 |

近期工作提出了 LoRA MoE + 在线 SFT 的技术路线作为可能的统一方案：

```
外部信号（文档/对话/反馈）
  → 在线 SFT 监督信号构造
  → LoRA B 矩阵更新（异步双缓冲，不阻塞推理）
  → MoE Router 根据任务上下文选择激活的 LoRA 适配器
  → 知识/身份/记忆以权重增量形式内化
```

- **LoRA MoE**：每种功能域（领域知识、身份、长期记忆、通信扰动）对应独立 LoRA 适配器。适配器可叠加（`W = W_base + Δ_id + Δ_knowledge + Δ_memory + ΣΔ_comm`）、可替换、可版本管理。
- **在线 SFT**：从 Agent 运行时产出（用户纠错、工具调用成功/失败、记忆提取结果）自动构造训练对，持续更新 LoRA B 矩阵。
- **TFlow 权重通信**：多 Agent 场景下，发送方将内部激活编入临时 LoRA 扰动，接收方在生成时无缝融合。详见 [阅读笔记](reading_summary/control-paradigm-migration.md)。

PEAM（arXiv 2605.27762）验证了跨回合技能固化的可行性，TMEM 验证了会话内在线更新的可行性。两者共享的消融漏洞——参数量不对等、秩 r 未扫参、同步阻塞设计——为后续实验设计提供了切入点。详见 [阅读笔记](reading_summary/parameterized-memory.md)。

> 四类误置与 §4 理论框架的对仗：知识误置对应软线知识维度，身份误置对应软线身份维度，记忆误置对应软线记忆维度，通信误置对应软线通信维度。§4 的"硬线 + 软线"结构即为回应这四类误置而提出的框架方案。

**3.3 根源**

当前实践的深层驱动力或许是一种补偿式工程思维——用 Harness 弥补基座模型不足，而非将模型能力边界作为训练目标来推进。如果 Parameter Is All You Need，那么 Harness 的补偿式膨胀就不再是必要的——它应该退回到脊椎的位置：零认知的硬线保护壳，加上软线的渐进内化通道。

> **本节 TODO**：绘制数据流图（ASCII 或 SVG），标注 ICL 注入点；从现有框架提取 3–5 个典型代码案例。

---

### 4 理论框架：大脑—脊椎—身体分工模型

**4.1 生物学映射**

| 生物结构 | Agent 对应 | 职责边界 | 认知负载 |
|---------|-----------|---------|---------|
| 大脑 | LLM | 条件反射中枢：接收编码感知并输出行为指令 | 全认知 |
| 脑干/脊椎 | Harness 核心 | 固定感知编码、可靠执行、非条件反射 | 零认知 |
| 身体 | 工具生态 | 物理/虚拟效应器 | 零认知 |

> **TODO**：完善生物学映射的严谨性——是否需要对标具体神经通路（如网状结构、γ 运动神经元），需进一步检索神经科学文献。

**4.2 如果 Parameter Is All You Need，Harness 还剩什么？**

将控制信号从上下文迁移至参数——这一命题若成立，Harness 的定位便需要重新审视。部分神经科学文献将脊椎描述为双重功能结构：传导运动指令的下行通路，以及承载感觉信号上行整合的后索通路。借用这一划分，Agent 的 Harness 或许同样由两条并行线构成——一条零认知且永不变更的硬线，另一条承载认知信号渐进内化的软线。Parameter Is All You Need 并不意味着 Harness 消失——它意味着 Harness 的职责从"代替模型思考"转变为"为模型的学习提供载体"。

硬线涵盖安全门控、错误恢复、节律存档、全量追踪、回归测评、Action 执行（`call_model` 与 `execute_tools`）、Hook 挂载和 Agent 编排。这些功能不参与"思考"，但对生存至关重要。

软线承载认知信号的渐进迁移。冷启动阶段以 ICL 作为兜底策略；运行时从交互信号中构造 SFT 监督样本，异步更新对应 LoRA 适配器；当信号收敛到阈值，热切换到参数化模式，释放原先被占用的上下文窗口。

| 维度 | 冷启动 (ICL) | 热机 (LoRA MOE) |
|------|-------------|-----------------|
| 身份 | SystemPrompt 定义角色 | Identity LoRA 固化人格 |
| 知识 | RAG 检索注入上下文 | Knowledge LoRA 参数化知识库 |
| 记忆 | memory.md 外挂摘要 | Memory LoRA 在线 SFT 写入 B 矩阵 |
| 通信 | 自然语言文本串行 | TFlow 权重空间扰动融合 |

这一描述将 Harness 定位为持续"内化"的机器——把身份信号、知识信号、记忆信号、通信信号从上下文空间渐进迁移至参数空间。脊椎不参与推理，但它是学习发生的载体。

ARF 工程实现状态：硬线全部已实现 ✅。软线中 LoRA MOE 路由和在线 SFT 管线为 §5 实验一至四的建设目标。

> **TODO**：检索是否有工作已提出过类似的"硬线 + 软线" Harness 结构定义。

**4.3 大脑的职责：渐进内化的主体**

大脑（LLM）是系统中唯一承载认知的组件。预训练阶段形成"本我"（语言能力与世界观），RLHF 阶段塑造"自我/超我"（价值观与安全边界），在线 LoRA 实现持续自演化——将 Harness 软线构造的 SFT 信号实时写入 FFN 权重。每一轮交互均可能改变模型的行为分布。最终权重构成为 `W_effective = W_base + Δ_identity + Δ_knowledge + Δ_memory + ΣΔ_communication`。

**4.4 设计准则（硬线的四条推论）**

| 准则 | 内容 | ARF 对应 |
|------|------|---------|
| 准则一 | 零认知状态编码 — 固定周期、固定字段、仅格式化 | `SystemPromptProvider` + `$INVENTORY` 模板 |
| 准则二 | 可逆行动执行 — 预演-执行-回滚 | `FunctionBackend.rollback()` + `RoundManager` undo |
| 准则三 | 硬编码安全 — 权限门控、缩手反射、节律存档独立于模型 | `PathCheckToolGuard` + `PermissionRegistry` (deny→ask→allow) |
| 准则四 | 越薄越好 — 随模型能力增强而递减，最终趋于隐形 | Plugin 体系：认知行为通过 Plugin 挂载，非核心引擎逻辑 |

---

### 5 研究路线与实验设计

#### 5.1 实验一：在线 LoRA 长期记忆保持

PEAM 和 TMEM 分别验证了参数化记忆在跨回合与会话内场景中的可行性，但两者的消融设计在参数量对等性和秩 r 扫参方面仍有讨论空间。

| 项 | 内容 |
|----|------|
| **假设** | 参数化记忆（在线 SFT 写入 LoRA B 矩阵）在记忆保持率、更新能力、抗干扰上可能优于外部记忆注入（`memory.md`），且不占用上下文窗口 |
| **数据集** | 公开：LoCoMo（长对话多跳推理）、LongMemEval（105K tokens 压力测试）；自建：DFC（Dynamic Facts & Contradictions，100–200 对话，分阶段注入/更新/冲突/干扰/查询） |
| **测评指标** | QA Accuracy、KUI (Knowledge Update Index)、MFR (Memory Forgetting Rate)、Query/Update Latency |
| **ARF 基线** | `MemoryPlugin` → `memory.md` + `ModelAdapter` |
| **状态** | ⬜ 数据集已确定，待实现 |

#### 5.2 实验二：后训练身份边界鲁棒性

Character-LLM 和 Neeko 等工作表明 LoRA 微调可以在角色扮演任务中取得接近甚至超越 Prompt 方法的一致性。但在对抗攻击场景下，LoRA 固化人格的边界保持能力尚缺乏系统评估。

| 项 | 内容 |
|----|------|
| **假设** | Identity LoRA 固化人格比 System Prompt 更难以被越狱攻击突破，且在身份冲突场景中一致性更高 |
| **数据集** | 公开：JailbreakBench（100+ 有害行为 + 对抗提示库）；自建：Persona Conflict（~50 个身份冲突场景） |
| **测评指标** | ASR (Attack Success Rate, GPT-4 裁判)、ICS (Identity Consistency Score, 1–5)、RQ (Refusal Quality, 1–3) |
| **ARF 基线** | `Guardrails` + `deny_patterns` + `SessionModeManager` |
| **状态** | ⬜ 数据集已确定，待实现 |

#### 5.3 实验三：参数化上下文压缩 vs. 外部摘要

TMEM 的同步阻塞设计可能是一个可优化的工程点——将 B 矩阵更新改为异步双缓冲（当前 B / 后台 B），或许能在保持信息保真度的同时消除更新延迟对推理的干扰。

| 项 | 内容 |
|----|------|
| **假设** | Context LoRA 异步压缩在信息保真度和端到端延迟上可能优于 LLM 摘要 |
| **数据集** | 公开：LongBench Multi-document QA（主测试集）、Single-document QA（辅助） |
| **测评指标** | F1 Score（LongBench 脚本）、Compression Ratio、E2EPL (End-to-End Processing Latency)、AUI (Asynchronous Update Impact) |
| **ARF 基线** | `CompactionPlugin`（token 感知滑动窗口 + LLM 摘要） |
| **状态** | ⬜ 数据集已确定，待实现 |

#### 5.4 实验四：TFlow 权重空间通信 vs. 自然语言通信

TFlow 初步验证了固定数量（3 个）发送方场景下权重空间通信的可行性，但扰动叠加的稳定性、发送方数量扩展后的行为，以及在噪声或延迟网络条件下的鲁棒性，均缺乏系统和理论的分析。

| 项 | 内容 |
|----|------|
| **假设** | 权重空间扰动融合在延迟、带宽占用、可扩展性上可能优于文本通信 |
| **数据集** | 无公开基准。自建 DRSA (Distributed Real-time Situational Awareness) 模拟环境：N 个 Agent（4/8/16/32）监控 2D 网格世界，部分可观察，每 tick 融合全局态势估计 |
| **测评指标** | E2EFL (End-to-End Fusion Latency)、CBU (Communication Bandwidth Usage)、GSA (Global Situational Accuracy)、SI (Scalability Index) |
| **ARF 基线** | `AgentBus` + `PeerAgent` + `ControlPlane.astream()` |
| **状态** | ⬜ 模拟环境待搭建。文献基础：TFlow |

#### 5.5 实验五（终局）：热机 LoRA MOE Harness vs. 传统 ICL Harness

前四个实验各自验证单一维度的参数化迁移效果。实验五检验论文标题的核心命题——当所有四个维度的 LoRA 适配器同时激活时，"Parameter Is All You Need" 是否在综合表现上击败"Context Is All You Need"。

| 项 | 内容 |
|----|------|
| **假设** | 热机 LoRA MOE Harness 在任务完成率、抗越狱、记忆保持、通信效率四个维度上可能显著优于纯 ICL Harness |
| **设计** | 同一 ARF 硬线，两组软线配置：A 组纯 ICL（SystemPrompt + RAG + memory.md + NL 文本通信），B 组热机 LoRA MOE（四个适配器全激活，低上下文）。同任务、同基座，四维评分 |
| **数据集与测评** | *（待实验一至四完成后汇总确定）* |
| **状态** | ⬜ 依赖实验一至四的结论和数据集 |

---

### 6 预期结果 · 讨论 · 结论

*（预留，待实验完成后填充）*

---

## ARF 在论文框架中的位置

```
论文第 1–3 节 (问题引出)  ← ARF 是这些问题驱动的工程产物
论文第 4 节   (理论框架)  ← ARF 提供"硬线+软线"的工程参考实现与四条设计准则
论文第 5 节   (实验设计)  ← ARF 是全部五项实验的统一台架
```

论文与 ARF 的关系或许可以这样表述：**论文是理论论证，ARF 是工程验证**。两者是同一思想在不同语言中的表达。

---

## 近期 TODO

### 高优先级

- [ ] **绘制 §3.1 数据流图**：ASCII 或 SVG 图，标注认知功能在 Harness 层的分布及ICL 注入点
- [ ] **文献检索**：Agent 架构综述 (2024–2026)、RAG vs Fine-tuning 对比、具身智能感知-行动接口。重点关注是否已有工作提出类似"大脑-身体"分工或批判 Harness 过载。检索关键词：`LLM-based Agent survey`、`RAG vs Fine-tuning`、`online LoRA continual learning`、`embodied agent state representation`、`Agent permission control rollback`
- [ ] **论文引言初稿**：README 和简历中已有原材料，可直接转化为学术语言

### 中优先级

- [ ] **第 2 节"相关工作"初稿**：基于文献检索结果填充
- [ ] **`docs/paper/` 持续更新**：每完成一步记录阶段性结果

### 持续

- [ ] **阅读笔记**：每读完一篇关键论文，写一页笔记存入 `reading_summary/`。已完成：PEAM + TMEM → [从 In-Context Learning 到 Weight Updates](reading_summary/parameterized-memory.md)
- [ ] **实验方案细化**：基于 PEAM/TMEM 消融漏洞（容量对齐、r 扫参、同步→异步），设计可发表的对比实验。数据集与测评方案已完成，详见 [experiment-design.md](experiment-design.md)

---

## 参考检索方向

1. **Agent 架构综述** — `LLM-based Agent survey 2024 2025` · 已有：[Harness 演进调查报告](reading_summary/harness-evolution-survey.md)（Metaso, 2026-06）
2. **RAG vs 微调** — 《RAG vs Fine-tuning: Pipelines, Tradeoffs, and a Case Study on Agriculture》等 · **待检索**：系统对比综述，重点关注是否有定量证据支持"参数化在效率上优于 RAG"
3. **参数化记忆** — PEAM (arXiv 2605.27762)、TMEM · ✅ 已阅读，[笔记](reading_summary/parameterized-memory.md)。**待补**：Memorizing Transformer、Unlimiformer、MemGPT 对比
4. **在线/持续学习与 LoRA** — `online LoRA continual learning LLM` · **待检索**：LoRA MoE 路由、多适配器协同、灾难性遗忘缓解
5. **具身智能感知-行动接口** — `embodied agent state representation modality alignment`
6. **Agent 安全与约束** — Agent permission control、rollback 机制
7. **现有 Harness/框架剖析** — LangChain、AutoGPT、OpenDevin 技术报告
8. **Agent 通信与协同** — TFlow 权重空间通信、Multi-Agent orchestration、A2A protocols
9. **角色扮演与身份固化** — Character-LLM、Neeko、RoleLLM、CharLoRA

---

## 阅读笔记索引

| 日期 | 论文 | 笔记 |
|------|------|------|
| 2026-06 | PEAM · TMEM — 参数化记忆的两条路径 | [从 In-Context Learning 到 Weight Updates](reading_summary/parameterized-memory.md) |
| 2026-06 | 2024–2026 AI Agent 框架技术演进与 Harness 全景解析 | [从构建块到操作系统](reading_summary/harness-evolution-survey.md) |
| 2026-06 | 从 ICL 到参数内化：Identity LoRA · Knowledge LoRA · TFlow | [LLM Agent 核心控制范式迁移](reading_summary/control-paradigm-migration.md) |
