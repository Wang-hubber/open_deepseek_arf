# 王协

**AI Agent 架构师 · 开源框架 ARF 作者 · Harness 工程师**

19156045942 · 997793170@qq.com · 32岁 · 9年+工程经验 · 吉林大学车辆工程本科

---

## 我在做什么：寻找 Agent 系统的脊椎

这几年做 Agent 应用，我发现一个普遍现象——**Harness 在膨胀**。RAG 知识注入、系统提示词身份赋予、上下文摘要、外部记忆管理……这些本该由模型承载的认知功能，正一块块往协调层里塞。每次模型能力不够，就在外面打一个补丁。

这不是哪个框架的问题。它指向一个更底层的疑问：**Harness 到底该做什么，不该做什么？**

我目前的假设是：**大模型是大脑，Harness 是脑干、脊椎与身体。** 借用生物学的分工来重新划定边界——

- 大脑负责条件反射——接收编码后的感知，输出行为指令。知识、身份、长期记忆的固化，应该是模型后训练要解决的事，不该在 Harness 里用 prompt 和 RAG 凑合。
- Harness 应该收敛为**零认知机械层**——固定感知编码、可靠行动执行、硬连线安全反射。它不思考，不追问，不澄清。它只是机械地编码、执行、门控。

这不是一个观点——它是一个**可验证的假设**。而我的计划很明确：把它做出来，然后做实验去验证。

为此我写了 ARF 框架来搭"台架"，写了论文框架来理清思路，写了 7 单元教学课程来让这个思路对其他人也可理解、可复现。

接下来是实验——固定状态接口 vs 传统提示、零认知 Harness 基准测试、在线 LoRA 记忆保持、后训练身份鲁棒性、内部上下文压缩。每一项都在这个台架上跑。详细的研究框架、ARF 映射关系和近期 TODO → https://gitee.com/dalaydata/open_deepseek_arf/blob/main/docs/paper/framework.md。

**我想做 Harness 工程师。** 不是某个 Agent 应用的业务逻辑，而是所有 Agent 共享的信息基础设施——把它做薄，而不是做厚。

---

## 开源作品（2025.05 – 至今）

### ARF — Agent Resources & Runtime Framework

https://gitee.com/dalaydata/open_deepseek_arf

**Harness 实验台架 · 验证"大脑-脊椎-身体"分工假设的工程工具**

ARF 是我从零设计的 Agent 基础设施框架，由 DeepSeek V4 Pro 与 Claude Code 协作完成（我只做设计审核，未手写代码）。核心贡献：

- **6 骨架架构**：Prompt 组装、资源注册（MCP）、权限控制、安全审核、沙箱执行器、控制平面——每个骨架对应一个 Protocol，依赖注入组装。框架无 Plugin 也能运行完整 Agent Loop。
- **三层机械层**：固定感知编码器（ResourceRegistry + SystemPromptProvider）→ 可靠行动执行器（SandboxManager + FunctionBackend rollback）→ 非条件反射层（PathCheckToolGuard + PermissionRegistry + RoundManager undo）——对应论文第 5 节设计准则的完整工程实现。
- **Plugin 体系**：9 个 Hook 挂载点。Memory、Compaction、Trace、HumanLoop、Checkpoint 等作为"反射弧"挂载——智能来自模型调用，Plugin 不做认知判断。
- **全链路可观测**：JSONL Trace、Token 统计、离线评估回放。
- **安全三道防线**：deny_patterns 正则 > deny/ask/allow 列表 > PathCheckToolGuard 路径扫描——纯 YAML 声明式配置，模型不可绕过。
- 技术栈：Python 3.11+ · FastAPI · asyncio · Pydantic · Protocol 接口隔离 · MCP 协议

### ARF App 教学项目

https://gitee.com/dalaydata/arf_app_021

**从 0 到 1 构建 ARF 应用的 7 单元渐进式教程**

- 单元 1-7 覆盖完整 Agent 开发链路：Hello ARF → 会话管理 → 工具系统 → 工具审批 → Guardrails 安全 → 长期记忆 → Agent 调优
- 每个单元包含完整可运行代码快照，严格"本单元只讲本单元"的边界约束
- 用"良子与三胖子"的叙事串联安全概念——把枯燥的权限控制变成故事
- 同时作为 ARF 框架的用户验收测试，在真实使用中验证 API 设计的完备性

---

## 竞赛与证书

### 第二届广州·琶洲算法大赛 | 个人独立参赛 | 2023.07

赛题由广州市人民政府主办，覆盖全球超 20 个国家，算法优选赛每赛题仅前 30 强进入复赛。

- **任务**：基于车辆行驶/充电/静置多维时序数据，预测动力电池 40%-90% SOC 区间充电容量（Ah）
- **方案**：综合 Autoformer、Informer、FEDformer 设计思想，自主设计时序预测模型。完成 PyTorch → PaddlePaddle 完整框架迁移，针对飞桨特性进行算法取舍与优化
- **成绩**：初赛 Score 约 0.9118，**排名第 15 / 全球**，晋级复赛

完整榜单：https://aistudio.baidu.com/competition/detail/948/0/leaderboard

### 大模型应用开发工程师 | 百度技术认证 | 2025.02

证书编号 AI0000560 · 有效期 2025.02 – 2029.02 · 深度学习技术及应用国家工程研究中心与百度文心大模型共同认证

---

## 职业经历

### AI Agent 架构师 / 大模型应用专家 | 大众安徽 | 2023.08 – 至今

- 设计"规划-执行-审核"三层多智能体框架，融合 RAG、动态工作流与模型路由，统一记忆与工具调用，落地售后、故障诊断等业务场景
- 构建 LLM-as-a-Judge 评估流水线与全链路可观测性，支持提示词持续优化与模型成本治理
- 技术栈：LangGraph · FastAPI · RAG · Multi-Agent 编排 · MCP · 数据闭环

### 车联网数据主管工程师 | 山东国金汽车 | 2022.10 – 2023.07

- 主导车联网数据全链路闭环（采集→清洗→建模）
- 独立开发基于 Transformer 的电池健康度预测模型（准确率 90%）
- 技术栈：PyTorch · Paddle · Transformer · Hadoop · Hive · HBase

### 车联网数据产品经理 / 数据挖掘 | 一汽解放 | 2016.07 – 2022.09

- 开发驾驶行为评分、服务站负载预测等算法产品
- 从 0 到 1 搭建商用车车联网 APP，用户活跃度提升 70%
- 持有整车公告及安全准入认证经验，具备跨部门项目管理与检测驻场能力
- 技术栈：时序预测 · MLP · 数据产品 · Django · 用户增长

---

## 技能

**Agent 与 LLM 工程**：LangChain / LangGraph · Multi-Agent 编排 · RAG 检索增强 · Tool Calling · 提示词与上下文工程 · 模型路由与动态调度 · LLM-as-a-Judge · MCP 协议

**编程语言**：Python（主力）· TypeScript / JavaScript · SQL

**AI / ML**：PyTorch · PaddlePaddle · Transformer · LSTM · 时序预测 · 特征工程

**数据工程**：Hadoop · Hive · HBase · Pandas · ECharts

**后端与基础设施**：FastAPI · WebSocket · SSE · Docker · Git · Linux · Vue 3 · Vite · SQLite

**语言**：英语（技术文献阅读 / 工作沟通）· 普通话（母语）

---

## 关于我

从一汽解放的车联网数据挖掘，到国金汽车的深度学习与 Transformer 时序建模，再到大众安徽的多智能体架构——这条看似跨越三个领域的技术路线，底层是同一个内驱力：**信息传递价值，数据驱动未来。**

做数据分析时，我关心的是怎么从海量车联网数据中提取有价值的信息。做电池健康度预测时，我关心的是怎么用 Transformer 从时序信号中学习到更好的表征。做大模型 Agent 时，我关心的是怎么让模型和工具之间的信息流动更高效、更可靠、更安全。

Harness 就是这个问题的终极形式——它不是某一个 Agent 的具体业务逻辑，而是所有 Agent 共享的信息基础设施。就像操作系统不是任何一个应用程序，但没有操作系统，就没有应用程序。

如果你也在思考 Agent 系统的边界问题，或者正在寻找一个真正理解 Harness 本质的工程师——我们可以聊聊。
