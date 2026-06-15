# Eval Benchmark — 测评与回归检测

> **Eval 是 `arf/plugins/eval/` 下的独立插件模块。接收 benchmark + trace，产出多维量化报告。裁判 LLM 与被测 Agent 完全独立。不挂载任何 lifecycle hook。**

ARF 提供基于 trajectory trace 的多维测评机制：从真实会话 trace 构建 golden benchmark，通过在线执行或离线回放对比实际输出，支持规则匹配和 LLM-as-judge 两种评判方式。插件配置（judge 默认值、裁判 prompts）集中在 `plugin.yaml`。

---

## 1. OS 方案演进

### 1.1 回归测试与 CI

**问题**：如何确保 Agent 配置、框架代码或模型版本的变更不会导致已有能力退化？

**传统方案**：CI 系统的回归测试套件。开发者在每次变更后运行固定的测试用例集合，对比历史基线结果，自动发现退化（regression）。

ARF 的 `EvalRunner` 直接对应回归测试运行器：输入 benchmark（测试用例集），输出 report（每个用例的通过/失败 + 量化指标）。`EvalComparator` 负责 diff 两版 report，标记退化和改善。

### 1.2 会话回放（Session Replay）

**问题**：Agent 的"正确行为"很难用静态断言描述。如何从真实用户对话中提取高质量的测试用例？

**方案**：会话回放。`BenchmarkBuilder` 从 TracePlugin 中的真实对话 trace 提取完整的 golden trajectory（每轮 assistant 回复、工具调用、工具结果、最终回复），生成可编辑的 benchmark JSON。

### 1.3 对 ARF 的启示

| OS/CI 概念 | ARF 对应 |
|-----------|----------|
| 回归测试套件 | `EvalBenchmark` — 一组 `EvalCase` |
| 测试运行器 | `EvalRunner.run_online()` / `EvalRunner.run_offline()` |
| 测试报告 | `EvalReport` — per_case + summary |
| 历史基线 | `EvalReport.to_json()` 持久化 |
| 回归检测 | `EvalComparator.compare(baseline, current)` → `EvalDiff` |
| 会话回放 | `BenchmarkBuilder.build(session_id, name)` |
| Golden 数据 | `data/{sid}/traces/{sid}.jsonl` — 原始 trace 文件，按需读取 |
| LLM 裁判 | `OutputQualityMetric` / `TrajectorySimilarityMetric` |

---

## 2. 架构

```
真实对话 → TracePlugin → data/{sid}/traces/{sid}.jsonl
                                    │
                           BenchmarkBuilder.build(session_id, name)
                                    │
                                    ▼
                           benchmarks/{name}.json (评测合约，仅含 expected_tools/expected_output_contains/max_turns)

  Case 边界：按 user_input 在 JSONL 中的位置索引切分，不依赖 turn 号。
  第 i 个到第 i+1 个 user_input 之间的所有事件属于 case_i。
                                    │
                                    ▼
                           EvalRunner
                            ├── online: agent.chat() → 等待 trace
                            └── offline: 读已有 trace 文件
                                    │
                           ┌────────┼────────┐
                           ▼        ▼        ▼
                      规则 metrics  LLM metrics  summary
                           │        │        │
                           ▼        ▼        ▼
                          EvalReport (终端 + JSON)
                                    │
                           EvalComparator.compare(baseline, current)
                                    │
                                    ▼
                                EvalDiff
```

---

## 3. API 参考

### 3.1 Benchmark 创建

```python
from arf.plugins.eval import BenchmarkBuilder
from arf.plugins.trace.plugin import TracePlugin

trace = TracePlugin({"data_dir": "./data"})

builder = BenchmarkBuilder(trace)

# 从真实对话 session 创建 benchmark
benchmark = builder.build(session_id="default", name="file_ops_v1")
benchmark.to_json("benchmarks/file_ops_v1.json")

# 人类编辑 JSON 后重新加载
from arf.plugins.eval.models import EvalBenchmark
benchmark = EvalBenchmark.from_json("benchmarks/file_ops_v1.json")
```

产出的 benchmark JSON 包含评测合约字段：

```json
{
  "name": "file_ops_v1",
  "source_session": "default",
  "cases": [
    {
      "id": "case_0",
      "input": "帮我读一下 README.md",
      "session_id": "default",
      "expected_tools": ["read"],
      "expected_output_contains": ["ARF", "Framework"],
      "max_turns": 1,
      "expected_tool_calls": [
        {"name": "read", "params": {"path": "README.md"}, "result_preview": "# ARF Framework\n\nARF 是一个...", "success": true}
      ]
    }
  ]
}
```

`golden_trajectory` 和 `original_output` 已从 benchmark 中移除——完整轨迹保留在 `data/{sid}/traces/{sid}.jsonl`，LLM metrics 的 reference 模式通过 `session_id` 按需读取。人类标注时直接查看 trace 文件。

### 3.2 运行 Eval（Online）

```python
import asyncio
from arf.plugins.eval import EvalRunner
from arf.plugins.eval.models import EvalConfig, JudgeModelConfig
from arf.core.model_registry import ResolvedModelConfig

config = EvalConfig(
    benchmark_path="benchmarks/file_ops_v1.json",
    trace_dir="./data",
    judge=JudgeModelConfig(),  # 仅语义配置，模型连接由 judge_model 提供
    judge_model=ResolvedModelConfig(
        model="deepseek-chat",
        api_base="https://api.deepseek.com",
        api_key_env="DEEPSEEK_API_KEY",
        kwargs={"temperature": 0.0, "max_tokens": 2000},
    ),
    metrics={
        "tool_call_accuracy": True,
        "turn_efficiency": True,
        "success_rate": True,
        "output_quality": True,
    },
)

# EvalRunner 也可接收 agent_config 自动 resolve judge_model：
# runner = EvalRunner(config, agent_config=agent.config)
runner = EvalRunner(config)
report = await runner.run_online(
    agent.chat,
    system_prompt=agent.system_prompt,   # agent 运行时组装好的完整 prompt
    tools=agent.tools_description,       # 已注册的工具描述清单
)
report.to_json("reports/file_ops_v1_20260611.json")
```

### 3.3 运行 Eval（Offline）

```python
config = EvalConfig(
    benchmark_path="benchmarks/file_ops_v1.json",
    trace_dir="./data",
    mode="offline",
    trace_session_ids=["s1", "s2", "s3"],  # 对应 benchmark 中的 3 个 cases
)

runner = EvalRunner(config)
report = await runner.run_offline()
```

### 3.4 CLI

```bash
# Offline — judge 模型连接通过 CLI flags 构建 ResolvedModelConfig
python -m arf.plugins.eval run \
  --benchmark benchmarks/file_ops_v1.json \
  --trace-dir ./data/traces \
  --mode offline \
  --traces s1,s2,s3 \
  --metrics tool_call_accuracy,turn_efficiency,output_quality \
  --judge-api-base https://api.deepseek.com \
  --judge-api-key-env DEEPSEEK_API_KEY \
  --judge-model deepseek-chat \
  --output report.json

# Online (Python API) — judge_model 可从 agent_config 自动 resolve
python -c "
import asyncio
from arf.plugins.eval import EvalRunner
from arf.plugins.eval.models import EvalConfig
config = EvalConfig(benchmark_path='benchmarks/file_ops_v1.json', ...)
runner = EvalRunner(config, agent_config=agent.config)
asyncio.run(runner.run_online(agent.chat))
"
```

### 3.5 对比运行报告

```python
from arf.plugins.eval import EvalComparator, EvalReport

baseline = EvalReport.from_json("reports/baseline.json")
current = EvalReport.from_json("reports/current.json")

diff = EvalComparator().compare(baseline, current)
print(f"Summary delta: {diff.summary_diff}")
print(f"Regressions: {diff.regressions}")
print(f"Improvements: {diff.improvements}")
```

---

## 4. 数据模型

### EvalCase

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | `str` | 用例 ID |
| `input` | `str` | 用户消息文本 |
| `session_id` | `str \| None` | 来源 trace session，LLM metrics 通过它读取完整 trace 做 reference 评测 |
| `expected_tools` | `list[str] \| None` | 预期调用的工具名列表（name-only，向后兼容） |
| `expected_tool_calls` | `list[dict] \| None` | 预期工具调用（含 name/params/blocked/success/result_preview），按名称与 actual 配对 |
| `expected_output_contains` | `list[str] \| None` | 预期输出包含的关键词，Builder 初始化为空列表 |
| `max_turns` | `int \| None` | 预期最大轮次数 |

`expected_tool_calls[i]` 结构：`{"name": "eat", "params": {"name": "良子"}, "blocked": false, "success": true, "result_preview": "吃完了..."}`。`params`、`blocked`、`success`、`result_preview` 均可选——不标（None）则该维度不参与匹配。

*已移除字段：`golden_trajectory`、`original_output`。完整轨迹保留在 `data/{sid}/traces/{sid}.jsonl`，LLM metrics reference 模式通过 `session_id` 按需读取。*

### JudgeModelConfig

JudgeModelConfig 仅包含**语义配置**——裁判的行为和提示词。模型连接信息（`api_base`、`api_key_env`、`model`、`temperature` 等）通过 `agent_config` 的 `model_defs` 注入为 `ResolvedModelConfig`（即 `EvalConfig.judge_model` 字段）。

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `system_prompt` | `str` | *(expert evaluator persona)* | 裁判 system message，所有 LLM 指标共用 |
| `response_format` | `dict \| None` | `None` | 强制 JSON 输出，如 `{"type": "json_object"}` |

### ResolvedModelConfig（裁判模型连接）

由 `agent_config.get_plugin_model_config("eval")` resolve，或 CLI 通过 `--judge-model`/`--judge-api-base`/`--judge-api-key-env` 构建，存储在 `EvalConfig.judge_model`：

| 字段 | 类型 | 说明 |
|------|------|------|
| `model` | `str` | 裁判模型名（如 `deepseek-chat`） |
| `api_base` | `str` | OpenAI 兼容 API 地址 |
| `api_key_env` | `str` | API key 环境变量名 |
| `kwargs` | `dict` | 透传给 `ModelAdapter` 的额外参数（`temperature`、`max_tokens` 等） |

### EvalConfig

| 字段 | 说明 |
|------|------|
| `benchmark_path` | Benchmark JSON 文件路径 |
| `trace_dir` | Session 数据目录（默认 `./data`），trace 路径为 `{trace_dir}/{sid}/traces/{sid}.jsonl` |
| `judge` | `JudgeModelConfig \| None` — 裁判语义配置（prompt、response_format） |
| `judge_model` | `ResolvedModelConfig \| None` — 裁判模型连接信息，由 `agent_config.get_plugin_model_config("eval")` resolve |
| `metrics` | 6 维 开关 dict |
| `prompts` | `dict[str, str]` | 覆盖 LLM 指标的 prompt（key: `tool_call_result_llm`/`output_quality`/`output_quality_free`/`trajectory_similarity`/`trajectory_similarity_free`），不传用内置默认 |
| `mode` | `"online"` / `"offline"` |
| `trace_session_ids` | offline 模式下的 session ID 列表 |

### EvalReport

| 字段 | 说明 |
|------|------|
| `run_id` | UUID |
| `snapshot_hash` | EnvSnapshot hash，关联配置版本 |
| `judge_model` | 裁判模型名 |
| `metrics_enabled` | 启用的 metric 列表 |
| `mode` | online / offline |
| `summary` | `EvalSummary` 汇总统计 |
| `per_case` | 每个用例的详细结果 |

`per_case[i]` 除 `case_id`、`passed`、`metrics`、`duration_seconds`、`session_id` 外，还包含从 trace 自动提取的统计：
`turns`（去重 turn 数）、`tokens_in` / `tokens_out`（所有 `model_call_end` 的 usage 总和）、`tool_calls`（工具调用次数）。

### EvalSummary

| 字段 | 类型 | 说明 |
|------|------|------|
| `total` / `passed` / `failed` | `int` | 基础计数 |
| `pass_rate` | `float` | 通过率 |
| `avg_turns` | `float` | 平均 turn 数 |
| `avg_tool_calls` | `float` | 平均工具调用次数 |
| `avg_duration_seconds` | `float` | 平均耗时 |
| `total_tokens_in` | `int` | 全部 token 输入总量 |
| `total_tokens_out` | `int` | 全部 token 输出总量 |
| `total_duration_seconds` | `float` | 总耗时 |
| `tool_call_accuracy` 等 | `float` | 各指标均值 |

---

## 5. Metrics

### 规则层

| Metric | 方法 | 输出 |
|--------|------|------|
| `SuccessRateMetric` | trace 中是否有 error 事件 | 0 或 1 |
| `ToolCallAccuracyMetric` | 按名称配对：name + params + blocked + success + result_preview 多字段匹配。`expected_tool_calls` 优先，`expected_tools` 兜底。同步统计 dependency_order_failures | 0–1 + dep_fail 计数 |
| `TurnEfficiencyMetric` | 实际 turn 数 vs `max_turns` | 0–1 |
| `OutputContainsMetric` | 实际最终输出是否包含 `expected_output_contains` 所有关键词（子串匹配） | 0–1 |

**ToolCallAccuracyMetric 匹配策略**：

1. 优先使用 `expected_tool_calls`（如果非空），按**名称**与 actual 配对（不关注执行顺序）
2. 每个 expected item 在 actual 中找同名的、params 子集匹配的，找到即算命中
3. 字符串参数用**子串匹配**（`"焖子"` in `"良子的焖子"`），非字符串用 `==`
4. `expected.blocked` 非 None 时需匹配 actual 的 blocked 状态（用于标注安全策略拦截的场景）
5. `expected.success` 非 None 时需匹配 actual 的 success 状态
6. `expected.result_preview` 非 None 时走子串匹配（`expected.result_preview` in `actual.result`）
7. actual 可以多出额外参数（如框架注入的 `_workspace`），不影响匹配
8. actual 多出 expected 没有的工具 → 降低总分（total 取 max(expected, actual)）
9. `expected_tool_calls=None` 时退化为 `expected_tools` 的 name-only 模式
10. 同步扫描 `tool_call_end` 事件：`success=false` 且 `error` 包含依赖关键词（`depends_on`、`blocked`、`not ready`、`not complete`、`dependency`、`must complete`、`waiting for`、`prerequisite`）→ 计入 `dependency_order_failures`

### LLM-as-judge

| Metric | 方法 | 输出 |
|--------|------|------|
| `OutputQualityMetric` | LLM 对比 final output vs golden，1-5 打分 | score + reason |
| `TrajectorySimilarityMetric` | LLM 对比完整 actual trajectory vs golden trajectory，1-5 打分 | score + reason |
| `ToolCallResultLLMMetric` | LLM 对比 expected vs actual tool results 语义等价，按名称配对 | 0–1 |

**ToolCallResultLLMMetric** 用于评估工具**返回值**的语义一致性。`ToolCallAccuracyMetric` 负责名称和参数（程序化、零开销），`ToolCallResultLLMMetric` 负责结果语义（需 judge LLM）。`expected.result` 可从 golden trajectory 自动提取，人工标注是可选的优化——当 golden result 太冗长时，人工可以改成松散的语义描述让 LLM 判得更准。推荐先跑程序化 metric，只在需要时开启 LLM 裁判。

LLM metrics 通过 `ModelAdapter`（而非 raw OpenAI client）调用 judge LLM，由 `EvalRunner` 在构造时从 `EvalConfig.judge_model` 构建。`temperature=0.0`。每个指标有内置的评分 prompt（含行为锚定的 1-5 评分标尺、边界案例指导、2-3 句推理要求），可通过 `EvalConfig.prompts` 按 key 覆盖。Judge API 调用失败时抛出 `EvalJudgeError`（fatal），Runner 会 [ABORT] 终止整个 run，防止在无效裁判下继续执行浪费资源。

Reference 模式（`OutputQualityMetric`、`TrajectorySimilarityMetric`）：通过 `golden_case.session_id` 从 `data/{sid}/traces/{sid}.jsonl` 按需读取 golden 数据，不再依赖 benchmark JSON 中的 `golden_trajectory` 字段。无 `session_id` 时自动降级为 no-reference 模式。

如果开启 LLM metric 但未配置 `judge` 或 `judge_model` → `EvalConfig.validate()` 抛错。

不可评估时（如 `output_quality` 缺 actual content、`trajectory_similarity` 缺 trajectory），LLM metrics 返回 `None` 而非伪造的默认分（如 `3`），由 `EvalSummary` 的 `avg_*` 计算跳过 `None` 值。

**裁判 prompt 默认值在 `arf/plugins/eval/plugin.yaml` 的 `config.prompts` 下集中管理**，五个 key：
- `tool_call_result_llm` — 判断工具返回值是否语义等价，传入 user_input + tool_name + expected/actual 结果
- `output_quality` — 参考式（有 session_id 且 trace 可读）：1-5 评分最终回答质量 vs golden
- `output_quality_free` — 无参考式（无 session_id / trace 缺失）：1-5 独立评分，以 system_prompt + tools 为约束
- `trajectory_similarity` — 参考式（有 session_id 且 trace 可读）：1-5 评分路径相似度 vs golden
- `trajectory_similarity_free` — 无参考式（无 session_id / trace 缺失）：1-5 独立评估解题路径

---

## 6. 人工标注指南

### 6.1 自动构建 → 人工精修

`BenchmarkBuilder.build()` 自动从 trace 提取 `expected_tool_calls`（含 name + params + result_preview）。被安全策略阻止的工具调用（`blocked: true`）也会被包含——这是正确行为的一部分。产出的 benchmark JSON 可作为起点，人工标注做三件事：

1. **删**：移除不关键的 turn（如中间探索性的 glob）
2. **改**：修正 `expected_output_contains` 预期关键词，缩紧 `max_turns`
3. **标**：给关键 tool_call 写 `params` 约束和 `result_preview` 预期
4. **查**：需要完整上下文时查看 `data/{sid}/traces/{sid}.jsonl` 原始 trace 文件

### 6.2 标注示例

**简单标注（只看工具名）：**

```json
{
  "id": "case_0",
  "input": "读一下 README.md",
  "expected_tools": ["read"],
  "max_turns": 1
}
```

**带参数约束（子集匹配）：**

```json
{
  "id": "case_1",
  "input": "良子去吃良子的焖子",
  "expected_tool_calls": [
    {
      "name": "eat",
      "params": {"name": "良子", "path": "良子的焖子"}
    }
  ]
}
```

`params` 使用子集匹配——actual 多出 `_workspace` 等框架参数不算错。字符串用子串匹配，所以 `"焖子"` 也能命中 `"良子的焖子"`。

**带 result 预期（需开启 ToolCallResultLLMMetric）：**

```json
{
  "id": "case_2",
  "input": "查一下今天天气",
  "expected_tool_calls": [
    {
      "name": "weather",
      "params": {"city": "北京"},
      "result": "晴，22°C"
    }
  ]
}
```

`result` 走 LLM 语义等价判断——"晴天，气温 22 摄氏度" 也能匹配 "晴，22°C"。

**带 blocked/success 预期（验证安全策略或工具执行状态）：**

```json
{
  "id": "case_3",
  "input": "读一下 /etc/passwd",
  "expected_tool_calls": [
    {
      "name": "read",
      "params": {"path": "/etc/passwd"},
      "blocked": true,
      "success": false
    }
  ]
}
```

`blocked` / `success` / `result` 在 `ToolCallAccuracyMetric` 中走程序化匹配（不走 LLM），仅在标注了非 None 值时生效。`result` 在程序化 metric 中走子串匹配，如需语义等价判断请开启 `ToolCallResultLLMMetric`。

### 6.3 标注注意事项

- **按名称匹配**：评估时按工具名配对，不关注执行顺序。并行 tool_call 返回顺序不确定也不影响评分
- **依赖顺序由工具自行校验**：skill 内部工具的依赖关系（如 `plan_create` 必须在 `plan_dispatch` 之前）由框架在运行时 enforce，违反时 `tool_call_end.success=false`，`ToolCallAccuracyMetric` 自动统计为 `dependency_order_failures`
- **多轮对话**：每轮（一个 user input → 最终 text response）一个 EvalCase。如果一轮中有多个 tool_call，全放在同一个 `expected_tool_calls` 数组里
- **params 标关键字段即可**：不用标全量参数，标对决策有影响的字段（如 `path`、`pattern`、`name`）。框架自动注入的参数（`_workspace`）不要标
- **result 可标可不标**：不标时 `ToolCallResultLLMMetric` 自动跳过（返回 1.0）。如需 LLM 裁判结果语义，写 "返回了用户列表 JSON" 而非原始返回值全文。LLM 做语义等价判断
- **程序化优先**：工具名称和参数走 `ToolCallAccuracyMetric`（零开销），只在需要判断结果语义时开启 `ToolCallResultLLMMetric`（需 judge LLM）
- **Backward compatible**: existing benchmark `expected_tools` don't need migration — `ToolCallAccuracyMetric` auto-fallbacks to name-only mode. `golden_trajectory` and `original_output` fields in old benchmarks are silently ignored.

---

## 7. 终端输出

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
  [OK] case_4: turns=2, tok=220/130, tool_acc=1.00, turn_eff=1.00, quality=5/5, traj_sim=5/5, 1.9s

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
   Trajectory sim:    5.0/5 (LLM)

 1 failed case(s):
   case case_2: ['tool_call_accuracy']
```

---

## 8. App 端配置指南

### 8.1 框架默认值（`plugin.yaml`）

`arf/plugins/eval/plugin.yaml` 提供全部默认值。App **不修改此文件**——通过 `EvalConfig` 在代码中覆盖所需字段即可。

### 8.2 使用 EvalConfig 覆盖默认值

```python
from arf.plugins.eval import EvalRunner
from arf.plugins.eval.models import EvalConfig, JudgeModelConfig

# 方式 1：通过 agent_config 自动 resolve judge_model（推荐）
config = EvalConfig(
    benchmark_path="benchmarks/file_ops_v1.json",
    trace_dir="./data",
    judge=JudgeModelConfig(),  # 仅语义配置（可用默认），模型连接由 agent_config 提供
    metrics={
        "tool_call_accuracy": True,
        "turn_efficiency": True,
        "success_rate": True,
        "output_quality": True,              # LLM，需 judge + judge_model
        "trajectory_similarity": True,       # LLM，需 judge + judge_model
        "tool_call_result_llm": False,       # LLM，需 judge + judge_model + 标注了 result
    },
    # prompts：可选，覆盖默认评分 prompt
    # prompts={"output_quality": "...", ...},
)

runner = EvalRunner(config, agent_config=agent.config)

# 方式 2：手动构建 ResolvedModelConfig
from arf.core.model_registry import ResolvedModelConfig
config = EvalConfig(
    benchmark_path="benchmarks/file_ops_v1.json",
    trace_dir="./data",
    judge=JudgeModelConfig(),
    judge_model=ResolvedModelConfig(
        model="deepseek-chat",
        api_base="https://api.deepseek.com",
        api_key_env="DEEPSEEK_API_KEY",
        kwargs={"temperature": 0.0, "max_tokens": 2000},
    ),
    metrics={...},
)
runner = EvalRunner(config)

# system_prompt + tools 从 agent 运行时对象直接取，不放进 EvalConfig
report = await runner.run_online(
    agent.chat,
    system_prompt=agent.system_prompt,
    tools=agent.tools_description,
)
```

### 8.3 App 需要注入的数据总结

| 参数 | 注入位置 | 来源 | 必填？ |
|------|---------|------|--------|
| `judge` | `EvalConfig` | App 配置（语义：prompt、response_format） | 开启 LLM 指标时必填（可用默认） |
| `judge_model` | `EvalConfig` 或 `agent_config` | `agent.yaml` 的 `plugins_config.eval` 或 CLI flags | 开启 LLM 指标时必填 |
| `agent_config` | `EvalRunner(...)` | agent.config（含 `get_plugin_model_config("eval")`） | 推荐传入，自动 resolve judge_model |
| `metrics` | `EvalConfig` | App 决策哪些维度要评估 | 都有默认值 |
| `prompts` | `EvalConfig` | App 可选覆盖 | 否，不传用框架默认 |
| `system_prompt` | `run_online()` | `agent.system_prompt`（运行时） | 建议传入，缺则无参考式缺上下文 |
| `tools` | `run_online()` | `agent.tools_description`（运行时） | 建议传入，缺则无参考式缺上下文 |

`system_prompt` 和 `tools` 是**运行时数据**，不放进 `EvalConfig`——它们由 agent 在运行时刻组装，App 直接从 agent 对象取即可，无需拷贝。

### 8.4 Benchmark 构建与标注

```python
from arf.plugins.eval import BenchmarkBuilder
from arf.plugins.trace.plugin import TracePlugin

# 1. 从已有 trace 构建 benchmark
trace = TracePlugin({"data_dir": "./data"})
builder = BenchmarkBuilder(trace)
benchmark = builder.build(session_id="default", name="file_ops_v1")
benchmark.to_json("benchmarks/file_ops_v1.json")

# 2. 人工编辑 JSON — 删除无关 turn、标注 expected_output_contains 关键词
#    需要完整上下文时查看 data/default/traces/default.jsonl

# 3. 加载已标注的 benchmark
benchmark = EvalBenchmark.from_json("benchmarks/file_ops_v1.json")
```

**标注流水线**：

```
Trace JSONL → Builder 自动提取 → 人工精修 JSON → 运行 Eval
                ↓                        ↓
         expected_tool_calls       expected_output_contains 填写
         (含 result_preview)       

  LLM 指标两种模式：
    session_id 有效且 trace 可读 → 参考式（golden vs actual 对比）
    无 session_id / trace 缺失   → 无参考式（仅用 system_prompt + tools + user_input）
```

### 8.5 运行与对比

```python
# 单次运行
runner = EvalRunner(config)
report = await runner.run_online(agent.chat)

# 对比两次运行（检测回归）
baseline = EvalReport.from_json("reports/baseline.json")
current = EvalReport.from_json("reports/current.json")
diff = EvalComparator().compare(baseline, current)
print(f"Regressions: {diff.regressions}")
```

---

## 9. 演进方向

- **HTML Report**：带 golden vs actual 并排对比的可视化报告
- **CI 集成**：退出码 0（通过）/ 1（退化）
- **并行执行**：多 case 并发运行，`asyncio.Semaphore` 控制并发度
- **Preference 数据导出**：从 trace 文件批量生成 chosen/rejected 对，导出 RLHF 训练格式
- **自动 benchmark 生成**：从多个 trajectory 批量构建，按意图聚类去重
