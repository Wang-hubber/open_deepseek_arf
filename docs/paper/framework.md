# 论文框架：寻找 Agent 系统的脊椎

> 大模型与 Harness 的严格分工与协同进化
>
> 这是研究路线图，不是已完成的论文。每一节标注了 ARF 已有工程支撑，以及近期待做的事。

---

## 论文结构总览

### 1 引言

**1.1 背景：从语言模型到 Agent**
- LLM 能力跃升 → Agent 范式兴起
- 两个落地领域：电脑世界（CLI/编程）· 真实世界（VLA）
- 两种范式：垂直专精 · 全域通用
- Harness 成为实际部署的必需组件

**Agent 框架的三阶段演进**（详见 [Harness演进调查报告](reading_summary/harness-evolution-survey.md)）：

| 阶段 | 时间 | 标志 | 特征 |
|------|------|------|------|
| 奠基与探索期 | 2023-2024 | LangChain、AutoGPT | 模块化组件（LLM/Prompt/Chain/Tool/Memory）+ 自主性极限探索 |
| 协作与专精期 | 2024-2025 | CrewAI、AutoGen、OpenDevin | 多Agent角色分工协同 + 垂直领域深耕 |
| Harness系统化 | 2025-2026 | Claude Code、OpenClaw、LangGraph | 系统级运行环境：LLM=CPU，框架=SDK，Harness=OS |

**Harness 八大核心功能**（行业共识，2026）：

1. 任务生命周期管理与编排引擎 — 动态规划、多Agent协同调度、状态机执行
2. 高级工具与技能管理系统 — 声明式注册、标准化调用、CLI-as-Tool
3. 精细化记忆与上下文工程 — 分层记忆、上下文压缩、KV-Cache优化
4. 企业级安全与治理框架 — 沙盒执行、细粒度权限、DLP、成本管控
5. 全链路可观测性与调试套件 — Trace追踪、结构化日志、执行回放
6. 系统化评估与基准测试平台 — 多维指标、过程性评分、A/B比对
7. 高可扩展的插件化与中间件架构 — 统一插件系统、Hook机制、模型路由
8. 标准化与互操作性接口层 — 通信协议、工具描述标准(OpenAPI)、开放基准

> **本节已基本完成**。框架综述已覆盖 LangChain/AutoGPT/CrewAI/OpenDevin/Claude Code/OpenClaw；八大功能列表已提取。待补充：将调查报告中的引用转化为学术格式。

---

### 2 相关工作

| 子方向 | 要点 | ARF 关联 |
|--------|------|---------|
| 2.1 Agent 架构 | 综述 ReAct、Plan-Execute、Multi-Agent 范式；指出 Harness 定义模糊 | ARF 的 `GraphEngine` + `LoopStrategy` 是 ReAct 参考实现 |
| 2.2 RAG vs 后训练 | RAG 临时注入 vs LoRA/RLHF 永久固化 | 对应论文核心论点：知识应内化到模型，而非 Harness 打补丁。已有 PEAM、TMEM 等参数化记忆工作提供实证支撑 |
| 2.3 记忆与上下文 | ... | ARF `MemoryPlugin` 是当前外部方案；实验一/三将探索 LoRA 参数化替代 |
| 2.4 具身智能 | 机器人学的感知编码、状态空间、身体图式 | 直接对应三层机械层的“固定感知编码器”设计 |

> **本节 TODO**：检索各小节 2023-2026 代表性论文；重点找是否有工作提出过类似“大脑-身体”分工

---

### 3 问题分析：Harness 的角色混淆

**3.1 典型数据流**：画 Agent 架构图，标注 RAG/提示词构造/记忆读取/摘要压缩 均在 Harness 层

**3.2 四类误置**

| 误置类型 | 现象 | 应然 | ARF 已有支撑 |
|---------|------|------|-------------|
| **知识误置** | 专业知识通过 RAG 外部注入 | 后训练内化 | 论文论点层面，ARF 尚无直接实验 |
| **身份误置** | 系统提示词临时赋予角色边界 | 模型权重固有自我认知 | `SystemPromptProvider` 当前仍用 prompt，但已模块化，方便后续替换 |
| **记忆误置** | ... | `MemoryPlugin` + `CompactionPlugin` 是当前基线，实验一/三将探索替代方案 |
| **通信误置** | ... | `AgentBus` 协议已定义；TFlow 作为实验四的理论基础 |

**3.2.1 四类误置的统一根源：外部知识管理的范式革命**

四类误置指向同一个根本问题：**外部信息如何管理和引入模型**——不仅是知识，还包括身份定义、记忆持久化和 Agent 间通信。

| 维度 | 当前范式（In-Context Learning） | 目标范式（Parameterization） |
|------|-------------------------------|---------------------------|
| **知识** | RAG 检索 → 拼入提示词 | LoRA 适配器编码领域知识，MoE 路由分发 |
| **身份** | System Prompt 注入角色描述 | 后训练将身份边界写入权重（"自我"在模型中而非 prompt 中） |
| **记忆** | memory.md / 向量库 → 摘要后注入上下文 | 在线 SFT 将记忆写入 LoRA B 矩阵，释放上下文窗口 |
| **通信** | Agent 间自然语言文本串行交互 → 上下文膨胀、延迟高 | 权重空间扰动融合（TFlow）：`W + ΔW_sender1 + ΔW_sender2`，O(1) 融合，无上下文开销 |
| **共性缺陷** | 每次推理都需重新注入，占用上下文窗口 | 一次训练/编译，永久内化/实例级融合，不占用上下文 |

**LoRA MoE + 在线 SFT 的技术路线**：

```
外部信号（文档/对话/反馈）
  → 在线 SFT 监督信号构造
  → LoRA B 矩阵更新（异步双缓冲，不阻塞推理）
  → MoE Router 根据任务上下文选择激活的 LoRA 适配器
  → 知识/身份/记忆以权重增量的形式内化，无需显式注入 prompt
```

- **LoRA MoE**：每种功能域（领域知识 / 身份 / 长期记忆 / 通信扰动）对应独立 LoRA 适配器，MoE Router 按需激活。适配器可叠加（`W = W_base + Δ_id + Δ_knowledge + Δ_memory + ΣΔ_comm`）、可替换、可版本管理
- **在线 SFT**：从 Agent 运行时产出（用户纠错、工具调用成功/失败、记忆提取结果）自动构造训练对，持续更新 LoRA B 矩阵
- **TFlow 权重通信**：多 Agent 协同场景下，发送方将内部激活编入临时 LoRA 扰动，接收方在生成时无缝融合，实现高带宽、低延迟的并行信息交换。详见 [阅读笔记](reading_summary/control-paradigm-migration.md)

> 这直接对应论文实验一（在线 LoRA 长期记忆保持）、实验二（后训练身份边界鲁棒性）、实验三（参数化上下文压缩）。三个实验共享同一基础架构——LoRA MoE + 在线 SFT——只是在不同的信号源和评估维度上展开。
>
> 文献基础：PEAM（arXiv 2605.27762）验证了跨回合技能固化的可行性；TMEM 验证了会话内在线更新的可行性。两者的共同消融漏洞（参数量不对等、r 未扫参、同步阻塞）正是本论文实验设计的切入点。详见 [阅读笔记](reading_summary/parameterized-memory.md)。

**3.3 根源**：补偿式工程思维——用 Harness 弥补基座模型不足，而非将其作为训练目标

> **本节 TODO**：绘制数据流图（或 ASCII 架构图，标注认知泄漏点）；从现有框架提取 3-5 个典型代码案例

---

### 4 理论框架：大脑-脊椎-身体分工模型

**4.1 生物学映射**

| 生物 | Agent | 职责 | 认知负载 |
|------|-------|------|---------|
| 大脑 | LLM | 条件反射中枢，接收编码感知 → 输出行为指令 | 全认知 |
| 脑干/脊椎 | Harness 核心 | 固定感知编码 + 可靠执行 + 非条件反射 | 零认知 |
| 身体 | 工具生态 | 物理/虚拟效应器 | 零认知 |

**4.2 大脑的职责**：后训练形成内模型（预训练“本我” → RLHF“自我/超我”）· 长上下文压缩 · 在线 LoRA 自演化

**4.3 Harness 的定义：硬线 + 软线** ← **ARF 的核心贡献**

Harness 是 Agent 的脊椎——两条并行线，一条零认知，一条承载认知信号的渐进内化。

**硬线（零认知，框架负责，永不变更）**

```
控制平面 (ControlPlane)
├── 安全门控     → PathCheckToolGuard · PermissionRegistry · deny→ask→allow
├── 错误恢复     → ErrorHandlerPlugin · SessionAbortedError
├── 节律存档     → RoundManager · state_store.put() · checkpoint
├── 全量追踪     → EventBus → FileTraceStore (JSONL)
├── 回归测评     → EvalRunner · BenchmarkBuilder · trace replay
│
├── Action 执行  → _execute_action(step)
│   ├── call_model      → ModelAdapter → 推理
│   └── execute_tools   → ConcurrentToolExecutor → 沙箱执行
│
├── Hook 挂载点  → 9 个生命周期事件
│
└── Agent 编排   → LoopStrategy · AgentBus (预留)
```

**软线（认知信号，渐进从 ICL 迁移到 LoRA MOE）**

| 维度 | 冷启动 (ICL) | 热机 (LoRA MOE) |
|------|-------------|-----------------|
| 身份 | SystemPrompt 定义角色 | Identity LoRA 固化人格 |
| 知识 | RAG 检索注入上下文 | Knowledge LoRA 参数化知识库 |
| 记忆 | memory.md 外挂摘要 | Memory LoRA 在线 SFT 写入 B 矩阵 |
| 通信 | 自然语言文本串行 | TFlow 权重空间扰动融合 |

Harness 的核心职责：冷启动时用 ICL 兜底，运行时接收交互信号，逐步构造 SFT 监督样本，异步更新对应 LoRA 适配器，完成后热切换到参数化模式。**Harness 是一个持续"内化"的机器**——把运行时产出的身份信号、知识信号、记忆信号、通信信号从上下文空间逐步迁移到参数空间。脊椎不思考，但它是大脑学习的载体。

**ARF 实现状态**：硬线全部已实现 ✅。软线的 LoRA MOE 路由和在线 SFT 管线是实验一到四的建设目标。 |

**4.4 协同进化原则**：Harness 状态 schema 必须作为模型后训练的原生感知语言

**4.5 控制平面与执行平面**：整合参数化范式的统一架构

| 平面 | 职责 | 组成 |
|------|------|------|
| **执行平面** | 大量同构、无状态的基础模型实例（GPU 资源池） | `W_base` — "裸模型"，Agent 的通用大脑基底 |
| **控制平面** | LoRA 仓库管理、任务路由、动态绑定、通信编排 | `ControlPlane` — ARF 的核心调度引擎 |

Agent 不再是一个静态程序，而是按需动态组合 **基础智能（Base Model）** + **人格（Identity LoRA）** + **技能（Knowledge LoRA）** + **记忆（Memory LoRA）** + **沟通方式（TFlow Perturbation）** 的弹性实体。这与 ARF 的命名和架构设计完全对齐：`ControlPlane` 就是控制平面的工程实现，`ModelAdapter` + LoRA 热插拔是执行平面的核心机制。

详见 [范式迁移研究报告](reading_summary/control-paradigm-migration.md) 第 3 节。

> **本节 TODO**：完善生物学映射的严谨性（是否需要对标具体神经通路）；为 4.4 寻找相关文献支撑

---

### 5 理想 Harness 的设计准则

| 准则 | 内容 | ARF 对应 |
|------|------|---------|
| 准则一 | 零认知状态编码 — 固定周期、固定字段、仅格式化 | `SystemPromptProvider` + `$INVENTORY` 模板 |
| 准则二 | 可逆行动执行 — 预演-执行-回滚 | `FunctionBackend.rollback()` + `RoundManager` undo |
| 准则三 | 硬编码安全 — 权限门控、缩手反射、节律存档独立于模型 | `PathCheckToolGuard` + `PermissionRegistry` deny→ask→allow |
| 准则四 | 越薄越好 — 随模型能力增强而递减，最终趋于隐形 | Plugin 体系：认知行为通过 Plugin 挂载（调用模型），非核心引擎逻辑 |

> **本节已基本完整**，ARF 工程实现了全部四条准则。待补充：与其他框架的横向对比数据。

---

### 6 研究路线与实验设计

#### 6.1 实验一：在线 LoRA 长期记忆保持

| 项 | 内容 |
|----|------|
| **假设** | 参数化记忆（在线 SFT 写入 LoRA B 矩阵）在记忆保持率、更新能力、抗干扰上均优于外部记忆注入（`memory.md`），且不占用上下文窗口 |
| **动机** | PEAM（跨回合技能固化）和 TMEM（会话内在线更新）分别验证了可行性。消融漏洞（参数量不对等、r 未扫参、同步阻塞）是本实验切入点 |
| **数据集** | 公开：LoCoMo（长对话多跳推理）、LongMemEval（105K tokens 压力测试）；自建：DFC（Dynamic Facts & Contradictions，100-200 对话，分阶段注入/更新/冲突/干扰/查询） |
| **测评** | QA Accuracy、KUI (Knowledge Update Index)、MFR (Memory Forgetting Rate)、Query/Update Latency |
| **ARF 已有** | `MemoryPlugin` → `memory.md` + `ModelAdapter` |
| **状态** | ⬜ 数据集已确定，待实现 |

#### 6.2 实验二：后训练身份边界鲁棒性

| 项 | 内容 |
|----|------|
| **假设** | Identity LoRA 固化人格比 System Prompt 更难被越狱攻击突破，且在身份冲突场景中一致性更高 |
| **数据集** | 公开：JailbreakBench（100+ 有害行为 + 对抗提示库，角色扮演攻击与实验直接相关）；自建：Persona Conflict（~50 个身份冲突场景，核心身份 vs 冲突性请求） |
| **测评** | ASR (Attack Success Rate, GPT-4 裁判)、ICS (Identity Consistency Score, 1-5)、RQ (Refusal Quality, 1-3) |
| **ARF 已有** | `Guardrails` + `deny_patterns` + `SessionModeManager` |
| **状态** | ⬜ 数据集已确定，待实现 |

#### 6.3 实验三：参数化上下文压缩 vs. 外部摘要

| 项 | 内容 |
|----|------|
| **假设** | Context LoRA 异步压缩在信息保真度和端到端延迟上均优于 LLM 摘要 |
| **动机** | TMEM 同步阻塞设计可改为异步双缓冲（当前 B / 后台 B），消除更新延迟对推理的干扰 |
| **数据集** | 公开：LongBench Multi-document QA（主测试集）、Single-document QA（辅助）。F1/ROUGE 指标成熟 |
| **测评** | F1 Score（LongBench 脚本）、Compression Ratio、E2EPL (End-to-End Processing Latency)、AUI (Asynchronous Update Impact) |
| **ARF 已有** | `CompactionPlugin`（token 感知滑动窗口 + LLM 摘要） |
| **状态** | ⬜ 数据集已确定，待实现 |

#### 6.4 实验四：TFlow 权重空间通信 vs. 自然语言通信

| 项 | 内容 |
|----|------|
| **假设** | 权重空间扰动融合在延迟、带宽占用、可扩展性上均优于文本通信 |
| **动机** | TFlow 仅验证了固定数量（3个）发送方；扰动叠加稳定性 + 异步聚合缺乏分析 |
| **数据集** | 无公开基准。自建 DRSA (Distributed Real-time Situational Awareness) 模拟环境：N 个 Agent（4/8/16/32）监控 2D 网格世界，部分可观察，每 tick 融合全局态势估计 |
| **测评** | E2EFL (End-to-End Fusion Latency)、CBU (Communication Bandwidth Usage)、GSA (Global Situational Accuracy)、SI (Scalability Index) |
| **ARF 已有** | `AgentBus` + `PeerAgent` + `ControlPlane.astream()` |
| **状态** | ⬜ 模拟环境待搭建。文献基础：TFlow |

#### 6.5 实验五（终局）：热机 LoRA MOE Harness vs. 传统 ICL Harness

| 项 | 内容 |
|----|------|
| **假设** | 热机后的 LoRA MOE Harness（Identity + Knowledge + Memory + Comm LoRA 全部激活）在任务完成率、抗越狱、记忆保持、通信效率四个维度上均显著优于传统纯 ICL Harness |
| **设计** | 同一套 ARF 硬线（硬线不变），对比两种软线配置——A 组纯 ICL（SystemPrompt + RAG + memory.md + NL 文本通信），B 组热机 LoRA MOE（四个 LoRA 适配器全部激活，低上下文）。同一任务、同一基座模型，2×2 横跨四个维度评分 |
| **数据集与测评** | *（待实验一至四完成后汇总）* |
| **状态** | ⬜ 依赖实验一至四的结论和数据集 |

---

### 7-8 预期结果 · 讨论 · 结论

*（预留，待实验完成后填充）*

---

## ARF 在论文框架中的位置

```
论文第 1-3 节 (问题引出)  ← ARF 是这些问题驱动的产物
论文第 4 节   (理论框架)  ← ARF 硬线 + 软线直接实现 4.3
论文第 5 节   (设计准则)  ← ARF 硬线体系实现全部四条准则
论文第 6 节   (实验设计)  ← 实验一至四验证各维度参数化迁移，实验五为终局对比
```

ARF 不是论文的附属品——**论文是 ARF 的理论论证，ARF 是论文的工程验证**。两者是同一思想的两种语言。

---

## 近期 TODO（按优先级）

### 高优先级（本月可启动）

- [ ] **绘制 3.1 数据流图**：一张 ASCII 或 SVG 图，标出典型 Agent 系统中认知功能在 Harness 层的分布，标注“认知泄漏点”
- [ ] **文献检索**：Agent 架构综述 (2024-2026) + RAG vs Fine-tuning 对比 + 具身智能感知-行动接口
  - 重点关注：是否有工作已提出类似“大脑-身体”分工、或批判过 Harness 过载
  - 检索关键词：`LLM-based Agent survey`、`RAG vs Fine-tuning`、`online LoRA continual learning`、`embodied agent state representation`、`Agent permission control rollback`
- [ ] **论文引言 (1.1-1.3) 初稿**：README 和简历中已有原材料，可直接转化为学术语言

### 中优先级（下月）

- [ ] **第 2 节“相关工作”初稿**：基于文献检索结果
- [ ] **`docs/paper/` 持续更新**：每完成一步在此目录记录阶段性结果

### 持续

- [ ] **阅读笔记**：每读完一篇关键论文，写一页笔记存入 `docs/paper/reading_summary/`
  - 已完成：PEAM + TMEM → [从 In-Context Learning 到 Weight Updates](reading_summary/parameterized-memory.md)
- [ ] **实验方案细化**：基于 PEAM/TMEM 的消融漏洞（容量对齐、r 扫参、同步→异步），设计可发表的对比实验。数据集与测评方案已完成，详见 [experiment-design.md](experiment-design.md)

---

## 参考检索方向

1. **Agent 架构综述** — `LLM-based Agent survey 2024 2025` · 已有：[Harness演进调查报告](reading_summary/harness-evolution-survey.md)（Metaso, 2026-06）
2. **RAG vs 微调** — 《RAG vs Fine-tuning: Pipelines, Tradeoffs, and a Case Study on Agriculture》等
3. **参数化记忆** — PEAM (arXiv 2605.27762)、TMEM、Memorizing Transformer、Unlimiformer
4. **在线/持续学习与 LoRA** — `online LoRA continual learning LLM`
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
| 2026-06 | 2024-2026 AI Agent框架技术演进与Harness全景解析 | [从构建块到操作系统](reading_summary/harness-evolution-survey.md) |
| 2026-06 | 从ICL到参数内化：Identity LoRA · Knowledge LoRA · TFlow | [LLM Agent 核心控制范式迁移](reading_summary/control-paradigm-migration.md) |
