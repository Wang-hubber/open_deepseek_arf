# 2.1 Agent 架构：主流范式综述 + Harness "厚度"批判审查

**报告日期：** 2026年6月10日
**面向章节：** 论文 §2.1

---

## 摘要

本报告系统性梳理了2023–2026年间三大主流 Agent 架构范式：**ReAct（推理-行动）**、**Plan-and-Execute（计划-执行）**、**Multi-Agent（多智能体协作）**。核心发现：

1. **主流范式已清晰**：ReAct、Plan-and-Execute 和 Multi-Agent 构成当前 Agent 架构研究的主要脉络
2. **Harness 讨论存在认知缺口**：现有综述倾向于将 Harness 的功能分解到"工具"、"记忆"等模块中，缺乏将其作为独立架构层审视
3. **对 Harness 过载的批判初现端倪**：虽无正式学术论文，但从业者社区对"厚 Harness"承担过多认知负载的批判已很普遍

---

## 1. 三大主流范式

### 1.1 ReAct：推理与行动的动态交织

核心思想：将推理（Reasoning）与行动（Acting）紧密结合，"思考-行动-观察"闭环，模仿人类"边想边做"。

- **优势**：动态适应性、可解释性（每一步有思考轨迹）
- **局限**：频繁 LLM 调用 → 延迟高、API 成本高
- **奠基论文**：Yao et al., "ReAct: Synergizing Reasoning and Acting in Language Models", ICLR 2023

### 1.2 Plan-and-Execute：结构化任务的宏观规划

核心思想：先全局规划再批量执行，减少与 LLM 交互次数。

- **优势**：结构化、高效率，适合目标明确的长链条任务
- **局限**：灵活性差，难以应对执行过程中的意外变化
- **代表工作**：Wang et al., "Plan-and-Solve Prompting", ACL 2023

### 1.3 Multi-Agent：通过协作涌现复杂智能

核心思想：多个专门化 Agent 通过通信协议和协作模式共同解决复杂目标。

- **优势**：可扩展性、处理高阶复杂任务
- **局限**：编排复杂度高（通信设计、冲突解决、资源分配）
- **代表框架**：AutoGen (Wu et al., 2023)、MetaGPT (Hong et al., 2023)

---

## 2. Harness/框架层：被低估的架构维度

### 2.1 Harness 的五大核心功能

| 功能 | 职责 |
|------|------|
| 编排循环 | 驱动任务流程，ReAct 的 Think-Act-Observe 或 Plan-Execute 的执行循环 |
| 工具调用 | 解析 LLM 工具请求 → 执行 → 格式化结果返回 |
| 记忆管理 | 短期（对话历史）和长期（向量DB检索）记忆的读写 |
| 上下文管理 | 动态构建 Prompt（系统指令+任务+记忆+工具输出），控制窗口长度 |
| 安全与成本 | 行为监控、权限门控、Token 追踪 |

### 2.2 现有综述的认知盲区

现有综述将 Harness 功能**碎片化**归入"规划"、"记忆"、"工具使用"等独立模块，导致：

1. **忽视 Harness 的整体性与协同性** — 各组件高度互联，分拆讨论失去对"操作系统"层面设计哲学的理解
2. **模糊模型与框架的责任边界** — 无法清晰界定哪些认知由 LLM 完成，哪些由框架代理
3. **阻碍跨框架深度比较** — 无法讨论 Harness 的"厚度"差异（LangChain vs LangGraph vs Claude Code 的架构哲学差异）

**关键 Gap：缺乏将 Harness 作为第一性架构维度的系统性分析。**

---

## 3. "厚 Harness"批判

### 3.1 "大模型 vs 大框架"的张力

- **大框架路线**（2023-2024 主流）：LLM 是认知引擎但缺乏结构化能力，需要复杂外骨骼（状态跟踪、错误处理、工具调度）
- **大模型路线**（2025+）：模型能力指数增长（百万 Token 窗口、原生工具调用）→ 认知负载应推回模型内部，Harness 应变薄

### 3.2 功能过载的四个批判维度

1. **设计假设问题**：Anthropic 工程师指出 "Harness 里的每个组件，都在假设'模型自己做不到这个'"——建立在模型不信任假设上的设计
2. **过度工程化与脆弱性**：大量胶水代码、规则驱动的复杂逻辑 → 难以适应任务变化、难以调试
3. **掩盖模型真实能力**：厚 Harness 可能"代劳"模型 → 难以区分是模型强还是框架工程强
4. **技术债务累积**：基于旧模型局限性设计的 Harness（如 8K 窗口的记忆压缩模块）在 1M 上下文窗口模型面前成为性能瓶颈

### 3.3 结论

对 Harness 过载的批判**存在**但非正式（从业者社区、技术博客），尚未被整理为学术语言。架构方向：从"框架驱动的认知"→ "模型驱动的认知"。

---

## 4. 推荐引用列表

- Yao et al., "ReAct: Synergizing Reasoning and Acting in Language Models", ICLR 2023
- Wang et al., "Plan-and-Solve Prompting", ACL 2023
- Wu et al., "AutoGen: Enabling Next-Gen LLM Applications via Multi-Agent Conversation", arXiv 2308.08155
- Hong et al., "MetaGPT: Meta Programming for Multi-Agent Collaborative Framework", arXiv 2308.00352
- Shinn et al., "Reflexion: Language Agents with Verbal Reinforcement Learning", NeurIPS 2024
- Xi et al., "The Rise and Potential of Large Language Model Based Agents: A Survey", arXiv 2309.07864
- "Architectural Design Decisions in AI Agent Harnesses", arXiv 2604.18071

---

## 5. 推荐组织结构

```
§2.1 Agent 架构
  §2.1.1 主流范式演进
    (a) ReAct — Yao 2023
    (b) Plan-and-Execute — Wang 2023
    (c) Multi-Agent — AutoGen, MetaGPT
  §2.1.2 隐形维度：Harness/框架层
    (a) Harness 定义与五大功能
    (b) 现有综述的认知盲区（功能碎片化）
  §2.1.3 "厚 Harness"批判
    (a) "大模型 vs 大框架"辩论
    (b) 功能过载的四维证据
    (c) 向"薄 Harness"演进
  §2.1.4 小结
```
