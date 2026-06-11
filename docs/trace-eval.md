# Trace & Eval — 数据全景与测评矩阵

> Trace 是 Agent 会话的全部原始记录，Benchmark 是从 Trace 自动构建的测评基准，EvalRunner 用 Benchmark 回放对比产出量化报告。本文覆盖数据流全链路：哪些数据可用 → 如何标注 → 规则测评 → LLM 测评 → 报告组织。

---

## 1. Trace 与 Benchmark 的可用数据

### 1.1 Trace 原始数据（JSONL，每条记录一行）

引擎在运行时向 trace 注入事件，每条记录的结构：

```json
{"type": "<event_type>", "turn": <int>, "timestamp": <float>,
 "data": { ... }, "session_id": "<id>"}
```

| 事件 type | 关键字段 | 产生时机 |
|-----------|---------|---------|
| `user_input` | `data.content` — 用户原始消息 | 每轮开始 |
| `model_call_start` | `data.model`, `data.turn` | 模型调用前 |
| `model_call_end` | `data.content` (文本回复), `data.reasoning`, `data.tool_calls[]` (name/params), **`data.usage`** (`prompt_tokens`/`completion_tokens`/`total_tokens`) | 模型返回后 |
| `tool_call_start` | `data.tool_name`, `data.arguments` (JSON str 或 dict) | 工具执行前 |
| `tool_call_end` | `data.tool_name`, `data.result`, `data.success`, `data.error`, `data.duration_ms` | 工具返回后 |
| `error` | `data.detail`/`data.message` | 异常时 |

### 1.2 Benchmark 自动构建（`BenchmarkBuilder.build(session_id, name)` → `EvalBenchmark`）

按 `user_input` 在 JSONL 中的**位置索引**切分 case（第 i 个到第 i+1 个 user_input 之间的所有事件属于 case_i），每个 `EvalCase` 自动提取：

| 字段 | 来源 | 说明 |
|------|------|------|
| `input` | `user_input.data.content` | 用户问题原文 |
| `expected_tools` | `tool_call_start.data.tool_name` | 该段内所有工具调用的名称列表 |
| `expected_tool_calls` | `_build_expected_tool_calls(golden_turns)` | 含 name + params + result + success，按索引将 tool_calls[i] 与 tool_results[i] 配对 |
| `original_output` | `_extract_original_output(events)` | 最后一轮完整回答原文，供标注人员参考 |
| `expected_output_contains` | *(显式留空)* | 标注人员从原文提炼的关键词列表 |
| `max_turns` | `len(golden_turns)` | 该段涉及的 turn 数量 |
| `golden_trajectory` | `_build_golden_turns(events)` | `{"annotated": <bool>, "turns": [...]}`，annotated 默认为 `false` |

`_build_golden_turns` 内部逻辑：

```
events → 按 turn 号分组 → 每组:
  model_call_end → 取 content (首个) + tool_calls 列表 (name + params)
  tool_call_end  → 取 tool_name + result + success → tool_results 列表
  如果存在 tool_results → 反向查找最后一条 model_call_end.content → assistant_final
```

被安全策略阻止的工具调用（`blocked: true`）也会被包含在 golden_trajectory 中——这是正确行为的一部分。

---

## 2. 人工标注：预留位置与体验评估

### 2.1 可标注的数据位置

`EvalCase` 的六个字段全部可人工编辑（benchmark 以 JSON 形式存储）。标注人员在 `BenchmarkBuilder.build()` 产出的 JSON 基础上做三件事：

1. **删** — 移除不关键的 turn（如中间探索性的 glob）
2. **改** — 参考 `original_output` 提炼 `expected_output_contains` 关键词，缩紧 `max_turns`
3. **标** — 给关键 `expected_tool_calls[i]` 写 `params` 约束和 `result` 预期

标注复杂度分三档：

| 深度 | 标注内容 | 示例 | 获取方式 |
|------|---------|------|---------|
| **名称级** | `expected_tools: ["read"]` | 工具名列表 | 自动提取，无需人工 |
| **参数级** | `expected_tool_calls[].params: {"path": "README.md"}` | 只标关键字段 | 人工，子集匹配 |
| **结果级** | `expected_tool_calls[].result: "返回了用户列表 JSON"` | 语义描述，非原文抄写 | 人工，不标则跳过 |

### 2.2 现有设计对标注人员的友好度

**有利因素**：
- 自动提取内容直接写入 JSON，标注人员的起点是完整的 golden_trajectory
- `params` 子集匹配——只标关键字段，actual 多出 `_workspace` 等框架参数不影响匹配
- `result` 可以写松散语义描述而非原文
- `expected_tool_calls` 按名称配对不按索引，标注时不需要关注并行调用顺序

**潜在风险**：
- `golden_trajectory` 原始 JSON 可能很长（多 turn、多 tool_call），标注人员需要手动折叠定位
- `original_output` 保留最后一轮完整回答原文，`expected_output_contains` 显式留空 → 标注人员看到原文后主动提取关键词，不会遗漏
- **无标注 UI**——纯手工编辑 JSON，对非技术标注人员不友好
- 标注人员需要理解 JSON 结构和字段含义，存在学习成本

---

## 3. 基于规则的测评矩阵

三组规则 metric，**零 LLM 开销**，默认全部开启（除 LLM 指标外）。

### 3.1 SuccessRateMetric

```
输入:    actual_trace
逻辑:    遍历事件，存在 type="error" → 0.0，否则 1.0
输出:    {"success_rate": 0.0 | 1.0}
用途:    pass/fail 判定关键因子 (must be > 0.0)
```

### 3.2 ToolCallAccuracyMetric

```
输入:    golden_case.expected_tool_calls (优先) 或 expected_tools (兜底)
         + actual_trace (tool_call_start + tool_call_end 事件)
逻辑:
  1. 从 actual_trace 收集 tool_call_start → name + arguments
  2. 从 actual_trace 收集 tool_call_end → dependency 错误检测
  3. expected_tool_calls 非空:
     按名称配对 (不关注执行顺序)
     检查 actual arguments 是否包含 expected params (子集匹配，字符串用子串)
  4. expected_tool_calls 为空:
     退化为 name-only 精确匹配
  5. total = max(len(expected), len(actual))
     多出或缺失的工具都降低分数
  6. 同步扫描 dependency_order_failures:
     检测 tool_call_end.error 是否包含关键词
     (depends_on/blocked/not ready/not complete/dependency/must complete/waiting for/prerequisite)
输出:    {"tool_call_accuracy": 0-1, "dependency_order_failures": <int> (可选)}
用途:    pass/fail 判定关键因子 (must be >= 0.5)
```

### 3.3 TurnEfficiencyMetric

```
输入:    golden_case.max_turns + actual_trace
逻辑:    从 actual_trace 取所有 turn 号去重 → actual_turns
         有 max_turns: min(1.0, max_turns / max(actual_turns, 1))
         无 max_turns: 1.0 (不评估效率)
输出:    {"turn_efficiency": 0-1}
用途:    辅助指标，不参与 pass/fail
```

### 3.4 pass/fail 判定

```
passed = (success_rate > 0.0) AND (tool_call_accuracy >= 0.5)
```

LLM 指标不参与 pass/fail——仅作为质量参考。

---

## 4. LLM-as-judge 的测评矩阵

三组 LLM 指标，**全部默认关闭**。需要在 `EvalConfig` 中显式开启且配置 `judge`。

### 4.1 统一调用机制

```
1. 从 actual_trace 和 golden_case 提取对比数据
2. 构造 messages = [system(judge.system_prompt), user(formatted prompt)]
3. 调用 OpenAI API (model/temperature=0.0/max_tokens 来自 JudgeModelConfig)
4. 解析 JSON 响应，解析失败返回安全默认值
```

所有 prompt 的完整文本在 `arf/plugins/eval/plugin.yaml` 的 `config.prompts` 下集中管理，可通过 `EvalConfig.prompts` 按 key 覆盖。输入截断规则：`user_input` 一律截断到 500 字符。

**参考式 / 无参考式自动切换**：`OutputQualityMetric` 和 `TrajectorySimilarityMetric` 根据 `golden_trajectory.annotated` 自动选择模式：

| annotated | 模式 | prompt key | 对比方式 |
|-----------|------|-----------|---------|
| `true` | 参考式 (ref) | `output_quality` / `trajectory_similarity` | 将 actual 与 golden 对比 |
| `false` 或缺省 | 无参考式 (free) | `output_quality_free` / `trajectory_similarity_free` | 独立评估 actual，以 `system_prompt` + `tools` 为约束 |

无参考式需要 `EvalConfig.system_prompt` 和 `EvalConfig.tools` 由 App 注入（默认空字符串）。缺失时 judge 会在 reason 中标注缺上下文。`ToolCallResultLLMMetric` 不受 annotated 影响——它始终按名称配对 expected vs actual 结果。

### 4.2 ToolCallResultLLMMetric

| 维度 | 内容 |
|------|------|
| **衡量什么** | 工具**返回值**的语义等价性（名称和参数已由 ToolCallAccuracyMetric 覆盖） |
| **Golden 数据** | `golden_case.expected_tool_calls[].result` (需人工标注) |
| **Actual 数据** | actual_trace 中 `tool_call_end` 事件 — `data.result` |
| **匹配策略** | 按名称配对，只对有 `result` 标注的 expected 项执行 judge |
| **Prompt 变量** | `{user_input}` (500), `{tool_name}`, `{expected}` (1500), `{actual}` (1500) |
| **裁判输出** | `{"match": <bool>, "reason": "<2-3 sentences>"}` |
| **最终分数** | `matches / total` → 0-1 |
| **跳过条件** | expected_tool_calls 为空 / 无 result 标注 / judge 未配置 → 返回 1.0 |

### 4.3 OutputQualityMetric

| 维度 | 内容 |
|------|------|
| **衡量什么** | Agent **最终文字回答**的质量（相比 golden reference） |
| **参考式 (annotated=true)** | 对比 golden_trajectory 最后一轮的 assistant_final.content |
| **无参考式 (annotated=false)** | 独立评估，以 `system_prompt` + `tools` 为行为约束 |
| **Actual 数据** | actual_trace 中**最后一条** `model_call_end.data.content` |
| **Prompt 变量 (ref)** | `{user_input}` (500), `{golden}` (2000), `{actual}` (2000) |
| **Prompt 变量 (free)** | `{system_prompt}` (2000), `{tools}` (2000), `{user_input}` (500), `{actual}` (2000) |
| **裁判输出** | `{"score": <int 1-5>, "reason": "<2-3 sentences>"}` |
| **跳过条件** | actual 内容为空 → 返回 3 ("missing actual content") |

### 4.4 TrajectorySimilarityMetric

| 维度 | 内容 |
|------|------|
| **衡量什么** | Agent **解题路径**与 golden trajectory 的相似度 |
| **参考式 (annotated=true)** | 对比 `golden_trajectory` 全文 |
| **无参考式 (annotated=false)** | 独立评估，以 `system_prompt` + `tools` 为约束 |
| **Actual 数据** | actual_trace → 按 turn 格式化为摘要<br>`[turn N] call <name>` / `result: ok|fail` / `output: <200chars>` |
| **Prompt 变量 (ref)** | `{user_input}` (500), `{golden}` (3000), `{actual}` (3000) |
| **Prompt 变量 (free)** | `{system_prompt}` (2000), `{tools}` (2000), `{user_input}` (500), `{actual}` (3000) |
| **核心原则** | ref: golden 是 "一种正确解"不是 "唯一正确解" · free: 根据可用工具集和 system prompt 判断合理性 |
| **裁判输出** | `{"score": <int 1-5>, "reason": "<2-3 sentences>"}` |
| **跳过条件** | actual_trace 无有效摘要 → 返回 3 ("empty") |

---

## 5. EvalReport 数据组织与测评开销

### 5.1 Report 结构

```
EvalReport
├── run_id, benchmark_name, timestamp, agent_config_hash
├── mode: "online" | "offline"
├── judge_model: "gpt-4" | "none"
├── metrics_enabled: ["tool_call_accuracy", "turn_efficiency", ...]
├── summary: EvalSummary
│   ├── total / passed / failed / pass_rate        ← 基础计数
│   ├── avg_turns / avg_tool_calls / avg_duration_seconds  ← 运行态均值
│   ├── total_tokens_in / total_tokens_out / total_duration_seconds  ← 总量
│   ├── success_rate / tool_call_accuracy / turn_efficiency  ← 规则 metric 均值
│   ├── output_quality / trajectory_similarity                  ← LLM metric 均值 (1-5)
│   └── tool_call_result_llm                                   ← LLM metric 均值 (0-1)
└── per_case[i]: dict
    ├── case_id, passed, duration_seconds, session_id
    ├── turns / tokens_in / tokens_out / tool_calls  ← 从 trace 提取
    └── metrics:
        ├── success_rate (0 | 1)
        ├── tool_call_accuracy (0-1)
        ├── turn_efficiency (0-1)
        ├── dependency_order_failures (可选)
        ├── output_quality (1-5) + reason           ← 仅当开启
        ├── trajectory_similarity (1-5) + reason    ← 仅当开启
        └── tool_call_result_llm (0-1)              ← 仅当开启
```

### 5.2 终端输出示例

```
 Eval Report: file_ops_v1
 ==================================================
 Mode: online   Judge: gpt-4   Hash: a1b2c3d4

 Cases: 5 to run

  [OK] case_0: turns=1, tok=120/85, tool_acc=1.00, turn_eff=1.00, quality=4/5, 2.3s
  [OK] case_1: turns=1, tok=150/96, tool_acc=1.00, turn_eff=1.00, quality=5/5, 3.1s
  [FAIL] case_2: turns=2, tok=310/178, tool_acc=0.50, turn_eff=1.00, quality=3/5, 4.2s
         input: 帮我读一下 README.md 和 config.yaml
  [OK] case_3: turns=1, tok=95/44, tool_acc=1.00, turn_eff=0.50, 2.8s
  [OK] case_4: turns=2, tok=220/130, tool_acc=1.00, turn_eff=1.00, quality=5/5, 5.0/5, 1.9s

 --------------------------------------------------
 Summary: 4/5 passed (80.0%)
   Avg turns:         1.4
   Avg duration:      2.9s
   Total duration:    14.3s
   Total tokens:      895 in / 533 out
   Avg tool calls:    1.6
   Tool accuracy:     0.90
   Turn efficiency:   0.90
   Output quality:    4.2/5 (LLM)

 1 failed case(s):
   case case_2: ['tool_call_accuracy']
```

### 5.3 测评开销统计

| 统计项 | 是否记录 | 位置 |
|--------|---------|------|
| 每个 case 的执行时间 | ✅ | `per_case[i].duration_seconds` (含 agent + judge) |
| 总耗时 | ✅ | `summary.total_duration_seconds` |
| 平均耗时 | ✅ | `summary.avg_duration_seconds` |
| Agent Token 使用 | ✅ | `per_case[i].tokens_in/out`（从 trace 的 `model_call_end.usage` 汇总） |
| Judge Token 使用 | ❌ | 未单独记录 |
| Judge API 调用次数 | ❌ | 未计数 |
| Judge 调用耗时 | ❌ | 包含在 duration_seconds 中，无法单独拆分 |

---

## 6. 演进方向

- **标注工具/UI** — 降低非技术标注人员的门槛，提供 golden trajectory 的可视化浏览和编辑
- **Judge 开销追踪** — 记录 judge LLM 的 token 消耗和调用次数，透明化测评成本
- **LLM metric 参与 pass/fail** — 目前仅依赖规则 metric，质量评分可设置阈值
- **并行执行** — 多 case 并发，`asyncio.Semaphore` 控制并发度
- **HTML Report** — 带 golden vs actual 并排对比的可视化报告
- **CI 集成** — 退出码 0（通过）/ 1（退化）
