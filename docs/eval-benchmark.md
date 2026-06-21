# Eval — 回归评测体系

## 概念

Eval 系统的核心流程：**从 session trace 构建 Benchmark → 在线重放或离线读取 → 规则 + LLM 双轨指标 → 回归对比**。

```
┌─ BenchmarkBuilder ──┐     ┌─ EvalRunner ──────────────────────────┐
│ build(session_id)   │     │ run_online(chat_fn) / run_offline()   │
│ 读取 trace JSONL    │ ──► │ 逐 case 执行 / 读取 trace             │
│ 切分 user_input     │     │ 逐 metric 计算                        │
│ 提取 expected 字段  │     │ 汇总 EvalReport                       │
└─────────────────────┘     └──────────────┬────────────────────────┘
                                           │
                                           ▼
                                    ┌─ EvalComparator ──┐
                                    │ compare(A, B)     │
                                    │ 逐 case + 汇总 diff│
                                    └───────────────────┘
```

**核心原则**：Benchmark 是"期望行为合约"——只描述 agent 应该调什么工具、输出包含什么关键词，不规定具体参数。Metric 按名称命中即可，全参数匹配没必要。

---

## 数据模型

### EvalCase — 单个评测用例

```python
@dataclass
class EvalCase:
    id: str                          # 唯一标识，如 "case_0"
    input: str                       # 用户消息，作为 chat_fn() 的输入
    session_id: str | None = None    # 来源 trace session
    expected_execution: list[str]    # 预期调用的工具名列表，按序，按名称命中
    expected_output_contains: list[str]  # 预期输出包含的关键词
    max_turns: int | None = None     # 允许的最大 ReAct 步数上限
    feedback: dict | None = None     # 人工标注 {"rating", "reason", "annotated_at"}
    source_round: int | None = None  # 0-based 的 interaction_round 索引
```

**`expected_execution`** 是工具名列表，不是完整调用参数。评测时只检查这些工具名是否在实际 trace 中出现——命中即得分。annotate 模式下占位为 `["[待标注] 预期工具名"]`。

**`expected_output_contains`** 是关键词列表。`OutputContainsMetric` 检查实际最终输出是否包含所有关键词。annotate 模式下占位为 `["[待标注] 该轮预期输出关键词..."]`。

### EvalBenchmark — 评测基准

```python
@dataclass
class EvalBenchmark:
    name: str                        # 基准名称，也是文件名 <name>.json
    source_session: str | None       # 来源 session ID
    created_at: float                # 构建时间戳
    cases: list[EvalCase]            # 有序用例列表
    trace_snapshot_path: str | None  # 冻结 trace 副本路径，如 benchmarks/<name>.trace.jsonl
```

`to_json(path)` / `from_json(path)` 支持 JSON 序列化，`from_json` 会过滤未识别的键。trace 快照与 benchmark JSON 一起写入 `benchmark_dir/`，确保后续评测使用冻结的 golden trace 而非可能被覆盖的原始 session trace。

### EvalSummary — 汇总统计

```python
@dataclass
class EvalSummary:
    total: int      # 总 case 数
    passed: int     # 通过数（success_rate > 0 且 tool_call_accuracy >= 0.5）
    failed: int
    pass_rate: float

    # 按 case 平均
    avg_turns: float
    avg_tool_calls: float
    avg_duration_seconds: float

    # 总计
    total_tokens_in: int
    total_tokens_out: int
    total_duration_seconds: float

    # 各 metric 平均值
    tool_call_accuracy: float      # 工具名命中率
    turn_efficiency: float         # 步数效率
    success_rate: float            # 成功率（无 error 事件）
    execution_accuracy: float      # 工具调用执行准确率
    output_contains: float         # 关键词包含率

    # LLM judge（仅启用时非 None）
    tool_call_result_llm: float | None    # 0-1
    output_quality: float | None          # 1-5
    trajectory_similarity: float | None   # 1-5
    reasoning_similarity: float | None    # 1-5
```

### EvalReport — 完整评测报告

```python
@dataclass
class EvalReport:
    run_id: str                  # 每次运行的 UUID
    benchmark_name: str
    agent_config_hash: str       # 配置快照 hash（12 位）
    timestamp: float
    summary: EvalSummary
    per_case: list[dict]         # 每个 case 的详细结果
    judge_model: str             # LLM judge 模型名（无则 "none"）
    metrics_enabled: list[str]   # 启用的 metric 名列表
    mode: str                    # "online" | "offline"
    snapshot_hash: str           # eval 配置 hash
```

`per_case` 中每条记录：

```python
{
    "case_id": "case_0",
    "passed": True,
    "metrics": {"tool_call_accuracy": 1.0, "success_rate": 1.0, ...},
    "duration_seconds": 2.3,
    "session_id": "eval_xxx",
    "turns": 3,
    "tokens_in": 1500,
    "tokens_out": 200,
    "tool_calls": 2,
}
```

### EvalDiff — 回归对比

```python
@dataclass
class EvalDiff:
    baseline_run_id: str
    current_run_id: str
    summary_diff: dict       # pass_rate, avg_turns, avg_tool_calls 等差值
    regressions: list[dict]  # [{case_id, metric, delta}] delta < -0.001
    improvements: list[dict] # 同上，delta > 0.001
```

### EvalConfig — 评测配置

```python
@dataclass
class EvalConfig:
    benchmark_path: str = ""           # benchmark JSON 路径
    data_dir: str = "./data"           # session 数据目录
    eval_dir: str = "./eval"           # 评测输出目录
    judge: JudgeModelConfig | None     # LLM judge 配置
    judge_model: ResolvedModelConfig | None  # judge 模型连接
    metrics: dict[str, bool]           # metric 开关
    mode: str = "online"               # "online" | "offline"
    trace_session_ids: list[str]       # 离线模式 session ID 列表
    output_path: str | None            # 报告输出路径
    timeout_per_case: float = 300.0    # 单 case 超时秒数
    prompts: dict[str, str]            # LLM metric prompt 覆盖
```

默认启用的 metric：`tool_call_accuracy`, `turn_efficiency`, `success_rate`, `execution_accuracy`, `output_contains`。LLM metric 默认关闭。

---

## Benchmark 构建

### BenchmarkBuilder

入口：`BenchmarkBuilder(trace_reader).build(session_id, name, annotate_mode=False)`

**构建流程：**

1. 从 trace JSONL 读取全量事件
2. 写入冻结的快照：`benchmarks/<name>.trace.jsonl`
3. 按 `user_input` 事件切分 case 边界
4. 每个 case：
   - `input` ← 该 `user_input` 事件的 `data.content`
   - `expected_execution` ← 从 `tool_call_start` 事件的 `data.name` 和 `model_call_end` 事件的 `data.tool_calls[].name` 收集工具名
   - `expected_output_contains` ← annotate 模式为占位文本，否则为 `[]`
   - `max_turns` ← 该 case 内有事件的 distinct turn 数
   - `feedback` ← 该 round 的最新 `user_annotation` 事件
   - `source_round` ← case 索引（0-based）

**annotate_mode=True（无 LLM 配置时）：**

三个评测字段均使用固定占位文案：

| 字段 | 占位值 |
|------|--------|
| `expected_execution` | `["[待标注] 预期工具名"]` |
| `expected_output_contains` | `["[待标注] 该轮预期输出关键词..."]` |

### 使用示例

```python
# 从 session trace 构建 benchmark
builder = BenchmarkBuilder(trace_reader)
bm = builder.build("session_abc123", "my_bench")
bm.to_json("benchmarks/my_bench.json")

# annotate 模式——仅生成占位，供人工标注
bm = builder.build("session_abc123", "my_bench", annotate_mode=True)
```

---

## 评测运行

### EvalRunner

入口：

```python
# 在线模式——通过实时 agent 运行
runner = EvalRunner(config, agent_config)
report = await runner.run_online(agent.chat)

# 离线模式——从已有 trace 读取
report = await runner.run_offline()
```

**在线模式流程（每个 case）：**

1. 调用 `chat_fn(case.input, session_id=sid)` → agent 执行完整一轮
2. 从 `data/{sid}/traces/{sid}.jsonl` 读取全量 trace
3. **按 JSONL append 位置隔离**：`actual_trace = full_trace[_last_event_count:]`（不依赖 turn 号，因为 turn 每轮重置）
4. 对每个启用的 metric 调用 `metric.compute(actual_trace, case)`
5. 提取 trace 统计（turns, tokens, tool_calls）
6. 判定 pass/fail：`success_rate > 0 且 tool_call_accuracy >= 0.5`
7. 追加 per_case 记录

**离线模式流程：**

与在线类似，但跳过 `chat_fn()` 调用，直接从 `config.trace_session_ids[i]` 读取对应 session 的完整 trace。

### 配置 hash 变更检测

运行开始时，Runner 对 `eval_dir` + `benchmark.name` + `metrics` + `mode` + `judge_model` 做 SHA256[:12] hash。如果与 benchmark 记录的历史 hash 一致，打印 "Config unchanged" 提示；否则显示旧/新 hash 对比。

### CLI

```bash
python -m arf.plugins.eval \
  --benchmark benchmarks/my_bench.json \
  --data-dir ./data \
  --mode offline \
  --traces sess_1,sess_2,sess_3 \
  --metrics tool_call_accuracy,turn_efficiency,success_rate \
  --output eval/report.json
```

在线模式需通过 Python API 使用，CLI 仅支持离线模式。

---

## 评测指标

### 规则类 Metric（不需 LLM）

#### SuccessRateMetric → `success_rate`

检查 trace 中是否出现 `error` 事件。有 error = 失败（0），无 error = 成功（1）。

```
success_rate = 0 if any event type is "error" else 1
```

#### ToolCallAccuracyMetric → `tool_call_accuracy`

按名称匹配：检查 `expected_execution` 中的每个工具名是否在 `actual_trace` 的 `tool_call_start` 事件中出现。

```
matches = count(exp_name in actual_names for exp_name in expected_execution)
total = max(len(expected_execution), len(actual_calls))
tool_call_accuracy = matches / total
```

额外检测 **依赖顺序失败**（`dependency_order_failures`）：扫描 `tool_call_end` 的 `data.error`，如果包含 `depends_on`、`blocked`、`not ready`、`dependency`、`must complete`、`waiting for`、`prerequisite` 等关键词，则计数。

#### TurnEfficiencyMetric → `turn_efficiency`

```
turn_efficiency = max_turns / actual_turns  （cap 1.0）
```
若 case 未设置 `max_turns`，直接返回 1.0。

#### OutputContainsMetric → `output_contains`

在 `model_call_end` 事件的 `data.content` 中搜索 `expected_output_contains` 中的每个关键词。

```
output_contains = matches / len(expected_output_contains)
```
若 `expected_output_contains` 为空，返回 1.0。

#### ExecutionAccuracyMetric → `execution_accuracy`

与 `ToolCallAccuracyMetric` 相同——按名称匹配 `expected_execution` 中的工具名。

```
execution_accuracy = matches / len(expected_execution)
```

### LLM-as-Judge Metric（需 Judge 模型）

#### ToolCallResultLLMMetric → `tool_call_result_llm`

比较工具调用结果的语义等价性。将 `expected_execution` 中带 `result` 的条目（dict 格式，向后兼容）按名称匹配到实际的 `tool_call_end` 事件，然后调用 LLM judge 判断结果是否等价。

```
分数: 0.0-1.0 (匹配率)
```

#### OutputQualityMetric → `output_quality`

评估最终输出的质量。两种模式：
- **Reference 模式**：从 frozen trace snapshot 读取 golden 输出，与当前输出比较
- **No-reference 模式**：仅根据 system prompt 和 tools 上下文评估当前输出

```
分数: 1-5 (Likert scale)
```

#### TrajectorySimilarityMetric → `trajectory_similarity`

评估 agent 执行轨迹与 golden trajectory 的相似度。将工具调用序列总结为文本，交给 LLM judge 比较。

```
分数: 1-5 (Likert scale)
```

---

## 回归对比

### EvalComparator

```python
comparator = EvalComparator()
diff = comparator.compare(baseline_report, current_report)
```

**对比逻辑：**
- 校验两个 report 的 `benchmark_name` 一致
- `summary_diff`：6 个关键字段的差值（pass_rate, avg_turns, avg_tool_calls, avg_duration_seconds, tool_accuracy, output_contains）
- 逐 case 对比 `tool_accuracy` 和 `output_contains`
- `delta < -0.001` → regression；`delta > 0.001` → improvement

---

## Trace 事件 schema

JSONL 每行一个 `AgentEvent`：

| 字段 | 类型 | 使用者 |
|------|------|--------|
| `type` | str | 所有 metric（事件类型分派） |
| `turn` | int | TurnEfficiencyMetric, trace stats |
| `timestamp` | float | 时间排序 |
| `session_id` | str | 文件关联 |
| `data.content` | str | OutputContainsMetric, OutputQualityMetric |
| `data.name` | str | ToolCallAccuracyMetric, ExecutionAccuracyMetric |
| `data.tool_name` | str | 旧格式兼容 |
| `data.arguments` | str/dict | ToolCallAccuracyMetric |
| `data.success` | bool | ToolCallAccuracyMetric |
| `data.result` | str | ToolCallResultLLMMetric |
| `data.error` | str | 依赖错误检测 |
| `data.usage.prompt_tokens` | int | Trace stats |
| `data.usage.completion_tokens` | int | Trace stats |
| `data.tool_calls` | list[dict] | BenchmarkBuilder 工具名提取 |
| `data.round` | int | round_end 事件携带的 round 编号 |
| `data.feedback` | str | user_annotation 事件的人工评分 |
| `data.reason` | str | user_annotation 事件的评分理由 |

**关键事件类型**：`session_start`, `user_input`, `tool_call_start`, `tool_call_end`, `model_call_end`, `round_end`, `error`, `user_annotation`, `config_mismatch`, `snapshot_created`。

---

## 文件布局

```
arf/plugins/eval/
├── __init__.py          # 公开 API 重导出 + EvalPlugin
├── models.py            # EvalCase, EvalBenchmark, EvalSummary, EvalReport, EvalDiff, EvalConfig
├── builder.py           # BenchmarkBuilder — 从 session trace 构建 benchmark
├── runner.py            # EvalRunner — 执行 benchmark，收集 metric，产出 report
├── metrics.py           # 全部 metric 类（规则 + LLM judge）
├── comparator.py        # EvalComparator — 两个 report 的回归对比
├── trace_adapter.py     # events_to_trace — AgentEvent 列表 → 结构化 trace dict
├── exceptions.py        # EvalError, EvalJudgeError
├── plugin.yaml          # 插件清单
└── plugin.py            # Legacy EvalPlugin（离线专用）
```

```
典型数据目录结构：

benchmarks/
├── my_bench.json           # EvalBenchmark JSON
└── my_bench.trace.jsonl    # 冻结的 golden trace 快照

data/{session_id}/
├── traces/{session_id}.jsonl  # session trace
└── state/{session_id}.json    # session state

eval/
├── reports/                   # EvalReport JSON 输出
└── snapshots/                 # 旧 XML 快照（已弃用）
```
