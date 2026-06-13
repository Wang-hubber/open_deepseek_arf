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

## 产物目录

```
eval/                              # 默认 ./eval/，永久存储（非运行时数据）
├── snapshots/
│   └── a1b2c3d4e5f6.xml           # 配置快照（内容寻址，同配置复用）
├── my_benchmark.json               # benchmark 定义
└── report_my_benchmark.json        # eval report（含 snapshot_hash）
```

每次 eval 运行时自动保存配置快照到 `{eval_dir}/snapshots/{hash}.xml`。

## 配置

```yaml
plugins:
  - eval

plugins_config:
  eval:
    eval_dir: ./eval              # 产物目录（默认 ./eval/）
    trace_dir: ./data/traces       # 读取 trace 的来源
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
