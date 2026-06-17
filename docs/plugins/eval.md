# Eval Plugin — 回归评测系统

离线评测框架。从 trace 构建 benchmark，回放对比，计算 9 种指标，生成 diff 报告。不是 hook 插件——hooks 返回空，显式调用。

---

## 1. 组件

| 组件 | 说明 |
|------|------|
| `EvalRunner` | 运行 online/offline benchmark，收集指标，产出 `EvalReport` |
| `BenchmarkBuilder` | 从 trace JSONL 提取 `EvalCase`，冻结 trace 快照 |
| `EvalComparator` | Diff 两次运行，检测回归/改进 |
| Metrics (9 种) | 规则化 + LLM-as-judge 指标 |
| Models | `EvalCase` / `EvalBenchmark` / `EvalReport` / `EvalSummary` / `EvalConfig` / `EvalDiff` |

---

## 2. EvalCase 结构

`EvalCase` 仅保留评测合约字段，不包含 golden 输出（改为从 trace 按需读取）：

| 字段 | 说明 |
|------|------|
| `id` | 唯一标识 |
| `input` | 用户输入 |
| `session_id` | 来源 session |
| `source_round` | 来源 round 编号 |
| `expected_execution` | 期望工具调用（含 `result_preview`） |
| `expected_output_contains` | 期望输出包含的关键词列表（用于 `OutputContainsMetric`） |
| `max_turns` | 最大 ReAct 步数限制 |
| `feedback` | 用户反馈注释（thumbs_up / thumbs_down） |

---

## 3. 9 种指标

### 规则化指标（无需 LLM judge）

| 指标 | 说明 | 默认启用 |
|------|------|---------|
| `success_rate` | 任务成功率（异常/超时 → 失败） | ✓ |
| `tool_call_accuracy` | 工具调用名称 + 参数子集匹配，顺序感知 | ✓ |
| `turn_efficiency` | ReAct 步数 vs golden 步数比值 | ✓ |
| `execution_accuracy` | 期望工具调用是否全部出现 | ✓ |
| `output_contains` | 关键词匹配——实际输出是否包含 `expected_output_contains` 全部关键词 | ✓ |

### LLM-as-judge 指标（需 judge model）

| 指标 | 说明 | 默认启用 |
|------|------|---------|
| `output_quality` | LLM 评分 1-5（支持参考模式/自由模式） | ✗ |
| `trajectory_similarity` | 与 golden trajectory 相似度（LLM 评分 1-5） | ✗ |
| `tool_call_result_llm` | LLM 判断工具调用结果语义等价（0-1） | ✗ |
| `reasoning_similarity` | 推理路径相似度（LLM 评分） | ✗ |

LLM judge 指标需要配置 `judge_model`。未配但启用 → `validate()` 抛错。

---

## 4. Online vs Offline

| 模式 | 数据来源 | 说明 |
|------|---------|------|
| `online` | 调用 `chat_fn(input, session_id)` | 实时运行 agent，适合回归测试 |
| `offline` | 已有 trace JSONL | 直接读取 trace 中的 final output，适合批量分析 |

```python
runner = EvalRunner(config)

# Online: 传入 agent chat 函数
report = await runner.run_online(agent.chat, system_prompt="...", tools="...")

# Offline: 从已有 trace 读取，无需 agent
report = await runner.run_offline(system_prompt="...", tools="...")
```

---

## 5. Benchmark 构建

`BenchmarkBuilder` 从 trace 会话构建 benchmark：

```python
builder = BenchmarkBuilder(trace_plugin)
benchmark = builder.build(
    session_id="abc123",
    name="my_benchmark",
    annotate_mode=False,   # True → 交互式标注模式
)
benchmark.to_json("benchmarks/my_benchmark.json")
```

构建时自动冻结 trace 快照到 `benchmarks/{name}.trace.jsonl`，保证 gold reference 不受后续会话影响。

---

## 6. EvalReport 结构

```python
EvalReport(
    run_id="uuid",
    benchmark_name="my_benchmark",
    agent_config_hash="a1b2c3",
    snapshot_hash="d4e5f6",
    mode="online",          # "online" | "offline"
    judge_model="gpt-4",
    metrics_enabled=["success_rate", "tool_call_accuracy", ...],
    summary=EvalSummary(
        total=10, passed=8, failed=2, pass_rate=0.8,
        avg_turns=3.2, avg_tool_calls=2.1,
        total_tokens_in=45000, total_tokens_out=8200,
        tool_call_accuracy=0.9, output_contains=0.85,
        output_quality=4.2,           # None if disabled
        trajectory_similarity=3.8,    # None if disabled
    ),
    per_case=[...],
)
```

---

## 7. Diff 比较

```python
from arf.plugins.eval.comparator import EvalComparator

diff: EvalDiff = EvalComparator.compare(report1, report2)
# diff.regressions   — list[dict] 退化的 case
# diff.improvements  — list[dict] 改进的 case
# diff.summary_diff   — dict 指标变化
```

---

## 8. CLI

```bash
python -m arf.plugins.eval run \
  --benchmark benchmarks/my_bench.json \
  --data-dir ./data \
  --mode offline \
  --traces abc123 \
  --judge-model gpt-4 \
  --metrics "tool_call_accuracy,turn_efficiency,success_rate" \
  --output reports/report.json
```

---

## 9. 配置

```yaml
plugins:
  - eval

plugins_config:
  eval:
    eval_dir: ./eval                        # 产物目录
    judge:
      system_prompt: "You are an expert..."  # judge system prompt
      response_format: {"type": "json_object"}  # 可选
    metrics:                                  # 指标开关
      tool_call_accuracy: true
      turn_efficiency: true
      success_rate: true
      execution_accuracy: true
      output_contains: true
      output_quality: false
      trajectory_similarity: false
      tool_call_result_llm: false
      reasoning_similarity: false
    timeout_per_case: 300.0
    prompts:                                  # 自定义 judge prompt
      output_quality: "..."
      trajectory_similarity: "..."
```

judge 模型从 `plugins_config.eval` 的 `model_defs` 解析，框架自动注入 `ResolvedModelConfig`。
