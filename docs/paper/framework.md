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

**1.2 问题的浮现：不断膨胀的 Harness**
- 观察：Harness 承担了 RAG、系统提示词身份注入、上下文摘要、外部记忆管理等认知性功能
- 判断：Harness 本应是轻量信息传递层，却变成补丁集合 → 角色混淆

**1.3 核心命题**
- 分工隐喻：**大模型 = 大脑，Harness = 脑干、脊椎与身体**
- 目标：澄清边界 + 指明认知功能回归模型侧的路径 + 定义理想 Harness 设计准则 + 给出验证路线

> **本节 TODO**：查近两年 Agent 框架综述（LangChain、AutoGPT、CrewAI、OpenDevin）+ 提取 Harness 典型功能列表

---

### 2 相关工作

| 子方向 | 要点 | ARF 关联 |
|--------|------|---------|
| 2.1 Agent 架构 | 综述 ReAct、Plan-Execute、Multi-Agent 范式；指出 Harness 定义模糊 | ARF 的 `GraphEngine` + `LoopStrategy` 是 ReAct 参考实现 |
| 2.2 RAG vs 后训练 | RAG 临时注入 vs LoRA/RLHF 永久固化 | 对应论文核心论点：知识应内化到模型，而非 Harness 打补丁。已有 PEAM、TMEM 等参数化记忆工作提供实证支撑 |
| 2.3 记忆与上下文 | 外部记忆（向量DB、MemGPT）vs 参数化记忆（LoRA 权重增量）。范式迁移：从 In-Context Learning → Weight Updates。详见 [阅读笔记](reading_summary/parameterized-memory.md) | ARF `MemoryPlugin` 是当前外部方案；实验三/五将探索 LoRA 参数化替代 |
| 2.4 具身智能 | 机器人学的感知编码、状态空间、身体图式 | 直接对应三层机械层的“固定感知编码器”设计 |

> **本节 TODO**：检索各小节 2023-2026 代表性论文；重点找是否有工作提出过类似“大脑-身体”分工

---

### 3 问题分析：Harness 的角色混淆

**3.1 典型数据流**：画 Agent 架构图，标注 RAG/提示词构造/记忆读取/摘要压缩 均在 Harness 层

**3.2 三类误置**

| 误置类型 | 现象 | 应然 | ARF 已有支撑 |
|---------|------|------|-------------|
| **知识误置** | 专业知识通过 RAG 外部注入 | 后训练内化 | 论文论点层面，ARF 尚无直接实验 |
| **身份误置** | 系统提示词临时赋予角色边界 | 模型权重固有自我认知 | `SystemPromptProvider` 当前仍用 prompt，但已模块化，方便后续替换 |
| **记忆误置** | 上下文压缩与长期记忆由外部模块处理 | 模型内部机制 | `MemoryPlugin` + `CompactionPlugin` 是当前基线，实验三/五将探索替代方案 |

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

**4.3 Harness 的职责：三层机械层** ← **ARF 的核心工程贡献**

| 层 | 功能 | ARF 实现 | 状态 |
|----|------|---------|------|
| L1 固定感知编码器 | 多源信息时间对齐，固定 schema StatePacket，不追问不澄清 | `SystemPromptProvider` + `ResourceResolver` + `FileWatcher` | ✅ 已实现 |
| L2 可靠行动执行器 | 解析函数调用，执行并收集反馈，预演-执行-回滚 | `SandboxManager` + `ConcurrentToolExecutor` + `FunctionBackend.rollback()` + `SkillPipeline` | ✅ 已实现 |
| L3 非条件反射层 | 硬编码安全门控、损伤回滚、节律存档，模型不可绕过 | `PathCheckToolGuard` + `PermissionRegistry` + `RoundManager` + Cancel Token | ✅ 已实现 |

**4.4 协同进化原则**：Harness 状态 schema 必须作为模型后训练的原生感知语言

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

#### 6.1 实验一：固定状态接口 vs. 传统提示 — 任务稳定性对比

| 项 | 内容 |
|----|------|
| **假设** | 结构化 StatePacket 比自由文本提示在相同任务上稳定性更高（方差更小） |
| **ARF 已有** | `SystemPromptProvider` 固定模板 + `$INVENTORY` 结构化上下文 |
| **待建设** | 对比基准：同一任务 × { ARF StatePacket, 传统自由文本提示 }，测量多次运行的任务完成稳定性 |
| **候选基准** | AgentBench、SWE-bench |
| **状态** | ⬜ 未开始 |

#### 6.2 实验二：零认知 Harness 原型 — 性能基准

| 项 | 内容 |
|----|------|
| **假设** | 零认知 Harness 在代码量、认知泄漏点数量上显著优于现有框架 |
| **ARF 已有** | 完整 6 骨架 + 三层机械层，端到端 Agent Loop 可运行 |
| **待建设** | 横向对比 ARF vs LangChain vs OpenDevin；指标：代码量、任务完成率、认知泄漏点（模型调用外执行语义解释的模块） |
| **状态** | ⬜ 未开始 |

#### 6.3 实验三：在线 LoRA 用于长期记忆保持

| 项 | 内容 |
|----|------|
| **假设** | 参数化记忆（LoRA 权重增量）的记忆保持质量优于外部记忆注入（`memory.md`），且不占用上下文窗口 |
| **动机** | PEAM（跨回合技能固化）和 TMEM（会话内在线更新）分别验证了参数化记忆的有效性。两篇论文均存在消融漏洞（参数量不对等、r 未扫参）——这正是本实验的切入点 |
| **ARF 已有** | `MemoryPlugin` → `memory.md` + `ModelAdapter` 抽象 + `FileMemoryStore` |
| **待建设** | `ModelAdapter` 接入 LoRA B 矩阵更新接口；用 `memory.md` 条目作为监督信号；扫 r=1,2,4,6,8 找性价比最优；控制总计算开销一致，对比参数化 vs 外挂摘要 |
| **状态** | ⬜ 未开始。文献基础：PEAM (arXiv 2605.27762)、TMEM，详见 [阅读笔记](reading_summary/parameterized-memory.md) |

#### 6.4 实验四：后训练身份边界鲁棒性

| 项 | 内容 |
|----|------|
| **假设** | 后训练固化的身份边界比系统提示词赋予的身份更难被越狱攻击突破 |
| **ARF 已有** | `Guardrails` + `deny_patterns` + `SessionModeManager` |
| **待建设** | 扩展 guardrails 做对抗测试；构建越狱基准；测量身份边界保持率 |
| **状态** | ⬜ 未开始 |

#### 6.5 实验五：参数化上下文压缩 vs. 外部摘要

| 项 | 内容 |
|----|------|
| **假设** | 将上下文关键信息在线写入 LoRA 权重（参数化压缩），保真度优于外部 LLM 摘要，且释放上下文窗口 |
| **动机** | 传统摘要是 token-level 有损压缩，每次需重新计算 KV。PEAM 和 TMEM 证明关键信息可压入 FFN 权重，实现真正的"内化"。TMEM 的同步阻塞设计可改为异步双缓冲 |
| **ARF 已有** | `CompactionPlugin`（token 感知滑动窗口 + LLM 摘要） |
| **待建设** | 在 `CompactionPlugin` 中集成 LoRA B 矩阵在线更新；设计异步双缓冲（当前 B / 后台 B）避免阻塞推理；对比保真度 vs 当前 LLM 摘要；扫 r 找最优性价比 |
| **状态** | ⬜ 未开始。文献基础：同实验三，详见 [阅读笔记](reading_summary/parameterized-memory.md) |

> **本节 TODO**：为每个实验方向找基线方法和评估基准；确认是否有团队做过类似实验

---

### 7-8 预期结果 · 讨论 · 结论

*（预留，待实验完成后填充）*

---

## ARF 在论文框架中的位置

```
论文第 1-3 节 (问题引出)  ← ARF 是这些问题驱动的产物
论文第 4 节   (理论框架)  ← ARF 三层机械层直接实现 4.3
论文第 5 节   (设计准则)  ← ARF 6 骨架 + Plugin 体系实现全部四条准则
论文第 6 节   (实验设计)  ← ARF 是全部五项实验的统一台架
```

ARF 不是论文的附属品——**论文是 ARF 的理论论证，ARF 是论文的工程验证**。两者是同一思想的两种语言。

---

## 近期 TODO（按优先级）

### 高优先级（本月可启动）

- [ ] **绘制 3.1 数据流图**：一张 ASCII 或 SVG 图，标出典型 Agent 系统中认知功能在 Harness 层的分布，标注“认知泄漏点”
- [ ] **文献检索**：Agent 架构综述 (2024-2026) + RAG vs Fine-tuning 对比 + 具身智能感知-行动接口
  - 重点关注：是否有工作已提出类似“大脑-身体”分工、或批判过 Harness 过载
  - 检索关键词：`LLM-based Agent survey`、`RAG vs Fine-tuning`、`online LoRA continual learning`、`embodied agent state representation`、`Agent permission control rollback`
- [ ] **实验一方案细化**：选定基准（AgentBench 或 SWE-bench），设计对比实验方案，写实验脚本
- [ ] **论文引言 (1.1-1.3) 初稿**：README 和简历中已有原材料，可直接转化为学术语言

### 中优先级（下月）

- [ ] **第 2 节“相关工作”初稿**：基于文献检索结果
- [ ] **实验二环境搭建**：搭建 LangChain/OpenDevin 对比环境，定义认知泄漏点检测方法
- [ ] **`docs/paper/` 持续更新**：每完成一步在此目录记录阶段性结果

### 持续

- [ ] **阅读笔记**：每读完一篇关键论文，写一页笔记存入 `docs/paper/reading_summary/`
  - 已完成：PEAM + TMEM → [从 In-Context Learning 到 Weight Updates](reading_summary/parameterized-memory.md)
- [ ] **实验三/五方案细化**：基于 PEAM/TMEM 的消融漏洞（容量对齐、r 扫参、同步→异步），设计可发表的对比实验

---

## 参考检索方向

1. **Agent 架构综述** — `LLM-based Agent survey 2024 2025`
2. **RAG vs 微调** — 《RAG vs Fine-tuning: Pipelines, Tradeoffs, and a Case Study on Agriculture》等
3. **参数化记忆** — PEAM (arXiv 2605.27762)、TMEM、Memorizing Transformer、Unlimiformer
4. **在线/持续学习与 LoRA** — `online LoRA continual learning LLM`
5. **具身智能感知-行动接口** — `embodied agent state representation modality alignment`
6. **Agent 安全与约束** — Agent permission control、rollback 机制
7. **现有 Harness/框架剖析** — LangChain、AutoGPT、OpenDevin 技术报告

---

## 阅读笔记索引

| 日期 | 论文 | 笔记 |
|------|------|------|
| 2026-06 | PEAM · TMEM — 参数化记忆的两条路径 | [从 In-Context Learning 到 Weight Updates](reading_summary/parameterized-memory.md) |
