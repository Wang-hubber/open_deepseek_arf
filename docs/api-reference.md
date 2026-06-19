# ARF Framework API Reference

> **Harness = OS Kernel. Model = CPU. Agent = Computer.**
> Token 是指令，Agent 会话是进程，工具调用是系统调用。

---

## 架构速览

```
┌─────────────────────────────────────────────────────┐
│  create_agent(config) → BaseAgent                   │
│  ├─ astream(msg) → AsyncGenerator[AgentEvent]       │
│  ├─ run(msg) → str                                  │
│  ├─ start() / stop() / approve() / evaluate()       │
│  └─ engine: ControlPlane                            │
│       ├─ astream(state) → AsyncGenerator[AgentEvent]│
│       ├─ close(state) / undo() / set_session_mode() │
│       └─ session_mode: SessionMode                  │
├─────────────────────────────────────────────────────┤
│  Streaming: SSEStreamAdapter / NDJSONStreamAdapter  │
├─────────────────────────────────────────────────────┤
│  Types: AgentEvent, AgentState, Primitive, Level    │
└─────────────────────────────────────────────────────┘
```

**核心原则**: 框架提供 mechanism，应用通过 configuration + instantiation 决定 what。依赖注入优先，不硬编码具体实现。

---

## 1. 入口: `create_agent()`

**文件**: `arf/agent/factory.py`

```python
def create_agent(
    *,
    config: AgentConfig | None = None,
    agent_dir: str | None = None,
    app_context: AppContext | None = None,
) -> BaseAgent:
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `config` | `AgentConfig` | 二选一 | 内存中的配置对象 |
| `agent_dir` | `str` | 二选一 | `agent.yaml` 所在目录路径 |
| `app_context` | `AppContext` | 否 | 应用上下文（root、tools_dir 等） |

**作用**: 框架的**唯一构造入口**。从 `agent.yaml` 或 `AgentConfig` 对象组装出完整的 `BaseAgent`。

**示例**:
```python
from arf.agent.factory import create_agent

agent = create_agent(agent_dir="./my_agent")
await agent.start()
text = await agent.run("Hello!")
print(text)
await agent.stop()
```

---

## 2. `BaseAgent` — Agent 实例

**文件**: `arf/agent/base.py`

Agent = model + tools(选配) + skills(选配)。所有 Protocol 实现通过构造函数 DI 注入。

### 2.1 构造函数参数

```python
class BaseAgent:
    def __init__(
        self,
        config: AgentConfig,
        app_context: AppContext | None = None,
        **override_protocols,  # DI: 覆盖任意 Protocol 实现
    ) -> None:
```

| 参数 | 类型 | 说明 |
|------|------|------|
| `config` | `AgentConfig` | Agent 配置（见 §5） |
| `app_context` | `AppContext \| None` | 应用上下文，指定 root、workspace 等路径 |
| `**override_protocols` | — | 依赖注入：覆盖 StateStore、EventBus、ToolExecutor 等实现 |

可覆盖的 Protocol（通过 `**override_protocols`）:

| Key | 类型 | 默认值 |
|-----|------|--------|
| `event_bus` | `EventBus` | `InMemoryEventBus()` |
| `state_store` | `StateStore` | `FileStateStore(data_dir)` |
| `hitl` | `HITLProtocol` | `DefaultHITL(event_bus, state_store, park_coordinator)` |
| `task_lifecycle` | `TaskLifecycleProtocol` | `DefaultTaskLifecycle(event_bus)` |
| `tools_dir` | `Path` | `app_context.tools_dir` |
| `skills_dir` | `Path` | `app_context.skills_dir` |
| `plugins_dir` | `Path` | `arf/plugins/` |
| `mcp_manager` | `McpClientManager` | 自动构建 |
| `tool_executor` | `ToolExecutor` | 自动构建 |
| `guard_runner` | `GuardRunner` | 自动构建 |

### 2.2 核心方法

#### `astream()` — **唯一的执行 API**

```python
async def astream(
    self,
    user_message: str,
    session_id: str = "default",
    stop_on_text: bool = False,
) -> AsyncGenerator[AgentEvent, None]:
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `user_message` | `str` | **必填** | 用户消息 |
| `session_id` | `str` | `"default"` | 会话 ID，跨轮次保持状态 |
| `stop_on_text` | `bool` | `False` | `True` = 第一个 text-only 响应后立即结束（用于子 Agent） |

**返回**: `AsyncGenerator[AgentEvent, None]` — 流式产出所有事件。

**作用**: 框架**唯一的**对话执行入口。替代已移除的 `chat()` 和 `invoke()`。所有对话通过此方法流转。

**事件流**:
```
session_start → user_input → round_start → turn_start →
  pre_action → model_call_start → thinking_delta* → model_call_end →
  [tool_call_start → tool_call_end]* →
  post_action → turn_end →
[多轮 turn 循环] →
round_end → [task_completed] → [park等待] →
session_end
```

每个事件携带 `primitive`（`input`/`action`/`output`/`wait`）和 `level`（`session`/`round`/`turn`）标注。

**示例**:
```python
async for event in agent.astream("帮我写一个 Python 脚本"):
    if event.type == "thinking_delta":
        print(event.data["content"], end="", flush=True)
    elif event.type == "model_call_end":
        print(event.data.get("content", ""))
    elif event.type == "need_human_input":
        # 展示审批 UI...
        pass
```

#### `run()` — 便捷方法

```python
async def run(
    self,
    user_message: str,
    session_id: str = "default",
) -> str:
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `user_message` | `str` | **必填** | 用户消息 |
| `session_id` | `str` | `"default"` | 会话 ID |

**返回**: `str` — 最终助手文本。

**作用**: `astream()` 的薄封装，收集最终文本。用于 CLI/测试/脚本场景。内部调用 `collect_response(agent.astream(...))`。

**示例**:
```python
text = await agent.run("1+1等于几？")
print(text)  # "1+1等于2"
```

### 2.3 生命周期方法

```python
async def start() -> None:
    """启动 FileWatcher 和 MCP manager（进入 event loop 后调用一次）。"""

async def stop() -> None:
    """停止 FileWatcher、MCP manager，关闭所有活跃 session。"""
```

**典型生命周期**:
```python
agent = create_agent(agent_dir="./my_agent")
await agent.start()
try:
    text = await agent.run("Hello!")
finally:
    await agent.stop()
```

### 2.4 HITL 方法

```python
def approve(self, decision_id: str, approved: bool = True) -> bool:
    """处理审批决定。返回 False 如果 ApprovalPlugin 未注册。"""
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `decision_id` | `str` | **必填** | 来自 `approval_required` 事件的决策 ID |
| `approved` | `bool` | `True` | `True` = 批准执行 |

### 2.5 评测方法

```python
async def evaluate(self, benchmark) -> EvalReport:
    """运行评测 benchmark 并返回 EvalReport。"""
```

### 2.6 属性

| 属性 | 类型 | 说明 |
|------|------|------|
| `agent.engine` | `ControlPlane` | 底层引擎，用于直接调用 `astream()`、`close()`、`undo()` |
| `agent.trace_store` | `TracePlugin \| None` | TracePlugin 实例，用于读取 trace 数据 |

---

## 3. `ControlPlane` / `PrimitiveEngine` — 执行引擎

**文件**: `arf/engine/control_plane.py`

框架的执行骨架。所有行为通过插件注入。`PrimitiveEngine` 是 `ControlPlane` 的别名。

### 3.1 构造函数参数

```python
class ControlPlane:
    def __init__(
        self,
        *,
        state_store: StateStore,
        tool_executor: ToolExecutor,
        event_bus: EventBus | None = None,
        blocking_plugins: list | None = None,
        side_plugins: list | None = None,
        call_model: Callable | None = None,
        stream_model: Callable | None = None,
        cancel_event: asyncio.Event | None = None,
        system_prompt: str = "",
        max_turns: int = 50,
        max_tokens: int | None = None,
        window_size: int = 131_072,
        workspace_dir: str = "",
        data_dir: str = "./data",
        memory_dir: str = "./data/memory",
        mcp_tool_resolver: Callable | None = None,
        call_timeout: float | None = 120.0,
        hitl_timeout: float = 300.0,
        session_mode_manager: SessionModeManager | None = None,
        hitl: HITLProtocol | None = None,
        task_lifecycle: TaskLifecycleProtocol | None = None,
        park_coordinator: ParkCoordinator | None = None,
    ) -> None:
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `state_store` | `StateStore` | **必填** | 状态持久化 |
| `tool_executor` | `ToolExecutor` | **必填** | 工具执行器 |
| `event_bus` | `EventBus \| None` | `None` | 事件总线 |
| `blocking_plugins` | `list \| None` | `None` | 阻塞式插件列表 |
| `side_plugins` | `list \| None` | `None` | 旁路插件列表 |
| `call_model` | `Callable \| None` | `None` | 模型调用函数 |
| `stream_model` | `Callable \| None` | `None` | 流式模型调用函数 |
| `cancel_event` | `asyncio.Event \| None` | `None` | 取消信号 |
| `system_prompt` | `str` | `""` | 系统提示词 |
| `max_turns` | `int` | `50` | 单轮最大 turn 数 |
| `max_tokens` | `int \| None` | `None` | token 预算上限（None = 无限制） |
| `window_size` | `int` | `131072` | 上下文窗口大小 |
| `workspace_dir` | `str` | `""` | 工作区根目录 |
| `data_dir` | `str` | `"./data"` | 数据目录 |
| `memory_dir` | `str` | `"./data/memory"` | 记忆目录 |
| `mcp_tool_resolver` | `Callable \| None` | `None` | MCP 工具解析函数 |
| `call_timeout` | `float \| None` | `120.0` | 单次模型调用超时（秒） |
| `hitl_timeout` | `float` | `300.0` | HITL 等待超时（秒） |
| `session_mode_manager` | `SessionModeManager \| None` | `SessionModeManager(ASK)` | session 权限模式 |
| `hitl` | `HITLProtocol \| None` | `DefaultHITL` | HITL 协议实现 |
| `task_lifecycle` | `TaskLifecycleProtocol \| None` | `DefaultTaskLifecycle` | 任务生命周期协议实现 |
| `park_coordinator` | `ParkCoordinator \| None` | `None` | 驻留协调器 |

### 3.2 核心方法

#### `astream()` — 引擎执行

```python
async def astream(
    self,
    state: AgentState,
    stop_on_text: bool = False,
) -> AsyncGenerator[AgentEvent, None]:
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `state` | `AgentState` | **必填** | 包含 messages、session_id 的初始状态 |
| `stop_on_text` | `bool` | `False` | 首次 text-only 响应后立即结束 |

**作用**: 引擎层的流式执行。从给定 state 开始执行 session/round/turn 生命周期，产出所有 `AgentEvent`。`BaseAgent.astream()` 内部调用此方法。

#### `close()` — 会话关闭

```python
async def close(self, state: AgentState) -> AsyncGenerator[AgentEvent, None]:
    """幂等关闭 session — 触发 session_end hook + 持久化 state。"""
```

#### `undo()` — 回滚

```python
def undo(
    self, steps: int,
    session_id: str = "",
    workspace_dir: str = "",
) -> dict | None:
    """回滚 N 轮。委托给 UndoPlugin 的 RoundManager。"""
```

#### `set_session_mode()` — 运行时权限切换

```python
async def set_session_mode(
    self, mode: SessionMode | str,
    session_id: str = "",
) -> None:
    """运行时切换 session 权限模式，产出 session_policy_switch 事件。
    mode: "auto" | "ask" | "plan"
    """
```

#### `checkpoint_count()`

```python
def checkpoint_count(self) -> int:
    """可用回滚检查点数量。"""
```

### 3.3 属性

```python
@property
def session_mode(self) -> SessionMode:
    """当前全局 session 模式: SessionMode.AUTO | ASK | PLAN"""
```

---

## 4. 流式适配器

### 4.1 `SSEStreamAdapter` — Server-Sent Events

**文件**: `arf/streaming/adapters/sse.py`

```python
class SSEStreamAdapter:
    def __init__(self, agent: BaseAgent) -> None: ...

    async def stream(
        self,
        user_message: str,
        session_id: str = "default",
        stop_on_text: bool = False,
    ) -> AsyncIterator[bytes]:
```

**作用**: 将 `agent.astream()` 包装为 SSE 格式（`data: <json>\n\n`），用于 HTTP 流式响应。

**产出格式**: `data: {"type":"thinking_delta","data":{"content":"好"},"primitive":"action","level":"turn"}\n\n`

**示例** (FastAPI):
```python
from fastapi.responses import StreamingResponse

@app.get("/stream")
async def stream():
    adapter = SSEStreamAdapter(agent)
    return StreamingResponse(
        adapter.stream("hello"),
        media_type="text/event-stream",
    )
```

### 4.2 `NDJSONStreamAdapter` — 行分隔 JSON

**文件**: `arf/streaming/adapters/ndjson.py`

```python
class NDJSONStreamAdapter:
    def __init__(self, agent: BaseAgent) -> None: ...

    async def stream(
        self,
        user_message: str,
        session_id: str = "default",
    ) -> AsyncIterator[bytes]:
```

**作用**: 每行一个 JSON 对象（`<json>\n`），适合 CLI 客户端或日志流水线。

---

## 5. `AgentConfig` — Agent 配置

**文件**: `arf/agent/config.py`

### 5.1 字段

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `schema_version` | `str` | `"1.0"` | Schema 版本（冻结） |
| `session_mode` | `"auto" \| "ask" \| "plan"` | `"ask"` | 全局 session 权限模式 |
| `name` | `str` | **必填** | Agent 名称 |
| `role` | `str` | `""` | Agent 角色描述 |
| `task` | `str` | `""` | 默认任务说明 |
| `description` | `str` | `""` | Agent 描述 |
| `data_path` | `str` | `"."` | 数据根目录 |
| `allow_paths` | `list[str]` | `[]` | 允许文件操作的路径 |
| `system_prompt` | `SystemPromptConfig` | `SystemPromptConfig()` | 系统提示词配置 |
| `models` | `list[ModelConfig]` | `[]` | 模型配置列表（旧格式） |
| `model_defs` | `list[dict]` | `[]` | 模型定义（新格式：`[{model, api_base, api_key_env, kwargs}]`） |
| `agent_models` | `list[dict]` | `[]` | Agent 模型引用（新格式：`[{model, kwargs}]`） |
| `plugins_config` | `dict` | `{}` | 插件配置（含模型引用） |
| `skills` | `list[SkillConfig]` | `[]` | 技能配置 |
| `tools` | `list[ToolConfig]` | `[]` | 工具配置 |
| `plugins` | `list[str]` | `[]` | 从 `arf/plugins/` 激活的插件名列表 |
| `mcp_servers` | `list[McpServerConfig]` | `[]` | 远程 MCP 服务器配置 |
| `hooks` | `list[HookDefinition]` | `[]` | 外部 hook 脚本定义 |
| `advanced` | `AdvancedConfig \| None` | `None` | 高级配置（None = 自动推导） |
| `supervisor` | `SupervisorConfig \| None` | `None` | 监督者配置 |

### 5.2 关键方法

| 方法 | 返回类型 | 说明 |
|------|---------|------|
| `get_model_registry()` | `ModelRegistry \| None` | 从 `model_defs` 构建模型注册表 |
| `get_agent_model_configs()` | `list \| None` | 解析 Agent 模型引用 |
| `get_plugin_model_config(name)` | `ResolvedModelConfig \| None` | 解析插件模型引用 |
| `effective_advanced()` | `AdvancedConfig` | 获取高级配置（含自动推导） |
| `from_yaml(path)` | `AgentConfig` | 从 `agent.yaml` 文件加载 |

### 5.3 agent.yaml 示例

```yaml
name: my_agent
session_mode: ask
role: "Python 开发助手"
task: "帮助用户编写和调试 Python 代码"
description: "一个会写 Python 的 AI Agent"
data_path: "./data"
model_defs:
  - model: deepseek-chat
    api_base: https://api.deepseek.com
    api_key_env: DEEPSEEK_API_KEY
    context_window: 131072
agent_models:
  - model: deepseek-chat
    kwargs:
      temperature: 0.7
plugins:
  - approval
  - compaction
  - tool_guard
  - trace
  - memory
  - error_handler
plugins_config:
  memory:
    interval: 3
  compaction:
    threshold: 0.7
```

---

## 6. 核心类型

### 6.1 `AgentEvent` — 统一事件模型

**文件**: `arf/core/events.py`

```python
@dataclass
class AgentEvent:
    type: EventType           # 事件类型（见下方 EventType 字面量）
    data: dict                # 事件负载数据
    timestamp: float          # 事件时间戳（time.time()）
    trace_id: str             # 追踪 ID
    span_id: str              # Span ID
    parent_span_id: str | None  # 父 Span ID
    session_id: str           # 会话 ID
    agent_name: str           # Agent 名称
    turn: int                 # 当前 turn 编号
    primitive: str            # 当前原语: "input"|"action"|"output"|"wait"
    level: str                # 当前层级: "session"|"round"|"turn"
```

#### `EventType` — 33 种事件类型

| 分类 | 事件类型 | 说明 |
|------|---------|------|
| **会话** | `session_start`, `session_end` | 会话生命周期边界 |
| **输入** | `user_input` | 用户消息进入系统 |
| **模型** | `model_call_start`, `model_call_end`, `thinking_delta` | 模型调用 + 逐 token 思考流 |
| **工具** | `tool_call_start`, `tool_call_end`, `tool_call_result` | 工具执行生命周期 |
| **压缩** | `compaction_start`, `compaction_end`, `truncation_start`, `truncation_end` | 上下文压缩事件 |
| **安全** | `guard_block`, `guard_pass` | 工具守卫检查结果 |
| **审批** | `approval_required`, `approval_resolved` | HITL 审批事件 |
| **钩子** | `hook_start`, `hook_end` | Hook 执行边界 |
| **回滚** | `undo_executed`, `rollback_executed` | 撤销/回滚事件 |
| **保护** | `rate_limited`, `circuit_opened`, `circuit_half_open`, `circuit_closed`, `breaker_blocked` | 速率限制 + 熔断器状态 |
| **错误** | `error` | 错误事件 |
| **策略** | `session_policy_switch` | Session 权限模式变更 |
| **生命** | `need_human_input`, `human_input_provided`, `task_completed` | HITL + 任务完成基元 |
| **其他** | `safeguard_triggered`, `user_annotation` | 安全阀触发、用户反馈标注 |

### 6.2 `AgentState` — Agent 状态

**文件**: `arf/core/state.py`

```python
class AgentState(TypedDict, total=False):
    session_id: str              # 会话 ID
    agent_name: str              # Agent 名称
    messages: list[dict]         # 消息列表 [{role, content}, ...]
    current_model: str           # 当前模型名
    current_turn: int            # 当前 turn 编号
    interaction_round: int       # 用户交互轮次（跨多个 turn）
    context_summary: str         # 压缩后的上下文摘要
    tool_results: dict[str, dict]  # 工具执行结果
    plan: dict | None            # Plan-Solve 计划
    metadata: dict               # 元数据
    child_tasks: list[dict]      # 子任务列表
```

**内部引擎字段**（以 `_` 前缀，不保证稳定）:
`_session_opened`, `_session_ended`, `_pending_tool_calls`, `_total_tokens`, `_aborted`, `_park_conditions` 等。

### 6.3 `Primitive` — 四大原语

**文件**: `arf/core/primitives.py`

```python
class Primitive(str, Enum):
    INPUT = "input"    # 信息进入系统
    ACTION = "action"  # 系统执行操作
    OUTPUT = "output"  # 系统产出结果
    WAIT = "wait"      # 系统等待外部信号
```

### 6.4 `Level` — 三大层级

```python
class Level(str, Enum):
    SESSION = "session"  # 会话层
    ROUND = "round"      # 轮次层
    TURN = "turn"        # 步数层
```

### 6.5 `PrimitiveHandler` — 原语处理器协议

```python
@runtime_checkable
class PrimitiveHandler(Protocol):
    name: str

    async def on_input(self, level: Level, ctx: PluginContext) -> None: ...
    async def on_action_start(self, level: Level, ctx: PluginContext) -> None: ...
    async def on_action_end(self, level: Level, ctx: PluginContext) -> None: ...
    async def on_output(self, level: Level, ctx: PluginContext) -> None: ...
    async def on_wait_start(self, level: Level, ctx: PluginContext) -> None: ...
    async def on_wait_end(self, level: Level, ctx: PluginContext) -> None: ...
    async def on_error(self, level: Level, ctx: PluginContext, exc: Exception) -> None: ...
```

---

## 7. 兼容工具

**文件**: `arf/engine/compat.py`

### 7.1 `collect_response()` — 收集最终文本

```python
async def collect_response(
    astream: AsyncGenerator[AgentEvent, None],
) -> str:
```

**作用**: 消费 `astream()` 的所有事件，提取最终助手文本。替代已移除的 `chat()`。

### 7.2 `collect_events()` — 收集所有事件

```python
async def collect_events(
    astream: AsyncGenerator[AgentEvent, None],
) -> list[AgentEvent]:
```

**作用**: 消费 `astream()` 的所有事件并返回列表，用于测试/调试。

### 7.3 `drain_astream()` — 消费并获取最终状态

```python
async def drain_astream(
    engine,       # ControlPlane
    state: dict,  # AgentState
) -> dict:
```

**作用**: 消费引擎 `astream()` 的所有事件，从 state_store 读取并返回最终 `AgentState`。替代已移除的 `engine.invoke()`。

### 7.4 `PrimitiveHookAdapter` — 旧插件包装器

```python
class PrimitiveHookAdapter:
    def __init__(self, plugins: list): ...
    # 实现 PrimitiveHandler 协议
    # 将 on_action_start → pre_action hook, on_output → round_end/turn_end 等
```

**作用**: 将旧 `PluginProtocol` 插件包装为新的 `PrimitiveHandler` 协议，逆向兼容。

---

## 8. 工厂方法

### `create_agent()` — 唯一构造入口

```python
# arf/agent/factory.py
def create_agent(
    *,
    config: AgentConfig | None = None,
    agent_dir: str | None = None,
    app_context: AppContext | None = None,
) -> BaseAgent:
```

### `collect_response()` — CLI 便捷函数

```python
# arf/engine/compat.py → 重新导出自 arf/engine/__init__.py
from arf.engine import collect_response
text = await collect_response(agent.astream("hello"))
```

---

## 9. 完整示例

### HTTP SSE 服务

```python
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from arf.agent.factory import create_agent
from arf.streaming.adapters.sse import SSEStreamAdapter

app = FastAPI()
agent = create_agent(agent_dir="./my_agent")

@app.on_event("startup")
async def startup():
    await agent.start()

@app.get("/stream/{session_id}")
async def stream(message: str, session_id: str = "default"):
    adapter = SSEStreamAdapter(agent)
    return StreamingResponse(
        adapter.stream(message, session_id),
        media_type="text/event-stream",
    )

@app.post("/approve/{decision_id}")
async def approve(decision_id: str, approved: bool = True):
    agent.approve(decision_id, approved)
    return {"ok": True}
```

### CLI 脚本

```python
import asyncio
from arf.agent.factory import create_agent

async def main():
    agent = create_agent(agent_dir="./my_agent")
    await agent.start()
    try:
        answer = await agent.run("帮我看看当前目录下有哪些 Python 文件")
        print(answer)
    finally:
        await agent.stop()

asyncio.run(main())
```

### 直接消费事件流

```python
async for event in agent.astream("重构 auth 模块"):
    match event.primitive:
        case "input":
            print(f"[IN] {event.level}: {event.type}")
        case "action":
            if event.type == "thinking_delta":
                print(event.data.get("content", ""), end="", flush=True)
        case "output":
            if event.type == "model_call_end":
                print(f"\n[OUT] {event.data.get('content', '')}")
        case "wait":
            if event.type == "need_human_input":
                print(f"\n[WAIT] 需要人类输入: {event.data.get('question')}")
```
