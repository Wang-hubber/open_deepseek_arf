# ARF Agent 普通用户动态对话测试报告

**测试时间**: 2026-05-15 15:38 - 16:20
**测试用户**: wangxie (模拟角色: 张伟, 运营助理)
**测试方式**: 动态对话 (非预设脚本), 28 轮
**服务器**: http://127.0.0.1:8000

---

## 1. 测试设计

### 1.1 测试目标

从**纯小白用户视角**出发，通过28轮动态对话（用户根据 Agent 回复实时决定下一句话），覆盖六大维度：

| 维度 | 测试方式 |
|------|---------|
| 指令遵循 | 使用模糊、不完整的日常语言，不使用技术指令 |
| 模型路由 | 通过任务复杂度变化观察模型切换行为 |
| 工具调用 | 不明确指定工具，观察 Agent 是否主动调用 |
| 工具生成 | 描述需求但不指定实现方式，观察 Agent 是否创建工具 |
| 记忆管理 | 跨轮次提供个人信息，后期检查回忆准确性 |
| 错误处理 | 制造文件丢失、错误操作等场景，观察恢复能力 |

### 1.2 用户角色

- **姓名**: 张伟
- **职位**: 运营助理
- **技术背景**: 无，不会写代码，不知道 API/CSV/openpyxl 等概念
- **典型场景**: 每天查快递物流、处理订单数据、做报表

---

## 2. 模型路由深度分析

### 2.1 路由机制

ARF 使用 LangGraph 引擎，每个 turn 经过 `classify → call_model → execute_tools → respond` 节点链路。`classify` 节点负责决定使用哪个模型。

**发现：classify 节点 trace 数据为空**

数据库 `trace_events` 表中，所有 `node='classify'` 的记录的 `model` 和 `metadata` 字段均为空。实际模型选择信息存储在 `node='call_model'` 事件的 `model` 字段中。这意味着：
- 分类决策**正在执行**（28次 classify 事件）
- 但分类结果**未持久化**到 trace 元数据中
- 这就是 `[ROUTE: ? → ?]` 的原因

### 2.2 动态会话模型使用分布

| 模型 | 调用次数 | 占比 |
|------|:------:|:----:|
| `quick_thinking` (deepseek-v4-flash) | 82 | 78.8% |
| `quick_no_thinking` (deepseek-v4-flash, 无推理) | 22 | 21.2% |
| `deep_thinking` (deepseek-v4-pro) | 0 | 0% |

**按 Turn 分布 (call_model 事件)**:

```
Turn  1: quick_no_thinking ×8  + quick_thinking ×20  ← 混合期
Turn  2: quick_no_thinking ×7  + quick_thinking ×18  ← 混合期
Turn  3: quick_no_thinking ×4  + quick_thinking ×15  ← 混合期
Turn  4: quick_no_thinking ×2  + quick_thinking ×8   ← quick_thinking 占比上升
Turn  5: quick_no_thinking ×1  + quick_thinking ×7   ← 逐渐收敛
Turn  6: quick_thinking ×4                           ← 纯 quick_thinking
Turn  7: quick_thinking ×4      ← 工具生成阶段
Turn  8: quick_thinking ×2      ← 测试阶段
Turn  9: quick_thinking ×2
Turn 10: quick_thinking ×1
Turn 11: quick_thinking ×1      ← 简洁回复期
```

### 2.3 model_switch 调用记录

动态会话中 `model_switch` 被调用了 2 次：

| Turn | 目标模型 | 实际切换 | 触发场景 |
|:----:|---------|:--------:|---------|
| 2 | `quick_thinking` | ✅ 成功 | 用户说"帮我更新tasks.txt + 查看可用工具" |
| 5 | `quick_thinking` | ✅ 成功 | 工具生成前，Agent 说"先切到推理模式" |

**关键发现**：Agent 口头说"切到推理模式/深度思考"，但两次实际调用都是 `model_switch(target="quick_thinking")`，说明模型路由的**语义理解和实际执行之间存在差距**。Agent 将"推理模式"理解为 `quick_thinking` 而非 `deep_thinking`，这可能是因为工具创建任务不需要极深度推理。

### 2.4 对比：预设脚本测试 vs 动态会话

| 指标 | 预设脚本 (63轮) | 动态对话 (28轮) |
|------|:-------------:|:-------------:|
| 总 call_model 事件 | ~500+ | 104 |
| 模型种类 | 3 种 | 2 种 (缺 deep_thinking) |
| model_switch 调用 | 多次 | 2 次 |
| classify 事件数 | 63 (每次1个) | 28 (每次1个) |
| classify 元数据 | 空 | 空 |

**结论**：动态对话中 Agent 倾向于保守的模型选择。预设脚本中包含"帮我分析客单价"等复杂问题，触发了 `deep_thinking`；动态对话中虽然 Agent 口头表示切换，但实际未使用深度模型。

### 2.5 改进建议

1. **修复 classify trace**：让 classify 节点记录 `model` 和分类依据到 `metadata` 字段
2. **语义映射**：将用户/Agent 的"深度思考""推理模式"等表述明确映射到 `deep_thinking`
3. **门槛调优**：降低触发 `deep_thinking` 的复杂度阈值，工具生成、框架设计等任务应使用深度模型

---

## 3. 六维度评估

### 3.1 指令遵循 (7/10)

| 场景 | 结果 | 说明 |
|------|:----:|------|
| "帮我看看都有什么" | ✅ | 主动使用 file_reader 探索工作区 |
| "帮我写一个文件" (无具体内容) | ✅ | 追问确认内容后再执行 |
| "帮我做一个快递查询工具" | ✅ | 完整执行工具生成流程 |
| "利润分析模板" → 做成库存模板 | ❌ | 第1次跑偏，需用户纠正 |
| "利润分析模板" → 做成 docx_reader | ❌ | 第2次跑偏，需再次纠正 |
| "利润分析模板" → 做成周报模板 | ❌ | 第3次跑偏，用户开始不耐烦 |
| 拼音"wenjian" | ✅ | 正确理解为"文件" |
| "打开利润分析模板看看" | ❌ | 去找了不存在的 docx 文件 |
| 最终纠正后的明确指令 | ✅ | 正确创建 CSV 并填入示例数据 |

**模式**：简单、明确的指令执行完美；多步骤并行任务易失焦，存在"惯性漂移"——Agent 倾向于执行它"认为"用户需要的而非用户"实际说"的。

### 3.2 模型路由 (8/10)

- ✅ 自动化分类决策工作正常（28次 classify）
- ✅ 混合使用 quick_thinking + quick_no_thinking，根据任务复杂度动态调整
- ✅ model_switch 工具正确工作，2/2 次切换成功
- ⚠️ deep_thinking 未被触发，即使工具生成这类复杂任务
- ⚠️ classify trace 元数据为空，无法审计路由决策

### 3.3 工具调用 (9/10)

动态会话共 112 次工具执行 (execute_tools)，分布在 10 个 turn：

| 工具 | 调用次数 | 占比 | 主动性 |
|------|:------:|:----:|:----:|
| `file_reader` | 62 | 55.4% | 主动 - 探索/验证文件 |
| `file_deleter` | 13 | 11.6% | 指令 - 清理文件 |
| `memory_store` | 13 | 11.6% | 主动 - 记忆持久化 |
| `express_tracker` | 8 | 7.1% | 指令 - 测试新工具 |
| `file_writer` | 8 | 7.1% | 指令 - 创建文件 |
| `resource_loader` | 5 | 4.5% | 主动 - 加载/激活资源 |
| `model_switch` | 2 | 1.8% | 主动 - 模型优化 |
| `docx_reader` | 1 | 0.9% | 误调用 - 跑偏时触发 |

- **主动调用率**: ~73% (file_reader/memory_store/resource_loader/model_switch 为 Agent 自主决策)
- **工具多样性**: 8 种不同工具，覆盖文件、记忆、资源、模型四大类
- **file_reader 过度使用**: 62 次占 55%，部分为冗余调用

### 3.4 工具生成 (8/10)

完整走通了工具生成全流程：

```
设计 (Gate 1) → 编码 (Gate 2) → 验证 (Gate 3) → 激活 (Gate 4) → 测试
```

| 阶段 | 工具 | 结果 |
|------|------|:----:|
| 需求理解 | — | 从"查快递很烦"提取出 express_tracker 设计 |
| 设计确认 | file_reader (读 skill scaffold) | 用户确认后再执行 |
| 代码编写 | file_writer ×2 (tool.yaml + function.py) | 支持批量查询、自动识别快递公司 |
| 验证 | file_reader ×4 (代码审查) | 8/8 结构检查通过 |
| 激活 | resource_loader | 激活成功 |
| 测试 | express_tracker ×8 | 3个单号成功查询，YT 单号返回 11 条完整轨迹 |

**亮点**: 整个流程对用户完全透明，用户只需说"确认"，无需理解任何技术细节。

### 3.5 记忆管理 (5/10)

**这是本次测试暴露的最大问题。**

| 场景 | 结果 | 说明 |
|------|:----:|------|
| 记住用户名 | ⚠️ | 存入 memory_store，但总结时仍叫"王小明" |
| 记住职业 | ⚠️ | 存入"运营助理"，总结时混入"数据分析师" |
| 记住偏好 | ✅ | 简洁风格被正确记录 |
| 跨 turn 回忆 | ❌ | Turn 23 的总结完全编造了对话内容 |
| 记忆清理 | ✅ | 被用户纠正后，清理并重写记忆 |
| 最终身份确认 | ✅ | Turn 27 正确回答"张伟，运营助理" |
| 最后一句 | ❌ | Turn 28 仍说"再见，王小明" |

**根本原因**：本工作区之前运行了大量"王小明/数据分析师"的测试会话。长期记忆 (`memory/`) 中残留了大量旧数据，Agent 在构建总结时优先读取了长期记忆而非会话上下文，导致总结全部错误。

**关键发现**：Agent 的**会话上下文**和**长期记忆**之间存在竞争关系。当长期记忆数据量大时，会"淹没"当前会话的真实内容。

### 3.6 错误处理 (7/10)

| 场景 | 结果 | 说明 |
|------|:----:|------|
| 文件不存在 | ✅ | "文件不存在。注意：路径要相对于工作区根目录" |
| 错误模板（库存→利润） | ✅ | 承认错误，删除错误文件，重建正确模板 |
| 工具不存在API返回错误 | ✅ | express_tracker 对假单号返回"参数错误"，妥善处理 |
| 上下文漂移（连续3次跑偏） | ⚠️ | 能恢复但需要用户反复纠正 |
| 记忆污染 | ❌ | 主动总结时未察觉内容完全错误 |

**软错误 vs 硬错误**：Agent 处理"硬错误"（文件不存在、API失败）表现优秀；但处理"软错误"（上下文漂移、记忆污染）能力弱。

---

## 4. 事件追踪统计

### 动态会话 `20260515_082039` (16:21 - 16:44)

| 事件类型 | 次数 | 说明 |
|----------|:----:|------|
| `hook` | 432 | 系统钩子（prompt 前后处理） |
| `execute_tools` | 112 | 工具执行 |
| `call_model` | 104 | LLM API 调用 |
| `classify` | 28 | 模型分类决策 |
| `respond` | 28 | 最终回复（每 turn 1 个） |

### 对比：63轮预设测试 vs 28轮动态对话

| 指标 | 预设测试 | 动态对话 |
|------|:------:|:------:|
| 轮次 | 63 | 28 |
| 总 trace 事件 | ~2700+ | 704 |
| 工具调用种类 | 6 | 8 |
| 模型种类 | 3 | 2 |
| classify 事件 | 63 | 28 |
| SSE 错误 | 0 | 0 |
| Token 消耗 | 876,878 | ~250,000 |

---

## 5. 总体评估

| 维度 | 评分 | 关键发现 |
|------|:----:|---------|
| **指令遵循** | 7/10 | 多任务并行时易漂移，需明确纠正 |
| **模型路由** | 8/10 | 自动化路由工作正常，classify trace 元数据缺失 |
| **工具调用** | 9/10 | 主动、多样、准确 |
| **工具生成** | 8/10 | 完整 pipeline，对用户透明 |
| **记忆管理** | 5/10 | 跨会话污染严重，需加强会话隔离 |
| **错误处理** | 7/10 | 硬错误处理优秀，软错误恢复弱 |

**综合**: **44/60 (73%)**

---

## 6. 改进建议

### 高优先级

1. **会话记忆隔离** — 长期记忆应与当前会话上下文明确分层，Agent 总结时应优先当前会话数据
2. **classify trace 修复** — 让 classify 节点记录分类结果（`model` + `classification` 到 `metadata`）
3. **多任务防漂移** — 当用户同时提多个需求时，Agent 应逐项执行而非跳跃

### 中优先级

4. **deep_thinking 触发优化** — 工具生成、框架设计等复杂任务应触发深度模型
5. **名称持久化验证** — memory_store 写入后应做读回验证，防止"写入张伟，读出王小明"
6. **file_reader 调用精简** — 55% 的工具调用是 file_reader，可能存在冗余

### 低优先级

7. **语义映射校准** — "推理模式""深度思考"等表述应明确映射到 deep_thinking
8. **总结自检** — Agent 在输出总结前应做上下文一致性校验

---

## 7. 附录：Trace 数据查询

### A1. classify 节点数据状态

```sql
SELECT node, model, metadata FROM trace_events WHERE node='classify' LIMIT 3;
-- 输出: classify||  (model 和 metadata 均为空)
```

### A2. 动态会话模型使用

```sql
SELECT turn, model, COUNT(*) FROM trace_events
WHERE session_id='20260515_082039' AND node='call_model'
GROUP BY turn, model ORDER BY turn;
```

### A3. 工具使用排行

```sql
SELECT tool_name, COUNT(*) FROM trace_events
WHERE session_id='20260515_082039' AND node='execute_tools'
GROUP BY tool_name ORDER BY COUNT(*) DESC;
-- Top 3: file_reader(62), file_deleter(13), memory_store(13)
```
