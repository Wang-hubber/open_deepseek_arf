# ARF 框架解耦设计

## 目标

将 ARF 拆分为**框架层**（`arf/`）和**应用层**（`app/`）。框架层解决通用 Agent Harness 的核心问题域，以代码优先、配置辅助的设计哲学构建。应用层是该框架之上的 ARF 产品实现。

## 问题域（框架层职责）

| 域 | OS 类比 | 解决的致命问题 | 最小可行实现 | Framework 接口 |
|---|---|---|---|---|
| **core** ✨ | 内核类型系统 | 跨模块 Protocol 散落各处，engine 无法合法引用 | 统一的 Protocol + 核心数据结构集合 | 所有 Protocol 定义 + `AgentState`、`TurnContext`、`AgentEvent` 等 |
| **agent** | 进程 | Agent 生命周期管理 | Pydantic 配置驱动，代码优先 | `create_agent(config=AgentConfig(...))` |
| **engine** | CPU 流水线 | 执行循环、自动 checkpoint、并行工具调用 | ReAct 循环 + StateStore 自动持久化 + ToolExecutor 并发 | `GraphEngine` + `LoopStrategy` + `StateStore` + `ToolExecutor` |
| **observability** ✨ | 系统监控 (perf/strace) | 框架黑盒，出问题无法定位 | 统一事件流 → OTel Span 导出 | `EventBus` + `Tracer`（共享事件源） |
| **streaming** ✨ | 管道 (pipe) | 用户盯白屏等结果 | 统一事件流 → SSE/WebSocket 推送 | `EventBus` + `EventStream`（共享事件源） |
| **guardrails** ✨ | 防火墙 + 杀毒软件 | 模型输出不可信，缺少语义安全层 | 输入/输出/工具参数三阶段校验 | `InputGuardrail` + `OutputGuardrail` + `ToolGuardrail` |
| **compaction** | 虚拟内存 + 页交换 | 上下文窗口爆掉 | 75% 阈值滑动窗口压缩 | `CompactionStrategy` |
| **memory** | 文件系统 + 搜索引擎 | 记了但不会"回忆" | store 层 + 独立检索层，engine 节点自动触发 | `MemoryStore` + `MemoryRetriever` |
| **routing** | 多级缓存 (L1/L2) | 所有请求打同一个模型 | 二级分类器 | `ModelRouter` |
| **hooks** | 系统调用 | 自定义扩展点 | 6 事件节点，subprocess + 退出码契约 | `HookRunner` + `HookDefinition` |
| **sandbox** | 进程隔离 | 工具访问越界 | 路径沙箱 | `ToolSandbox` |
| **concurrency** | 乱序执行 + 多核 | 任务层面并行调度 | 顺序执行（占位） | `TaskScheduler` |
| **human_loop** | 硬件中断 + 审批工作流 | 该停时停不下来，停了恢复不了 | 暂停/审批/超时/恢复 + 依赖 StateStore 快照 | `ApprovalPoint` + `ApprovalChannel` |
| **communication** ✨ | 进程间通信 (IPC) | 多 Agent 无法协作 | Agent 消息总线 + 任务委托 + Supervisor 编排 | `AgentBus` + `TaskDelegator` + `Supervisor` + `SharedWorkspace` |
| **resources** | 文件系统索引 + 远程挂载 | 工具只能本地 YAML，无法接入远程 | 静态 YAML + MCP Provider + ToolRetriever | `ResourceRegistry` + `ToolProvider` + `ToolBackend` + `ToolRetriever` |
| **errors** ✨ | 异常处理 + 看门狗 | 工具/模型失败行为不可预测 | 重试次数 + 退避 + 降级策略 | `ErrorPolicy` |

## 核心设计：`arf/core` — 统一类型层

**解决的问题**: engine 的零依赖契约要求 engine 不能 import 任何 `arf/` 子模块。所有跨模块 Protocol 必须集中到一个 engine 可以合法依赖的位置。

`arf/core/` 是框架的类型内核，只包含 Protocol 定义和纯数据结构，零实现逻辑。

```
arf/core/
├── __init__.py           # 导出所有公共符号
├── protocols/
│   ├── tracer.py         # Tracer
│   ├── event_bus.py      # EventBus (streaming + observability 共享)
│   ├── guardrails.py     # InputGuardrail, OutputGuardrail, ToolGuardrail
│   ├── compaction.py     # CompactionStrategy
│   ├── memory.py         # MemoryStore, MemoryRetriever, ToolRetriever
│   ├── routing.py        # ModelRouter
│   ├── hooks.py          # HookRunner
│   ├── sandbox.py        # ToolSandbox
│   ├── concurrency.py    # TaskScheduler
│   ├── human_loop.py     # ApprovalPoint, ApprovalChannel
│   ├── communication.py  # AgentBus, TaskDelegator, Supervisor
│   ├── resources.py      # ToolProvider, ToolBackend
│   ├── engine.py         # LoopStrategy, StateStore, ToolExecutor
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

---

## 目录结构

```
open_deepseek_arf/
├── pyproject.toml
│
├── arf/                           # 框架 (pip install -e .)
│   ├── __init__.py                # create_agent, AgentConfig, public Protocols
│   ├── core/                      # ★ 统一类型层: 所有 Protocol + 数据结构
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
│   ├── human_loop/                # approval_points.py, channels/
│   │   └── channels/              # console.py, websocket.py
│   ├── streaming/                 # adapters/ (SSE, WebSocket — EventBus 的传输层)
│   ├── observability/             # otel.py (EventBus → OTel Span 转换器)
│   ├── communication/             # in_memory_bus.py, supervisor.py
│   ├── errors/                    # retry.py, fallback.py
│   └── concurrency/               # sequential.py
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

### ToolRetriever — 与 MemoryRetriever 对等

```python
# arf/core/protocols/memory.py

class ToolRetriever(Protocol):
    """根据当前任务上下文动态挑选 top-k 工具/技能。
    全局可能注册 500+ 工具（含 MCP 远程），全部发给模型是灾难。
    engine 在构建 tool_definitions 前调用此接口。"""
    async def retrieve(
        self,
        query_context: str,
        available_tools: list[ToolConfig],
        top_k: int = 10,
    ) -> list[ToolConfig]: ...
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

---

## 配置格式

**设计原则**: 代码优先，YAML 辅助。用户主要入口是 `create_agent(config=AgentConfig(...))`。YAML 是 AgentConfig 的序列化格式，用于分享和持久化，而非主要编程界面。

### agent.yaml（完整版）

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
    # true: engine 在构建 tool_definitions 前用 ToolRetriever 精简
    # false: 直接用 kernel 列表
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
      # cost_threshold: 费用预估超阈值触发
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

# 推荐入口: 代码组装
agent = create_agent(config=AgentConfig(
    name="my_agent",
    role="编程助手",
    task="协助开发者完成日常编码、调试和代码审查",
    description="擅长 Python、TypeScript、Go。不擅长 UI 设计。",
    system_prompt=SystemPromptConfig(
        template="You are {{AGENT_NAME}}...",
        pipeline=[...],
        critical_rules="1. Always read before editing...",
    ),
    models=[
        ModelConfig(name="quick", api_type="openai", model="deepseek-v4-flash"),
        ModelConfig(name="deep", api_type="openai", model="deepseek-v4-pro"),
    ],
    loop_strategy="react",
    max_turns=50,
))

# YAML 入口: 加载已有配置
agent = create_agent(agent_dir="./my_agent")

# 导出 YAML: 分享用途
agent.config.to_yaml("./my_agent_export/")

# 配置热更新: 在 turn 边界安全切换
agent.reconfigure(loop_strategy="direct")
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
    """Agent 完整配置，Pydantic 校验 + IDE 类型补全"""
    name: str                                         # 必填
    role: str                                         # 必填
    task: str                                         # 必填
    description: str                                  # 必填: 能力描述
    system_prompt: SystemPromptConfig                 # 必填
    models: list[ModelConfig]                         # 必填

    # 可选——不填则对应模块使用默认或禁用
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

## 创建流程

```
代码路径 (推荐)                                 YAML 路径 (导入)
─────────────────                               ────────────────
AgentConfig(name=..., ...)  ← Pydantic 校验      YAML 文件 → AgentConfig(**data)
        │                                              │
        └──────────────────┬───────────────────────────┘
                           ▼
                 BaseAgent._from_config(cfg)
                           │
            ┌──────────────┼──────────────┐
            │              │              │
      StateStore     EventBus        ToolExecutor
      (checkpoint)   (emit events)   (并行工具调用)
            │              │              │
            └──────────────┼──────────────┘
                           ▼
            GraphEngine(...)  ← DI 注入所有协议实现
              + LoopStrategy (react / plan_execute / direct)
              + MemoryRetriever (在 compact 前自动触发)
              + ToolRetriever (在构建 tool_defs 前自动触发)
              + ErrorPolicy (工具/模型/护栏失败时自动执行)
              + HumanLoopManager (审批暂停 → StateStore.put → 恢复)
```

用户传入可选项 → 按用户配置使用；未传入 → 框架默认（通常为透传、旁路或禁用）。

## 迁移步骤

1. **`arf/core` + 骨架** — 建立统一类型层，所有 Protocol 和数据结构收拢到 `arf/core/`。所有子模块的 `protocol.py` 移入 `core/protocols/`。
2. **引擎层** — 搬运 `engine/`，加入 `StateStore` checkpoint、`ToolExecutor` 并发工具调用、MemoryRetriever 触发节点、ErrorPolicy 集成。
3. **EventBus** — 统一事件总线替代 streaming/observability 两套系统。
4. **resources + memory + hooks + agent** — 搬运并清理，加入 `ToolBackend`、`ToolRetriever`、`MemoryRetriever`。
5. **补齐所有默认实现** — 为每个域提供最小可行实现（如上述各节所述）。
6. **前端隔离 + 验证** — 前端移入 `app/web/`，确认框架零应用依赖。

每步独立提交。备份分支 `arfwithapp` 保留当前完整代码。
