# 实验设计：数据集与测评方法

> 报告日期：2026-06-09
>
> 针对 ARF 论文四个核心实验的数据集发现与测评方案设计。

## 核心论点

将 Agent 的控制信号从 In-Context Learning 迁移到参数内化（LoRA），涉及四个维度：

1. 记忆持久化：memory.md 外挂摘要 → 在线 SFT 写入 LoRA B 矩阵
2. 身份认同：System Prompt → Identity LoRA 固化人格
3. 知识注入：RAG → Knowledge LoRA 参数化知识
4. Agent 通信：自然语言文本 → TFlow 权重空间扰动融合

---

## 实验三：在线 LoRA 长期记忆

**核心对比**：参数化记忆（在线 SFT 更新 Memory LoRA B 矩阵）vs 外挂摘要（`memory.md` + RAG）

### 数据集

| 方案 | 名称 | 类型 | 说明 |
|------|------|------|------|
| 公开 | LoCoMo | 公开基准 | 长对话记忆，含多跳查询、时间推理。可直接使用 |
| 公开 | LongMemEval | 公开基准 | 平均 105K tokens 上下文，知识更新、回避判断。压力测试用 |
| 自建 | DFC (Dynamic Facts & Contradictions) | 补充数据集 | 100-200 个长对话，每个 50 轮。分阶段：事实注入→查询→更新/冲突→干扰→最终查询 |

### 测评

- QA Accuracy：LoCoMo/DFC 中正确回答比例（LLM 裁判评估）
- KUI (Knowledge Update Index)：接收到更新信息后采纳新事实的能力
- MFR (Memory Forgetting Rate)：早/中/晚期准确率差异
- Query Latency & Update Latency：查询和更新的端到端耗时

---

## 实验四：身份边界鲁棒性

**核心对比**：Identity LoRA 固化人格 vs System Prompt 角色定义

### 数据集

| 方案 | 名称 | 类型 | 说明 |
|------|------|------|------|
| 公开 | JailbreakBench (JBB-Behaviors) | 公开基准 | 100+ 有害行为数据集 + 对抗性提示库。角色扮演攻击与本实验直接相关 |
| 自建 | Persona Conflict | 补充数据集 | ~50 个身份冲突场景。在核心身份和用户请求间制造伦理冲突 |

### 测评

- ASR (Attack Success Rate)：JailbreakBench 评估脚本 + GPT-4 裁判
- ICS (Identity Consistency Score)：人类评估 1-5 分，评估回答与核心身份的一致性
- RQ (Refusal Quality)：拒绝质量 1-3 分

---

## 实验五：参数化上下文压缩

**核心对比**：Context LoRA 异步压缩 vs LLM 摘要

### 数据集

| 方案 | 名称 | 类型 | 说明 |
|------|------|------|------|
| 公开 | LongBench (Multi-document QA) | 公开基准 | 多文档问答，对压缩后关键细节保留最敏感。主测试集 |
| 公开 | LongBench (Single-document QA) | 公开基准 | 单文档问答。辅助测试集 |

### 测评

- F1 Score：信息保真度（LongBench 官方脚本）
- Compression Ratio：压缩率
- E2EPL (End-to-End Processing Latency)：压缩 + 推理总耗时
- AUI (Asynchronous Update Impact)：异步更新对新信息即时性的影响

---

## 实验六：TFlow 权重通信

**核心对比**：权重空间扰动融合 vs 自然语言文本通信

### 数据集

无公开基准。自建 DRSA (Distributed Real-time Situational Awareness) 模拟环境：
- N 个 Agent（4/8/16/32）监控 2D 网格世界
- 部分可观察（每个 Agent 仅局部视野 + 噪声）
- 共同目标：每个 tick 生成全局精确态势估计
- Python 模拟服务器 + 可选 ns-3 网络模拟层

### 测评

- E2EFL (End-to-End Fusion Latency)：从接收局部观察到完成全局融合的延迟
- CBU (Communication Bandwidth Usage)：每 tick 网络传输总量
- GSA (Global Situational Accuracy)：全局态势估计准确率
- SI (Scalability Index)：N 变化时性能退化曲线

---

## 数据策略总结

| 实验 | 公开数据集 | 自建方案 |
|------|----------|---------|
| 三（记忆） | LoCoMo、LongMemEval ✅ | DFC 长对话脚本 |
| 四（身份） | JailbreakBench ✅ | Persona Conflict 场景 |
| 五（压缩） | LongBench QA 子集 ✅ | — |
| 六（通信） | 无公开基准 ❌ | DRSA 模拟环境 |
