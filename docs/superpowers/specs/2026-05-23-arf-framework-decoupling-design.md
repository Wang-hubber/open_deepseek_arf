# ARF 框架解耦设计

## 目标

将 ARF 拆分为**框架层**（`arf/`）和**应用层**（`app/`）。框架层是一个配置驱动、文件系统原生的 Agent 框架，解决通用 Agent Harness 的 8 个核心问题域。应用层是该框架之上的 ARF 产品实现（双代理、工作区等）。

## 问题域（框架层职责）

| 域 | OS 类比 | 最小可行实现 | framework 接口 |
|---|---|---|---|
| **agent** | 进程 | YAML/Pydantic 配置驱动的 Agent 定义与生命周期 | `create_agent()` / `BaseAgent.from_dir()` |
| **engine** | CPU 流水线 | LangGraph 状态图，DI 注入所有可替换件 | `GraphEngine`（具体类，纯注入） |
| **compaction** | 虚拟内存 + 页交换 | 75% 阈值滑动窗口压缩 | `CompactionStrategy` Protocol |
| **memory** | 文件系统 | 文件记忆 store（session/long_term/archive） | `MemoryStore` Protocol |
| **routing** | 多级缓存 (L1/L2) | 二级分类器 (medium → quick, complex → deep) | `ModelRouter` Protocol |
| **hooks** | 系统调用 | 6 事件节点，subprocess + 退出码契约 | `HookRunner` + `HookDefinition` |
| **sandbox** | 进程隔离 | 路径沙箱，入逃逸防护 | `ToolSandbox` Protocol |
| **concurrency** | 乱序执行 + 多核 | 顺序执行（占位） | `TaskScheduler` Protocol |
| **interrupts** | 硬件中断 | 暂停/注入/恢复信号（占位） | `InterruptBus` Protocol |
| **resources** | 文件系统索引 | 目录 → YAML 资源加载，双源注册 | `ResourceRegistry` + `ToolConfig`/`SkillConfig` |

## 目录结构

```
open_deepseek_arf/
├── pyproject.toml
│
├── arf/                           # 框架 (pip install -e .)
│   ├── __init__.py                # create_agent, BaseAgent, AgentConfig
│   ├── agent/                     # agent.py, config.py, factory.py
│   ├── engine/                    # graph.py, nodes.py, state.py, router.py
│   ├── resources/                # registry.py, adapter.py, schemas.py
│   ├── hooks/                     # protocol.py, runner.py
│   ├── compaction/               # protocol.py, sliding_window.py
│   ├── memory/                    # protocol.py, file_store.py
│   ├── routing/                   # protocol.py, two_tier.py
│   ├── sandbox/                   # protocol.py, path_sandbox.py
│   ├── interrupts/               # protocol.py
│   └── concurrency/              # protocol.py, sequential.py
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

## 配置格式

### agent.yaml

```yaml
# Agent 身份与行为配置 | Agent identity & behavior configuration
name: my_agent
  # Agent 唯一标识符，用于日志、trace、会话路由
description: 执行日常编程任务的助手
  # Agent 能力简述，注入到 system_prompt
system_prompt: |
  你是一个编程助手。遵循最佳实践，优先使用工具获取信息。
  # 核心人格与行为指令，由 BaseAgent._prompt_pipeline 拼装
max_turns: 50
  # 单次会话最大工具调用轮次，防止死循环

# 上下文压缩策略 | Context compaction (省略=不压缩)
compaction:
  strategy: sliding_window
    # sliding_window | summarization | none
  threshold: 0.75
    # 上下文占用比例临界值 (0.0~1.0)

# 长程记忆存储 | Long-term memory (省略=无持久记忆)
memory:
  store: file
    # file | sqlite | none
  workspace: ./memory

# 工具执行沙箱 | Tool sandbox (省略=默认路径隔离)
sandbox:
  allow_escape: false
    # 是否允许访问工作区外路径
  writable_dirs: ["./output"]
    # 可写目录白名单
```

### models.yaml

```yaml
# 模型清单与路由策略 | Model inventory & routing
models:
  quick:
    name: quick
    provider: openai          # openai | anthropic | custom
    model: deepseek-v4-flash  # 模型 ID
    api_base: https://api.deepseek.com/v1
    api_key_env: DEEPSEEK_API_KEY
    kwargs:                   # 透传给 provider，框架不校验
      reasoning_effort: high
      max_tokens: 8192
      temperature: 1.0

  deep:
    name: deep
    provider: openai
    model: deepseek-v4-pro
    api_base: https://api.deepseek.com/v1
    api_key_env: DEEPSEEK_API_KEY
    kwargs:
      reasoning_effort: max
      max_tokens: 8192

  cheap:
    name: cheap
    provider: openai
    model: deepseek-v4-flash
    api_base: https://api.deepseek.com/v1
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

execution:
  sandbox: inherit
    # inherit: 继承 agent sandbox 配置
    # full: 无额外限制
    # read_only: 只读模式
  timeout: 30s

activation:
  mode: kernel
    # kernel: 始终激活，每次 API 调用携带 tool_definition
    # discoverable: 按需激活（Agent 通过 resource_loader 加载）
    # passive: 不自动激活，仅手动引用
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
```

## Pydantic 模型（代码创建路径）

### ModelConfig

```python
class ModelConfig(BaseModel):
    """单个模型的配置 | framework 仅管理路由/适配所需最小字段"""
    name: str                             # 模型逻辑名
    provider: Literal["openai", "anthropic", "custom"] = "openai"
    model: str                            # provider 模型 ID
    api_base: str = "https://api.deepseek.com/v1"
    api_key_env: str = "DEEPSEEK_API_KEY"
    kwargs: dict = {}                     # 透传给 provider，框架不校验
```

### AgentConfig

```python
class AgentConfig(BaseModel):
    """Agent 完整配置"""
    name: str                             # 必填
    role: str                             # 必填: Agent 角色
    task: str                             # 必填: 任务场景
    system_prompt: str | None = None      # 可选，不填由 role+task 生成
    models: list[ModelConfig]             # 必填
    router: RouterConfig | None = None
    compaction: CompactionConfig | None = None
    memory: MemoryConfig | None = None
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
                    _build_hooks / _build_registry
                              │
                              ▼
                    GraphEngine(...)  ← DI 注入
```

用户传入可选参数 → 按用户配置写入文件；未传入 → 从框架默认模板 copy 到 `agent_name/` 下。

## 迁移步骤

1. **骨架 + Protocol 定义** — 创建 `arf/` 目录及 10 个子模块，各域写 `protocol.py`，更新 `pyproject.toml`
2. **引擎层迁移** — 搬运 `engine/`，移除 server import、硬编码模型名/工具名、中文、dispatcher
3. **resources + hooks + agent 迁移** — 搬运并清理，抽出应用层实现
4. **补齐问题域默认实现** — compaction/sliding_window, memory/file_store, routing/two_tier, sandbox/path_sandbox, concurrency/sequential
5. **前端隔离 + 验证** — 前端移入 `app/web/`，应用层占位 `app/arf_app/`，确认框架零应用依赖

每步可独立提交，不破坏上一步功能。备份分支 `arfwithapp` 保留当前完整代码。
