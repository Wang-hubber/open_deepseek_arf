# ARF 框架解耦设计

## 目标

将 ARF 拆分为**框架层**（`arf/`）和**应用层**（`app/`）。框架层是一个配置驱动、文件系统原生的 Agent 框架，解决通用 Agent Harness 的核心问题域。应用层是该框架之上的 ARF 产品实现（双代理、工作区等）。

## 问题域（框架层职责）

| 域 | OS 类比 | 解决的致命问题 | 最小可行实现 | Framework 接口 |
|---|---|---|---|---|
| **agent** | 进程 | Agent 生命周期管理 | YAML/Pydantic 配置驱动 | `create_agent()` / `BaseAgent.from_dir()` |
| **engine** | CPU 流水线 | 执行循环与状态管理 | 可切换的 Agent 循环策略 | `GraphEngine` + `LoopStrategy` Protocol |
| **observability** ✨ | 系统监控 (perf/strace) | 框架黑盒，出问题无法定位 | OpenTelemetry Span/Trace 自动埋点 | `Tracer` Protocol |
| **guardrails** ✨ | 防火墙 + 杀毒软件 | 模型输出不可信，没有语义安全层 | 输入/输出/工具参数三阶段校验 | `Guardrail` Protocol |
| **compaction** | 虚拟内存 + 页交换 | 上下文窗口爆掉 | 75% 阈值滑动窗口压缩 | `CompactionStrategy` Protocol |
| **memory** (重构) | 文件系统 + 搜索引擎 | 记了东西但不会"回忆"，等于没记 | store 层 + 独立检索策略层 | `MemoryStore` Protocol + `MemoryRetriever` Protocol |
| **routing** | 多级缓存 (L1/L2) | 所有请求打同一个模型，浪费钱 | 二级分类器 | `ModelRouter` Protocol |
| **hooks** | 系统调用 | 自定义扩展点 | 6 事件节点，subprocess + 退出码契约 | `HookRunner` + `HookDefinition` |
| **sandbox** | 进程隔离 | 工具访问越界 | 路径沙箱 | `ToolSandbox` Protocol |
| **concurrency** | 乱序执行 + 多核 | 并行任务调度 | 顺序执行（占位） | `TaskScheduler` Protocol |
| **human_loop** ✨ (替代 interrupts) | 硬件中断 + 审批工作流 | 该停的时候停不下来，停了恢复不了 | 暂停/审批/超时/恢复 + 状态快照 | `ApprovalPoint` + `ApprovalChannel` Protocol |
| **streaming** ✨ | 管道 (pipe) | 用户盯着白屏等结果，体验灾难 | SSE/WebSocket 标准事件流 | `EventStream` Protocol |
| **communication** ✨ | 进程间通信 (IPC) | 多 Agent 之间是聋子，无法协作 | Agent 消息总线 + 任务委托 | `AgentBus` + `TaskDelegator` Protocol |
| **resources** (扩展) | 文件系统索引 + 远程挂载 | 工具只能本地 YAML 定义，无法发现远程工具 | 静态 YAML + MCP Provider | `ResourceRegistry` + `ToolProvider` Protocol |

## 目录结构

```
open_deepseek_arf/
├── pyproject.toml
│
├── arf/                           # 框架 (pip install -e .)
│   ├── __init__.py                # create_agent, BaseAgent, AgentConfig
│   ├── agent/                     # config.py, base.py, factory.py
│   ├── engine/                    # graph.py, nodes.py, state.py, router.py
│   │   └── loop_strategies/       # react.py, plan_execute.py, direct.py
│   ├── resources/                 # registry.py, adapter.py, schemas.py
│   │   └── providers/             # static_yaml.py (默认), mcp.py
│   ├── hooks/                     # protocol.py, runner.py
│   ├── observability/             # protocol.py, otel.py (默认: OTEL 埋点)
│   ├── guardrails/                # protocol.py, none_guard.py (默认: 透传)
│   ├── compaction/                # protocol.py, sliding_window.py
│   ├── memory/                    # store.py (protocol), retriever.py (protocol)
│   │   ├── file_store.py          # 默认 store 实现
│   │   └── recent_first.py        # 默认 retriever 实现
│   ├── routing/                   # protocol.py, two_tier.py
│   ├── sandbox/                   # protocol.py, path_sandbox.py
│   ├── human_loop/                # protocol.py, console_channel.py
│   ├── streaming/                 # protocol.py, sse.py
│   ├── communication/             # protocol.py, in_memory_bus.py
│   └── concurrency/               # protocol.py, sequential.py
│
├── app/                           # 应用层 + 前端
│   ├── web/                       # 前端 (现在的 frontend/)
│   └── arf_app/                   # ARF 应用层 (用户后续搭建)
│
└── tests/
```

## 依赖规则

1. `arf/engine/` 不 import 任何其他 `arf/` 子模块（纯 DI 注入）
2. `arf/` 下任何文件不 import `app/`
3. 默认实现只能依赖 `protocol.py` + engine + resources + hooks 公共接口
4. `observability/` 的 Span 埋点由 engine 内部 emit，通过注入的 `Tracer` 接口导出——engine 不依赖具体导出器

## 配置格式

### agent.yaml

```yaml
# ============================================================================
# Agent 身份与行为配置 | Agent identity & behavior configuration
# ============================================================================

name: my_agent
  # Agent 唯一标识符，用于日志、trace、会话路由
  # Unique agent identifier, used for logging, traces, session routing

description:
  # Agent 能力概述 | Agent capability overview
  # 描述该 Agent 擅长什么、能处理哪些类型的任务。
  # 此描述注入到 system_prompt 的 {{INVENTORY}} section 中。
  # Describes what the agent is good at and what types of tasks it handles.
  # Injected into the {{INVENTORY}} section of system_prompt.
  #
  # 撰写规则 | Writing guidelines:
  #   1. 描述领域擅长，而非具体行为指令
  #      Describe domains of expertise, not specific behaviors
  #   2. 列出支持的技术栈（语言、框架、工具链）
  #      List supported tech stacks (languages, frameworks, toolchains)
  #   3. 明确能力边界: 能做什么、不能做什么
  #      State boundaries clearly: what it can and cannot do
  #   4. 避免和 critical_rules 内容重复
  #      Don't duplicate critical_rules content
  擅长代码生成、调试、重构和架构设计。
  支持 Python、TypeScript、Go 等多语言开发。
  可读写文件、搜索网络、管理项目依赖。
  不擅长前端 UI 设计与视觉审美判断。

system_prompt:
  # ==========================================================================
  # 系统提示词管道模板 | System prompt pipeline template
  #
  # 运行时按 priority 从小到大依次拼接各 section 到 template 中对应
  # {{PLACEHOLDER}} 位置。
  # At runtime, sections are assembled into the template in ascending
  # priority order, filling each {{PLACEHOLDER}} position.
  #
  # 占位符 | Placeholders (框架运行时自动填充 | auto-filled by framework):
  #   {{WORKSPACE}}       → 工作区路径与结构
  #   {{MEMORY}}          → 当前会话 + 长期积累的记忆
  #   {{CRITICAL_RULES}}  → 不可违背的核心行为约束
  #   {{INVENTORY}}       → 能力描述 + 工具/技能/模型清单
  #   {{LANGUAGE}}        → 输出语言指令
  # ==========================================================================

  template: |
    You are {{AGENT_NAME}}, an AI assistant.

    {{WORKSPACE}}

    {{CRITICAL_RULES}}

    {{INVENTORY}}

    {{MEMORY}}

    {{LANGUAGE}}
    # 占位符在 template 中的位置即最终 prompt 中的位置
    # Placeholder position in template = final position in prompt

  # --- 管道拼接顺序 | Pipeline assembly order ---
  # 值越小越靠前；同 priority 按定义顺序
  # Lower value = earlier in prompt; same priority = definition order
  pipeline:

    - priority: 10
      section: workspace
        # 工作区信息 | Workspace info
        #
        # 框架自动生成，告知模型当前的目录结构和可用资源位置。
        # 模型据此判断文件路径、搜索范围和上下文边界。
        # Auto-generated by framework. Tells the model the current directory
        # layout and resource locations; model uses this to resolve file
        # paths, search scope, and context boundaries.
        # 用户无需手动编写 | No manual editing needed

    - priority: 20
      section: memory
        # 统一记忆（会话 + 长期） | Unified memory (session + long-term)
        #
        # 框架从 MemoryStore + MemoryRetriever 联合加载。MemoryRetriever 根据
        # 当前对话上下文自动检索相关记忆条目，MemoryStore 提供持久化存储。
        # 模型据此避免重复提问、保持回答一致性、主动应用已学到的用户习惯。
        # Auto-loaded from MemoryStore + MemoryRetriever. Retriever searches
        # relevant entries based on current context; store provides persistence.
        # User doesn't manually write this section.
        # 用户无需手动编写 | No manual editing needed

    - priority: 25
      section: critical_rules
        # 核心约束规则 | Non-negotiable behavioral rules
        #
        # 提示词中优先级最高的用户可控部分。模型将此视为不可妥协的硬约束。
        # The highest-priority user-controllable section. Model treats these
        # as non-negotiable hard constraints.
        #
        # 撰写规则 | Writing guidelines:
        #   1. 只放"违反会导致严重后果"的规则，不要放通用建议
        #      Only rules whose violation causes serious harm; no generic advice
        #   2. 每条用祈使句，正面表述（"先读文件再编辑"而非"不要直接编辑"）
        #      Use imperative affirmative form ("Read before editing" not
        #      "Don't edit without reading")
        #   3. 控制在 5 条以内，超过会稀释模型遵守力度
        #      Limit to ≤5 rules; more dilutes compliance
        #   4. 规则之间不应互相矛盾
        #      Rules must not contradict each other
        #   5. 避免和框架默认规则重复（沙箱边界由 sandbox 模块保证，
        #      语义安全由 guardrails 模块保证）
        #      Don't duplicate framework defaults (sandbox boundaries,
        #      semantic safety are handled by sandbox/guardrails modules)

    - priority: 30
      section: inventory
        # 能力描述 + 工具/技能/模型清单 | Capability + tool/skill/model inventory
        #
        # 模型认知"我是谁、我能做什么"的核心区域。模型据此决定何时调用
        # 工具、激活技能、或切换模型。能力描述来自 description 字段，
        # 工具/技能/模型清单由框架根据已激活资源自动生成。
        # The core region where the model learns "who I am, what I can do."
        # Model uses this to decide when to invoke tools, activate skills,
        # or switch models. Capability comes from description field; tool/
        # skill/model list is auto-generated by framework from active resources.
        # 清单部分自动生成，用户无需编写 | Inventory auto-generated

    - priority: 60
      section: language
        # 输出语言指令 | Output language directive
        #
        # 控制模型的输出语言和风格。排在最后起"最终提醒"作用——前面的
        # section 可能包含大量英文内容，此 section 确保模型不被带偏。
        # Controls output language and style. Placed last as a "final
        # reminder" — earlier sections may contain large amounts of English;
        # this section ensures the model doesn't drift.
        # 框架根据会话语言环境自动生成 | Auto-generated from session locale

  # --- 核心规则 | Non-negotiable critical rules ---
  # 插入 {{CRITICAL_RULES}} 占位符处
  critical_rules: |
    ## Critical Rules (DO NOT VIOLATE)
    1. Always read a file before editing it — never edit from memory.
    2. When a tool fails, analyze the error and retry; do not skip the step.
    3. Respect sandbox boundaries — do not access paths outside the workspace.
    4. If uncertain about any operation, ask for clarification before proceeding.

# ----------------------------------------------------------------------------
# 执行循环策略 | Execution loop strategy (省略则 react | omit = react)
# 控制 Agent 的思考-行动循环模式
# Controls the agent's think-act loop pattern
# ----------------------------------------------------------------------------
loop_strategy: react
  # react: 标准 Think → Act → Observe 循环 | Standard ReAct loop
  # plan_execute: 先列计划再逐步执行 | Plan first, then execute each step
  # direct: 不调用工具，直接回复 | No tool calls, respond directly
  # 对应 engine/loop_strategies/ 下的实现 | Maps to engine/loop_strategies/

max_turns: 50
  # 单次会话最大工具调用轮次 | Max tool-call turns per session

# ----------------------------------------------------------------------------
# 上下文压缩策略 | Context compaction (省略则不压缩 | omit = no compaction)
# ----------------------------------------------------------------------------
compaction:
  strategy: sliding_window
    # sliding_window | summarization | none
  threshold: 0.75
    # 上下文窗口占用比例临界值 (0.0~1.0)，达到此水位触发压缩

# ----------------------------------------------------------------------------
# 长程记忆存储与检索 | Memory: storage + retrieval (省略则无记忆 | omit = none)
# ----------------------------------------------------------------------------
memory:
  store: file
    # 存储后端 | Storage backend: file | sqlite | none
  workspace: ./memory
    # 记忆文件存储路径 | Storage path for memory files
  retriever: recent_first
    # 检索策略 | Retrieval strategy
    #   recent_first: 最近 N 条，按时间倒序 | Most recent N entries
    #   semantic: 基于 embedding 语义相似度检索 | Embedding-based semantic search

# ----------------------------------------------------------------------------
# 安全护栏 | Safety guardrails (省略则透传 | omit = pass-through)
# ----------------------------------------------------------------------------
guardrails:
  input:
    # 输入护栏，用户消息进入 engine 前执行 | Runs before user message enters engine
    strategy: none
      # none: 透传 | Pass-through (default)
      # regex_block: 正则黑名单拦截 | Regex-based blocklist
      # llm_classifier: LLM 越狱检测 | LLM-based jailbreak detection
  output:
    # 输出护栏，模型响应离开 engine 前执行 | Runs before model output leaves engine
    strategy: regex_clean
      # none: 透传 | Pass-through
      # regex_clean: 正则清洗敏感信息（API key、手机号等） | Regex-based PII redaction
      # llm_classifier: LLM 内容安全检测 | LLM-based content safety check
  tool_params:
    # 工具参数护栏，工具调用前执行 | Runs before tool invocation
    strategy: path_check
      # none: 透传 | Pass-through
      # path_check: 路径穿越检测，禁止 ../ 和绝对路径逃逸 | Path traversal detection
      # command_check: 命令注入检测 | Command injection detection

# ----------------------------------------------------------------------------
# 人类审批 | Human-in-the-loop approval (省略则自动放行 | omit = auto-approve)
# ----------------------------------------------------------------------------
human_loop:
  approval_points:
    - tool_name_allowlist
      # 审批策略 | Approval strategy
      #   always_auto: 从不暂停，直接放行 | Never pause, always approve (default)
      #   tool_name_allowlist: 仅列表中工具触发审批 | Only listed tools trigger approval
      #   cost_threshold: 费用预估超过阈值触发审批 | Estimated cost exceeds threshold
    allowlist:
      - delete_file
      - execute_command
  channel: console
    # 审批通道 | Approval communication channel
    #   console: 终端交互式审批 | Terminal interactive (default)
    #   websocket: WebSocket 实时推送 | WebSocket real-time push
    #   callback: 自定义回调函数 | Custom callback
  timeout: 3600s
    # 审批超时后行为 | Action on approval timeout
    #   超时后默认拒绝 | Default: reject after timeout

# ----------------------------------------------------------------------------
# 流式事件 | Streaming event output (省略则不推送实时事件 | omit = no streaming)
# ----------------------------------------------------------------------------
streaming:
  enabled: true
    # 是否启用实时事件推送 | Enable real-time event push
  transport: sse
    # 事件传输方式 | Event transport
    #   sse: Server-Sent Events (默认 | default)
    #   websocket: WebSocket
    #   callback: 自定义回调 | Custom callback
  events:
    # 推送的事件类型白名单 | Event types to emit (all by default)
    - thinking
    - tool_call_start
    - tool_call_result
    - model_output_delta
    - compaction
    - approval_required
    - error

# ----------------------------------------------------------------------------
# 工具执行沙箱 | Tool sandbox (省略则默认路径隔离 | omit = default path sandbox)
# ----------------------------------------------------------------------------
sandbox:
  allow_escape: false
    # 是否允许工具访问工作区外路径
  writable_dirs: ["./output"]
    # 工具可写的目录白名单
```

### models.yaml

```yaml
# 模型清单与路由策略 | Model inventory & routing
models:
  quick:
    name: quick
    api_type: openai           # openai | anthropic | custom
      # API 协议类型，决定框架用哪个 ModelAdapter 构造请求
      # API protocol type — which ModelAdapter handles request construction
    model: deepseek-v4-flash  # 模型 ID | Model ID
    api_base: https://api.deepseek.com
      # API 端点 | API endpoint. 框架根据 api_type 自动追加路径
      # (openai → /v1/chat/completions, anthropic → /v1/messages)
      # Framework auto-appends path based on api_type
    api_key_env: DEEPSEEK_API_KEY
      # 从哪个环境变量读取 API Key | Which env var holds the API key
    kwargs:                   # 透传给 API，框架不校验 | Passthrough, unvalidated
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
      # 代码补全、搜索、文件读写 → quick
    complex: deep
      # 架构设计、多步推理、调试 → deep
  background: cheap
    # compaction、标题生成、摘要 → cheap

fallback:
  deep → quick
  quick → cheap
```

### hooks.yaml

```yaml
# 生命周期 Hook 定义 | Lifecycle hook definitions
#
# 执行规则 | Execution rules
# ──────────────────────────────
# 1. Hook 间并行：同事件节点的所有 hook 默认同时启动
# 2. Hook 内串行：每个 hook 的 run 列表顺序执行；
#    前一个退出 0 后才启动下一个，非 0 则后续跳过
# 3. 排序 API: agent.set_hook_order({type: [name1, name2, ...]})
#    列出的按序串行，未列出的与列出的并行
# 4. 退出码: 0=继续, 1=阻断, 2=注入消息
# 5. 超时: 默认 30s，超时 SIGTERM → SIGKILL
# 6. 环境变量自动注入:
#    $ARF_SESSION_ID, $ARF_AGENT_NAME, $ARF_WORKSPACE
#    $ARF_TOOL_NAME, $ARF_TOOL_PARAMS (pre/post_tool_exec)
#    $ARF_MODEL_NAME (pre/post_model_call)

hooks:
  - name: log_start
    type: session_start
      # session_start | pre_tool_exec | post_tool_exec
      # pre_model_call | post_model_call | session_end
    run:
      - ./hooks/log_start.sh

  - name: load_context
    type: session_start
    run:
      - python ./hooks/load_context.py --session-id $ARF_SESSION_ID

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
description: 读取工作区内的文件内容并返回

parameters:
  # JSON Schema 参数定义
  type: object
  properties:
    path:
      type: string
      description: 文件路径（相对于工作区）
  required: [path]

provider: static_yaml
  # 工具来源 | Tool source
  #   static_yaml: 本地 YAML + function.py（默认）
  #   mcp: 通过 MCP 协议从远程服务器加载
  # MCP 模式下 parameters 由远程服务器提供，本地无需定义
  # In MCP mode, parameters are provided by the remote server

execution:
  sandbox: inherit
    # inherit: 继承 agent sandbox 配置
    # full: 无额外限制
    # read_only: 只读模式
  timeout: 30s

activation:
  # 激活策略 | Activation strategy
  # 控制工具何时出现在模型的 tool_definitions 中（会占用上下文 token）
  # Controls when the tool appears in model's tool_definitions (costs context tokens)
  mode: kernel
    # ─── kernel ───
    # 始终激活。每次 API 调用的 tool_definitions 都包含此工具。
    # 适用: 高频核心工具（文件读写、搜索），Agent 在几乎每轮对话都可能用到
    # Always active. Included in every API call's tool_definitions.
    # Use for: high-frequency core tools (file I/O, search) needed in most turns.
    #
    # ─── discoverable ───
    # 按需激活。默认不发 tool_definition，Agent 通过 resource_loader 按需加载。
    # 适用: 使用频率中等的工具，减少每轮调用的上下文开销
    # On-demand. Tool definition not sent by default; Agent loads via
    # resource_loader when needed. Use for: medium-frequency tools to
    # reduce per-turn context overhead.
    #
    # ─── passive ───
    # 被动可见。不自动激活，仅手动引用。适用: 实验性工具、危险操作
    # Passive. Not auto-activated; manual reference only.
    # Use for: experimental tools, dangerous operations
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
  # 激活策略 | Activation strategy
  # 控制 skill 何时被注入到模型的上下文中的可见范围
  # Controls when the skill becomes visible in the model's context
  mode: discoverable
    # ─── kernel ───
    # 始终激活。每次 API 调用的 system_prompt 都包含此 skill 的 prompt。
    # 适用: 核心能力，Agent 在所有任务中都需要知道它
    # Always active. Skill's prompt is included in every API call's
    # system_prompt. Use for: core capabilities the agent always needs.
    #
    # ─── discoverable ───
    # 按需激活。Agent 在 inventory 中看到 skill 名称和简介，需要时通过
    # resource_loader 工具加载完整 prompt。推荐大多数 skill 使用此模式。
    # 好处: 不占用上下文窗口直到实际需要
    # On-demand. Agent sees the skill name + summary in inventory; loads
    # full prompt via resource_loader tool when needed. Recommended for
    # most skills. Benefit: no context-window cost until actually used.
    #
    # ─── passive ───
    # 被动可见。不在 inventory 中列出，仅当 Agent 通过其他途径知晓后
    # 手动引用时才加载。适用: 实验性 skill、仅特定场景触发的 skill
    # Passive. Not listed in inventory; only loaded when the agent
    # explicitly references it by name. Use for: experimental skills,
    # skills triggered only in specific scenarios
```

## 新增域的设计

以下逐域补充 Protocol 定义和默认实现说明。

### observability

**解决的致命问题**: 框架内部是黑盒，Agent 做错决策时只能靠 grep 日志猜当时 router 为什么选了 quick 而不是 deep、compaction 是否触发、hook 在哪个节点超时。

**OS 类比**: Linux `perf`/`strace`/`dtrace`，在每个 syscall、上下文切换、缺页异常处有静态探针，无需用户手动加 log。

```python
# arf/observability/protocol.py

class Tracer(Protocol):
    """框架内部埋点接口，由 engine 在各关键节点调用"""
    def start_span(self, name: str, attributes: dict) -> SpanContext: ...
    def end_span(self, context: SpanContext, status: SpanStatus): ...
    def set_attribute(self, context: SpanContext, key: str, value): ...

class SpanContext:
    trace_id: str
    span_id: str

class SpanStatus:
    code: Literal["ok", "error"]
    message: str = ""
```

**默认实现**: `OtelTracer` — 创建 OTLP Span，携带 `session_id`、`agent_name`、`model_name`、`turn_count` 等标准属性。用户通过 `OTEL_EXPORTER=console|otlp|none` 环境变量选择导出器。

**在 engine 中的埋点位置**: router 决策前后、model_call 前后、compaction 触发时、hook 执行时、tool 调用前后。

**限制**: 不做日志存储/可视化（那是 Jaeger/Grafana 的事）。不做自定义日志（那是 hooks 的事）。

---

### guardrails

**解决的致命问题**: 沙箱只隔离文件系统，没有语义安全层。输出可能泄露 API key，用户可能用 "ignore previous rules" 绕过 critical_rules。

**OS 类比**: 防火墙 + 杀毒软件。`seccomp` 管系统调用但不管 SQL 注入——需要 WAF。Guardrails 是 Agent 的 WAF。

```python
# arf/guardrails/protocol.py

class InputGuardrail(Protocol):
    """用户消息进入 engine 前执行"""
    async def check(self, message: str, context: dict) -> GuardResult: ...

class OutputGuardrail(Protocol):
    """模型输出离开 engine 前执行"""
    async def check(self, message: str, context: dict) -> GuardResult: ...

class ToolGuardrail(Protocol):
    """工具参数在调用前执行"""
    async def check(self, tool_name: str, params: dict) -> ToolResult: ...

@dataclass
class GuardResult:
    allowed: bool
    reason: str = ""
    modified_message: str | None = None  # 修改后的内容（清洗用途）
```

**默认实现**:
- `NoneInputGuard` — 透传所有输入
- `RegexOutputGuard` — 正则清洗 API key、手机号等 PII
- `PathToolGuard` — 检测路径穿越 (`../`、绝对路径)

**限制**: 不做自定义分类器训练，不实现 LLM 越狱检测（那是高级插件的事）。guardrails 与 sandbox 正交——sandbox 管 OS 层隔离，guardrails 管应用层语义安全。

---

### memory 重构：MemoryStore + MemoryRetriever

**解决的致命问题**: 原来的 MemoryStore 只管"往哪里存"，没管"取什么注入到 prompt"。三个月前的信息存了但没人去检索。

**OS 类比**: 文件系统负责磁盘存储，搜索引擎负责按需检索。分两层才能解决记忆的"使用"问题。

```python
# arf/memory/protocol.py

class MemoryStore(Protocol):
    """持久化存储——只管"存哪里" | Persistence — only handles "where to store" """
    async def save(self, entry: MemoryEntry) -> None: ...
    async def load(self, session_id: str) -> list[MemoryEntry]: ...
    async def delete(self, entry_id: str) -> None: ...

class MemoryRetriever(Protocol):
    """检索策略——管"取什么注入到 {{MEMORY}}" | Retrieval — "what to inject" """
    async def retrieve(
        self,
        store: MemoryStore,
        query_context: str,      # 当前对话上下文，用于语义匹配
        session_id: str,
        max_tokens: int,         # 分配给记忆的 token 预算
        top_k: int = 5,
    ) -> list[MemoryEntry]: ...
```

**默认实现**:
- `FileStore` — JSON 文件持久化
- `RecentFirstRetriever` — 最近 N 条，按时间倒序

**限制**: embedding 语义检索不放入默认实现（依赖外部向量库），但接口兼容。

---

### human_loop（替代 interrupts）

**解决的致命问题**: interrupts 只定义了"暂停信号"，缺少三个生产级硬骨头：谁来定义审批规则？超时后怎么处理？审批通过什么通道送达人类？

**OS 类比**: 硬件中断 + 审批工作流。中断让 CPU 停下，但**谁响应中断、响应什么、超时怎么处理**，是中断控制器和调度器的职责。

```python
# arf/human_loop/protocol.py

class ApprovalPoint(Protocol):
    """定义何时需要暂停 | When to pause for human approval"""
    def should_pause(self, context: TurnContext) -> bool: ...
    def approval_form(self, context: TurnContext) -> ApprovalRequest: ...

class ApprovalChannel(Protocol):
    """审批的通信通道 | Communication channel for approval"""
    async def send(self, request: ApprovalRequest) -> str: ...  # → approval_id
    async def wait(self, approval_id: str, timeout: int) -> ApprovalResponse: ...

@dataclass
class ApprovalRequest:
    agent_name: str
    session_id: str
    turn: int
    tool_name: str
    params: dict
    reason: str              # 为什么需要审批 | Why approval is needed

@dataclass
class ApprovalResponse:
    action: Literal["approve", "reject", "modify"]
    modified_params: dict | None = None
    comment: str = ""
```

**默认实现**:
- `AlwaysAutoApprove` — 所有操作放行（默认，HITL 关闭）
- `ToolNameAllowlist` — 白名单工具触发审批
- `ConsoleChannel` — 终端交互式审批

**限制**: 不实现 WebSocket 服务端（属于 app 层），不实现邮件/短信通知。

---

### streaming

**解决的致命问题**: 无实时事件推送，前端只能等整个 turn 结束才能渲染。用户盯白屏 30 秒不知道 Agent 在思考还是发呆。

**OS 类比**: Unix 管道 (`pipe`)。`ls | grep foo` 一个字符接一个字符流过去，不等 `ls` 全执行完。

```python
# arf/streaming/protocol.py

@dataclass
class AgentEvent:
    type: Literal["thinking", "tool_call_start", "tool_call_result",
                  "model_output_delta", "compaction", "approval_required", "error"]
    data: dict
    timestamp: float

class EventStream(Protocol):
    def emit(self, event: AgentEvent) -> None: ...
    async def subscribe(self) -> AsyncIterator[AgentEvent]: ...
```

**默认实现**: `SseEventStream` — 通过 SSE 推送标准事件流。

**与 observability 的区别**: streaming 面向**用户体验**（用户看到的实时进度），observability 面向**开发者排障**（结构化 trace）。两者可以共享底层 transport，但职责不同。

**限制**: 不捆绑特定前端协议，不实现 WebSocket server。

---

### communication

**解决的致命问题**: 多 Agent 之间只能通过文本互相喂，没有结构化的任务委托和消息路由。应用层只能 if-else 硬捏 Agent 间通信。

**OS 类比**: IPC — 管道、消息队列、共享内存、信号量。Linux D-Bus。

```python
# arf/communication/protocol.py

@dataclass
class AgentMessage:
    sender: str
    receiver: str | None         # None = broadcast
    type: Literal["task_delegate", "info", "query", "handoff"]
    payload: dict
    reply_to: str | None
    correlation_id: str          # 关联会话 | Correlation for tracing

class AgentBus(Protocol):
    async def send(self, message: AgentMessage) -> None: ...
    async def receive(self, agent_name: str) -> AsyncIterator[AgentMessage]: ...

class TaskDelegator(Protocol):
    """管理任务委托的生命周期 | Manages task delegation lifecycle"""
    async def delegate(self, task: str, from_agent: str, to_agent: str) -> TaskHandle: ...
    async def get_result(self, handle: TaskHandle, timeout: int) -> dict: ...
```

**默认实现**: `InMemoryBus` — 单进程内存队列（asyncio.Queue）。单 Agent 场景下此域不激活。

**限制**: 不实现分布式消息队列。接口兼容 Redis/MQ。

---

### engine 扩展：LoopStrategy

**解决的致命问题**: engine 只硬编码 ReAct 循环，但翻译任务不需要工具调用、复杂规划需要 Plan-Execute 模式。

**OS 类比**: CPU 微架构。Intel vs ARM 的流水线级数不同，但上层指令集兼容。LoopStrategy = Agent 的微架构。

```python
# arf/engine/loop_strategies/protocol.py

class LoopStrategy(Protocol):
    """定义 Agent 的执行循环模式 | Execution loop pattern"""
    def build_graph(self, nodes: dict, edges: list) -> StateGraph: ...
    def should_continue(self, state: AgentState) -> bool: ...
    def next_step(self, state: AgentState) -> str: ...
```

**默认实现**:
- `ReActStrategy` — 标准 think-act-observe
- `DirectStrategy` — 不调用工具，直接回复
- `PlanExecuteStrategy` (后续) — 先规划再执行

---

### resources 扩展：ToolProvider

**解决的致命问题**: 工具只能本地 YAML + function.py 定义，无法接入 MCP 远程工具生态。

**OS 类比**: 文件系统 + 远程挂载。本地 YAML = 硬盘，MCP = NFS/Samba 远程共享。

```python
# arf/resources/protocol.py

class ToolProvider(Protocol):
    """工具来源抽象 — 可以是本地 YAML 或远程 MCP 服务器"""
    async def list_tools(self) -> list[ToolConfig]: ...
    async def get_tool(self, name: str) -> ToolConfig: ...
    async def execute(self, name: str, params: dict) -> ToolResult: ...

class StaticYamlToolProvider:
    """默认: 从 tools/*.yaml + function.py 加载"""

class McpToolProvider:
    """通过 MCP 协议连接远程工具服务器"""
```

`ResourceRegistry` 聚合 `list[ToolProvider]`，不再直接读文件。

---

## Pydantic 模型（代码创建路径）

### ModelConfig

```python
class ModelConfig(BaseModel):
    """单个模型的配置 | framework 仅管理路由/适配所需最小字段"""
    name: str                                      # 模型逻辑名
    api_type: Literal["openai", "anthropic", "custom"] = "openai"
    model: str                                     # provider 模型 ID
    api_base: str = "https://api.deepseek.com"
        # API 端点，框架根据 api_type 自动追加路径后缀
    api_key_env: str = "DEEPSEEK_API_KEY"
    kwargs: dict = {}                              # 透传给 API，框架不校验
```

### AgentConfig

```python
class AgentConfig(BaseModel):
    """Agent 完整配置"""
    name: str                             # 必填
    role: str                             # 必填: Agent 角色
    task: str                             # 必填: 任务场景
    description: str                      # 必填: 能力描述
    system_prompt: SystemPromptConfig     # 管道模板 + critical_rules
    models: list[ModelConfig]             # 必填

    # --- 可选域（不填则对应模块使用默认配置或禁用） ---
    loop_strategy: str = "react"          # react | plan_execute | direct
    router: RouterConfig | None = None
    compaction: CompactionConfig | None = None
    memory: MemoryConfig | None = None
    guardrails: GuardrailsConfig | None = None
    human_loop: HumanLoopConfig | None = None
    streaming: StreamingConfig | None = None
    sandbox: SandboxConfig | None = None
    hooks: list[HookDefinition] = []
    tools: list[ToolConfig] = []
    skills: list[SkillConfig] = []
    max_turns: int = 50
```

## 双路径创建

```
YAML 文件路径                          代码路径
─────────────                         ─────────
agent.yaml ─┐                        create_agent(name=..., ...)
models.yaml ─┤                              │
hooks.yaml  ─┤                              ▼
tools/*/    ─┼→ AgentConfig(**data)  ←── AgentConfig(**kwargs)
skills/*/   ─┘        │                     │
                       └──────┬──────────────┘
                              ▼
                    BaseAgent._from_config(cfg)
                              │
                    _build_router / _build_compactor /
                    _build_memory / _build_sandbox /
                    _build_guardrails / _build_human_loop /
                    _build_streaming / _build_hooks /
                    _build_registry
                              │
                              ▼
                    GraphEngine(...)  ← DI 注入
                      + Tracer (observability)
                      + EventStream (streaming)
                      + Guardrails (input/output/tool)
                      + HumanLoopManager
```

用户传入可选参数 → 按用户配置写入文件；未传入 → 从框架默认模板 copy 到 `agent_name/` 下。

## 迁移步骤

1. **骨架 + Protocol 定义** — 创建 `arf/` 目录及全部子模块，各域写 `protocol.py`，更新 `pyproject.toml`。新增域（observability, guardrails, human_loop, streaming, communication）只做 Protocol，不做实现。
2. **引擎层迁移** — 搬运 `engine/`，移除 server import、硬编码模型名/工具名、中文、dispatcher。将图结构抽象为 `LoopStrategy`。
3. **resources + hooks + agent 迁移** — 搬运并清理，抽出应用层实现。resources 增加 `ToolProvider` 抽象层。
4. **补齐问题域默认实现** — compaction/sliding_window, memory/file_store + recent_first, routing/two_tier, sandbox/path_sandbox, guardrails/regex+path, human_loop/console, streaming/sse, communication/in_memory, concurrency/sequential, observability/otel。
5. **前端隔离 + 验证** — 前端移入 `app/web/`，应用层占位 `app/arf_app/`，确认框架零应用依赖。

每步可独立提交，不破坏上一步功能。备份分支 `arfwithapp` 保留当前完整代码。
