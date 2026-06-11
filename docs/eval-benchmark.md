# Eval Benchmark — 测评与回归检测

> **Eval 是独立的 CLI 模块。接收 benchmark + trace，产出多维量化报告。裁判 LLM 与被测 Agent 完全独立。**

ARF 提供基于 trajectory trace 的多维测评机制：从真实会话 trace 构建 golden benchmark，通过在线执行或离线回放对比实际输出，支持规则匹配和 LLM-as-judge 两种评判方式。

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
| Golden 数据 | `EvalCase.golden_trajectory` — 完整多轮轨迹 |
| LLM 裁判 | `OutputQualityMetric` / `TrajectorySimilarityMetric` |

---

## 2. 架构

```
真实对话 → TracePlugin → {trace_dir}/{session}.jsonl
                                    │
                           BenchmarkBuilder.build(session_id, name)
                                    │
                                    ▼
                           benchmarks/{name}.json (含 golden_trajectory, 人类可编辑)

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
from arf.evaluation import BenchmarkBuilder
from arf.plugins.trace.plugin import TracePlugin

trace = TracePlugin({"trace_dir": "./data/traces"})

builder = BenchmarkBuilder(trace)

# 从真实对话 session 创建 benchmark
benchmark = builder.build(session_id="default", name="file_ops_v1")
benchmark.to_json("benchmarks/file_ops_v1.json")

# 人类编辑 JSON 后重新加载
from arf.evaluation.models import EvalBenchmark
benchmark = EvalBenchmark.from_json("benchmarks/file_ops_v1.json")
```

产出的 benchmark JSON 包含 `golden_trajectory`：

```json
{
  "name": "file_ops_v1",
  "source_session": "default",
  "cases": [
    {
      "id": "case_0",
      "input": "帮我读一下 README.md",
      "expected_tools": ["read"],
      "expected_output_contains": ["ARF", "Framework"],
      "max_turns": 1,
      "golden_trajectory": {
        "turns": [
          {
            "turn": 1,
            "assistant": {
              "content": "好的，我来读取",
              "tool_calls": [{"name": "read", "params": {"path": "README.md"}}]
            },
            "tool_results": [
              {"tool_name": "read", "result": "# ARF Framework...", "success": true}
            ],
            "assistant_final": {"content": "README.md 的内容是：ARF 是一个..."}
          }
        ]
      }
    }
  ]
}
```

人类可直接编辑 `golden_trajectory` 中的 `assistant.content` 来构造 golden answer，删除 `assistant_final` 让测评只关注工具调用。

### 3.2 运行 Eval（Online）

```python
import asyncio
from arf.evaluation import EvalRunner
from arf.evaluation.models import EvalConfig, JudgeModelConfig

config = EvalConfig(
    benchmark_path="benchmarks/file_ops_v1.json",
    trace_dir="./data/traces",
    judge=JudgeModelConfig(
        api_base="https://api.deepseek.com",
        api_key_env="DEEPSEEK_API_KEY",
        model="deepseek-chat",
        temperature=0.0,
    ),
    metrics={
        "tool_call_accuracy": True,
        "turn_efficiency": True,
        "success_rate": True,
        "output_quality": True,
    },
)

runner = EvalRunner(config)
report = await runner.run_online(agent.chat)
report.to_json("reports/file_ops_v1_20260611.json")
```

### 3.3 运行 Eval（Offline）

```python
config = EvalConfig(
    benchmark_path="benchmarks/file_ops_v1.json",
    trace_dir="./data/traces",
    mode="offline",
    trace_session_ids=["s1", "s2", "s3"],  # 对应 benchmark 中的 3 个 cases
)

runner = EvalRunner(config)
report = await runner.run_offline()
```

### 3.4 CLI

```bash
# Offline
python -m arf.evaluation run \
  --benchmark benchmarks/file_ops_v1.json \
  --trace-dir ./data/traces \
  --mode offline \
  --traces s1,s2,s3 \
  --metrics tool_call_accuracy,turn_efficiency,output_quality \
  --judge-api-base https://api.deepseek.com \
  --judge-model deepseek-chat \
  --output report.json

# Online (Python API)
python -c "
import asyncio
from arf.evaluation import EvalRunner
from arf.evaluation.models import EvalConfig
config = EvalConfig(benchmark_path='benchmarks/file_ops_v1.json', ...)
runner = EvalRunner(config)
asyncio.run(runner.run_online(agent.chat))
"
```

### 3.5 对比运行报告

```python
from arf.evaluation import EvalComparator, EvalReport

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
| `expected_tools` | `list[str] \| None` | 预期调用的工具名列表（name-only，向后兼容） |
| `expected_tool_calls` | `list[dict] \| None` | 预期工具调用（含 name/params/result），按名称与 actual 配对 |
| `expected_output_contains` | `list[str] \| None` | 预期输出包含的关键词 |
| `max_turns` | `int \| None` | 预期最大轮次数 |
| `golden_trajectory` | `dict \| None` | 完整 golden trajectory，可用于 SFT |

`expected_tool_calls[i]` 结构：`{"name": "eat", "params": {"name": "良子"}, "result": "吃完了"}`。`params` 和 `result` 可选——不标则只比名称。

### JudgeModelConfig

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `api_base` | `str` | `https://api.openai.com/v1` | OpenAI 兼容 API 地址 |
| `api_key_env` | `str` | `OPENAI_API_KEY` | API key 环境变量名 |
| `model` | `str` | `gpt-4` | 裁判模型 |
| `temperature` | `float` | `0.0` | 裁判需确定性 |
| `max_tokens` | `int` | `2000` | 回复 token 上限 |

### EvalConfig

| 字段 | 说明 |
|------|------|
| `benchmark_path` | Benchmark JSON 文件路径 |
| `trace_dir` | Trace 文件目录 |
| `judge` | `JudgeModelConfig \| None` |
| `metrics` | 5 维 开关 dict |
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

---

## 5. Metrics

### 规则层

| Metric | 方法 | 输出 |
|--------|------|------|
| `SuccessRateMetric` | trace 中是否有 error 事件 | 0 或 1 |
| `ToolCallAccuracyMetric` | 按名称配对：name + params 子集匹配。`expected_tool_calls` 优先，`expected_tools` 兜底。同步统计 dependency_order_failures | 0–1 + dep_fail 计数 |
| `TurnEfficiencyMetric` | 实际 turn 数 vs `max_turns` | 0–1 |

**ToolCallAccuracyMetric 匹配策略**：

1. 优先使用 `expected_tool_calls`（如果非空），按**名称**与 actual 配对（不关注执行顺序）
2. 每个 expected item 在 actual 中找同名的、params 子集匹配的，找到即算命中
3. 字符串参数用**子串匹配**（`"焖子"` in `"良子的焖子"`），非字符串用 `==`
4. actual 可以多出额外参数（如框架注入的 `_workspace`），不影响匹配
5. actual 多出 expected 没有的工具 → 降低总分（total 取 max(expected, actual)）
6. `expected_tool_calls=None` 时退化为 `expected_tools` 的 name-only 模式
7. 同步扫描 `tool_call_end` 事件：`success=false` 且 `error` 包含依赖关键词（`depends_on`、`blocked`、`not ready`、`not complete`、`dependency`、`must complete`、`waiting for`、`prerequisite`）→ 计入 `dependency_order_failures`

### LLM-as-judge

| Metric | 方法 | 输出 |
|--------|------|------|
| `OutputQualityMetric` | LLM 对比 final output vs golden，1-5 打分 | score + reason |
| `TrajectorySimilarityMetric` | LLM 对比完整 actual trajectory vs golden trajectory，1-5 打分 | score + reason |
| `ToolCallResultLLMMetric` | LLM 对比 expected vs actual tool results 语义等价，按名称配对 | 0–1 |

**ToolCallResultLLMMetric** 用于评估工具**返回值**的语义一致性。`ToolCallAccuracyMetric` 负责名称和参数（程序化、零开销），`ToolCallResultLLMMetric` 负责结果语义（需 judge LLM）。`expected.result` 可从 golden trajectory 自动提取，人工标注是可选的优化——当 golden result 太冗长时，人工可以改成松散的语义描述让 LLM 判得更准。推荐先跑程序化 metric，只在需要时开启 LLM 裁判。

LLM metrics 使用 OpenAI API 兼容接口，`temperature=0.0`。如果开启 LLM metric 但未配置 `judge` → `EvalConfig.validate()` 抛错。

---

## 6. 人工标注指南

### 6.1 自动构建 → 人工精修

`BenchmarkBuilder.build()` 自动从 trace 提取 `expected_tool_calls`（含 name + params + result）和 `golden_trajectory`。产出的 benchmark JSON 可作为起点，人工标注做三件事：

1. **删**：移除不关键的 turn（如中间探索性的 glob）
2. **改**：修正 `expected_output_contains` 预期关键词，缩紧 `max_turns`
3. **标**：给关键 tool_call 写 `params` 约束和 `result` 预期

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

### 6.3 标注注意事项

- **按名称匹配**：评估时按工具名配对，不关注执行顺序。并行 tool_call 返回顺序不确定也不影响评分
- **依赖顺序由工具自行校验**：skill 内部工具的依赖关系（如 `plan_create` 必须在 `plan_dispatch` 之前）由框架在运行时 enforce，违反时 `tool_call_end.success=false`，`ToolCallAccuracyMetric` 自动统计为 `dependency_order_failures`
- **多轮对话**：每轮（一个 user input → 最终 text response）一个 EvalCase。如果一轮中有多个 tool_call，全放在同一个 `expected_tool_calls` 数组里
- **params 标关键字段即可**：不用标全量参数，标对决策有影响的字段（如 `path`、`pattern`、`name`）。框架自动注入的参数（`_workspace`）不要标
- **result 可标可不标**：不标时 `ToolCallResultLLMMetric` 自动跳过（返回 1.0）。如需 LLM 裁判结果语义，写 "返回了用户列表 JSON" 而非原始返回值全文。LLM 做语义等价判断
- **程序化优先**：工具名称和参数走 `ToolCallAccuracyMetric`（零开销），只在需要判断结果语义时开启 `ToolCallResultLLMMetric`（需 judge LLM）
- **向后兼容**：已有 benchmark 的 `expected_tools` 无需迁移，ToolCallAccuracyMetric 自动 fallback 到 name-only 模式

---

## 7. 终端输出

```
 Eval Report: file_ops_v1
 ==================================================
 Mode: online   Judge: gpt-4   Hash: a1b2c3d4

 Cases: 5 to run

  [OK] case_0: tool_acc=1.00, turn_eff=1.00, quality=4/5   2.3s
  [OK] case_1: tool_acc=1.00, turn_eff=1.00, quality=5/5   3.1s
  [FAIL] case_2: tool_acc=0.50, turn_eff=1.00, quality=3/5   4.2s
         input: 帮我读一下 README.md 和 config.yaml
  [OK] case_3: tool_acc=1.00, turn_eff=0.50   2.8s
  [OK] case_4: tool_acc=1.00, turn_eff=1.00, quality=5/5, traj_sim=5/5   1.9s

 --------------------------------------------------
 Summary: 4/5 passed (80.0%)
   Tool accuracy:     0.90
   Turn efficiency:   0.90
   Output quality:    4.2/5 (LLM)
   Trajectory sim:    5.0/5 (LLM)

 1 failed case(s):
   case case_2: ['tool_call_accuracy']
```

---

## 8. 演进方向

- **HTML Report**：带 golden vs actual 并排对比的可视化报告
- **CI 集成**：退出码 0（通过）/ 1（退化）
- **并行执行**：多 case 并发运行，`asyncio.Semaphore` 控制并发度
- **Preference 数据导出**：从 golden_trajectory 生成 chosen/rejected 对，导出 RLHF 训练格式
- **自动 benchmark 生成**：从多个 trajectory 批量构建，按意图聚类去重
