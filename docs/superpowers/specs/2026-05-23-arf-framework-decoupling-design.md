# ARF 框架解耦设计

## 目标

将 ARF 拆分为**框架层**（`arf/`）和**应用层**（`app/`）。框架层解决通用 Agent Harness 的核心问题域，以代码优先、配置辅助的设计哲学构建。应用层是该框架之上的 ARF 产品实现。

## 问题域（框架层职责）

| 域 | 用户可见 | OS 类比 | 解决的致命问题 | 最小可行实现 | Framework 接口 |
|---|---|---|---|---|---|
| **agent** | 核心用户界面 | 进程 | Agent 生命周期管理 | 极简配置: name + description + 4种资源 | `create_agent(config=AgentConfig(...))` |
| **models** | 核心用户界面 | CPU 型号 | 用什么模型 | 声明模型名 + API 端点 + 可选 kwargs | `ModelConfig` |
| **skills** | 核心用户界面 | 可执行程序 | 组合 prompt + 工具的复合能力 | prompt 模板 + 关联工具列表 | `SkillConfig` |
| **tools** | 核心用户界面 | 指令集 (ISA) | Agent 可调用的外部能力 | tool.yaml + function.py / MCP 远程 | `ToolConfig` + `ToolResolver` |
| **hooks** | 核心用户界面 | 系统调用 | 生命周期扩展点 | 6 事件节点 + subprocess + 退出码契约 | `HookDefinition` + `HookRunner` |
| **multi-agent** | 核心用户界面 | 分布式系统 | 声明子 Agent + 协作关系，框架自动构建通信基础设施 | agents 列表 + handover/supervisor | `AgentConfig.agents` + `HandoverConfig` |
| **core** | 框架内部 | 内核类型系统 | 跨模块 Protocol 散落各处，engine 无法合法引用 | 统一的 Protocol + 核心数据结构集合 | 所有 Protocol 定义 + `AgentState`、`TurnContext`、`AgentEvent` 等 |
| **engine** | 框架内部 | CPU 流水线 + 事务管理器 | 执行循环、checkpoint、并行tool调用、事务回滚、规划 | ReAct + StateStore + ToolExecutor + Transaction + Planner | `GraphEngine` + `LoopStrategy` + `StateStore` + `ToolExecutor` + `TransactionContext` + `Planner` |
| **resources** | 框架内部 | 文件系统索引 + 远程挂载 | 工具来源统一封装：本地 YAML + MCP + 检索 + 执行 | ToolResolver 封装 Provider+Retriever+Backend | `ToolResolver` (engine 唯一 tool 入口) |
| **observability** | 高级可覆盖 | 系统监控 + 录放机 | 框架黑盒，出问题无法定位且无法复现 | OTel Span + Rich TUI + Record/Replay (默认开启) | `EventBus` + `Tracer` + `TuiDashboard` + `ReplayController` |
| **streaming** | 高级可覆盖 | 管道 (pipe) | 用户盯白屏等结果 | 统一事件流 → SSE 全事件推送 (默认开启) | `EventBus` + `EventStream`（共享事件源） |
| **guardrails** | 高级可覆盖 | 防火墙 + 杀毒软件 | 模型输出不可信，缺少语义安全层 | 默认透传输入 + 正则清洗输出 + 路径检查 | `GuardRunner` (engine 三处硬编码调用) |
| **routing** | 高级可覆盖 | 多级缓存 (L1/L2) | 所有请求打同一个模型 | 单模型 static (默认) → two_tier (多模型时自动启用) | `ModelRouter` |
| **compaction** | 高级可覆盖 | 虚拟内存 + 页交换 | 上下文窗口爆掉 | 75% 阈值滑动窗口 (根据模型 ctx 大小自动判定是否启用) | `CompactionStrategy` |
| **memory** | 高级可覆盖 | 文件系统 + 搜索引擎 + 知识编辑器 | 只检索不写入，记忆无法生长 | file store + recent_first + rule-based write (默认开启) | `MemoryStore` + `MemoryRetriever` + `MemoryWriter` |
| **error** | 高级可覆盖 | 异常处理 + 看门狗 | 工具/模型失败行为不可预测 | 工具 2 次指数退避重试 + 模型 3 次重试/5xx 降级 + 事务回滚 (默认开启) | `ErrorPolicy` + `TransactionContext` |
| **human_loop** | 高级可覆盖 | 硬件中断 + 审批工作流 | 该停时停不下来，停了恢复不了 | 自动放行 (默认) → 可配置工具白名单触发审批 | `ApprovalPoint` + `ApprovalChannel` |
| **sandbox** | 高级可覆盖 | 进程隔离 | 工具访问越界 | 路径隔离 (默认开启) | `ToolSandbox` |
| **evaluation** | 用户提供数据 | 基准测试 (benchmark) | 改了prompt/工具不知道变好变坏 | 用户提供 EvalDataset → 框架跑指标 + 对比基线 | `EvalRunner` + `MetricCollector` |
| **communication** | 声明式配置 | IPC + 分布式共识 | 多Agent聋子, 共享状态无并发保护 | 用户声明 handover/supervisor → 框架自动构建 AgentBus/Peer/Lock | `AgentBus` + `PeerAgent` + `Supervisor` + `SharedWorkspace` + `Lock` |
| **concurrency** | 框架内部 | 乱序执行 + 多核 | 任务层面并行调度 | 顺序执行（占位） | `TaskScheduler` |

> **用户可见性说明 | Visibility guide**:
> - **核心用户界面**: 用户直接配置，Agent 的 4+1 种核心资源（model/skill/tool/hook/agent）
> - **高级可覆盖**: `AdvancedConfig.default()` 提供生产级默认值，用户通过 `advanced:` 字段 opt-in 调优
> - **用户提供数据**: 框架提供运行器，用户提供测试数据集（eval dataset）/ 交接规则（handover rules）
> - **声明式配置**: 用户声明意图（谁和谁交接），框架自动构建底层基础设施
> - **框架内部**: 对用户完全透明，框架自动处理

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

**解决的问题**: streaming 和 observability 共享同一个事件源，消除重复抽象。engine 只 emit 一次，不同消费者各取所需。

```python
# arf/core/events.py

@dataclass
class AgentEvent:
    """框架内所有可观测状态变化的统一载体。
    Streaming 从此过滤用户可见事件；Observability 从此导出 OTel Span。
    """
    type: Literal[
        "session_start", "session_end",
        "thinking_delta",           # 模型推理 token 流
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

```python
# arf/core/protocols/event_bus.py

class EventBus(Protocol):
    """统一事件总线。engine 在关键节点调用 emit()。
    Streaming 适配器 subscribe() 过滤用户可见事件推送前端；
    Observability 适配器 subscribe() 消费所有事件导出 OTel Span。
    """
    def emit(self, event: AgentEvent) -> None: ...
    async def subscribe(
        self,
        event_types: list[str] | None = None,  # None = 全部
    ) -> AsyncIterator[AgentEvent]: ...

class EventStream(Protocol):
    """事件传输适配器——只负责传输协议，不定义事件结构"""
    async def publish(self, event: AgentEvent) -> None: ...
    async def listen(self) -> AsyncIterator[AgentEvent]: ...
```

**默认实现**:
- `InMemoryEventBus` — 单进程内 asyncio 广播
- `SseStream` — SSE 传输适配器

streaming/observability 不再是两个独立域，而是 EventBus 的两个消费者。

### Observability 默认实现：Tracer + TUI Dashboard

两个消费者订阅同一个 EventBus，职责不同：

```
EventBus.subscribe(event_types=None)  ← 全量事件
  ├── OTelTracer     → OTLP Span 导出 (Jaeger/Grafana)
  └── TuiDashboard   → Rich 终端实时面板 (本地调试)
```

#### Tracer：OTel Span 导出

```python
# arf/core/protocols/tracer.py

class Tracer(Protocol):
    """消费 EventBus 全量事件，转换为 OTel Span 导出。
    通过环境变量 OTEL_EXPORTER=console|otlp|none 选择导出方式。"""
    async def consume(self, bus: EventBus) -> None: ...
```

默认 `OtelTracer` 将 `AgentEvent` 的 `trace_id`/`span_id`/`parent_span_id` 直接映射为 OTel Span，span name = event type。携带 `session_id`、`agent_name`、`model_name`、`turn` 等标准 attribute。

#### TuiDashboard：Rich 实时调试面板

```python
# arf/observability/tui.py

class TuiDashboard:
    """Rich 驱动的终端实时调试面板。消费 EventBus，显示 Agent 运行的
    关键指标和事件时间线。通过环境变量 ARF_TUI=1 或 agent.yaml 配置启用。
    仅在开发/调试场景使用，生产环境关闭。"""

    async def consume(self, bus: EventBus) -> None: ...
```

面板布局：

```
┌─────────────────────────────────────────────────────────────┐
│  ARF Agent: my_agent                    Session: abc123     │
│  Uptime: 00:12:34    Turn: 5/50                             │
├────────────────────────────────┬────────────────────────────┤
│  Model Calls                   │  Tool Calls Timeline       │
│  ─────────────────────         │  ─────────────────────     │
│  quick_thinking:  3 calls      │  12:00:01  file_reader  ██ │
│     tokens: 1,200 in / 800 out │  12:00:03  web_search   ███│
│  deep_thinking:   2 calls      │  12:00:08  file_writer  █  │
│     tokens: 800 in / 3,200 out │  12:00:15  file_reader  ██ │
│  ─────────────────────         │  12:00:22  web_fetch   ████│
│  Tokens this session: 12,400   │                             │
├────────────────────────────────┴────────────────────────────┤
│  Last Model Output                                          │
│  ────────────────────────────────────────────────────────── │
│  I'll read the file first to understand the current         │
│  implementation before making changes...                    │
│                                                             │
│  [Enter: pause  |  q: quit dashboard  |  t: token detail]   │
└─────────────────────────────────────────────────────────────┘
```

**面板区域**:
- **Header**: agent name、session id、运行时长、当前 turn
- **Model Calls**: 每种模型的调用次数、token 消耗（输入/输出）
- **Tool Timeline**: 工具调用的甘特式时间线，显示调用顺序和耗时
- **Output**: 最近一条模型输出摘要，实时刷新
- **热键**: `Enter` 暂停/恢复刷新，`q` 退出面板，`t` 切换 token 明细

**数据来源均来自 EventBus**，TuiDashboard 不主动轮询任何状态。默认在 `ARF_TUI=1` 时启用，或通过 agent config:

```yaml
observability:
  tui_enabled: true
    # 是否启用 Rich TUI 调试面板 (仅开发环境)
    # 生产环境建议关闭，使用 OTLP 导出
  otel_exporter: console
    # console | otlp | none
```

**与 streaming 的区别**: TUI 是开发者本地调试工具，展示内部指标（token 消耗、调用次数、工具耗时）；streaming 是用户产品级 UI 的数据源，展示实时进度和结果。两者都从 EventBus 读取，但展示不同维度的信息。

---

## 目录结构

```
open_deepseek_arf/
├── pyproject.toml
│
├── arf/                           # 框架 (pip install -e .)
│   ├── __init__.py                # create_agent, AgentConfig, public Protocols
│   ├── core/                      # 统一类型层: 所有 Protocol + 数据结构
│   │   ├── protocols/             # engine.py, memory.py, guardrails.py, ...
│   │   ├── events.py              # AgentEvent
│   │   ├── state.py               # AgentState, TurnContext, MemoryEntry
│   │   ├── config.py              # AgentConfig + 子配置 Pydantic 模型
│   │   └── results.py             # GuardResult, ToolResult, HookResult, ...
│   ├── agent/                     # base.py, factory.py (create_agent)
│   ├── engine/                    # graph.py, nodes.py
│   │   ├── loop_strategies/       # react.py (默认), plan_execute.py
│   │   ├── tool_executor.py       # 并发工具执行: 顺序/并行/max_concurrency
│   │   └── checkpoint.py          # StateStore 集成: 每 turn/tool/human_loop 边界自动保存
│   ├── resources/                 # registry.py, adapter.py
│   │   ├── providers/             # static_yaml.py, mcp.py (ToolProvider 实现)
│   │   └── backends/              # function.py, subprocess.py (ToolBackend 实现)
│   ├── memory/                    # store.py, retriever.py
│   │   ├── file_store.py          # MemoryStore 默认实现
│   │   ├── recent_first.py        # MemoryRetriever 默认实现
│   │   └── tool_retriever.py      # ToolRetriever 默认实现
│   ├── hooks/                     # runner.py
│   ├── routing/                   # two_tier.py
│   ├── compaction/                # sliding_window.py
│   ├── sandbox/                   # path_sandbox.py
│   ├── guardrails/                # none_guard.py, regex_clean.py, path_check.py
│   ├── evaluation/                # runner.py, metrics.py (EvalRunner + 内建指标)
│   ├── human_loop/                # approval_points.py, channels/
│   │   └── channels/              # console.py, websocket.py
│   ├── streaming/                 # adapters/ (SSE, WebSocket — EventBus 的传输层)
│   ├── observability/             # otel.py, tui.py, replay.py
│   ├── communication/             # in_memory_bus.py, supervisor.py, peer.py
│   ├── errors/                    # retry.py, fallback.py
│   └── concurrency/               # sequential.py
│   ├── testing/                   # InMemory* 测试替身，方便开发者单元测试
│   │   ├── __init__.py             # 导出所有 fake 实现
│   │   ├── fake_bus.py             # InMemoryEventBus
│   │   ├── fake_store.py           # InMemoryStateStore, InMemoryMemoryStore
│   │   ├── fake_tools.py           # InMemoryToolResolver, InMemoryToolExecutor
│   │   ├── fake_guards.py          # InMemoryGuardRunner (透传)
│   │   ├── fake_agents.py          # InMemoryAgentBus, InMemorySupervisor
│   │   └── fake_channel.py         # InMemoryApprovalChannel
│
├── app/                           # 应用层 + 前端
│   ├── web/                       # 前端 (现在的 frontend/)
│   └── arf_app/                   # ARF 应用层 (用户后续搭建)
│
└── tests/
```

## 依赖规则

1. `arf/core/` **零依赖** — 不 import 任何 `arf/` 下的其他模块，只使用标准库 + `pydantic` + `typing`
2. `arf/engine/` **只能 import `arf.core`** — 通过 DI 注入实现对象
3. 框架默认实现只能依赖 `arf.core` + engine 公共接口
4. `arf/` 下任何文件不 import `app/`

## Engine 核心契约

### StateStore：自动 Checkpoint

```python
# arf/core/protocols/engine.py

class StateStore(Protocol):
    """状态持久化——engine 在关键边界自动调用 put()"""
    async def put(self, session_id: str, state: AgentState) -> None: ...
    async def get(self, session_id: str) -> AgentState | None: ...
    async def delete(self, session_id: str) -> None: ...
```

engine 自动触发 `put()` 的时机：每个 turn 结束后、human_loop 暂停前、工具调用前。`get()` 在会话恢复时调用。这是中断恢复、审批恢复、事后审计的基础，不是可选功能。

### ToolExecutor：内建并行工具调用

```python
class ToolExecutor(Protocol):
    """engine 内部使用——一次 model 响应可能返回多个 tool_calls，
    需要并发执行而非顺序调用。LoopStrategy 的 build_graph 将此作为工具节点。"""
    async def execute(
        self,
        tool_calls: list[ToolCall],
        strategy: Literal["sequential", "parallel"] = "parallel",
        max_concurrency: int = 5,
    ) -> dict[str, ToolResult]: ...
```

这是 engine 的内部组件，不是外层 `TaskScheduler`。`TaskScheduler` 管 Agent 之间的任务并行，`ToolExecutor` 管一个 turn 内的多工具并发——两个层级。

### MemoryRetriever 触发时机

engine 在 **compact 之前** 调用 `MemoryRetriever.retrieve()`，`query_context` 为当前会话最近 N 条消息。检索结果写入 `state.context_summary`，由 prompt pipeline 的 `{{MEMORY}}` 占位符消费。**这是 engine 节点的内置行为，不是可选的 hook。**

### MemoryWriter — 记忆写入与融合

**解决的问题**: MemoryStore + MemoryRetriever 只覆盖了记忆的消费侧。Agent 在对话中通过工具调用和模型输出持续产生新知识——用户偏好、项目决策、关键事实——这些需要在对话过程中被主动提取、去重、与旧记忆融合或淘汰。

OS 类比: 文件系统的 write + 日志合并 (compaction)。不是每次 write 就开新文件，而是写入日志、定期合并、清理冗余。

```python
# arf/core/protocols/memory.py

class MemoryWriter(Protocol):
    """记忆写入与融合——记忆管线的生产侧。
    engine 在每个 turn 结束后调用，传入当前对话片段。
    writer 内部完成提取→去重→融合→淘汰的完整流程。"""

    async def extract_and_write(
        self,
        store: MemoryStore,
        turn_messages: list[dict],      # 当前 turn 的对话消息
        existing_entries: list[MemoryEntry],  # 已有相关记忆（由 retriever 提供）
    ) -> list[MemoryEntry]:             # 返回写入后的受影响条目
        ...

@dataclass
class MemoryEntry:
    id: str
    content: str                         # 记忆内容
    category: Literal["fact", "preference", "decision", "context"]
    timestamp: float
    source_turn: int                     # 来源 turn
    relevance_score: float = 1.0         # 检索相关性，writer 可通过设为 0.0 标记淘汰
    replaces: str | None = None          # 替代的旧条目 ID（冲突解决）
```

**engine 中的调用位置**: 每个 turn 结束后，在 `StateStore.put()` 之前调用。流程:

```
turn 结束
  ├─ MemoryRetriever.retrieve(query, session_id, max_tokens, top_k)
  │     → 获取已有相关记忆（用于去重和融合判断）
  ├─ MemoryWriter.extract_and_write(store, turn_messages, existing_entries)
  │     → 提取本 turn 新知识，与已有记忆合并、去重、淘汰过期信息
  └─ StateStore.put(session_id, state)
```

**默认实现**: `RuleBasedWriter` — 基于提示词模板让模型从对话中提取事实、偏好、决策，与已有条目比较后决定 add/update/delete。写入策略可配置: `append_only`（只累加不淘汰）、`dedup_merge`（去重合并）、`lru_evict`（LRU 淘汰，超出 max_entries 时淘汰最旧/最低分条目）。

### ToolResolver — Resources 层高层接口

```python
# arf/core/protocols/resources.py

class ToolResolver(Protocol):
    """Resources 层对 engine 暴露的唯一工具接口。
    内部封装 ToolProvider + ToolRetriever + ToolBackend 的全部复杂度。
    engine 不关心工具来自本地 YAML 还是 MCP 远程，也不关心如何检索——
    它只需要根据当前上下文拿到一组可用的 ToolDefinition。
    """
    async def get_tool_definitions(
        self,
        query_context: str,           # 当前对话上下文
        top_k: int = 10,              # 检索后保留的工具数量上限
    ) -> list[ToolDefinition]: ...

    async def execute(
        self,
        tool_name: str,
        params: dict,
    ) -> ToolResult: ...


@dataclass
class ToolDefinition:
    """发给模型 API 的标准化 tool schema——engine 只关心这个"""
    name: str
    description: str
    parameters: dict                  # JSON Schema
```

**内部实现层次** (`resources/`):
```
ToolResolver.get_tool_definitions()
  ├── ToolProvider.list_tools()        # 聚合 static_yaml + MCP → 全量列表
  ├── ToolRetriever.retrieve()         # 语义检索 top-k
  └── → list[ToolDefinition]           # engine 可直接序列化给 API

ToolResolver.execute()
  ├── ToolProvider.resolve(name)       # 找到工具来源
  ├── ToolBackend.execute()            # function / subprocess → ToolResult
  └── → ToolResult
```

ToolProvider、ToolRetriever、ToolBackend 是 Resources 层内部实现细节，不暴露给 engine。

```python
# 内部 Protocol (engine 不可见 | engine never sees these)

class ToolProvider(Protocol):
    """工具来源抽象 — 本地 YAML 或远程 MCP 服务器"""
    async def list_tools(self) -> list[ToolConfig]: ...
    async def resolve(self, name: str) -> ToolConfig | None: ...

class ToolRetriever(Protocol):
    """根据任务上下文动态挑选 top-k 工具"""
    async def retrieve(
        self,
        query_context: str,
        available_tools: list[ToolConfig],
        top_k: int = 10,
    ) -> list[ToolConfig]: ...

class ToolBackend(Protocol):
    """工具执行后端 — 绑定到具体 Python 函数或 subprocess"""
    async def execute(self, tool_config: ToolConfig, params: dict) -> ToolResult: ...
```

### ErrorPolicy

```python
# arf/core/protocols/errors.py

class ErrorPolicy(Protocol):
    """标准化错误处理。LoopStrategy 在工具/模型调用失败时走此策略，
    而非让模型自由发挥。"""
    def on_tool_error(self, error: Exception, tool_name: str, attempt: int) -> ErrorAction: ...
    def on_model_error(self, error: Exception, model_name: str, attempt: int) -> ErrorAction: ...
    def on_guardrail_block(self, result: GuardResult, context: TurnContext) -> ErrorAction: ...

@dataclass
class ErrorAction:
    action: Literal["retry", "fallback", "ask_user", "abort"]
    delay: float = 0.0             # retry 前的退避延迟
    fallback_model: str | None = None
    message: str = ""
```

默认实现: `DefaultErrorPolicy` — 工具错误重试 2 次指数退避，模型 429 重试 3 次，5xx 降级到 fallback 链中下一个模型。

### TransactionContext — 事务性回滚

**解决的问题**: StateStore 在 turn 边界保存快照，但工具链执行到一半失败时，仅重试无法回滚已执行的工具副作用（文件已写入、API 已调用）。需要**将一组工具调用包装为事务**，失败时自动回滚到上一个一致性边界。

OS 类比: 数据库的 ACID 事务。`BEGIN` → 执行一组操作 → 成功则 `COMMIT`，失败则 `ROLLBACK`。文件系统的 journaling（ext4 的 jbd2）。

```python
# arf/core/protocols/engine.py

class TransactionContext(Protocol):
    """将一组工具调用包装为原子事务。engine 在执行工具链前调用 begin()，
    全部成功后调用 commit()，任何失败调用 rollback()。"""

    async def begin(self, session_id: str, turn: int) -> Transaction: ...

    async def commit(self, tx: Transaction) -> None: ...

    async def rollback(self, tx: Transaction, error: Exception) -> RollbackResult: ...

@dataclass
class Transaction:
    id: str
    session_id: str
    turn: int
    state_snapshot: dict           # turn 前的 AgentState 快照
    tool_results: list[ToolResult]  # 已执行的工具结果（用于回滚参考）

@dataclass
class RollbackResult:
    success: bool                   # 是否全部回滚成功
    rollbacks: list[dict]           # 每个工具的回滚状态
    unresolved: list[str]           # 无法自动回滚的副作用（需人工介入）
    restored_state: dict            # 恢复到 turn 边界的状态
```

**engine 调用流程**:
```
engine: 收到 tool_calls
  ├─ TransactionContext.begin(session_id, turn)
  │     → 保存 state snapshot，标记事务开始
  ├─ ToolExecutor.execute(tool_calls)  ← 并行/顺序执行
  │     ├─ 全部成功 → TransactionContext.commit(tx)
  │     │     → 清除 snapshot，写入结果到 state
  │     └─ 任一失败 → ErrorPolicy.on_tool_error() 决定是否重试
  │           └─ 重试耗尽 → TransactionContext.rollback(tx, error)
  │                 → 恢复 state snapshot，执行每个工具的回滚逻辑
  └─ 继续 loop 或 respond
```

**默认实现**: `SnapshotRollback` — begin 时保存完整 AgentState 深拷贝，commit 直接丢弃快照，rollback 恢复快照 + 对每个已执行的 `ToolResult` 调用工具自身的 `rollback` 回调（如果工具定义了的话）。对于无回滚回调的副作用（如网络请求已发出），标记为 `unresolved` 并写入 state 供后续 human_loop 审批。

**限制**: 框架不保证所有工具副作用可逆（如已发送的邮件）。不可逆副作用通过 `RollbackResult.unresolved` 上报，由 `ErrorPolicy` → `HumanLoopManager` 升级为人工决策。

### Planner — 规划与自我修正

**解决的问题**: `loop_strategy: plan_execute` 只是一个标签。真实的多步任务需要: 生成计划 → 跟踪子目标状态 → 执行中各子目标间切换 → 检测偏离 → 修正计划。没有规划状态机和子目标跟踪，Agent 在复杂任务中会迷路。

OS 类比: CPU 的指令调度器 + 分支预测 + 推测执行恢复。CPU 不是"先规划完全部指令再执行"，而是在执行中持续预测、检测、回滚。Agent 的 Planner 同理。

```python
# arf/core/protocols/engine.py

class Planner(Protocol):
    """通用规划上下文。不是"一次生成计划然后执行"，而是持续维护计划状态机，
    在执行中根据反馈修正。LoopStrategy 的 build_graph 将 Planner 作为
    规划节点插入到执行循环中。"""

    async def generate_plan(
        self,
        task: str,
        context: TurnContext,
        tools: list[ToolDefinition],
    ) -> Plan: ...

    async def update_progress(
        self,
        plan: Plan,
        completed_step: PlanStep,
        result: ToolResult,
    ) -> Plan: ...

    async def detect_divergence(
        self,
        plan: Plan,
        current_state: AgentState,
    ) -> DivergenceResult: ...

    async def revise(
        self,
        plan: Plan,
        divergence: DivergenceResult,
        context: TurnContext,
    ) -> Plan: ...


@dataclass
class Plan:
    id: str
    goal: str                          # 最终目标
    steps: list[PlanStep]              # 有序子目标
    current_step_index: int = 0
    status: Literal["draft", "executing", "revising", "completed", "failed"]

@dataclass
class PlanStep:
    id: str
    description: str                   # 子目标描述
    tool_hint: str | None = None       # 预期使用的工具
    status: Literal["pending", "in_progress", "completed", "skipped", "failed"]
    depends_on: list[str]              # 依赖的前置步骤 ID 列表
    result_summary: str = ""

@dataclass
class DivergenceResult:
    diverged: bool
    reason: str
    affected_steps: list[str]
    suggested_revision: str
```

**默认实现**: `PromptBasedPlanner` — 通过模型调用生成和修正计划，plan state 存储在 `AgentState.plan` 字段中，每个 turn 结束后自动调用 `update_progress` 和 `detect_divergence`。

**engine 中的规划循环**:
```
START
  ├─ [无计划] → Planner.generate_plan(task, ctx, tools)
  │              → state.plan = Plan (draft)
  ├─ [有计划] → 取 state.plan.steps[plan.current_step_index]
  │              → call_model (注入当前子目标上下文)
  │              → execute_tools
  │              → Planner.update_progress(plan, step, result)
  ├─ Planner.detect_divergence(plan, state)
  │     ├─ diverged → Planner.revise(plan, divergence, ctx)
  │     │              → state.plan.status = "revising"
  │     └─ on track → 继续下一个 step
  └─ [所有 steps completed] → respond
```

### GuardRunner — engine 中的护栏执行点

```python
# arf/core/protocols/guardrails.py

class GuardRunner(Protocol):
    """engine 在固定位置调用的护栏统一入口。
    内部组装 InputGuardrail + OutputGuardrail + ToolGuardrail，
    支持串联多个规则链。engine 不关心内部实现。"""

    async def check_input(self, message: str, context: dict) -> GuardResult:
        """engine 在收到用户消息后、进入 loop 前调用"""
        ...

    async def check_output(self, message: str, context: dict) -> GuardResult:
        """engine 在模型输出后、传递给用户或工具前调用"""
        ...

    async def check_tool_params(self, tool_name: str, params: dict) -> GuardResult:
        """engine 在工具执行前调用"""
        ...

@dataclass
class GuardResult:
    allowed: bool
    reason: str = ""
    modified_message: str | None = None  # 护栏修改后的内容（PII 清洗等）
```

**engine 中的调用位置**（硬编码，用户不可移除。通过 `strategy: none` 透传）:

```
engine loop:
  │
  ├─ 收到用户消息
  │    └─ guard_runner.check_input(message, ctx)      ← 输入护栏
  │       ├─ allowed  → 继续
  │       ├─ modified → 替换为 modified_message，继续
  │       └─ blocked  → ErrorPolicy.on_guardrail_block()
  │
  ├─ model 返回 response
  │    └─ guard_runner.check_output(response, ctx)     ← 输出护栏
  │       ├─ allowed  → 继续
  │       ├─ modified → 替换为 modified_message，继续
  │       └─ blocked  → ErrorPolicy.on_guardrail_block()
  │
  └─ 工具调用前
       └─ guard_runner.check_tool_params(name, params) ← 工具参数护栏
          ├─ allowed  → 执行工具
          ├─ modified → 使用 modified_params，继续
          └─ blocked  → ErrorPolicy.on_guardrail_block()
```

**内部实现**（engine 不可见 | engine never sees these）:

```python
class DefaultGuardRunner:
    def __init__(self, input_guard, output_guard, tool_guard):
        self._input = input_guard
        self._output = output_guard
        self._tool = tool_guard

    async def check_input(self, message, ctx):
        return await self._input.check(message, ctx)
    # ... check_output, check_tool_params 同理

class InputGuardrail(Protocol):
    async def check(self, message: str, context: dict) -> GuardResult: ...

class OutputGuardrail(Protocol):
    async def check(self, message: str, context: dict) -> GuardResult: ...

class ToolGuardrail(Protocol):
    async def check(self, tool_name: str, params: dict) -> GuardResult: ...
```

**默认实现**:
- `NoneInputGuard` — 透传所有输入
- `RegexOutputGuard` — 正则清洗 API key、手机号等 PII
- `PathToolGuard` — 检测路径穿越 (`../`、绝对路径)

**与 hook / sandbox 的关系**:
- guardrails 是框架安全基础设施，**不可被用户移除**（只能通过策略配置透传）
- hooks 是用户可选扩展，用户可以增删改
- sandbox 管 OS 层资源隔离（文件系统边界）
- guardrails 管应用层语义安全（内容安全、注入防御）

### ReplayController — Record & Replay 确定性重放

**解决的问题**: 调试 Agent 的难点在于非确定性——LLM 随机性、并行工具调度时序、外部 API 返回变化。即便有 TUI 面板和 OTel 导出，开发者也无法精确复现某次异常会话进行步进调试。从"监控"到"可调试"的鸿沟需要 Record & Replay 来填补。

OS 类比: rr (Mozilla 的 record & replay debugger)。录制整个进程的一次执行，包括所有非确定性输入（系统调用返回值、信号到达时序），然后无限次回放，每次回放都确定性地走到同一个执行路径。Agent 的"系统调用"就是 model 输出和 tool 返回。

```python
# arf/core/protocols/replay.py

class ReplayController(Protocol):
    """录制和回放 Agent 会话。录制时拦截所有非确定性输入（model response、
    tool result、hook 注入），写入轨迹文件。回放时按顺序注入录制值，
    Agent 走确定性执行路径。"""

    async def start_recording(self, session_id: str) -> None: ...

    async def record_model_output(
        self, session_id: str, turn: int, model_name: str, output: str
    ) -> None: ...

    async def record_tool_result(
        self, session_id: str, turn: int, tool_name: str, params: dict, result: dict
    ) -> None: ...

    async def stop_recording(self) -> ReplayTrace: ...

    async def replay(
        self,
        trace: ReplayTrace,
        *,
        start_turn: int = 0,           # 从指定 turn 开始回放
        breakpoints: list[int] | None = None,  # 在指定 turn 暂停，允许步进
        mock_model_outputs: dict[int, str] | None = None,  # 覆盖特定 turn 的模型输出
    ) -> AsyncIterator[AgentEvent]: ...


@dataclass
class ReplayTrace:
    session_id: str
    agent_config_hash: str            # 录制时的 config 指纹
    arf_version: str                  # 录制时的框架版本
    turns: list[TurnRecord]

@dataclass
class TurnRecord:
    turn: int
    model_name: str
    model_input: dict                 # 发给 API 的完整 messages
    model_output: str                 # API 返回的完整 response
    tool_calls: list[ToolCallRecord]

@dataclass
class ToolCallRecord:
    tool_name: str
    params: dict
    result: dict
    timestamp: float
```

**录制与回放的集成点**:

```
录制模式 (ARF_REPLAY_MODE=record):
  engine: call_model → ReplayController.record_model_output(...)
  engine: execute_tool → ReplayController.record_tool_result(...)
  session_end → ReplayController.stop_recording() → ReplayTrace.json

回放模式 (ARF_REPLAY_MODE=replay):
  engine: call_model → ReplayController.replay(...) 返回录制的 model_output（不调API）
  engine: execute_tool → 正常执行（默认）或 mock 录制值
  breakpoints → 在指定 turn 暂停，await 开发者输入继续
```

**默认实现**: `FileReplayController` — 轨迹存为 JSON 文件，回放时按 turn 顺序注入。支持单步模式（每 turn 暂停等待回车）。

**使用场景**:
- **回归测试**: 录制正确行为轨迹，改 prompt/工具后回放，对比输出差异
- **调试**: 录制异常会话，在错误 turn 前设 breakpoint，步进观察
- **CI**: 回放轨迹作为集成测试，验证框架升级后行为一致性

### Evaluation — 基准测试与回归测试

**解决的问题**: Agent 开发的核心痛点之一是"改了 prompt/工具/路由策略，不知道是变好还是变坏"。可观测性只解决"看到"（发生了什么），不解决"衡量"（这是更好的吗）。框架应内建评估基础设施，让开发者定义测试集、运行评估、对比基线。

OS 类比: 基准测试套件 (SPEC, sysbench) + 回归测试框架。CPU 改微架构后跑 SPEC 得知性能变化；Agent 改 prompt 后跑 eval 得知质量变化。

```python
# arf/core/protocols/evaluation.py

class EvalRunner(Protocol):
    """评估运行器——对数据集运行 Agent，收集轨迹和指标，生成对比报告。"""

    async def run(
        self,
        agent: "BaseAgent",
        dataset: EvalDataset,
        metrics: list[MetricCalculator],
        *,
        baseline: EvalReport | None = None,   # 基线报告（对比用）
        max_parallel: int = 1,                 # 并行运行数
    ) -> EvalReport: ...


class MetricCalculator(Protocol):
    """从轨迹中提取指标。框架提供内置指标，用户可自定义。"""

    async def compute(
        self,
        trace: ReplayTrace,
        expected: EvalCase,
    ) -> dict[str, float]: ...


@dataclass
class EvalDataset:
    name: str
    cases: list[EvalCase]

@dataclass
class EvalCase:
    id: str
    input: str                         # 用户消息
    expected_tools: list[str] | None    # 预期调用的工具名（可选）
    expected_output_contains: list[str] | None  # 预期输出包含的关键词（可选）
    max_turns: int | None               # 允许的最大 turn 数

@dataclass
class EvalReport:
    run_id: str
    dataset_name: str
    agent_config_hash: str
    timestamp: float
    summary: EvalSummary               # 总览: 成功率、平均耗时、工具准确性
    per_case: list[CaseResult]         # 每个 case 的详细结果
    comparison: ComparisonReport | None  # 与基线的对比（如有）

@dataclass
class EvalSummary:
    total: int
    passed: int
    failed: int
    pass_rate: float
    avg_turns: float
    avg_tool_calls: float
    avg_duration_seconds: float
    tool_accuracy: float               # expected_tools 匹配率

@dataclass
class CaseResult:
    case_id: str
    passed: bool
    turns: int
    tool_calls: list[str]
    duration_seconds: float
    trace: ReplayTrace                 # 完整轨迹，用于深入分析
    metrics: dict[str, float]          # {metric_name: value}
    error: str | None = None

@dataclass
class ComparisonReport:
    baseline_run_id: str
    changes: list[MetricChange]

@dataclass
class MetricChange:
    metric_name: str
    baseline_value: float
    current_value: float
    delta: float
    direction: Literal["improved", "regressed", "unchanged"]
```

**框架内建指标**:
- `SuccessRate` — 是否在 max_turns 内完成任务（无 tool error、无 guardrail block）
- `ToolAccuracy` — `expected_tools` 是否按序出现在实际工具调用中
- `TurnEfficiency` — 完成任务所需的平均 turn 数（越少越好）
- `OutputContains` — `expected_output_contains` 中几个关键词出现在最终响应中

**最小可行实现**: 从 YAML/JSON 文件加载 `EvalDataset`，`agent.evaluate(dataset, metrics)` 运行并输出 `EvalReport.to_markdown()`:

```
# Eval Report: code_generation_v1
| Case | Pass | Turns | Tools | Duration |
|------|------|-------|-------|---------|
| basic_fix   | PASS | 3 | file_reader, file_writer | 12.3s |
| multi_file  | PASS | 5 | file_reader×3, file_writer×2 | 28.1s |
| error_retry | FAIL | 8 | file_reader, file_reader, file_deleter | 45.2s |
| --- | --- | --- | --- | --- |
| Summary      | 2/3 (66.7%) | avg 5.3 turns | accuracy 80% |

Comparison vs baseline (abc123):
  - SuccessRate: 66.7% → 66.7% (unchanged)
  - TurnEfficiency: 5.7 → 5.3 (improved -7%)
  - ToolAccuracy: 75% → 80% (improved +5%)
```

### 去中心化通信原语

**解决的问题**: 多 Agent 通信设计以 `Supervisor` 为中心编排，但真实多 Agent 场景需要去中心化的点对点协商（AutoGen swarm、多智能体辩论）。`SharedWorkspace` 仅简单读写，无锁、一致性保证、冲突解决——多 Agent 并行操作共享状态时数据竞争和死锁推给应用层。

```python
# arf/core/protocols/communication.py (追加)

class PeerAgent(Protocol):
    """去中心化 Agent 端点——不依赖 Supervisor 即可相互发现、协商、协作。
    每个 PeerAgent 同时是消息生产者与消费者，Supervisor 是可选的上层编排器。"""
    async def broadcast(self, message: AgentMessage) -> None: ...
    async def negotiate(self, proposal: dict, peers: list[str]) -> ConsensusResult: ...
    async def join_swarm(self, swarm_id: str) -> None: ...
    async def leave_swarm(self) -> None: ...

class Lock(Protocol):
    """SharedWorkspace 的并发控制原语。多 Agent 写入同一 key 前获取锁，
    防止竞争写入导致数据损坏。"""
    async def acquire(self, resource_key: str, owner: str, ttl: float = 30.0) -> bool: ...
    async def release(self, resource_key: str, owner: str) -> None: ...
    async def wait_for(self, resource_key: str, timeout: float) -> bool: ...  # 阻塞等待锁释放

class ConsensusProtocol(Protocol):
    """多 Agent 达成一致意见的协商机制。如: 多数投票、轮值主席、raft 简化版。
    用于 Agent 群体需要一致决策的场景（如代码审查是否通过、技术方案选型）。"""
    async def propose(self, proposal: dict, voters: list[str]) -> ConsensusResult: ...
    async def vote(self, proposal_id: str, vote: Literal["approve", "reject", "abstain"]) -> None: ...

@dataclass
class ConsensusResult:
    proposal_id: str
    approved: bool
    vote_counts: dict[str, int]    # {"approve": 3, "reject": 1, "abstain": 0}
    threshold: float               # 通过阈值
    resolution: str                # 最终决议
```

**默认实现**:
- `PeerAgent` → 基于 `AgentBus` 的去中心化实现，无 Supervisor 也可工作（Supervisor 是可选的编排层插件）
- `Lock` → `InMemoryLock`（单进程 async primitives）
- `ConsensusProtocol` → `MajorityVote`（多数票通过，默认 threshold 0.5）

**在 SharedWorkspace 中的集成**: `SharedWorkspace.write(key, value)` 内部可选获取 `Lock`。agent config 中配置冲突策略:

```yaml
shared_workspace:
  conflict_strategy: lock        # lock: 写前获取锁, merge: 自动合并, last_wins: 最后写入覆盖
  lock_ttl: 30s
  consensus: majority_vote       # majority_vote | unanimous | delegated
```

---

## 配置格式

**设计原则**: 用户界面极简——Agent 只是 model / skill / tool / hook 四种资源的组合。框架内置领域最佳默认行为，高级配置 opt-in 下沉。

**用户心智模型**:
```
Agent = 身份(name, description) + Model(用什么模型) + Skill(有什么技能) + Tool(有什么工具) + Hook(生命周期回调)
多Agent = 多个 Agent + Handover(交接规则) 或 Supervisor(监督规则)
```

所有 compaction、memory、routing、guardrails、errors、human_loop 等框架内部机制由 `AdvancedConfig.default()` 自动推导，用户只在需要调优时通过 `advanced:` 字段覆盖。

### agent.yaml（极简版 — 用户日常编写的形态）

```yaml
# ============================================================================
# Agent 身份与行为配置 | Agent identity & behavior configuration
#
# 此为 YAML 导出格式。推荐入口: create_agent(config=AgentConfig(...))
# This is the YAML export format. Recommended entry: create_agent(config=AgentConfig(...))
# ============================================================================

name: my_agent
  # Agent 唯一标识符 | Unique agent identifier

description:
  # Agent 能力概述 | Capability overview
  # 描述该 Agent 擅长什么、能处理哪些类型的任务
  # 1. 描述领域擅长，而非行为指令
  # 2. 列出支持的技术栈
  # 3. 明确能力边界
  擅长代码生成、调试、重构和架构设计。
  支持 Python、TypeScript、Go 等多语言开发。

system_prompt:
  # ==========================================================================
  # 系统提示词管道模板 | System prompt pipeline template
  #
  # 运行时按 priority 从小到大依次拼接。占位符由 engine 自动填充。
  # 占位符: {{WORKSPACE}} {{MEMORY}} {{CRITICAL_RULES}} {{INVENTORY}} {{LANGUAGE}}
  # ==========================================================================

  template: |
    You are {{AGENT_NAME}}, an AI assistant.

    {{WORKSPACE}}

    {{CRITICAL_RULES}}

    {{INVENTORY}}

    {{MEMORY}}

    {{LANGUAGE}}

  pipeline:
    # 值越小越靠前 | Lower = earlier in prompt
    - priority: 10
      section: workspace
        # 框架自动生成当前目录结构 | Auto-generated

    - priority: 20
      section: memory
        # 框架从 MemoryStore + MemoryRetriever 自动加载 | Auto-loaded

    - priority: 25
      section: critical_rules
        # 不可违背的核心行为约束 | Non-negotiable rules
        # 撰写规则:
        #   1. 只放"违反导致严重后果"的规则，≤5 条
        #   2. 正面表述: "先读文件再编辑" 而非 "不要直接编辑"
        #   3. 不重复框架默认规则（sandbox、guardrails 已覆盖安全层）

    - priority: 30
      section: inventory
        # 能力描述 + 工具/技能/模型清单 | Auto-generated from description + active resources

    - priority: 60
      section: language
        # 输出语言指令 | Auto-generated from session locale

  critical_rules: |
    ## Critical Rules (DO NOT VIOLATE)
    1. Always read a file before editing it — never edit from memory.
    2. When a tool fails, analyze the error and retry; do not skip the step.
    3. Respect sandbox boundaries — do not access paths outside the workspace.
    4. If uncertain about any operation, ask for clarification before proceeding.

# ----------------------------------------------------------------------------
# 执行循环策略 | Execution loop strategy
# ----------------------------------------------------------------------------
loop_strategy: react
  # react: 标准 Think → Act → Observe 循环 (默认)
  # direct: 不调用工具，直接回复
  # plan_execute: 先列计划再逐步执行 (后续)

max_turns: 50
  # 单次会话最大工具调用轮次，超过后引擎强制 respond

# ----------------------------------------------------------------------------
# 上下文压缩 | Context compaction
# ----------------------------------------------------------------------------
compaction:
  strategy: sliding_window
  threshold: 0.75

# ----------------------------------------------------------------------------
# 长程记忆（存储 + 检索） | Memory (store + retrieval)
# ----------------------------------------------------------------------------
memory:
  store: file
    # 存储后端 | file | sqlite | none
  workspace: ./memory
    # 记忆文件存储路径
  retriever: recent_first
    # 检索策略 | recent_first | semantic (需 embedding)
  max_tokens: 2000
    # 注入 {{MEMORY}} 的 token 预算
  top_k: 5
    # 检索条数上限

# ----------------------------------------------------------------------------
# 工具检索 | Tool retrieval (全局工具 > kernel 数量时启用)
# ----------------------------------------------------------------------------
tool_retrieval:
  enabled: false
    # true: ToolResolver 启用检索模式（内部调用 ToolRetriever 精简后返回）
    # false: 直接返回 kernel 列表，不走检索
  top_k: 10
    # 检索后保留的工具数量

# ----------------------------------------------------------------------------
# 安全护栏 | Safety guardrails
# ----------------------------------------------------------------------------
guardrails:
  input:
    strategy: none
      # none | regex_block (正则黑名单) | llm_classifier (越狱检测)
  output:
    strategy: regex_clean
      # none | regex_clean (PII 清洗) | llm_classifier (内容安全)
  tool_params:
    strategy: path_check
      # none | path_check (路径穿越检测) | command_check (命令注入)

# ----------------------------------------------------------------------------
# 错误处理 | Error handling
# ----------------------------------------------------------------------------
errors:
  tool_retry: 2
    # 工具失败最大重试次数
  tool_backoff: exponential
    # 退避策略: exponential | linear | none
  model_retry: 3
    # 模型调用失败最大重试次数
  model_5xx_action: fallback
    # 5xx 错误行为: fallback (降级) | retry | abort
  guardrail_block_action: abort
    # 护栏拦截行为: abort | ask_user

# ----------------------------------------------------------------------------
# 人类审批 | Human-in-the-loop
# ----------------------------------------------------------------------------
human_loop:
  approval_points:
    strategy: tool_name_allowlist
      # always_auto: 从不暂停（默认）
      # tool_name_allowlist: 仅白名单工具触发
    allowlist:
      - delete_file
      - execute_command
  channel: console
    # console | websocket | callback
  timeout: 3600s
    # 审批超时后默认拒绝

# ----------------------------------------------------------------------------
# 流式事件 | Streaming
# ----------------------------------------------------------------------------
streaming:
  enabled: true
  transport: sse
    # sse | websocket | callback
  event_types:
    # 推送的事件白名单 (默认全部)
    - thinking_delta
    - tool_call_start
    - tool_call_result
    - model_call_end
    - compaction_start
    - approval_required
    - error

# ----------------------------------------------------------------------------
# 工具沙箱 | Tool sandbox
# ----------------------------------------------------------------------------
sandbox:
  allow_escape: false
  writable_dirs: ["./output"]

# ----------------------------------------------------------------------------
# 配置热更新 | Hot reload (默认关闭)
# ----------------------------------------------------------------------------
reload:
  watch: false
    # true: 监控 agent 目录内 YAML 变化，在 turn 边界自动 reconfigure
  signals: ["SIGHUP"]
    # 触发重载的系统信号列表
```

### models.yaml

```yaml
# 模型清单与路由策略 | Model inventory & routing
models:
  quick:
    name: quick
    api_type: openai
      # openai | anthropic | custom
    model: deepseek-v4-flash
    api_base: https://api.deepseek.com
      # 框架根据 api_type 自动追加请求路径
    api_key_env: DEEPSEEK_API_KEY
    kwargs:
      # 透传给 API，框架不校验
      reasoning_effort: high
      max_tokens: 8192
      temperature: 1.0

  deep:
    name: deep
    api_type: openai
    model: deepseek-v4-pro
    api_base: https://api.deepseek.com
    api_key_env: DEEPSEEK_API_KEY
    kwargs:
      reasoning_effort: max
      max_tokens: 8192

  cheap:
    name: cheap
    api_type: openai
    model: deepseek-v4-flash
    api_base: https://api.deepseek.com
    api_key_env: DEEPSEEK_API_KEY
    kwargs:
      max_tokens: 1024

router:
  strategy: two_tier
    # two_tier | static
  default: quick
  classify:
    medium: quick
    complex: deep
  background: cheap

fallback:
  deep → quick
  quick → cheap
```

### hooks.yaml

```yaml
# ============================================================================
# 生命周期 Hook 定义 | Lifecycle hook definitions
#
# 执行规则:
# 1. Hook 间并行: 同事件节点的所有 hook 默认同时启动
# 2. Hook 内串行: 每个 hook 的 run 列表顺序执行；前一个 exit 0 后才启动下一个
# 3. 排序: agent.set_hook_order({type: [name1, name2]})
#    列出的按序串行，未列出的与列出的并行
# 4. 退出码: 0=继续, 1=阻断, 2=注入消息
# 5. 超时: 默认 30s，超时 SIGTERM → SIGKILL
# 6. 环境变量自动注入: $ARF_SESSION_ID $ARF_AGENT_NAME $ARF_WORKSPACE
#    $ARF_TOOL_NAME $ARF_TOOL_PARAMS (pre/post_tool_exec)
#    $ARF_MODEL_NAME (pre/post_model_call)
# ============================================================================

hooks:
  - name: log_start
    type: session_start
    run:
      - ./hooks/log_start.sh

  - name: load_context
    type: session_start
    run:
      - python ./hooks/load_context.py

  - name: audit_tool
    type: pre_tool_exec
    run:
      - python ./hooks/check_permissions.py
      - python ./hooks/log_access.py
    env:
      TOOL_NAME: $ARF_TOOL_NAME
      TOOL_PARAMS: $ARF_TOOL_PARAMS
    timeout: 10s

  - name: extract_memory
    type: post_model_call
    run:
      - python ./hooks/memory_extractor.py
    timeout: 15s

  - name: archive_session
    type: session_end
    run:
      - python ./hooks/compress_logs.sh
      - python ./hooks/session_archiver.py
    timeout: 60s
```

### tool.yaml

```yaml
# 工具定义 | Tool definition
name: file_reader
description: 读取工作区内的文件内容

parameters:
  # JSON Schema 参数定义
  type: object
  properties:
    path:
      type: string
      description: 文件路径（相对于工作区）
  required: [path]

provider: static_yaml
  # 工具来源 | static_yaml | mcp
  # MCP 模式下 parameters 由远程服务器提供

backend: function
  # 执行后端 | function | subprocess
  # function: 直接调用同目录 function.py 中的 execute(**kwargs)
  # subprocess: 启动子进程执行，stdin 传入 JSON params，stdout 读取结果

execution:
  sandbox: inherit
    # inherit | full | read_only
  timeout: 30s

activation:
  mode: kernel
    # kernel: 始终激活，每次 API 调用的 tool_definitions 都包含
    #   适用: 高频核心工具（文件读写、搜索）
    # discoverable: 按需激活，Agent 通过 resource_loader 加载
    #   适用: 中频工具，减少每轮上下文开销
    # passive: 不自动激活，仅手动引用
    #   适用: 实验性工具、危险操作
```

### skill.yaml

```yaml
# 技能定义 | Skill definition
name: code_review
description: 对代码变更进行结构化审查

prompt: |
  你是一个严格的代码审查者。按以下步骤审查：
  1. 理解变更意图
  2. 识别逻辑错误、边界条件、安全问题
  3. 附上具体位置和修复建议
  4. 按严重程度排序

tools:
  - file_reader
  - web_search

activation:
  mode: discoverable
    # kernel: 始终激活，每次 API 调用携带 skill prompt
    # discoverable: 在 inventory 中可见，Agent 通过 resource_loader 加载
    #   (推荐大多数 skill)
    # passive: 不在 inventory 中列出，仅手动引用
```

---

## 多 Agent 通信

### AgentBus + Supervisor + SharedWorkspace

```python
# arf/core/protocols/communication.py

class AgentBus(Protocol):
    """Agent 间消息路由。单 Agent 场景不激活。"""
    async def send(self, message: AgentMessage) -> None: ...
    async def receive(self, agent_name: str) -> AsyncIterator[AgentMessage]: ...
    async def register(self, agent: AgentInfo) -> None: ...
    async def discover(self, capability: str | None = None) -> list[AgentInfo]: ...

class TaskDelegator(Protocol):
    """任务委托的生命周期管理"""
    async def delegate(self, task: TaskSpec, from_agent: str, to_agent: str) -> TaskHandle: ...
    async def get_result(self, handle: TaskHandle, timeout: int) -> TaskResult: ...
    async def cancel(self, handle: TaskHandle) -> None: ...

class Supervisor(Protocol):
    """多 Agent 编排——决定任务分派给谁、何时介入、何时汇总。
    这是 AutoGen GroupChat/Manager 的等价抽象。"""
    async def route_task(self, task: TaskSpec, available_agents: list[AgentInfo]) -> str: ...
    async def should_intervene(self, task: TaskHandle, progress: TaskProgress) -> bool: ...
    async def synthesize(self, results: list[TaskResult]) -> str: ...

class SharedWorkspace(Protocol):
    """多 Agent 共享黑板——多个 Agent 对同一任务进展有共同理解。
    不依赖消息传递来保持同步。"""
    async def write(self, key: str, value: dict) -> None: ...
    async def read(self, key: str) -> dict | None: ...
    async def watch(self, key: str) -> AsyncIterator[dict]: ...
```

**默认实现**:
- `InMemoryBus` — asyncio.Queue 内存队列
- `RoundRobinSupervisor` — 轮询分派
- `DictWorkspace` — 内存 dict 实现

单 Agent 场景下这些模块完全旁路。

---

## Pydantic 模型（代码创建路径）

### 代码优先入口

```python
from arf import create_agent, AgentConfig, ModelConfig

# 新用户入门: 只需 4+1 个核心概念
agent = create_agent(config=AgentConfig(
    name="code-helper",
    description="擅长 Python、TypeScript 代码生成与调试。不擅长 UI 设计。",
    models=[
        ModelConfig(name="quick", api_type="openai", model="deepseek-v4-flash"),
        ModelConfig(name="deep", api_type="openai", model="deepseek-v4-pro"),
    ],
    skills=[
        SkillConfig(
            name="code_review",
            description="结构化代码审查",
            prompt="You are a strict code reviewer...",
            tools=["file_reader", "web_search"],
        ),
    ],
    tools=[
        ToolConfig(name="file_reader", description="读取文件", parameters=...),
        ToolConfig(name="web_search", description="搜索网络", source="./tools/web_search.yaml"),
    ],
    hooks=[
        HookDefinition(name="audit", type="pre_tool_exec", run=["python ./hooks/audit.py"]),
    ],
))
# 框架自动推导: AdvancedConfig.default() → loop_strategy、compaction、memory、routing、guardrails...
# Framework auto-derives all internal policies from the 4 core resources.

# 高级用户: 通过 advanced= 覆盖特定域
agent = create_agent(config=AgentConfig(
    name="code-helper",
    description="...",
    models=[...],
    tools=[...],
    advanced=AdvancedConfig(
        loop_strategy="plan_execute",
        memory=MemoryConfig(store="sqlite", retriever="semantic"),
        routing=RoutingConfig(strategy="two_tier", classify={"medium": "quick", "complex": "deep"}),
    ),
))

# 多 Agent: 声明子 Agent + 交接规则，框架自动构建通信基础设施
team = create_agent(config=AgentConfig(
    name="dev-team",
    description="Multi-agent development team",
    models=[...],
    agents=[
        AgentConfig(name="architect", description="System architecture", tools=[...]),
        AgentConfig(name="coder", description="Code writing", tools=[...]),
        AgentConfig(name="reviewer", description="Code review", skills=[...]),
    ],
    handover=HandoverConfig(rules=[
        HandoverRule(from_agent="architect", to_agent="coder", trigger="design approved"),
        HandoverRule(from_agent="coder", to_agent="reviewer", trigger="code ready"),
    ]),
))
# 内部: 自动构建 AgentBus(InMemoryBus)、为子 Agent 注入 PeerAgent、构建交接路由表

# YAML 入口: 加载已有配置
agent = create_agent(agent_dir="./my_agent")

# 导出 YAML: 分享用途，自动写入 arf_version 元数据
agent.config.to_yaml("./my_agent_export/")

# 配置热更新: 在 turn 边界安全切换
agent.reconfigure(advanced=AdvancedConfig(loop_strategy="direct"))
```

### ModelConfig

```python
class ModelConfig(BaseModel):
    """框架仅管理路由/适配所需最小字段，其余透传"""
    name: str
    api_type: Literal["openai", "anthropic", "custom"] = "openai"
    model: str
    api_base: str = "https://api.deepseek.com"
    api_key_env: str = "DEEPSEEK_API_KEY"
    kwargs: dict = {}
```

### AgentConfig

```python
class AgentConfig(BaseModel):
    """Agent 完整配置。
    用户界面极简: name + description + 4 种核心资源。
    所有内部机制通过 AdvancedConfig.default() 自动推导。
    """
    schema_version: str = Field(default="1.0", frozen=True)
        # 框架自动管理，用户不设置

    # ---- 身份 | Identity (必填) ----
    name: str
    description: str
        # Agent 能力概述，注入 system prompt 的 {{INVENTORY}} section

    # ---- 4 种核心资源 | 4 Core Resources ----
    models: list[ModelConfig]                         # 必填: 至少一个
    skills: list[SkillConfig] = []                    # 可选
    tools: list[ToolConfig] = []                      # 可选
    hooks: list[HookDefinition] = []                  # 可选

    # ---- 高级配置 | Advanced (全部可选) ----
    advanced: AdvancedConfig | None = None
        # None → 框架调用 AdvancedConfig.default() 自动推导
        # 用户可覆盖任意子域，未覆盖的域保持默认

    # ---- 多 Agent | Multi-Agent (可选) ----
    agents: list["AgentConfig"] | None = None         # 子 Agent
    handover: HandoverConfig | None = None            # 交接规则
    supervisor: SupervisorConfig | None = None        # 监督规则


class AdvancedConfig(BaseModel):
    """所有框架内部机制，全部有生产级默认值。
    用户不填 = 框架自动选择最优策略。
    """
    loop_strategy: Literal["react", "direct", "plan_execute"] = "react"
    max_turns: int = 50
    critical_rules: str = ""                          # 注入 system prompt 的硬约束
        # 撰写规则: ≤5 条，正面祈使句，不与 sandbox/guardrails 重复

    routing: RoutingConfig | None = None
        # None → 单模型 static；多模型时自动启用 two_tier
    compaction: CompactionConfig | None = None
        # None → 模型 ctx < 32K 时自动启用 75% sliding_window
    memory: MemoryConfig | None = None
        # None → file store + recent_first + rule-based writer
    guardrails: GuardrailsConfig | None = None
        # None → 全透传（生产环境建议至少启用 output: regex_clean）
    errors: ErrorConfig | None = None
        # None → 工具 2 次指数退避重试 + 模型 3 次重试/5xx 降级
    human_loop: HumanLoopConfig | None = None
        # None → 自动放行
    streaming: StreamingConfig | None = None
        # None → SSE 全事件推送
    sandbox: SandboxConfig | None = None
        # None → 路径隔离，禁止逃逸
    tool_retrieval: ToolRetrievalConfig | None = None
        # None → tools > 20 时自动启用 top_k=10
    reload: ReloadConfig | None = None
        # None → 关闭

    @classmethod
    def default(cls) -> "AdvancedConfig":
        return cls()

    @classmethod
    def auto_derive(cls, agent: "AgentConfig") -> "AdvancedConfig":
        """根据 4 种核心资源自动推导策略。"""
        adv = cls.default()
        total_tools = len(agent.tools) + sum(len(s.tools) for s in agent.skills)
        if total_tools > 20:
            adv.tool_retrieval = ToolRetrievalConfig(enabled=True, top_k=10)
        if len(agent.models) > 1:
            adv.routing = RoutingConfig(strategy="two_tier")
        return adv


class HandoverConfig(BaseModel):
    """多 Agent 交接规则 — 用户声明意图，框架自动构建基础设施"""
    rules: list[HandoverRule]

class HandoverRule(BaseModel):
    from_agent: str
    to_agent: str
    trigger: str                                      # 触发条件描述


class SupervisorConfig(BaseModel):
    """中心化监督 — 替代 handover 模式"""
    type: Literal["round_robin", "llm_router", "custom"] = "round_robin"
    llm_model: str | None = None                      # llm_router 模式使用的模型名
```

## YAML Schema 版本化

**解决的问题**: `AgentConfig` 随框架演进会增减字段、重命名或改语义。已导出的 YAML 配置在升级框架后必须能被正确识别。

### 机制

```yaml
# agent.yaml 头部元数据 | YAML header metadata
# arf_version: 1.0
name: my_agent
# ...
```

- **导出时**: `AgentConfig.to_yaml()` 自动在第一行写入 `# arf_version: {schema_version}`
- **加载时**: `BaseAgent.from_dir()` 读取 `arf_version`，与框架支持的版本范围比较
- **兼容规则**: 同主版本号兼容（1.x ↔ 1.x），跨主版本拒绝加载并提示迁移

```python
# arf/agent/config.py

class AgentConfig(BaseModel):
    schema_version: str = Field(default="1.0", frozen=True)
        # 框架自动写入，用户不设。导出时写入 arf_version 元数据。
        # Auto-managed by framework. Written as arf_version in YAML header.

    @classmethod
    def from_yaml(cls, path: str) -> "AgentConfig":
        raw = yaml.safe_load(open(path))
        version = raw.get("schema_version", "0.0")  # 0.0 = 未版本化的旧配置
        if version not in cls._supported_versions():
            raise SchemaVersionError(
                f"Config schema v{version} not supported by this framework "
                f"(supports: {cls._supported_versions()}). "
                f"Run `arf migrate --from {version}` to upgrade."
            )
        if version != cls._current_version():
            raw = cls._migrate(raw, from_version=version)
        return cls(**raw)

    @staticmethod
    def _supported_versions() -> set[str]:
        return {"1.0"}

    @staticmethod
    def _current_version() -> str:
        return "1.0"

    @staticmethod
    def _migrate(data: dict, from_version: str) -> dict:
        # 未来版本迁移链: 0.0→1.0, 1.0→2.0, ...
        migrations = {
            "0.0": _migrate_0_to_1,
        }
        migrator = migrations.get(from_version)
        return migrator(data) if migrator else data
```

未带 `schema_version` 的历史配置（如当前 `arf_user_agent.yaml`）自动识别为 `"0.0"`，由迁移链处理。

## `arf/testing` — InMemory 测试替身

**解决的问题**: 所有核心组件都是 Protocol，开发者单元测试自己的 Agent 逻辑时，不希望依赖真实的 API 调用、subprocess、外部 MCP 服务器或审批终端交互。框架应提供一套完整的 InMemory 实现，让测试只需 import `arf.testing` 即可开始。

### 使用示例

```python
from arf.testing import (
    InMemoryEventBus,
    InMemoryStateStore,
    InMemoryMemoryStore,
    InMemoryToolResolver,
    InMemoryGuardRunner,
    InMemoryApprovalChannel,
)

# 组装测试 Agent
test_agent = create_agent(config=AgentConfig(
    name="test",
    role="test",
    task="test",
    description="test agent",
    system_prompt=SystemPromptConfig(
        template="You are a test assistant.",
        pipeline=[],
        critical_rules="",
    ),
    models=[ModelConfig(name="mock", api_type="openai", model="mock")],
))

# 替换为 fake 实现——所有操作在内存中完成
test_agent._inject(
    event_bus=InMemoryEventBus(),
    state_store=InMemoryStateStore(),
    tool_resolver=InMemoryToolResolver({
        "get_weather": ToolDefinition(
            name="get_weather",
            description="Get weather",
            parameters={"type": "object", "properties": {}, "required": []},
        ),
    }),
    guard_runner=InMemoryGuardRunner(),  # 全部透传
)

# 测试——无网络、无 subprocess、无外部依赖
response = await test_agent.chat("Hello")
assert response is not None

# 验证工具调用历史
assert test_agent.state_store.get("test_session") is not None
events = test_agent.event_bus.collect("tool_call_start")
assert len(events) == 0  # 不应触发工具调用
```

### 提供的 InMemory 实现

| Fake 类 | 实现 Protocol | 默认行为 |
|---|---|---|
| `InMemoryEventBus` | `EventBus` | asyncio.Queue 内存广播，`collect(type)` 方法用于断言 |
| `InMemoryStateStore` | `StateStore` | dict 存储，支持 `snapshots` 属性遍历所有 checkpoint |
| `InMemoryMemoryStore` | `MemoryStore` | dict 存储，可预设 `seed_entries` |
| `InMemoryMemoryRetriever` | `MemoryRetriever` | 返回预设的 `seed_entries`（不做语义检索） |
| `InMemoryToolResolver` | `ToolResolver` | 预设 `tool_map: dict[str, ToolDefinition]`，`execute` 返回假结果 |
| `InMemoryToolExecutor` | `ToolExecutor` | 并发执行预设函数，记录调用历史 |
| `InMemoryGuardRunner` | `GuardRunner` | 全部透传（allowed=True） |
| `InMemoryApprovalChannel` | `ApprovalChannel` | 自动批准，`responses` 属性可遍历审批历史 |
| `InMemoryAgentBus` | `AgentBus` | 内存消息路由，`sent_messages` 属性可遍历 |
| `InMemoryTaskDelegator` | `TaskDelegator` | 预设返回值，记录委托历史 |

所有 Fake 类提供 `reset()` 方法清空状态，`history` / `calls` 属性暴露操作记录用于断言。默认行为是最小可行的旁路实现——需要模拟失败场景时通过 `set_error()` / `set_block()` 等方法配置。

## 创建流程

```
代码路径 (推荐)                                    YAML 路径 (导入)
─────────────────                                  ────────────────
AgentConfig(                                        YAML 文件 → AgentConfig(**data)
  name, description,                                       │
  models, skills, tools, hooks,  ← 4 种核心资源             │
  agents, handover,              ← 多 Agent 声明            │
  advanced=AdvancedConfig(...)   ← 可选覆盖                 │
)                                                           │
        │                                                   │
        ├─ advanced is None → AdvancedConfig.auto_derive()  │
        └──────────────────┬────────────────────────────────┘
      StateStore     EventBus     ToolExecutor     ToolResolver   GuardRunner
      (checkpoint)  (events)     (并行tool调用)    (统一tool入口)  (三处硬编码)
            │              │              │              │              │
            │         ReplayController  TransactionCtx  Planner    MemoryWriter
            │         (录制/回放/断点)   (事务回滚)     (规划修正)  (记忆写入)
            │              │              │              │              │
            └──────────────┼──────────────┼──────────────┼──────────────┘
                           ▼
            GraphEngine(...)  ← DI 注入所有协议实现
              + LoopStrategy (react / plan_execute / direct)
              + MemoryRetriever (compact 前)
              + MemoryWriter (turn 结束后)
              + ToolResolver (engine 唯一 tool 入口)
              + GuardRunner (三处硬编码调用)
              + TransactionContext (工具链事务包装)
              + Planner (计划生成/跟踪/修正)
              + ErrorPolicy (失败 → 重试/降级/回滚)
              + HumanLoopManager (暂停 → StateStore.put → 恢复)
              + ReplayController (录制模式拦截 model/tool, 回放模式注入)
```

用户传入可选项 → 按用户配置使用；未传入 → 框架默认（通常为透传、旁路或禁用）。

## 迁移步骤

1. **`arf/core` + 骨架** — 建立统一类型层，所有 Protocol 和数据结构收拢到 `arf/core/`。所有子模块的 `protocol.py` 移入 `core/protocols/`。
2. **引擎层** — 搬运 `engine/`，加入 `StateStore` checkpoint、`ToolExecutor` 并发工具调用、MemoryRetriever 触发节点、ErrorPolicy 集成。
3. **EventBus** — 统一事件总线替代 streaming/observability 两套系统。
4. **resources + memory + hooks + agent** — 搬运并清理，resources 层实现 `ToolResolver`；memory 加入 `MemoryRetriever` + `MemoryWriter`。
5. **补齐所有默认实现** — 为每个域提供最小可行实现（含 `MemoryWriter`、`TransactionContext`、`Planner`、`ReplayController`、`EvalRunner`、`PeerAgent`、`Lock`、`ConsensusProtocol`）。
6. **前端隔离 + 验证** — 前端移入 `app/web/`，确认框架零应用依赖。

每步独立提交。备份分支 `arfwithapp` 保留当前完整代码。
