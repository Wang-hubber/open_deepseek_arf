# Eval Benchmark — 会话回放与回归检测

## 动机

`DefaultEvalRunner.run()` 调 `agent.chat()` 后硬编码 `trace = {"turns": []}`，
4 个 MetricCalculator 始终在空数据上计算，评估系统完全无效。

修复方向：通过 `FileTraceStore` 的会话存档创建 benchmark，用 `EventBus` 增量采集
真实 trace，使 metrics 返回有意义的数值，支持跨配置/跨模型的回归对比。

## 架构

```
真实对话 → FileTraceStore → memory/traces/{session_id}.json
                                        │
                               BenchmarkBuilder.build(session_id)
                                        │
                                        ▼
                               benchmarks/{name}.json (用户可编辑)
                                        │
                               EvalRunner.run(benchmark)
                                        │
                          ┌─────────────┼─────────────┐
                          ▼             ▼             ▼
                    agent.chat()   EventBus采集   metrics.compute()
                          │             │             │
                          ▼             ▼             ▼
                    EvalReport A   EvalReport B   EvalComparator.compare(A, B)
```

## 文件布局（继承 AppContext 约定）

```
{root}/
├── memory/traces/               # FileTraceStore → {session_id}.json (已有)
├── benchmarks/                  # 用户创建/编辑的 benchmark JSON (新增)
│   └── {name}.json
└── reports/                     # eval.run() 输出 (新增)
    └── {name}_{date}.json
```

## 数据模型

### EvalBenchmark

```python
@dataclass
class EvalCase:
    id: str                                      # case_0, case_1 ...
    input: str                                   # 用户消息
    expected_tools: list[str] | None = None      # 从 trace 自动提取，用户可编辑
    expected_output_contains: list[str] | None = None
    max_turns: int | None = None

@dataclass
class EvalBenchmark:
    name: str
    source_session: str | None = None            # 来源 session，手动构造时为 None
    created_at: float = 0.0
    cases: list[EvalCase] = field(default_factory=list)

    def to_json(self, path: str) -> None: ...
    @classmethod
    def from_json(cls, path: str) -> "EvalBenchmark": ...
```

### EvalReport

```python
@dataclass
class EvalSummary:
    total: int; passed: int; failed: int
    pass_rate: float
    avg_turns: float; avg_tool_calls: float; avg_duration_seconds: float
    tool_accuracy: float; output_contains: float

@dataclass
class EvalReport:
    run_id: str
    benchmark_name: str
    agent_config_hash: str           # agent.yaml 的 SHA256，追踪配置变更
    timestamp: float
    summary: EvalSummary
    per_case: list[dict]             # 每个 case 的详细结果 + trace

    def to_json(self, path: str) -> None: ...
    @classmethod
    def from_json(cls, path: str) -> "EvalReport": ...
```

### EvalDiff

```python
@dataclass
class EvalDiff:
    baseline_run_id: str
    current_run_id: str
    summary_diff: dict               # 每个 summary 字段的 delta
    regressions: list[dict]          # pass_rate 下降、tool_accuracy 下降 的 case
    improvements: list[dict]
```

## 组件 API

### 1. EventBus 新增（框架层）

```python
class InMemoryEventBus:
    def event_count(self) -> int: ...
    def events_since(self, index: int) -> list[AgentEvent]: ...
```

### 2. events_to_trace()（框架层，新增）

```python
def events_to_trace(events: list[AgentEvent]) -> dict:
    """扁平 AgentEvent 列表 → {turns: [{tool_calls, model_output, error, duration_ms}]}"""
```

### 3. BenchmarkBuilder（框架层，新增）

```python
class BenchmarkBuilder:
    def __init__(self, trace_store: FileTraceStore): ...

    def build(self, session_id: str, name: str) -> EvalBenchmark:
        """从真实会话 trace 创建 benchmark。
        每条 user_input 成为一个 case。
        expected_tools / expected_output_contains 从对应 turn 的 trace 推断。
        """
```

### 4. EvalRunner（框架层，重写）

```python
class EvalRunner:
    def __init__(self, agent, event_bus: InMemoryEventBus): ...

    async def run(self, benchmark: EvalBenchmark, *,
                  max_parallel: int = 1) -> EvalReport:
        """逐个 case 调 agent.chat(case.input, session_id=eval_{benchmark}_{case.id})，
        通过 EventBus.events_since() 增量采集 trace，计算所有 metric。"""
```

### 5. EvalComparator（框架层，新增）

```python
class EvalComparator:
    def compare(self, baseline: EvalReport, current: EvalReport) -> EvalDiff:
        """逐指标 delta，标记 regressions (退化) 和 improvements (改善)"""
```

### 6. MetricCalculator（已有，不动）

```python
class SuccessRateMetric:    # trace turns 中无 error → 1.0，有 → 0.0
class ToolAccuracyMetric:   # 实际 tool 调用 vs expected_tools 交集率
class TurnEfficiencyMetric: # turn 总数
class OutputContainsMetric: # 输出关键词匹配率
```

## 执行流程

### create_benchmark

```
BenchmarkBuilder.build(session_id, name)
  → FileTraceStore.load(session_id)
  → 提取所有 type="user_input" 事件 → 每条作为一个 EvalCase
  → 为每个 case 提取:
      - expected_tools: 该 turn 内 tool_call_start 事件的 tool_name 列表
      - expected_output_contains: 从 model_call_end 的 content 提取关键词（基础实现：
        按空格分词取前 3 个长词）
      - max_turns: len(turns) * 2
  → 返回 EvalBenchmark
```

### eval.run

```
for each case in benchmark.cases:
    start_idx = event_bus.event_count()
    session_id = f"eval_{benchmark.name}_{case.id}"
    try:
        response = await agent.chat(case.input, session_id=session_id)
        events = event_bus.events_since(start_idx)
        trace = events_to_trace(events)
        case_result = {"passed": True, "trace": trace, "response": response}
        for metric in metrics:
            case_result["metrics"].update(await metric.compute(trace, case))
    except Exception as e:
        case_result = {"passed": False, "trace": {"turns": []}, "error": str(e)}
→ 汇总 EvalSummary → 返回 EvalReport (含 agent_config_hash)
```

### eval.compare

```
EvalComparator.compare(baseline, current):
    for each summary field:
        summary_diff[field] = current.summary[field] - baseline.summary[field]
    for each case in current.per_case:
        baseline_case = find matching case in baseline
        if current case regressed → regressions.append(diff)
        if current case improved → improvements.append(diff)
    return EvalDiff
```

## 并行模式

`max_parallel > 1` 时，用 `asyncio.gather` + `Semaphore` 并发执行。
每个 case 独占 session_id = `f"eval_{benchmark.name}_{case.id}"`，state 不共享。

## 错误处理

| 场景 | 行为 |
|------|------|
| session 不存在 | raise `EvalError(f"Session '{id}' not found in trace store")` |
| session 无 user_input | raise `EvalError("No user messages found in session")` |
| trace JSON 损坏 | raise `EvalError(f"Corrupted trace file: {detail}")` |
| agent.chat() 超时 | case.failed, error="timeout after Ns" |
| agent.chat() 抛异常 | case.failed, error=str(exc) |
| compare 不同 benchmark | raise `EvalError("Cannot compare different benchmarks")` |
| compare case 数量不同 | 按 current 的 case 集合计算，new/removed 标记 |

## 增量交付计划

| 阶段 | 内容 |
|------|------|
| **MVP (本期)** | EventBus.event_count/events_since + events_to_trace + EvalRunner 重写 + 4 个 metric 返回真实值 + BenchmarkBuilder + EvalComparator + JSON 序列化 |
| **二期** | CLI 集成 (`python cli.py eval run/compare`)、HTML report、CI 集成 |
| **三期** | 高级 metric (语义相似度)、并行模式、增量 benchmark 更新 |

## 不做的

- 不实现语义相似度 metric（三期）
- 不修改 `EvalCase` / `EvalDataset` 协议（已被 `EvalBenchmark` 取代）
- 不迁移旧 `DefaultEvalRunner` — 直接重写
