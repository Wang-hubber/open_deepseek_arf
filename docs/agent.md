# Agent

## 概念

Agent = `name` + `system_prompt` + `models`。它是一个**被动的消息状态机**，由外部 Harness 驱动执行。

```
┌─ AgentConfig (agent.yaml) ──┐     ┌─ AgentHarness ───────────────┐
│ name / system_prompt        │     │ run() — ReAct 主循环         │
│ models / model_defs         │ ──► │ checkpoints — 插件调度       │
└─────────────────────────────┘     │ park / resume — 人机等待     │
        │                           └──────────────┬───────────────┘
        ▼                                          │
┌─ PrimitiveAgent ────────────┐                    │
│ state: messages, waiting    │ ◄──────────────────┘
│ input() / model_call()      │    Harness 读写 state
│ wait() / finish_wait()      │
└─────────────────────────────┘
```

**核心原则**：PrimitiveAgent 只知道消息和模型调用，不知道 tools/hooks/sandbox/events。这些是 Harness + Plugin 的职责。

---

## 配置

### agent.yaml 完整示例

```yaml
schema_version: "1.0"
name: my-agent

# ── 模型 ──
model_defs:
  - model: deepseek-chat
    api_base: https://api.deepseek.com/v1
    api_key_env: DEEPSEEK_API_KEY
    context_window: 131072
    message_format: deepseek        # "openai"（默认）| "deepseek"
    temperature: 0.7
    kwargs:
      thinking_enabled: true
      reasoning_effort: high

# ── 系统提示词 ──
system_prompt:
  prefix:
    role: "你是一个有用的助手"
    critical_rules: "禁止编造文件路径"

# ── 模式与路径 ──
session_mode: ask                   # "auto" | "ask" | "plan"
allow_paths:                        # 路径白名单（sandbox 用）
  - ./
  - /tmp/output

# ── 工具与插件 ──
plugins: [filesystem, memory, approval, tool_guard]
tools: [read_file, grep]            # 启用的 user__ 工具（空 = 全部）

plugins_config:
  tool_guard:
    deny: [bash, python_exec]
    allow: [read_file, grep]
    ask: [write_file]
  approval:
    ask_list: [write_file]
    timeout: 0
  memory:
    interval: 5
    model:
      api_base: https://api.deepseek.com/v1
      api_key_env: DEEPSEEK_API_KEY
      model: deepseek-chat
      context_window: 131072
```

### 配置字段速查

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | `str` | Agent 唯一标识 |
| `model_defs` | `list[dict]` | 模型定义列表（api_base, api_key_env, model, context_window, message_format, kwargs） |
| `system_prompt.prefix.role` | `str` | Agent 角色定义（第一条 system 消息） |
| `system_prompt.prefix.critical_rules` | `str` | 硬约束规则（第二条 system 消息） |
| `session_mode` | `"auto" \| "ask" \| "plan"` | 全局权限模式，默认 `"ask"` |
| `allow_paths` | `list[str]` | 路径白名单，支持相对/绝对路径 |
| `plugins` | `list[str]` | 启用的插件列表 |
| `tools` | `list[str]` | 启用的 user__ 工具（空 = 全部） |
| `plugins_config` | `dict` | 按插件名分组的配置，覆盖 plugin.yaml 默认值 |

---

## 组装

两条路径，二选一：

### 路径 A：`create_harness()`（推荐）

```python
from arf.harness.factory import create_harness

harness = await create_harness("agent.yaml")

async for event in harness.run("用户输入"):
    if event.type == "model_chunk":
        send_sse(event.data)
    elif event.type == "model_call_end":
        print(event.data["content"])
```

### 路径 B：`BaseAgent`

```python
from arf.agent.base import BaseAgent
from arf.agent.config import AgentConfig

config = AgentConfig.from_yaml("agent.yaml")
agent = BaseAgent(config)

async for event in agent.astream("用户输入"):
    ...
```

内部实际是 `PrimitiveAgent` + `AgentHarness`。

---

## Session 生命周期

一次 `harness.run(user_message)` 调用 = 一个 round。多次调用共享 `session_id` = 一个 session。**session_id 和 state 由 harness 全权管理**——harness 创建 `FileStateStore`，新 session 时分配 ID + 注入 system prompt + 触发 `session_start`；续接 session 时从 state_store 恢复历史消息；每个 round 结束（含 park）自动落盘。`BaseAgent` 只负责解析 session_id、委托给 harness、异常兜底。

```
session_start                   ← 仅新 session 时触发一次（harness 检测 session_id 为空）
  ├─ 框架注入 system prompt（role + critical_rules）
  └─ 插件注入（memory 等）
    │
    ▼
┌─ Round 循环 ──────────────────────────────────────────┐
│ before_round                                          │
│   │                                                   │
│   ▼                                                   │
│ before_model → 工具发现 + 过滤                         │
│   │                                                   │
│   ▼                                                   │
│ model_call → LLM 推理                                  │
│   │                                                   │
│   ▼                                                   │
│ [有 tool_calls] before_tools → 权限控制 → 工具执行     │
│ [无 tool_calls] 本轮结束                               │
│   │                                                   │
│   ▼                                                   │
│ after_tools                                           │
│   │                                                   │
│   ▼                                                   │
│ after_round → memory 提取                             │
└───────────────────────────────────────────────────────┘
```

---

### session_start — 新会话初始化

新 session 时（`session_id` 首次分配），Engine 先注入系统提示词，再触发 `session_start` checkpoint 让插件注入上下文。

**框架注入**（Engine 通过 `DefaultSystemPromptProvider.build()` 写入）：

```
1. system: <role>\n\n<critical_rules>
```

**插件注入**（`session_start` checkpoint side 事件，memory 插件等）：

```
3. system: ## User Memory\n- ...（memory 插件）
4. system: ## Project Memory\n- ...（memory 插件）
```

之后 Engine 注入用户消息 `user: "hello"`，进入 Round 循环。

---

### Round 循环

#### 1. before_model — 工具发现与过滤

Engine 调用 `tool_manager.get_tool_definitions()` 获取全部工具，按 `AgentConfig` 过滤：

| namespace | 过滤规则 |
|-----------|---------|
| `kernel__` | 始终可用 |
| `user__` | 按 `tools` 列表过滤，空列表 = 全部 |
| `{plugin}__` | 按 `plugins` 列表过滤 |
| `{server}__` | 配置了 `mcp_servers` 就全部可用 |

过滤后的工具通过 `to_openai_tools()` 转为 OpenAI 格式，传给 `model_call()`。

---

#### 2. model_call — LLM 推理

PrimitiveAgent 读取 `state.messages` 全部消息，调用 LLM API。详见 [PrimitiveAgent](#primitiveagent)。

**消息格式转换**：`ModelAdapter.format_messages()` 在 API 边界统一处理。`assistant` 的 content 可能是 `{content, tool_calls}` dict，`tool` 的 content 可能是 `{tool_call_id, result, error}` dict。详见 [消息格式适配](#消息格式适配)。

---

#### 3. before_tools — 权限控制

模型返回 `tool_calls` 后，Engine 将 `_pending_tool_calls` + `_tool_defs` + `_allow_paths` 注入 `hook_data`，触发 `before_tools` checkpoint。**`tool_guard` 和 `approval` 插件统一在此执行判定。**

##### 决策矩阵

`deny` 和 `deny_patterns` **始终优先**，在任何 mode 下都拒绝。然后 mode 决定 `allow` / `ask` / `unknown` 的处理：

| mode | deny / deny_patterns | allow | ask | unknown |
|------|---------------------|-------|-----|---------|
| **AUTO** | 拒绝 | 放行 | 放行（跳过审批） | 放行 |
| **PLAN** | 拒绝 | 检查 `readOnlyHint` | 检查 `readOnlyHint` | 拒绝 |
| **ASK**（默认） | 拒绝 | 放行 | 转交 approval 审批 | 拒绝* |

> \* ASK 下 unknown：若 `deny` 非空则拒绝；若 `deny` 为空则放行（开发阶段隐式全局允许）。

##### Session Mode

```yaml
session_mode: ask   # "auto" | "ask" | "plan"
```

| 模式 | 适用场景 |
|------|---------|
| `auto` | 信任模式 — 除 deny 外全部放行，无需审批 |
| `ask`（默认） | 标准模式 — allow 放行、ask 审批、unknown 拒绝 |
| `plan` | 只读模式 — 额外检查 `readOnlyHint`，副作用工具即使在 allow/ask 中也阻止 |

**运行时切换**：

```python
harness.set_session_mode("plan")
harness.set_session_mode(SessionMode.AUTO)
```

调用后 emit `session_policy_switch` 事件，下一轮 `run()` 生效。

##### Plugin Config

```yaml
plugins_config:
  tool_guard:
    deny: [bash, python_exec]        # 黑名单 — 始终拒绝
    deny_patterns: ["rm -rf"]        # 参数内容正则 — 对 json.dumps(params) 做 re.search
    allow: [read_file, grep]         # 白名单 — AUTO/ASK 放行，PLAN 还需检查 readOnlyHint
    ask: [write_file]                # 审批列表 — ASK 下转交 approval
    sandbox_check: true              # 启用路径 sandbox（默认 true）
  approval:
    ask_list: [write_file]           # 实际触发审批
    timeout: 0                       # 0 = 无限等待；> 0 = 超时自动拒绝（秒）
```

##### 工具名匹配：裸名 vs 全名

工具运行时带 namespace 前缀（`filesystem__read_file`），配置文件可写裸名或全名。`matches_perm()` 两层匹配：

| 配置写法 | 匹配范围 |
|---------|---------|
| `read_file`（裸名） | 所有 namespace |
| `filesystem__read_file`（全名） | 仅该 namespace |

多 namespace 同名工具差异化管控时用全名。

##### 工具声明 readOnlyHint（PLAN 模式）

`has_side_effect(name, tool_defs)` 三级判定：

1. **工具自声明（权威）** — 读 `tool.yaml` 的 `annotations.readOnlyHint`：`true` → 放行，`false` → 阻止
2. **Kernel 硬编码兜底** — 仅 3 个剩余 kernel tools（`ask_user`、`use_skill`、`task_complete`）
3. **未知 → assume side effect** — 安全默认

```yaml
# tool.yaml
annotations:
  readOnlyHint: true   # 只读，PLAN 放行
```

##### Sandbox（路径白名单）

`sandbox_check: true` 时，tool_guard 对每个 `format: path` 参数检查是否在 `allow_paths` 内：

1. `os.path.normpath(value)` 标准化路径
2. 检查是否等于或以 `allow_path + os.sep` 开头
3. 不在白名单内 → `guard_block` + 写入 `_blocked_results`

```yaml
# agent.yaml
allow_paths:
  - /home/user/project     # 绝对路径
  - ./workspace            # 相对路径 → 以 AppContext.root 为基准解析
```

##### 工具处理管道

被阻断的工具写入 `_blocked_results` 并从 `_pending_tool_calls` 移除，后续插件不再看到：

```
_pending_tool_calls (model 返回的全部 tool call)
    │
    ▼
tool_guard._guard()
    ├─ Layer 1: deny_patterns → _block_tool → 移除
    ├─ Layer 2: deny          → _block_tool → 移除
    ├─ Layer 3: sandbox       → _block_tool → 移除
    └─ Layer 4: mode + allow/ask/unknown
         ├─ AUTO: 全部 guard_pass
         ├─ PLAN: readOnlyHint → 副作用 → _block_tool → 移除
         └─ ASK: allow→guard_pass, ask→留在 pending 等 approval
    │
    ▼ (仅未被移除的工具继续)
approval._check_approval()
    ├─ AUTO mode → 直接 return
    ├─ 已决议 → 拒绝时 _block_tool → 移除
    └─ 新审批 → park 等待 → 拒绝时 _block_tool → 移除
    │
    ▼
engine 执行
    ├─ _all_tool_calls（原始列表）→ 全部 tool 都有 tool_call_start/end 事件
    ├─ _pending_tool_calls（剩余）→ execute_batch 实际执行
    └─ _blocked_results            → 合并为 ToolResult(success=False)
```

**关键设计**：阻断不丢事件。Engine 保存 `_all_tool_calls` 原始列表，被阻断移除的工具仍通过 `_blocked_results` 注入失败结果。

##### 审批流程 (Park/Resume)

`approval` 插件使用 Engine 的 park/resume 机制，不内部阻塞：

```
Turn N, before_tools checkpoint:
  approval._check_approval()
    ├─ 检查 _pending_tool_calls 中是否有 ask_list 工具
    ├─ emit approval_required 事件（→ captured_events → REPL）
    ├─ ctx.agent.wait("before_tools", ...) 注册等待
    └─ return（不阻塞）

  Engine._checkpoint() → waiting 非空 → return True
  Engine yield captured events（approval_required 到达 REPL）
  Engine yield parked 事件
  Engine._do_park() → await park_event.wait()

外部（REPL）:
  ├─ plugin.approve(decision_id, approved=True/False)
  │     └─ 存储决议 + agent.finish_wait(wait_id)
  └─ engine.resolve_wait(wait_id) → park_event.set()

Turn N, before_tools checkpoint 重入（loop）:
  approval 检测到已决议 → 通过/拒绝 → _block_tool → Engine 执行剩余工具
```

**关键设计点**：
- 审批插件不 `raise`，拒绝只是 `_block_tool` → Session 不崩溃
- `tool_guard` 的 `ask` 列表不移除工具，留给 `approval` 处理
- Engine 在 `before_tools` checkpoint 上 loop，支持审批决议后重入过滤

---

#### 4. tool_execution — 工具执行

工具执行收口到 `McpClientManager`，按 namespace 路由：

```
AgentHarness.run()
  └─ tool_manager.execute_batch(active_calls)   # asyncio.gather 并行
       └─ tool_manager.execute(name, params)    # 单次调用，按 namespace 路由
            ├─ kernel__     → 进程内 handler
            ├─ user__       → ToolProvider — tools/ 目录 function.py
            ├─ {plugin}__   → PluginProvider — 插件 tools/ 目录 function.py
            └─ {server}__   → 远程 MCP subprocess
```

**并行优先**：Engine 优先调用 `execute_batch()`（`asyncio.gather` 并行）。仅当 `tool_manager` 不提供 `execute_batch` 时才 fallback 顺序执行。

**阻断处理**：Engine 检查 `_blocked_results`，跳过已阻断工具的 `execute_batch`，将预注入的失败结果合并到 `tool_results`，统一走 `tool_call_end` 事件和 `agent.input("tool", ...)`。

---

#### 5. after_round — Memory 自动提取

`memory` 插件是完全自持的——自己管理 `MemoryIndex`、`SecretsStore` 和专有提取模型。

**四层记忆**：

| 层 | 文件 | 写入方 | 内容 |
|----|------|--------|------|
| **project** | `data/memory/project.md` | Agent 调 `memory__write_project_memory` | 架构决策、约定、修复记录 |
| **user** | `data/memory/user.md` | Agent 调 `memory__write_user_memory` + 插件 `round_end` 自动提取 | 用户角色、偏好、决策 |
| **secrets** | `data/memory/secrets.enc` | Agent 调 `memory__write_secret` | 加密的 API key、密码 |
| **task_memory** | `data/memory/tasks.md` | 插件 `task_completed` 自动提取 + 合并 | 可复用任务经验 |

**6 个 memory 工具**（`memory__*` namespace，PluginProvider 自动加载）：

| 工具 | 类型 | 说明 |
|------|------|------|
| `memory__write_user_memory` | 写 | 持久化用户级记忆 |
| `memory__write_project_memory` | 写 | 持久化项目级记忆 |
| `memory__search_task_memory` | 读 | LLM 搜索 tasks.md |
| `memory__list_secrets` | 读 | 列出 secret 名称 |
| `memory__read_secret` | 读 | 解密返回 secret 值 |
| `memory__write_secret` | 写 | 加密存储 secret |

**自动提取**：

- **session_start**：`MemoryIndex.build_injected_messages()` → 注入已有 memory 为 system 消息
- **round_end**（每 N 轮）：取最近 20 条消息 → LLM 提取用户事实 → 写入 `user.md`。无新信息输出 `NO_NEW_MEMORY` 跳过
- **task_completed**：取完整对话 → LLM 提取类别/方案/教训 → LLM 合并到 `tasks.md`（去重、计数、裁剪）

```yaml
plugins_config:
  memory:
    interval: 5                    # 提取间隔（每 N 轮）
    max_memory_size: 300           # 消息截断阈值
    model:                         # 专有提取模型（必需）
      api_base: https://api.deepseek.com/v1
      api_key_env: DEEPSEEK_API_KEY
      model: deepseek-chat
      context_window: 131072
```

---

### 可用插件总览

| 插件 | 配置项 | 触发点 | 说明 |
|------|------|--------|------|
| `tool_guard` | `allow`, `deny`, `ask`, `deny_patterns`, `sandbox_check` | `before_tools` | 统一权限门禁 + sandbox |
| `approval` | `ask_list`, `timeout` | `before_tools` | 人机审批：park → REPL → approve → resume |
| `memory` | `interval`, `model` | `session_start`, `round_end`, `task_completed` | 记忆注入 + LLM 提取 |
| `compaction` | `tool_output` | `tool_output`, `round_end` | 工具输出外部化 + 消息窗口压缩 |
| `plan_solve` | `max_depth`, `timeout` | — | Plan-Solve 执行 |
| `error_handler` | `max_retries` | `on_error` | 错误恢复策略 |

---

## PrimitiveAgent

### 构造函数

```python
PrimitiveAgent(
    agent_id: str,
    model_config: dict,                     # {api_base, api_key_env, model_name, context_window}
    call_model: Callable[[list[dict], list[dict] | None], Awaitable[ModelResult]],
    stream_model: Callable[[list[dict], list[dict] | None], AsyncIterator[dict]] | None = None,
)
```

| 参数 | 说明 |
|------|------|
| `agent_id` | 唯一标识，通常取自 `AgentConfig.name` |
| `model_config` | 模型元信息，持久化到 `AgentState` |
| `call_model` | 非流式调用，由 `_build_call_model()` 注入 |
| `stream_model` | 流式调用，可为 None |

### 状态属性 `state: AgentState`

```python
@dataclass
class AgentState:
    agent_id: str
    session_id: str                        # Harness 在 session 开始时赋值
    messages: list[Message]                # [{message_id, role, content}]
    waiting: dict[str, list[WaitItem]]     # hook_name → [WaitItem]
    model_config: dict                     # 构造时传入
```

### 6 个原语

| 方法 | 签名 | 说明 |
|------|------|------|
| `input` | `(role, content) → Message` | 向 `state.messages` 注入消息，role: `"system"` `"user"` `"assistant"` `"tool"` |
| `model_call` | `async (stream=True, tools=None) → ModelResult \| ModelStream` | 发起 LLM 调用，默认流式 |
| `wait` | `(hook_name, reason) → WaitItem` | 向 `state.waiting[hook_name]` 追加等待项 |
| `finish_wait` | `(wait_id) → dict` | 移除等待项 |
| `stop` | `() → AgentState` | 停用并返回持久化状态 |
| `resume` | `(state, call_model, stream_model=None) → PrimitiveAgent` | 从持久化状态重建（类方法） |

---

## `model_call()` 详细文档

### 签名

```python
async def model_call(self, stream: bool = True, tools: list[dict] | None = None) -> ModelResult | ModelStream
```

### 行为

读取 `state.messages` 全部消息，构造 `[{role, content}]` 列表，传给 `call_model` 或 `stream_model`。消息格式转换由 `ModelAdapter.format_messages()` 在 API 边界完成。

```
state.messages ──► [{role, content}] ──► ModelAdapter.format_messages() ──► LLM API
       tools ──────────────────────────────────────────────────┘
```

### 返回值类型

#### `ModelResult`（非流式）

```python
@dataclass
class ModelResult:
    content: str                          # 完整文本
    tool_calls: list[dict] = []           # [{id, name, params}]
    usage: dict = {}                      # {prompt_tokens, completion_tokens, total_tokens}
    finish_reason: str = "stop"           # "stop" | "tool_calls"
```

#### `ModelStream`（流式）

既是 `AsyncIterator[dict]`，又提供 `.result` 聚合属性。

**Chunk 类型**：

| chunk["type"] | 说明 |
|------|------|
| `chunk` | 文本增量，可能含 `reasoning` |
| `tool_call_chunk` | 工具调用增量 |
| `tool_call` | 完整工具调用（`finish_reason=tool_calls` 时） |
| `usage` | token 用量 |
| `error` | API 错误 |

### App 消费模式

```python
async for event in harness.run("用户输入"):
    if event.type == "model_chunk":
        chunk = event.data
        if chunk["type"] == "chunk":
            show_content(chunk["content"])
        elif chunk["type"] == "tool_call":
            show_tool_call(chunk["name"], chunk["arguments"])
    elif event.type == "model_call_end":
        print(f"tokens={event.data.get('usage', {})}")
```

### 消息格式适配

`ModelAdapter.format_messages()` — 所有 provider 格式适配的**唯一入口**。

| 格式 | 配置值 | 行为 |
|------|------|------|
| OpenAI（默认） | `"openai"` | 标准 OpenAI 消息格式 |
| DeepSeek | `"deepseek"` | 将 `reasoning_content` 回写到 assistant 消息 |

**内部格式 → API 格式转换规则：**

| role | 内部 content | API |
|------|-------------|-----|
| `assistant` (tool_calls) | `{content, tool_calls: [{id, name, params}]}` | `content: null` + `tool_calls: [...]` |
| `assistant` (reasoning) | `{..., reasoning_content: str}` | DeepSeek: `reasoning_content`；OpenAI: 忽略 |
| `tool` | `{tool_call_id, result, error}` | `content: result` + `tool_call_id` |
| 其他 | `str` | 透传 |

---

## AgentEvent

Harness 执行循环产出的事件流。App 通过 `async for event in harness.run()` 消费。

```python
@dataclass
class AgentEvent:
    type: str          # 事件类型
    data: dict         # 事件负载
    session_id: str    # 当前会话 ID
    turn: int          # 当前 turn 编号
```

### 事件类型

| `event.type` | `event.data` | 触发时机 |
|------|------|------|
| `model_chunk` | `{type, content, reasoning?, ...}` | 流式模型输出的每个 chunk |
| `model_call_end` | `{content, tool_calls, usage, finish_reason}` | 模型调用完成 |
| `tool_call_start` | `{name, id}` | 工具执行开始 |
| `tool_call_end` | `{name, id, success}` | 工具执行完成 |
| `approval_required` | `{decision_id, tool_name, params}` | 需要人工审批 |
| `approval_resolved` | `{decision_id, approved, reason}` | 审批已决议 |
| `guard_block` | `{tool_name, reason}` | 工具被门禁阻止 |
| `guard_pass` | `{tool_name}` | 工具通过门禁 |
| `parked` | `{hook_name, waiting}` | 执行暂停，等待人工输入 |
| `session_policy_switch` | `{new_mode}` | 运行时 mode 切换 |
| `error` | `{detail}` | 发生错误 |
| `task_completed` | — | 任务完成 |

---

## API 参考

### 配置加载

#### `AgentConfig.from_yaml(path)`

```python
config: AgentConfig = AgentConfig.from_yaml("agent.yaml")
```

| 参数 | 类型 | 说明 |
|------|------|------|
| `path` | `str \| Path` | agent.yaml 文件路径 |

### 工厂入口

#### `create_harness(agent_config_path, ...)`

一站式创建 `AgentHarness`。内部完成：配置加载、ModelAdapter 构建、PrimitiveAgent 创建、Plugin 发现与实例化、`McpClientManager` 组装。

```python
harness = await create_harness(
    agent_config_path="agent.yaml",
    harness_config_path="harness.yaml",   # 可选
    plugin_dir="path/to/plugins",         # 可选，默认 arf/plugins/
    event_bus=None,                       # 可选，默认 InMemoryEventBus
    data_dir="./data",                    # 可选
)
```

### Agent 生命周期

#### `agent.start()` / `agent.stop()`

空操作，占位用于未来资源初始化 / 清理。

### 对话执行

#### `agent.astream(user_message, session_id)`

流式执行一个 round，逐 `AgentEvent` yield。

```python
async for event in agent.astream("你好", session_id="my-session"):
    if event.type == "model_chunk":
        ui_stream(event.data["content"])
    elif event.type == "model_call_end":
        print(event.data["content"])
```

#### `agent.run(user_message, session_id)`

便捷方法：内部调用 `astream()`，收集所有事件后返回最终文本。

```python
title = await agent.run("为对话生成标题", session_id="title-gen")
```

### 会话状态管理

通过 `agent.state_store` 访问（`FileStateStore` 或 `InMemoryStateStore`）。

| 方法 | 说明 |
|------|------|
| `state_store.put(session_id, state)` | 写入会话状态 |
| `state_store.get(session_id)` | 读取会话状态，无存档返回 `None` |
| `state_store.delete(session_id)` | 删除会话存档 |
| `state_store.list_sessions()` | 列出所有有存档的 session ID |

### 工具执行

#### `McpClientManager.execute_batch(tool_calls)`

并行执行多个工具调用。`{call_id: ToolResult}`。

#### `McpClientManager.execute(name, params)`

执行单个工具，按 namespace 前缀路由。

### 兼容工具

| 函数 | 说明 |
|------|------|
| `collect_response(astream)` | 收集事件流中的最终文本 |
| `collect_events(astream)` | 收集所有事件到列表 |
| `drain_astream(engine, state)` | 消费引擎事件流，返回持久化状态 |
