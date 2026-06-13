# Eval Plugin — 回归评测系统

离线评测框架。从 trace 构建基准，回放对比，计算多维度指标，生成 diff 报告。

---

## 组件

| 组件 | 文件 | 说明 |
|------|------|------|
| `EvalRunner` | `runner.py`（369行） | 运行在线/离线基准，收集指标，产出 EvalReport |
| `BenchmarkBuilder` | `builder.py`（173行） | 从 trace JSONL 提取 EvalCase |
| `EvalComparator` | `comparator.py`（46行） | Diff 两次基准运行，检测回归/改进 |
| Metrics | `metrics.py`（656行） | 6 种 LLM 评测指标 |
| Models | `models.py`（248行） | EvalCase / EvalBenchmark / EvalReport 数据模型 |

## 6 种评测指标

| 指标 | 说明 |
|------|------|
| `SuccessRateMetric` | 任务成功率 |
| `ToolCallAccuracyMetric` | 工具调用名称 + 参数子集匹配，依赖顺序感知 |
| `ToolCallResultLLMMetric` | LLM 判断语义等价 |
| `TurnEfficiencyMetric` | ReAct 步数效率 |
| `OutputQualityMetric` | LLM 评分 1-5（支持带/不带参考） |
| `TrajectorySimilarityMetric` | 与 golden_trajectory 的相似度（LLM 评分 1-5） |

## 配置

```yaml
plugins:
  - eval

plugins_config:
  eval:
    judge:
      model: deepseek-v4
      system_prompt: "You are an expert evaluator..."
```

## 使用

```python
# 回放 trace 并计算指标
plugin = EvalPlugin(config)
report = await plugin.run_eval(trace_session_id="abc123", target_model="deepseek-v4")

# 比较两次运行
diff = EvalComparator.compare(report1, report2)
```
