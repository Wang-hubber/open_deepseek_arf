# 配置平面参考

本文档面向 App 开发者，完整描述 ARF 框架的配置体系——从 `agent.yaml` 到每个子系统的传递链条、默认值与降级路线。

---

## 配置架构

```
models/*.yaml  ──────────┐
  (文件系统是真源)        │  merge: FS 为基础, agent.yaml 覆盖叠加
                          ├──→ AgentConfig.from_yaml() ──→ BaseAgent(config, app_context)
agent.yaml ──────────────┘                                  │
  advanced: ...                                               │ destructure to subsystems
  tools: [...]                                                ↓
  skills: [...]                                          GraphEngine
  hooks: [...]                                            + Memory + Routing + Compaction
  agents: [...]                                           + Guardrails + Sandbox + Hooks
                                                          + Protection + ErrorPolicy
                                                          + Observability + Promotion
```

**核心原则**: 文件系统是资源真源（tools/skills/models），`agent.yaml` 仅做覆盖和装配。框架提供 mechanism，App 通过配置决定 policy。

---

## 入口：AgentConfig

`AgentConfig` 是用户态配置的根模型，定义在 `arf/agent/config.py:71`。通过 `AgentConfig.from_yaml(path)` 加载。

### 顶级字段

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `schema_version` | `str` | `"1.0"` (frozen) | 配置文件 schema 版本 |
| `name` | `str` | **required** | Agent 标识名，注入到 `{{AGENT_NAME}}` |
| `role` | `str` | `""` | Agent 角色描述，注入到 `{{AGENT_ROLE}}` |
| `task` | `str` | `""` | Agent 任务描述，注入到 `{{AGENT_TASK}}` |
| `description` | `str` | `""` | 能力描述，模板为空时自动插入 |
| `system_prompt` | `SystemPromptConfig` | `SystemPromptConfig()` | 系统提示词模板（见 §SystemPrompt） |
| `models` | `list[ModelConfig]` | `[]` | 模型定义——文件系统是主源，这里仅覆盖 |
| `tools` | `list[ToolConfig]` | `[]` | 工具定义——文件系统是主源，这里仅覆盖 |
| `skills` | `list[SkillConfig]` | `[]` | 技能定义——文件系统是主源，这里仅覆盖 |
| `plugins` | `list[str]` | `[]` | 从 `arf/plugins/` 激活的插件名 |
| `hooks` | `list[HookDefinition]` | `[]` | Hook 定义 |
| `advanced` | `AdvancedConfig` | `None` | 全部内部机制配置，省略时自动推导 |
| `agents` | `list[AgentConfig]` | `None` | 子 Agent 定义 |
| `handover` | `HandoverConfig` | `None` | Agent 间交接规则 |
| `supervisor` | `SupervisorConfig` | `None` | （保留字段，尚未启用） |

### 配置加载过程

1. `AgentConfig.from_yaml(path)` 解析 `agent.yaml`
2. 扫描 `models/` 目录，加载所有 `.yaml` 文件为 `ModelConfig`
3. 合并：文件系统模型 + agent.yaml 中同 `type` 的覆盖字段（`exclude_none=True` 合并）
4. agent.yaml 中独有的模型被追加
5. 校验 `schema_version`（仅接受 `"1.0"` 和 `"0.0"`）

---

## AdvancedConfig：框架全部机制

`AdvancedConfig` 定义在 `arf/agent/config.py:26`。`agent.yaml` 中对应 `advanced:` 块。

当 `agent.yaml` 中未提供 `advanced:` 时，框架调用 `AdvancedConfig.auto_derive(tools_count, models_count)` 自动推导：
- `tools_count > 20` → 启用 `tool_retrieval`
- `models_count > 1` → 启用 `routing.strategy = "two_tier"`

### 全部字段一览

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `loop_strategy` | `"react"\|"direct"\|"plan_execute"` | `"react"` | 主循环策略 |
| `max_turns` | `int` | `50` | 每轮对话最大循环次数（断路保护） |
| `max_undo_depth` | `int` | `3` | 最大撤销步数 |
| `system_model` | `str\|None` | `None` | 后台系统模型名（记忆/路由/压缩共用），未设置时降级到 `models[0].type` |
| `routing` | `RoutingConfig\|None` | `None` | 模型路由（见 §Routing） |
| `compaction` | `CompactionConfig\|None` | `None` | 上下文压缩（见 §Compaction） |
| `memory` | `MemoryConfig\|None` | `None` | 内存管理（见 §Memory） |
| `guardrails` | `GuardrailsConfig\|None` | `None` | 安全护栏（见 §Guardrails） |
| `errors` | `ErrorConfig\|None` | `None` | 错误处理（见 §Errors） |
| `human_loop` | `HumanLoopConfig\|None` | `None` | 人机审批（见 §HumanLoop） |
| `tool_retrieval` | `ToolRetrievalConfig\|None` | `None` | 工具检索（工具 > 20 时自动启用） |
| `concurrency` | `ConcurrencyConfig\|None` | `None` | 并发策略（见 §Concurrency） |
| `sandbox` | `SandboxConfig\|None` | `None` | 路径沙箱（见 §Sandbox） |
| `reload` | `ReloadConfig\|None` | `None` | 热加载（见 §Reload） |
| `protection` | `ProtectionConfig\|None` | `None` | API 保护（见 §Protection） |
| `recovery` | `RecoveryConfig` | `RecoveryConfig()` | 错误恢复预算（始终存在） |
| `promotion` | `PromotionConfig\|None` | `None` | 权限提升策略（见 §Promotion） |
| `observability` | `ObservabilityConfig\|None` | `None` | 可观测性（见 §Observability） |

---

## 配置消费者链

每个子系统的配置降级链路：

```
1. override_protocols["subsystem"]     ← DI 注入，最高优先
2. agent.yaml advanced.subsystem       ← App 显式配置
3. AdvancedConfig.default().subsystem  ← 框架 Pydantic 默认
4. 组件内部硬编码默认值                 ← 最终兜底
```

---

## ModelConfig：模型配置

**定义**: `arf/core/config_base.py:13`

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `type` | `"quick" \| "deep"` | **required** | 模型类型标识 |
| `api_type` | `"openai" \| "anthropic" \| "custom"` | `"openai"` | API 协议 |
| `model` | `str` | **required** | API 模型名 |
| `api_base` | `str` | `"https://api.deepseek.com"` | API 端点 |
| `api_key_env` | `str` | `"DEEPSEEK_API_KEY"` | API key 环境变量名 |
| `context_window` | `int` | `131072` | 上下文窗口 token 数 |
| `max_token` | `int \| None` | `None` | 每次调用输出 token 上限（映射为 API `max_tokens`） |
| `kwargs` | `dict` | `{}` | 传递给 API 的额外参数 |
| `activation` | `"kernel" \| "discoverable"` | `"discoverable"` | 内核模式 / 按需发现 |

### 传递链

```
ModelConfig → ModelAdapter({base_url, api_key, model_name, max_tokens, ...kwargs})
           → _call_model / _stream_model (注入 GraphEngine)
           → Protection 包裹 (rate limit + circuit breaker)
           → API 调用
```

### 系统模型

`system_model` 指定的模型会额外强制覆盖：`temperature=0.3`, `thinking_enabled=False`, `max_tokens=1024`。用于记忆提取、路由分类、压缩摘要等后台任务。

**推荐配置**:

```yaml
# models/deep.yaml
type: deep
api_type: openai
model: deepseek-v4-pro
api_base: https://api.deepseek.com
api_key_env: DEEPSEEK_API_KEY
context_window: 131072
max_token: 10240
kwargs:
  reasoning_effort: max
activation: kernel

# models/quick.yaml
type: quick
api_type: openai
model: deepseek-v4-flash
api_base: https://api.deepseek.com
api_key_env: DEEPSEEK_API_KEY
context_window: 131072
max_token: 4096
kwargs:
  reasoning_effort: high
  temperature: 0.7
activation: kernel
```

---

## SystemPrompt：提示词模板

**定义**: `arf/agent/config.py:61`

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `template` | `str` | `""` | Jinja2 风格模板，支持 {{PLACEHOLDER}} |
| `critical_rules` | `str` | `""` | 关键规则，注入到 `{{CRITICAL_RULES}}` |
| `pipeline` | `list[PipelineSection]` | `[]` | Pipeline 模式：按 priority 排序注入 section |

### 支持占位符

| 占位符 | 填充时机 | 来源 |
|--------|---------|------|
| `{{AGENT_NAME}}` | 初始化 | `config.name` |
| `{{AGENT_ROLE}}` | 初始化 | `config.role` |
| `{{AGENT_TASK}}` | 初始化 | `config.task` |
| `{{CRITICAL_RULES}}` | 初始化 | `config.system_prompt.critical_rules` |
| `{{INVENTORY}}` | 初始化 | 内核工具 + 可发现工具 + 技能列表（自动生成） |
| `{{MEMORY}}` | 初始化 | resident memory 文件内容 |
| `{{WORKSPACE}}` | 运行时 | 引擎填充 |
| `{{LANGUAGE}}` | 运行时 | 引擎填充 |

### Pipeline 模式 vs Legacy 模式

**Pipeline 模式** (`pipeline` 不为空): Section 按 `priority` 排序注入，精确控制 prompt 结构。

```yaml
system_prompt:
  template: |
    You are {{AGENT_NAME}}.
    {{RULES}}
    {{TOOLS}}
  pipeline:
    - priority: 0
      section: rules
    - priority: 1
      section: tools
```

**Legacy 模式**: 简单占位符替换，向后兼容。

---

## Routing：模型路由

**定义**: `arf/core/config_base.py:57`

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `strategy` | `"two_tier" \| "static"` | `"two_tier"` | 路由策略 |
| `default` | `str` | `""` | 默认 model type |
| `classify` | `dict[str, str]` | `{}` | 复杂度级别 → model type 映射 |
| `background` | `str \| None` | `None` | 后台任务专用 model type |
| `fallback` | `dict[str, str]` | `{}` | 错误降级映射（如 `deep→quick`） |

### 传递链

```
RoutingConfig → TwoTierRouter(config, models, classifier_call)
             → engine.model_router
             → GraphEngine 每轮调用 router.route(query)
               → 关键词启发式先分类 (E2E Bug 3.4)
               → 模糊时 LLM classifier 兜底
               → classify 表映射到具体 model type
```

### 路由激活条件

- `advanced.routing` 不为 `None`
- `len(config.models) > 1`（有多模型可选择）

### 推荐配置

```yaml
advanced:
  routing:
    strategy: two_tier
    default: quick
    classify:
      medium: quick
      complex: deep
    fallback:
      deep: quick
```

---

## Compaction：上下文压缩

**定义**: `arf/core/config_base.py:65`

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `strategy` | `"sliding_window" \| "none"` | `"sliding_window"` | 压缩策略 |
| `threshold` | `float` (0.0~1.0) | `0.75` | 触发阈值：token 使用量 / context_window |

### 传递链

```
CompactionConfig → SlidingWindowCompactor(threshold, summarizer=LLM)
                → engine.compaction
                → 每次 model call 后检查 last_token_usage > threshold * window_size
                → 触发时：保留最近 8 条消息，LLM 摘要旧消息
```

### 工作流程

1. 每次模型调用后，引擎记录 `state["last_token_usage"]`
2. 下轮调用前，`should_compact()` 比对阈值
3. 超过阈值 → `compact()`：保留最新 8 条 UA 消息 + 关联 tool 消息，旧消息送 LLM 摘要
4. 摘要写入 `state["context_summary"]`
5. 2 轮 cooldown 避免误触发

---

## Memory：内存管理

**定义**: `arf/core/config_base.py:70`

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `store` | `"file" \| "sqlite" \| "none"` | `"file"` | 存储后端 |
| `workspace` | `str` | `"./data/memory"` | 内存目录（被 AppContext 覆盖） |
| `retriever` | `"recent_first" \| "llm"` | `"llm"` | 检索策略 |
| `writer` | `"rule" \| "llm"` | `"llm"` | 写入策略 |
| `max_tokens` | `int` | `2000` | 注入 prompt 的内存 token 预算 |
| `top_k` | `int` | `5` | 检索条数 |
| `resident_file` | `str` | `"memory.md"` | Resident memory 文件名 |
| `max_size_kb` | `int` | `300` | Resident memory 文件大小上限 |

> **注意**: `retriever` 和 `writer` 的 LLM 实现在 `arf/plugins/memory/` 插件中。框架层只提供 `FileMemoryStore` 和 resident memory 加载。

### 传递链

```
MemoryConfig.max_tokens/max_size_kb/resident_file → _load_resident_memory()
MemoryConfig.max_tokens/max_size_kb               → engine.memory_max_tokens
MemoryConfig.top_k                                 → engine.memory_top_k
```

---

## Guardrails：安全护栏

**定义**: `arf/core/config_base.py:95`

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `input` | `"none" \| "regex_block" \| "llm_classifier"` | `"none"` | 输入检查（仅 `"none"` 已实现） |
| `output` | `"none" \| "regex_clean" \| "llm_classifier"` | `"regex_clean"` | 输出清理 |
| `tool_params` | `"none" \| "path_check" \| "command_check"` | `"path_check"` | 工具参数检查 |
| `permissions` | `PermissionsConfig` | `PermissionsConfig()` | 权限列表 |
| `output_patterns` | `list[RegexPatternConfig]` | `[]` | 自定义输出正则（空 = 内置默认） |

### PermissionsConfig

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `allow` | `list[str]` | `[]` | 白名单工具名 |
| `deny` | `list[str]` | `[]` | 黑名单工具名 |
| `ask` | `list[str]` | `[]` | 需审批工具名 |
| `deny_patterns` | `list[str]` | `[]` | 黑名单正则模式 |

### 权限判定流程

```
1. deny 匹配 → 拒绝
2. ask 匹配 → 发起审批
3. allow 匹配 → 放行
4. deny_patterns 匹配 → 拒绝
5. 都不匹配 → 拒绝（默认安全）
```

---

## Sandbox：路径沙箱

**定义**: `arf/core/config_base.py:130`

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `allow_escape` | `bool` | `false` | 设为 `true` 可绕过全部检查（仅调试用） |
| `writable_dirs` | `list[str]` | `[]` | 允许写入的额外目录 |
| `checks.path_traversal` | `bool` | `true` | 拦截 `..` 路径穿越 |
| `checks.absolute_path` | `bool` | `true` | 拦截绝对路径 |
| `checks.workspace_containment` | `bool` | `true` | 拦截工作区外路径 |
| `checks.symlink` | `bool` | `true` | 拦截符号链接穿越 |

### 检查执行顺序

```
1. path_traversal (.. 检查)
2. absolute_path (/ 开头)
3. workspace_containment (PathSandbox 验证)
4. symlink (符号链接检测)
```

### 推荐配置

```yaml
advanced:
  sandbox:
    allow_escape: false
    writable_dirs: ["/tmp/jupyter"]
    checks:
      path_traversal: true
      absolute_path: true
      workspace_containment: true
      symlink: true
```

---

## HumanLoop：人机审批

**定义**: `arf/core/config_base.py:115`

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `approval_points` | `"always_auto" \| "tool_name_allowlist"` | `"always_auto"` | 审批模式 |
| `allowlist` | `list[str]` | `[]` | 需要审批的工具名列表 |
| `channel` | `"console" \| "websocket" \| "callback"` | `"console"` | 审批通道 |
| `timeout` | `str` | `"3600s"` | 审批超时 |

### 启用方式

```yaml
advanced:
  human_loop:
    approval_points: tool_name_allowlist
    allowlist:
      - file_writer
      - file_deleter
    channel: websocket
    timeout: 60s
```

设置为 `always_auto`（默认）时，无需审批。

---

## Protection：API 保护

**定义**: `arf/core/config_base.py:193`

### 顶层

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | `bool` | `true` | 是否启用保护层 |
| `rate_limit` | `ProtectionRateLimitConfig` | `ProtectionRateLimitConfig()` | Token Bucket 限流 |
| `circuit_breaker` | `ProtectionCircuitBreakerConfig` | `ProtectionCircuitBreakerConfig()` | 熔断器 |

### 限流配置

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `requests_per_second` | `float` | `5.0` | 每秒请求数 |
| `max_burst` | `int` | `10` | 突发容量 |

### 熔断器配置

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `failure_threshold` | `int` | `3` | 连续失败 N 次后熔断 |
| `base_cooldown` | `str` | `"10s"` | 基础冷却时间 |
| `cooldown_multiplier` | `float` | `2.0` | 冷却倍增因子 |
| `max_cooldown` | `str` | `"300s"` | 最大冷却时间 |
| `half_open_max_requests` | `int` | `1` | 半开状态允许的探测请求数 |

### 传递链

```
ProtectionConfig → ModelCallProtector(event_bus, model_map, rl_cfg, cb_cfg)
                → 包裹 _call_model / _stream_model
                → TokenBucket (per api_base) + CircuitBreaker (per model_name)
                → rate_limited → circuit_opened → half_open → closed 状态机
```

---

## Errors：错误处理

**定义**: `arf/core/config_base.py:108`

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `tool_retry` | `int` | `2` | 工具调用最大重试次数 |
| `tool_backoff` | `"exponential" \| "linear" \| "none"` | `"exponential"` | 重试退避策略 |
| `model_5xx_action` | `"fallback" \| "retry" \| "abort"` | `"fallback"` | 模型 5xx 错误响应 |
| `guardrail_block_action` | `"abort" \| "ask_user"` | `"abort"` | 护栏阻断响应 |

### 传递链

```
ErrorConfig → DefaultErrorPolicy(tool_retry, tool_backoff, model_5xx_action, guardrail_block_action)
           → engine.error_policy
           → on_tool_error(): retry with backoff
           → on_model_error(): fallback via router or abort
           → on_guardrail_block(): abort or ask_user
```

---

## Recovery：错误恢复

**定义**: `arf/agent/config.py:16`

| 字段 | 类型 | 默认值 (范围) | 说明 |
|------|------|--------|------|
| `max_continuation` | `int` | `3` (0..10) | Continue 恢复最大次数（max_tokens 截断） |
| `max_compaction` | `int` | `3` (0..10) | Compact 恢复最大次数（上下文溢出） |
| `max_transport_retry` | `int` | `3` (0..10) | Transport 重试最大次数（网络瞬时故障） |
| `backoff_base` | `float` | `1.0` (0.1..60.0) | 退避基础延迟（秒） |
| `backoff_max` | `float` | `30.0` (1.0..300.0) | 退避最大延迟（秒） |

> 此配置始终存在（`default_factory=RecoveryConfig`），无需在 agent.yaml 中显式声明。

### 恢复流程

```
模型调用出错 → _choose_recovery(stop_reason, error_text)
           → continue / compact / backoff / fail
           → _apply_recovery() 检查预算 → 执行恢复
           → 预算耗尽 → RuntimeError
```

---

## Concurrency：并发策略

**定义**: `arf/core/config_base.py:141`

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `strategy` | `"parallel" \| "sequential"` | `"parallel"` | 工具执行策略 |
| `max_concurrency` | `int` (≥1) | `5` | 最大并发数 |

### 传递链

```
ConcurrencyConfig → ConcurrentToolExecutor(resource_resolver, strategy, max_concurrency)
                 → engine.tool_executor
```

---

## Reload：热加载

**定义**: `arf/core/config_base.py:146`

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `watch` | `bool` | `true` | 是否监控文件变更 |
| `poll_interval` | `float` | `5.0` | 轮询间隔（秒） |
| `signals` | `list[str]` | `["SIGHUP"]` | 触发重载的信号 |

### 传递链

```
ReloadConfig → FileWatcher(poll_interval)
            → 监控 tools_dir / skills_dir / models_dir
            → 文件变更 → resource_resolver.reload_dynamic()
```

---

## Promotion：权限提升

**定义**: `arf/core/config_base.py:208`

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `strategy` | `"auto" \| "ask" \| "plan"` | `"ask"` | 提升策略 |
| `allow` | `list[str]` | `[]` | 自动放行的工具 |
| `deny` | `list[str]` | `[]` | 禁止的工具 |
| `ask` | `list[str]` | `[]` | 需用户确认的工具 |
| `deny_patterns` | `list[str]` | `[]` | 禁止的正则模式 |

### 传递链

```
PromotionConfig → Promotion(strategy, deny, ask, allow, deny_patterns)
               → engine._promotion
               → evaluate(executable) → allow / deny / ask
```

---

## Observability：可观测性

**定义**: `arf/core/config_base.py:200`

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `trace_dir` | `str` | `"./memory/traces"` | Trace 文件目录（被 AppContext 覆盖） |
| `usage_dir` | `str` | `"./memory"` | 用量统计文件目录 |
| `trace_enabled` | `bool` | `true` | 是否启用 trace |
| `otel_exporter` | `"none" \| "console" \| "otlp"` | `"none"` | OpenTelemetry 导出器 |

### 传递链

```
ObservabilityConfig.trace_dir → FileTraceStore(event_bus, dir=trace_dir)
ObservabilityConfig.usage_dir → UsageTracker(event_bus, dir=usage_dir)

两者均由 BaseAgent 自动创建，不需要 App 层手动管理。
```

---

## Hooks：生命周期钩子

**定义**: `arf/core/config_base.py:45`

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `name` | `str` | **required** | Hook 名称 |
| `type` | `"session_start" \| "round_start" \| ... \| "session_end"` | **required** | 触发事件 |
| `run` | `list[str]` | **required** | Shell 命令列表 |
| `env` | `dict[str, str]` | `{}` | 环境变量 |
| `timeout` | `str` | `"30s"` | 超时时间 |

### 支持的事件类型

`session_start`, `round_start`, `round_end`, `pre_tool_exec`, `post_tool_exec`, `pre_model_call`, `post_model_call`, `session_end`

### Shell 环境变量

Hook 子进程注入以下环境变量：

| 变量 | 说明 |
|------|------|
| `ARF_SESSION_ID` | 当前会话 ID |
| `ARF_ROUND` | 当前交互轮次 |
| `ARF_MEMORY_DIR` | 内存目录 |
| `ARF_WORKSPACE` | 工作区目录 |
| `ARF_TRACE_DIR` | Trace 目录 |
| `ARF_SYSTEM_MODEL` | 系统模型名 |
| `ARF_RUNTIME` | JSON 格式的完整 runtime 信息 |

---

## 子 Agent

子 Agent 定义在 `agent.yaml` 的 `agents:` 数组中。每个子 Agent 拥有自己的：

- `system_prompt`（模板 + 规则）
- `models`（独立模型列表，覆盖主 Agent 配置）
- `tools`（工具列表，独立权限）
- `skills`（技能列表）
- `advanced`（独立的高级配置，如 `max_turns`, `routing`）

### Handover：Agent 间交接

```yaml
handover:
  rules:
    - from_agent: main
      to_agent: sys_agent
      trigger: "创建或修改 resources 目录下的文件"
      context:
        raw_turns: 5      # 携带最近 5 轮对话 (-1 = 全部)
        task_summary: true # 生成任务摘要
```

交接流程：
1. 匹配 `from_agent` + `trigger`（LLM 语义匹配）
2. 保存当前 Agent 状态（`session_id/agent_name`）
3. 构建上下文（`raw_turns` 条消息 + task summary）
4. 切换到目标 Agent（恢复历史状态或新建）
5. 目标 Agent 执行任务
6. 返回 `handoff` 工具调用 → 切回原 Agent

---

## 配置最佳实践

### 1. 模型在文件系统中定义

```yaml
# ✅ 正确：models/deep.yaml
type: deep
model: deepseek-v4-pro
# ...

# agent.yaml 仅覆盖差异
models:
  - type: deep
    temperature: 0.3  # 仅覆盖温度
```

### 2. 充分利用默认值

```yaml
# ✅ 简洁：只写需要改的
advanced:
  max_turns: 100
  routing:
    default: quick
```

`Errors`, `Recovery`, `Concurrency`, `Reload`, `Protection` — 框架提供生产级默认值，一般无需配置。

### 3. system_model 选择

```yaml
# ✅ 明确指定：选 flash 模型做后台任务
advanced:
  system_model: quick
```

`system_model` 用于记忆提取、路由分类、压缩摘要——应选择廉价快速模型。未配置时降级到 `models[0].type`。

### 4. 安全护栏全开

```yaml
advanced:
  sandbox:
    checks:
      path_traversal: true
      absolute_path: true
      workspace_containment: true
      symlink: true
  guardrails:
    output: regex_clean        # 开启输出清理
    tool_params: path_check    # 开启路径检查
    permissions:
      allow: [read, grep, glob, file_writer]  # 最小权限原则
```

### 5. 权限策略

- `allow` 列出明确允许的工具（白名单优先）
- `deny_patterns` 用正则匹配危险 URI scheme（如 `file://`, `php://`）
- 不在 `allow` 中的工具默认拒绝

---

## 已知限制与演进方向

| 项目 | 状态 | 说明 |
|------|------|------|
| `SupervisorConfig` | 定义但未消费 | 多 Agent 协调使用 Handoff 机制，Supervisor 模式待实现 |
| `GuardrailsConfig.input` | 仅 `"none"` 实现 | `regex_block` 和 `llm_classifier` 待实现 |
| `HumanLoopConfig.channel` | `"websocket"` 和 `"callback"` 未实现 | 当前仅 `"console"` 可用 |
| `MemoryConfig.store` | 仅 `"file"` 实现 | `"sqlite"` 后端待实现 |
| `ObservabilityConfig.otel_exporter` | `"otlp"` 未实现 | OTLP 导出待实现 |
| `ToolRetrievalConfig` | 自动推导但检索逻辑未完整实现 | 当 tools > 20 时启用 |
| `LoopStrategy` | 仅 `"react"` 实现 | `"direct"` 和 `"plan_execute"` 待实现 |
