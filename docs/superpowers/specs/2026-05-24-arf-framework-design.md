# ARF 框架解耦设计

## 目标

将 ARF 拆分为**框架层**（`arf/`）和**应用层**（`app/`）。框架层解决通用 Agent Harness 的核心问题域，以代码优先、配置辅助的设计哲学构建。应用层是该框架之上的 ARF 产品实现。

## 问题域（框架层职责）

| 域 | OS 类比 | 解决的致命问题 | 最小可行实现 | Framework 接口 |
|---|---|---|---|---|
| **core** | 内核类型系统 | 跨模块 Protocol 散落各处，engine 无法合法引用 | 统一的 Protocol + 核心数据结构集合 | 所有 Protocol 定义 + `AgentState`、`TurnContext`、`AgentEvent` 等 |
| **agent** | 进程 | Agent 生命周期管理 | Pydantic 配置驱动，代码优先 | `create_agent(config=AgentConfig(...))` |
| **engine** | CPU 流水线 + 事务管理器 | 执行循环、checkpoint、并行tool调用、事务回滚、规划跟踪 | ReAct + StateStore + ToolExecutor + Transaction + Planner | `GraphEngine` + `LoopStrategy` + `StateStore` + `ToolExecutor` + `TransactionContext` + `Planner` |
| **observability** | 系统监控 + 录放机 | 框架黑盒，出问题无法定位且无法复现 | OTel Span + Rich TUI + Record/Replay | `EventBus` + `Tracer` + `TuiDashboard` + `ReplayController` |
| **streaming** | 管道 (pipe) | 用户盯白屏等结果 | 统一事件流 → SSE/WebSocket 推送 | `EventBus` + `EventStream`（共享事件源） |
| **guardrails** | 防火墙 + 杀毒软件 | 模型输出不可信，缺少语义安全层 | engine 三处硬编码调用点 (输入/输出/工具参数) | `GuardRunner` (engine 统一入口，内部封装三种护栏) |
| **evaluation** | 基准测试 (benchmark) | 改了prompt/工具不知道变好变坏 | 轨迹收集 + 指标计算 + 数据集回放 + 回归测试 | `EvalRunner` + `MetricCollector` |
| **compaction** | 虚拟内存 + 页交换 | 上下文窗口爆掉 | 75% 阈值滑动窗口压缩 | `CompactionStrategy` |
| **memory** | 文件系统 + 搜索引擎 + 知识编辑器 | 只检索不写入，记忆无法生长 | store + retrieve + write/fusion 完整闭环 | `MemoryStore` + `MemoryRetriever` + `MemoryWriter` |
| **routing** | 多级缓存 (L1/L2) | 所有请求打同一个模型 | 二级分类器 | `ModelRouter` |
| **hooks** | 系统调用 | 自定义扩展点 | 6 事件节点，subprocess + 退出码契约 | `HookRunner` + `HookDefinition` |
| **sandbox** | 进程隔离 | 工具访问越界 | 路径沙箱 | `ToolSandbox` |
| **concurrency** | 乱序执行 + 多核 | 任务层面并行调度 | 顺序执行（占位） | `TaskScheduler` |
| **human_loop** | 硬件中断 + 审批工作流 | 该停时停不下来，停了恢复不了 | 暂停/审批/超时/恢复 + 依赖 StateStore 快照 | `ApprovalPoint` + `ApprovalChannel` |
| **communication** | IPC + 分布式共识 | 多Agent聋子, Supervisor中心化, 无共享状态并发保护 | AgentBus + 去中心化Peer + SharedWorkspace锁 | `AgentBus` + `PeerAgent` + `Supervisor` + `SharedWorkspace` + `Lock` |
| **resources** | 文件系统索引 + 远程挂载 | 工具只能本地 YAML，无法接入远程 | ToolResolver (内部封装 Provider+Retriever+Backend) | `ToolResolver` (engine 唯一 tool 接口) |
| **errors** | 异常处理 + 看门狗 | 工具/模型失败行为不可预测 | 重试 + 退避 + 降级 + 事务回滚 | `ErrorPolicy` + `TransactionContext` |

## 核心设计：`arf/core` — 统一类型层

**解决的问题**: engine 的零依赖契约要求 engine 不能 import 任何 `arf/` 子模块。所有跨模块 Protocol 必须集中到一个 engine 可以合法依赖的位置。

`arf/core/` 是框架的类型内核，只包含 Protocol 定义和纯数据结构，零实现逻辑。

```
arf/core/
├── __init__.py           # 导出所有公共符号
├── protocols/
│   ├── tracer.py         # Tracer
│   ├── event_bus.py      # EventBus (streaming + observability 共享)
│   ├── guardrails.py     # GuardRunner, InputGuardrail, OutputGuardrail, ToolGuardrail
│   ├── eval.py           # EvalRunner, MetricCalculator
│   ├── replay.py         # ReplayController
│   ├── compaction.py     # CompactionStrategy
│   ├── memory.py         # MemoryStore, MemoryRetriever, MemoryWriter
│   ├── resources.py      # ToolResolver, ToolProvider, ToolRetriever, ToolBackend
│   ├── routing.py        # ModelRouter
│   ├── hooks.py          # HookRunner
│   ├── sandbox.py        # ToolSandbox
│   ├── concurrency.py    # TaskScheduler
│   ├── human_loop.py     # ApprovalPoint, ApprovalChannel
│   ├── communication.py  # AgentBus, PeerAgent, Supervisor, SharedWorkspace, Lock, ConsensusProtocol
│   ├── engine.py         # LoopStrategy, StateStore, ToolExecutor, TransactionContext, Planner
│   └── errors.py         # ErrorPolicy
├── events.py             # AgentEvent — 统一事件模型
├── state.py              # AgentState, TurnContext, MemoryEntry
├── config.py             # AgentConfig + 所有子配置模型 (Pydantic)
└── results.py            # GuardResult, ToolResult, HookResult, ApprovalResponse
```

### 统一事件模型

```python
@dataclass
class AgentEvent:
    type: Literal[
        "session_start", "session_end",
        "thinking_delta",
        "model_call_start", "model_call_end",
        "tool_call_start", "tool_call_end",
        "tool_call_result",
        "compaction_start", "compaction_end",
        "approval_required", "approval_resolved",
        "hook_start", "hook_end",
        "error",
    ]
    data: dict
    timestamp: float
    trace_id: str
    span_id: str
    parent_span_id: str | None
    session_id: str
    agent_name: str
    turn: int
```

### Observability：Tracer + TUI Dashboard

两个消费者订阅同一个 EventBus：
- `OTelTracer` → OTLP Span 导出
- `TuiDashboard` → Rich 终端实时面板

## 目录结构

```
open_deepseek_arf/
├── pyproject.toml
├── arf/                           # 框架
│   ├── __init__.py
│   ├── core/                      # 统一类型层
│   │   ├── protocols/
│   │   ├── events.py
│   │   ├── state.py
│   │   ├── config.py              # AgentConfig + 子配置
│   │   └── results.py
│   ├── agent/                     # base.py, factory.py
│   ├── engine/                    # graph.py, nodes.py
│   │   ├── loop_strategies/
│   │   ├── tool_executor.py
│   │   └── checkpoint.py
│   ├── resources/                 # registry.py, adapter.py
│   │   ├── providers/
│   │   └── backends/
│   ├── memory/
│   ├── hooks/
│   ├── routing/
│   ├── compaction/
│   ├── sandbox/
│   ├── guardrails/
│   ├── evaluation/
│   ├── human_loop/
│   ├── streaming/
│   ├── observability/
│   ├── communication/
│   ├── errors/
│   ├── concurrency/
│   └── testing/
├── app/
│   ├── web/                       # 前端
│   └── arf_app/                   # 应用层
└── tests/
```

## 依赖规则

1. `arf/core/` **零依赖** — 不 import 任何 `arf/` 下的其他模块
2. `arf/engine/` **只能 import `arf.core`** — 通过 DI 注入实现对象
3. 框架默认实现只能依赖 `arf.core` + engine 公共接口
4. `arf/` 下任何文件不 import `app/`

## Engine 核心契约

### StateStore：自动 Checkpoint

engine 自动触发 `put()` 的时机：每个 turn 结束后、human_loop 暂停前、工具调用前。

### ToolExecutor：内建并行工具调用

多 tool_calls 并发执行而非顺序调用。`strategy: "sequential" | "parallel"`，`max_concurrency: 5`。

### MemoryRetriever 触发时机

engine 在 **compact 之前** 调用 `MemoryRetriever.retrieve()`。

### MemoryWriter — 记忆写入与融合

每个 turn 结束后，在 `StateStore.put()` 之前调用。内部完成提取→去重→融合→淘汰。

### ErrorPolicy

标准化的错误处理，重试/降级/回滚策略。

### TransactionContext — 事务性回滚

工具链执行到一半失败时，回滚已执行的工具副作用。

### Planner — 规划与自我修正

多步任务的计划生成、子目标跟踪、偏离检测、计划修正。

### GuardRunner — engine 中的护栏执行点

三处硬编码调用: check_input、check_output、check_tool_params。

### ReplayController — Record & Replay 确定性重放

录制和回放 Agent 会话，用于调试和回归测试。

### Evaluation — 基准测试与回归测试

定义测试集、运行评估、对比基线。

## 配置格式

### 设计原则

代码优先，YAML 辅助。用户主要入口是 `create_agent(config=AgentConfig(...))`。
YAML 是 AgentConfig 的序列化格式。

### AgentConfig

```python
class AgentConfig(BaseModel):
    schema_version: str = "1.0"
    name: str                # 必填
    role: str                # 必填
    task: str                # 必填
    description: str         # 必填: 能力描述
    system_prompt: SystemPromptConfig  # 必填
    models: list[ModelConfig]          # 必填

    # 可选
    loop_strategy: Literal["react", "direct", "plan_execute"] = "react"
    router: RouterConfig | None = None
    compaction: CompactionConfig | None = None
    memory: MemoryConfig | None = None
    tool_retrieval: ToolRetrievalConfig | None = None
    guardrails: GuardrailsConfig | None = None
    errors: ErrorConfig | None = None
    human_loop: HumanLoopConfig | None = None
    streaming: StreamingConfig | None = None
    sandbox: SandboxConfig | None = None
    hooks: list[HookDefinition] = []
    tools: list[ToolConfig] = []
    skills: list[SkillConfig] = []
    max_turns: int = 50
    reload: ReloadConfig | None = None
```

### 系统提示词管道

```yaml
system_prompt:
  template: |
    You are {{AGENT_NAME}}, an AI assistant.
    {{WORKSPACE}} {{CRITICAL_RULES}} {{INVENTORY}} {{MEMORY}} {{LANGUAGE}}
  pipeline:
    - priority: 10
      section: workspace
    - priority: 20
      section: memory
    - priority: 25
      section: critical_rules
    - priority: 30
      section: inventory
    - priority: 60
      section: language
  critical_rules: |
    ## Critical Rules (DO NOT VIOLATE)
    1. Always read a file before editing it — never edit from memory.
    ...
```

### Agent = model/skill/tool/hook 的组合

```
用户可见的配置层只有: models, tools, skills, hooks
Agent 是这四个资源的组合体
多 Agent 通过 handover 或 Supervisor 协作
```

