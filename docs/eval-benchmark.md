# Eval Benchmark — 会话回放与回归检测

ARF 提供基于真实会话 trace 的回归测评机制：从 `FileTraceStore` 的会话存档创建 benchmark，通过 `EventBus` 采集真实执行轨迹重放对话，跨配置文件/模型切换对比运行报告。

---

## 1. OS 方案演进

### 1.1 回归测试与 CI

**问题**：如何确保 Agent 配置、框架代码或模型版本的变更不会导致已有能力退化？

**传统方案**：CI 系统的回归测试套件。开发者在每次变更后运行固定的测试用例集合，对比历史基线结果，自动发现退化（regression）。

ARF 的 `EvalRunner` 直接对应回归测试运行器：输入 benchmark（测试用例集），输出 report（每个用例的通过/失败 + 量化指标）。`EvalComparator` 负责 diff 两版 report，标记退化和改善。

### 1.2 会话回放（Session Replay）

**问题**：Agent 的"正确行为"很难用静态断言描述。如何从真实用户对话中提取高质量的测试用例？

**方案**：会话回放。`BenchmarkBuilder` 从 `FileTraceStore` 中的真实对话 trace 提取用户消息序列，自动推断预期工具调用和输出关键词，生成可编辑的 benchmark JSON。

### 1.3 对 ARF 的启示

| OS/CI 概念 | ARF 对应 |
|-----------|----------|
| 回归测试套件 | `EvalBenchmark` — 一组 `EvalCase` |
| 测试运行器 | `EvalRunner.run()` |
| 测试报告 | `EvalReport` — per_case + summary |
| 历史基线 | `EvalReport.to_json()` 持久化到 `reports/` |
| 回归检测 | `EvalComparator.compare(baseline, current)` → `EvalDiff` |
| 会话回放 | `BenchmarkBuilder.build(session_id, name)` |
| CI 集成 | 命令行 `python -c "runner.run(bm)"` 退出码 |

---

## 2. 架构

```
真实对话 → FileTraceStore → memory/sessions/{session}.json
                                    │
                           BenchmarkBuilder.build(session_id, name)
                                    │
                                    ▼
                           benchmarks/{name}.json (用户可编辑)
                                    │
                           EvalRunner.run(benchmark)
                                    │
                     ┌──────────────┼──────────────┐
                     ▼              ▼              ▼
               agent.chat()   EventBus采集    metrics.compute()
                     │              │              │
                     ▼              ▼              ▼
               EvalReport A    EvalReport B    EvalComparator.compare(A, B)
                                                        │
                                                        ▼
                                                    EvalDiff
```

---

## 3. API 参考

### 3.1 Benchmark 创建

```python
from arf.evaluation import BenchmarkBuilder
from arf.observability.file_trace import FileTraceStore

store = FileTraceStore(agent.event_bus, dir="./memory/sessions")
builder = BenchmarkBuilder(store)

# 从真实对话会话创建 benchmark
benchmark = builder.build(session_id="default", name="file_ops_v1")
benchmark.to_json("benchmarks/file_ops_v1.json")

# 用户手动编辑 JSON 后可重新加载
from arf.evaluation import EvalBenchmark
benchmark = EvalBenchmark.from_json("benchmarks/file_ops_v1.json")
```

### 3.2 运行 Benchmark

```python
from arf.evaluation import EvalRunner

runner = EvalRunner(agent, agent.event_bus)
report = await runner.run(benchmark)
report.to_json("reports/file_ops_v1_baseline.json")
```

### 3.3 对比运行报告

```python
from arf.evaluation import EvalComparator, EvalReport

baseline = EvalReport.from_json("reports/file_ops_v1_baseline.json")
current = EvalReport.from_json("reports/file_ops_v1_20260528.json")

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
| `id` | `str` | 用例 ID，如 `case_0` |
| `input` | `str` | 用户消息文本 |
| `expected_tools` | `list[str] \| None` | 预期调用的工具名列表，自动提取 |
| `expected_output_contains` | `list[str] \| None` | 预期输出包含的关键词列表 |
| `max_turns` | `int \| None` | 预期最大轮次数 |

### EvalBenchmark

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | `str` | Benchmark 名称 |
| `source_session` | `str \| None` | 来源 session ID |
| `created_at` | `float` | 创建时间戳 |
| `cases` | `list[EvalCase]` | 测试用例列表 |

### EvalReport

| 字段 | 类型 | 说明 |
|------|------|------|
| `run_id` | `str` | 运行 ID (UUID) |
| `benchmark_name` | `str` | 关联的 benchmark 名 |
| `agent_config_hash` | `str` | Agent 配置的 SHA256 摘要 |
| `timestamp` | `float` | 运行时间戳 |
| `summary` | `EvalSummary` | 汇总统计 |
| `per_case` | `list[dict]` | 每个用例的详细结果 (含 trace) |

### EvalSummary

| 字段 | 说明 |
|------|------|
| `total` / `passed` / `failed` | 用例计数 |
| `pass_rate` | 通过率 |
| `avg_turns` | 平均轮次数 |
| `avg_tool_calls` | 平均工具调用数 |
| `avg_duration_seconds` | 平均耗时 |
| `tool_accuracy` | 工具调用准确率 |
| `output_contains` | 输出关键词匹配率 |

---

## 5. 指标说明

### SuccessRateMetric

trace 所有 turn 中无 error 事件 → 1.0，有 → 0.0

### ToolAccuracyMetric

实际调用的工具序列 vs `expected_tools`，按顺序匹配。比例 = 匹配数 / `len(expected_tools)`

### TurnEfficiencyMetric

返回 trace 中的 turn 总数

### OutputContainsMetric

最后一个 model_output 中包含 `expected_output_contains` 中关键词的比例

---

## 6. 配置

```yaml
# 无需额外配置。eval 模块在 arf/evaluation/ 下自动可用。
# Benchmark 和 report 的存储路径由 App 层决定，API 接受任意相对/绝对路径：
#   benchmarks/{name}.json → 用户创建和编辑的 benchmark JSON
#   reports/{name}.json    → runner 输出的 report JSON
#   memory/sessions/       → FileTraceStore 写入的真实对话 trace
```

---

## 7. 演进方向

- **CLI 集成**：`python cli.py eval run <benchmark>` / `python cli.py eval compare <baseline> <current>`
- **HTML Report**：带瀑布图的可视化对比报告
- **CI 集成**：退出码 0（通过）/ 1（退化）的 CI 就绪模式
- **语义相似度 Metric**：用 LLM 评估输出内容的语义差异，而非关键词匹配
- **增量 Benchmark 更新**：基于真实对话自动追加新用例
