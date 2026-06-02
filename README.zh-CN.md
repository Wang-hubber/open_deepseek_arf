<p align="center">
  <h1 align="center">ARF — Agent Resources & RunTime FrameWork</h1>
</p>

<p align="center">
  <a href="./README.md">English</a>
  &nbsp;·&nbsp;
  <strong>简体中文</strong>
  &nbsp;·&nbsp;
  <a href="https://gitee.com/dalaydata/open_deepseek_arf">Gitee</a>
  &nbsp;·&nbsp;
  <a href="https://github.com/Wang-hubber/open_deepseek_arf">GitHub</a>
</p>

<p align="center">
  <a href="./pyproject.toml"><img src="https://img.shields.io/badge/python-3.11+-5fa04e?style=flat-square&labelColor=161b22&logo=python&logoColor=white" alt="Python 3.11+"/></a>
  <a href="./LICENSE"><img src="https://img.shields.io/badge/license-MIT-8b949e?style=flat-square&labelColor=161b22" alt="license"/></a>
  <a href="https://github.com/Wang-hubber/open_deepseek_arf/stargazers"><img src="https://img.shields.io/github/stars/Wang-hubber/open_deepseek_arf.svg?style=flat-square&color=dbab09&labelColor=161b22&logo=github&logoColor=white" alt="GitHub stars"/></a>
</p>

<br/>

<h3 align="center">Harness = 操作系统内核。Model = CPU。Agent = 计算机。</h3>
<p align="center">本地优先 · 约定大于配置 · 全程可追溯 · 自我演进</p>

<br/>

> **本项目由 DeepSeek V4 Pro 与 Claude Code 协作完成。** 作者仅提供设计思路与代码审核，未手写任何一行代码。

<br/>

## 阅读指引

这份文档分为两大部分和底部待办清单：

- **第一部分 — 框架**：核心设计理念和问题域全景。表格中每个问题名称都是一个链接，指向深度设计文档（每篇含 OS 方案演进 / 当前实现 / 演进方向三章）
- **第二部分 — 参考应用**：展示应用层如何调用框架的每项能力，附配置示例和设计文档链接
- **底部 [TODO](#todo)**：已知问题与演进方向汇总，面向贡献者

新读者建议先扫一遍总览表格建立全景，再按需深入具体文档。

<br/>

---

## 第一部分 — 框架

### 设计理念

模型是裸算力——强大，但不是一台可用的计算机。它需要内存管理、进程调度、中断响应、文件系统和安全边界。ARF 提供这一切。它是一个**智能体框架**，建立在一个核心架构洞察之上：**Harness 层就是 AI 原生计算的内核态**。

操作系统的经典抽象——虚拟内存、缓存层次、系统调用、保护环——直接映射到每个智能体工程师日常面对的问题。ARF 不发明新抽象，而是将经过数十年验证的 OS 模式适配到 Token 时代。

### Harness 即内核——6 骨架架构

> **Model + Harness = Agent。CPU + Kernel = Computer。**
>
> Token 是指令。Agent 会话是进程。工具调用是系统调用。

ARF 建立在 **6 个骨架**之上——最小可运行框架。每个骨架对应一个 Protocol。框架可以只用这 6 个骨架运行 Agent；其余一切都是挂载在生命周期 Hook 点上的 **Plugin**。

*点击第一列的名称可查看深度设计文档。*

| # | 骨架 | OS 类比 | 当前实现 | 演进方向 |
|---|------|--------|----------|----------|
| 1 | **[Prompt 组装](docs/prompt-assembly.md)** | 程序加载器 (execve) | `SystemPromptProvider` — prefix（role + critical_rules）+ suffix（`$INVENTORY` 模板）。`string.Template` 占位符（`$MEMORY`、`$WORKSPACE`、`$TURN_BUDGET`）。引擎每轮替换。 | 多 Agent prompt 组合；基于角色的模板分发 |
| 2 | **[资源注册 (MCP)](docs/resource-registry.md)** | 文件系统 + 注册表 | 约定优于配置：`tool.yaml`+`function.py` 每工具，`skills/*.yaml`。模型在 `agent.yaml` 中内联定义（`model_defs`）。`FileWatcher` inotify+轮询热加载。`ResourceResolver` 覆盖合并。MCP 统一接口（本地 MCP Server 子进程，stdio JSON-RPC）聚合本地与外部资源。 | 层次化覆盖合并；MCP 多源 Provider；交叉引用验证 |
| 3 | **[权限控制](docs/tool-sandbox.md)** | ACL + 能力位 | `SessionModeManager`（auto/ask/plan）+ `PermissionRegistry` deny→ask→allow 执行。Per-agent `policy` 覆盖。`deny_patterns` 正则匹配。 | OAuth 范围权限；基于角色的访问控制 |
| 4 | **[安全审核](docs/tool-sandbox.md)** | 保护环 (Ring 0-3) | `PathCheckToolGuard` — 递归扫描（..、符号链接、深度/数量配额）。`ContentGuard` — 执行前/后 + 输出前基于规则的筛查。`GuardDefaults` 三道防线。 | 逐次调用沙箱；内容感知扫描 |
| 5 | **[执行器 (沙箱)](docs/tool-sandbox.md)** | 进程隔离 (chroot/namespace) | `SandboxManager` — 每会话隔离工作区，可配置黑名单，自动销毁。`ConcurrentToolExecutor` 并行执行。`FunctionBackend` 可选 `rollback()`。 | 容器级沙箱；资源配额 |
| 6 | **[控制平面](docs/agent-execution.md)** | 进程调度器 + 信号 | `GraphEngine` 统一 `_execute` 路径。`LoopStrategy` ReAct 模式 + TODO 追踪。State 管理（运行时会话状态）。9 个 Hook 注入点（`session_start`、`round_start`、`pre_model_call`、`post_model_call`、`post_permission`、`pre_tool_exec`、`post_tool_exec`、`sandbox_persist`、`round_end`、`session_end`）。 | Plan-Execute 循环策略；暂停/恢复/检查点；多 Agent DAG |

### Plugin 体系

**Plugin ≠ Tool。** Tool 是 MCP 管理的函数资源，由 Agent 调用。Plugin 是挂载在 Hook 点上的行为——在框架生命周期事件时自动触发。框架无 Plugin 也能运行；Plugin 添加预置或自定义能力。

| Plugin | Hook | 状态 | 描述 |
|--------|------|------|------|
| **Memory** | `round_end` | DONE | 长期记忆提取，system model 驱动，原子写入 `memory.md` |
| **TODO** | `round_start`, `round_end` | DONE | 任务列表追踪 + 提醒注入 |
| **UNDO** | `round_end`, `sandbox_persist` | DONE | Round 级状态 + 文件回滚 |
| ~~**Model Routing**~~ | `pre_model_call` | **已弃用** | `TwoTierRouter` — 廉价 LLM 分类：简单→flash，复杂→pro。已弃用，改用直接模型配置。 |
| **Human Loop** | `post_permission`, `pre_tool_exec` | DONE | SSE 审批通道，60s 超时 |
| **Compaction** | `round_end` | DONE | `CompactionPlugin` — token 感知，75% 阈值，保留 8 条 + LLM 摘要 |
| **Checkpoint** | `round_end`, `session_end` | DONE | `CheckpointPlugin` — round 快照 + session 归档，支持 undo/restore |
| **Trace** | 全部 hook（跨切面） | DONE | `TracePlugin` — JSONL 事件记录，用于调试、回放、评估 |
| **Evaluation** | 离线 | DONE | `EvalPlugin` — 重放 trace、计算指标、diff 报告 |
| Planner | (延后) | P1 | 任务分解，system model 驱动 |
| bash | (延后) | P1 | Shell 执行器，注入安全审计 |
| code_interpreter | (延后) | P1 | Python 沙箱 |

### 弃用/延后

| 模块 | 处理 | 原因 |
|------|------|------|
| A2A 通信 (`arf/communication/`) | 弃用 | 先聚焦 agent+subagent |
| TaskScheduler (`arf/concurrency/`) | 弃用 | 仅单 Agent 执行 |
| Plan-Execute 策略 | 延后 | ReAct + TODO 当前足够 |

**边界原则**：框架提供 mechanism（怎么做），应用通过 configuration + instantiation 决定做什么。`agent.yaml` 是桥接点——框架读取它自动装配全部能力；应用只需声明"用什么"，不需要知道"怎么实现"。

| 层级 | 范畴 | 能力 |
|------|------|------|
| **框架** (`arf/`) | **6 骨架** | **Prompt 组装** — `SystemPromptProvider`（prefix + suffix + `$INVENTORY` 模板）。**资源注册** — MCP 统一接口、`ResourceResolver`、`FileWatcher` 热加载。**权限控制** — `SessionModeManager` + `PermissionRegistry` deny→ask→allow。**安全审核** — `PathCheckToolGuard`、`ContentGuard` 规则筛查。**执行器** — `SandboxManager` 每会话隔离、`ConcurrentToolExecutor` 并行执行。**控制平面** — `GraphEngine`+`LoopStrategy` ReAct、State 管理、9 个 Hook 注入点。 |
| | **Plugins** | `InProcessHookRunner` 在生命周期 Hook 上执行 `PluginProtocol` 实例。内置：`CompactionPlugin`（token 感知滑动窗口）、`CheckpointPlugin`（round 快照 + session 归档）、`TracePlugin`（JSONL 事件记录）、`EvalPlugin`（离线 trace 回放 + 指标）、`MemoryPlugin`（长期记忆提取）、`TodoPlugin`（任务追踪）、`UndoPlugin`（round 回滚）、`ModelRouterPlugin`（快/慢分发）、`HumanLoopPlugin`（SSE 审批）。 |
| | **基础设施** | `ModelAdapter` 指数退避 + 重试、`TokenBucket` 限流、`CircuitBreaker` 故障隔离、`DefaultErrorPolicy`/`FunctionBackend` 回滚、`SubprocessHookRunner` 外部 Hook 脚本、`SkillPipeline` 依赖排序 |
| | **协议层** | Protocol 类（`core/protocols/`）——定义 `LoopStrategy`、`StateStore`、`ToolExecutor`、`PluginProtocol`、`HookRunner`、`GuardRunner`、`EventBus`、`ModelRouter` 等全部抽象接口 |
| **应用** (`app/`) | **前端** | Vue 3 + TypeScript + Vite SPA、Pinia 状态管理 / VueRouter 路由、ECharts 图表 / i18n 中英双语、ChatPanel / TraceView / ResourcePanel 等组件 |
| | **HTTP 服务** | FastAPI + Uvicorn + Streamable HTTP (NDJSON)、REST 端点（chat / trace / resources / config / usage …）、WebSocket 端点、CORS / SPA fallback / StaticFiles |
| | **CLI 工具** | init / start / stop / chat / list / validate / config |
| | **配置与数据** | `agent.yaml` — 模型定义（`model_defs`）+ agent/subagent 模型引用（`agent_models`）+ plugin 配置（`plugins_config`），自定义 `tools/`（file_*, web_*, python_exec …）、自定义 `skills/`、自定义 `hooks/`、DeepSeek API key 管理 |

<br/>

---

## 第二部分 — 参考应用：如何调用框架能力

参考应用 `app/arf_default_assistant/` 展示了一个应用如何调用框架的每一项能力。以下各节从应用层设计出发，链接到框架实现细节。

### 约定大于配置

四种实体类型——**model**、**tool**、**skill**、**hook**——每种遵循可预期的目录约定。框架自动发现，无需手动注册。一个工具就是两个文件：`tool.yaml`（Schema）+ `function.py`（逻辑）。

工具和技能定义在文件系统——每个工具一个 `tool.yaml`+`function.py`，技能 `skills/*.yaml`。框架自动发现。`agent.yaml` 仅在需要时覆盖个别字段：

```yaml
tools:
  - name: file_reader
    activation: kernel   # 仅覆盖激活方式，其余来自文件系统

skills:
  - name: code_review
    activation: discoverable
```

### 渐进式披露

仅必备内核工具始终激活，其余按需通过 `resource_loader` 加载、执行、停用。智能体只为实际使用的能力付费。

### MCP 统一资源接口

工具和技能通过统一的 MCP（Model Context Protocol）接口访问。本地 MCP Server 子进程聚合本地文件系统资源（`tools/`、`skills/`、`plugins/`）与可选的外部 MCP 连接：

```yaml
# agent.yaml — 可选的外部 MCP 服务器
mcp_servers:
  - name: search
    transport: sse
    url: http://localhost:9000/sse
```

Agent 通过 stdio JSON-RPC 通信。应用层无需关心工具来源——本地工具、插件工具、远程工具对 Agent 透明。

### 记忆——自动抽取与检索

应用**不实现**自己的记忆系统。框架的记忆提取位于 [`memory` 插件](docs/plugins/memory.md)（`arf/plugins/memory/`）——挂载在 `round_end` hook，通过 `plugins_config.memory.model` 指定独立模型。提取的事实、偏好和决策原子写入 `memory.md`（≤300KB），会话启动时加载并注入系统提示。

```yaml
plugins_config:
  memory:
    model: deepseek-v4-flash        # 引用 model_defs 中定义的模型
    interval: 5                      # 每 5 轮提取一次
    max_memory_size: 300             # memory.md 的 KB 上限
```

[设计文档 →](docs/plugins/memory.md)

### 压缩——Token 感知的上下文管理

`CompactionPlugin`（挂载在 `round_end` hook）监控上一轮的 token 用量。达到模型上下文窗口 75% 时触发：保留最近 8 条消息，旧轮次通过 LLM 生成摘要存入 `context_summary`。长工具输出（>2000 字符）落盘，上下文保留摘要指针。通过 `plugin.yaml` 配置：

```yaml
# arf/plugins/compaction/plugin.yaml
config:
  threshold: 0.75
  window_size: 131072
  keep_count: 8
```

[设计文档 →](docs/context-management.md)

### 模型配置——统一定义与引用

模型在 `agent.yaml` 顶部内联定义，通过 `model` 字段作为唯一标识。Agent 和 SubAgent 按名引用并支持有序降级；Plugin 引用单个模型。

```yaml
model_defs:                          # 顶部全局定义
  - model: deepseek-v4-pro
    api_base: https://api.deepseek.com
    api_key_env: DEEPSEEK_API_KEY    # 环境变量的变量名，非 Key 值
    kwargs: {reasoning_effort: max}
  - model: deepseek-v4-flash
    api_base: https://api.deepseek.com
    api_key_env: DEEPSEEK_API_KEY
    kwargs: {temperature: 0.7}

agent_models:                        # Agent: 有序降级 [pro → flash]
  - model: deepseek-v4-pro
  - model: deepseek-v4-flash

plugins_config:                      # Plugin: 单模型引用
  compaction:
    model: deepseek-v4-flash
  memory:
    model: deepseek-v4-flash
```

引用时支持部分覆盖（未写字段从定义区继承）：
```yaml
agent_models:
  - model: deepseek-v4-pro
  - model: deepseek-v4-flash
    kwargs: {temperature: 0.0}      # 仅覆盖 temperature
```

降级触发：5xx、429、网络错误。客户端错误（4xx）不降级。

### 沙箱与权限

`PathCheckToolGuard` 在每次工具调用前阻断路径穿越和绝对路径。`SessionModeManager` 解析全局会话模式 (`session_mode`) 与 per-agent policy 生成有效模式，`PermissionRegistry` 据此执行 deny→ask→allow 规则。工具在进程内执行，守卫检查每次调用。

三种会话模式：
- **auto**: 所有工具直接执行，忽略权限列表
- **ask**: 按 agent 策略 + deny/ask/allow 列表裁决（推荐，默认）
- **plan**: 全局只读，所有写/执行工具被拒绝（安全审查场景）

```yaml
session_mode: ask
advanced:
  guardrails:
    permissions:
      policy: ask   # per-agent 策略，仅 session_mode=ask 时生效
      deny: []
      ask: [python_exec, file_deleter]
      allow: [file_reader, web_search, web_fetch]
```

[设计文档 →](docs/tool-sandbox.md)

### 中断——取消与撤销

引擎每轮检查 `asyncio.Event` 取消令牌。`POST /api/chat/cancel` 或客户端断开即可停止 Agent。`RoundManager` 维护 3 个 Round 级滚动快照——undo 恢复到任意最近轮次开始时的状态+文件，跨 agent handoff 边界也生效。`undo_executed` trace 事件标记回滚边界但不删除历史。数据修改类工具可导出可选的 `rollback()` 函数——`FunctionBackend` 在 `execute()` 抛出异常时自动调用，实现 Tool 级副作用回滚。Hook 退出码 2 的消息注入对话流。

[设计文档 →](docs/interrupt.md)

### 技能流水线——工具执行顺序

Skill 可声明工具流水线与显式依赖。引擎强制执行顺序——依赖未满足的工具步骤无法执行。

```yaml
- name: resource_scaffold
  tools: [file_writer, resource_loader]
  pipeline:
    - tool: file_writer
    - tool: resource_loader
      depends_on: [file_writer]
```

[设计文档 →](docs/skill-pipeline.md)

### Trace——全链路可观测

`TracePlugin`（跨切面，挂载在全部 9 个 hook 点）将每个生命周期事件记录为 JSONL trace 文件。每条事件携带 `round`（用户交互轮次）和 `turn`（内部迭代）。`/traces` 瀑布流按轮次分组，可展开查看：模型响应 → 工具调用 → Hook。`UsageTracker` 提供 token 统计。`/trace-viewer` 提供独立 HTML 查看器。

[设计文档 →](docs/trace.md)

### 双智能体架构

User Agent 处理用户任务。System Agent 负责内部操作——资源创建、工具生成、校验。独立执行，共享工作区。用户看到一个连贯的助手；双智能体架构是实现细节。

```yaml
agents:
  - name: sys_agent
    role: 系统工程师
    task: 资源创建、模型配置、工具/技能生成
    models:
      - model: deepseek-v4-pro
```

<br/>

---

## 快速开始

需要 Python ≥ 3.11。

```bash
git clone git@gitee.com:dalaydata/open_deepseek_arf.git
cd open_deepseek_arf
pip install -e .
cd app/arf_default_assistant
python test_setup.py   # 验证环境
python cli.py start    # 启动服务
```

浏览器打开 **http://127.0.0.1:8000**，输入 API 密钥即可开始。

<br/>

## 二次开发 / 框架应用

**基于 ARF 构建 App**：详见 [APP 开发者指南](./APP开发者指南.md)——从零写一个 `agent.yaml`，配置模型、工具、技能、Hook，启动服务。

**参与框架开发**：参见底部 [TODO](#todo) 中的待修复问题和演进方向。框架代码位于 `arf/`，依赖注入设计允许替换任意默认实现。

```bash
git clone git@gitee.com:dalaydata/open_deepseek_arf.git
cd open_deepseek_arf
pip install -e .
cd app/web && npm install && npm run dev
```

**核心技术栈：** Python 3.11+ · FastAPI · Vue 3 · TypeScript · Vite

<br/>

---

## TODO

### 已知代码问题 (2026-05-26 事实校验)

### TODO — 待改进项

> 基于代码逐项验证。

| # | 标题 | 代码路径 | 功能域 | 类型 | 详情 |
|---|------|---------|--------|------|------|
| 1 | ~~Engine `invoke`/`astream` 代码重复~~ → **已修复** | `arf/engine/graph.py` | 进程调度 | 框架 | ~~~400 行几乎相同的 Agent Loop 逻辑在两处~~ → 提取 `_execute()` + `_step_call_model()` + `_step_execute_tools()` 统一路径，invoke/astream 简化为薄包装（净删除 ~370 行）。 |
| 2 | ~~`BaseAgent.__init__` 巨型构造~~ → **已修复** | `arf/agent/base.py` | 进程创建 | 框架 | ~~构造函数内直接实例化 20+ 个实现~~ → 提取了 `_merge_models()` 和 `_build_resource_resolver()` 工厂方法；吸收遗留的 `transaction_ctx` 覆盖。 |
| 3 | ~~`server.py` 单文件混杂~~ → **已修复** | `app/arf_default_assistant/routers/` | 用户界面 | App | ~~REST 路由、WebSocket、SSE 流、CORS、文件服务、状态管理、配置 API 全在一个文件。~~ → 拆分为 `routers/` 按路由组：`chat.py`、`trace.py`、`config.py`、`resources.py`、`misc.py`。`server.py` 从 846→137 行（app 创建 + lifespan + router 挂载）。共享状态在 `routers/state.py`。 |
| 4 | ~~`SnapshotRollback` 状态快照为空~~ → **已修复** | `arf/resources/backends/function.py` | 故障恢复 | 框架 | ~~`begin()` 中 `"state_snapshot": None` 始终不存快照~~ → 改为 `FunctionBackend` 内联回滚：tool `function.py` 可选导出 `rollback()`，`execute()` 异常时自动调用。`TransactionContext` 协议和 `SnapshotRollback` 类已移除。 |
| 5 | ~~`EvalRunner` 指标空转~~ → **已修复** | `arf/evaluation/runner.py` | 质量保证 | 框架 | ~~trace 硬编码为 `{"turns": []}`~~ → 重写：`EvalRunner` 通过 `EventBus.events_since()` 采集真实 trace，`events_to_trace()` 组装结构化 turn 数据，4 个 metric 在真实数据上计算。`BenchmarkBuilder` 从 `FileTraceStore` 会话创建 benchmark，`EvalComparator` 跨运行 diff 检测回归。 |
| 6 | ~~全局状态 `registry._agent`~~ → **已修复** | `arf/agent/registry.py` 已删除 | 进程隔离 | 框架 | ~~`_agent: Any = None` 模块级单例~~ → 已删除。`_engine` 和 `_state_store` 现通过工具执行器参数注入（与 `_agent_mode` 同模式）。`undo` 工具通过函数签名接收。`server.py` 不再调用 `set_agent()`。 |
| 7 | ~~`PromptBasedPlanner` 返回空计划~~ → **已修复** | `arf/plugins/planner/` | 任务规划 | 框架 | ~~`generate_plan()` 始终返回 `{"steps": []}`~~ → 由插件系统取代。`arf/plugins/` 提供框架插件（planner、todo、undo...）。App 在 `agent.yaml` 中声明 `plugins: [planner, todo]`。`PluginProvider` 扫描插件目录，`ResourceResolver` 合并插件 tools/skills 与 App 资源。**遗留问题**：`generate_plan()` 仍返回 `{"steps": []}`，`detect_divergence()` 仍返回 `{"diverged": False}`，`_call_model` 已注入但 LLM 从未被调用执行规划。`Planner` 协议是自主 Agent 的关键扩展点，调用方收到空结果可能误认为"无需分解"。 |
| 8 | ~~SSE 监听器泄漏~~ → **已修复** | `arf/streaming/adapters/sse.py` | 通信协议 | 框架 | ~~回调移除依赖 async generator 的 `finally`，但 CPython 在 `break`/exception 时不调用。~~ → 改为 `@asynccontextmanager`：`async with stream.listen() as queue` — `__aexit__` 在所有退出路径上保证清理。 |
| 9 | ~~代码规范不统一~~ → **已修复** | 13 文件 + `graph.py` + `planner.py` | 文档系统 | 框架 | ~~14 文件缺模块 docstring；10 处裸 `dict` 类型~~ → 全部 13 个文件已加模块 docstring。核心签名用 `dict[str, Any]` 替代裸 `dict`。`test_code_style.py` 强制执行规范。 |
| 10 | ~~无 Rate Limiting / Circuit Breaker~~ → **已修复** | `arf/protection/` | 进程调度 | 框架 | ~~LLM API 调用无速率限制、无断路器保护。~~ → `ModelCallProtector` 组合 `TokenBucket`（按 api_base）+ `CircuitBreaker`（按模型，指数冷却）。在 `BaseAgent._inject_model_calls()` 中以 decorator 模式包装 `_call_model`/`_stream_model`。5 种事件通过 EventBus → trace viewer 可观测。移除了 `DefaultErrorPolicy` 中的 engine 级重试。GraphEngine/ModelAdapter 零侵入。参见 [`docs/api-protection.md`](docs/api-protection.md)。 |
| 11 | 开源基建缺失 | — | 打包分发 | 框架 | 无 `CONTRIBUTING.md`、PR/Issue 模板、`CHANGELOG.md`、版本发布流程。文档丰富但缺乏外部贡献的流程指引。**风险**：潜在贡献者不知道提交标准；无 changelog 则用户无法评估升级影响 |

**Plugins** — 挂载在 Hook 点上的能力包。每个 Plugin 包含 `plugin.yaml`（name + hooks + config）和 `plugin.py`（PluginProtocol 实现）。`PluginLoader` 扫描 `arf/plugins/{name}/`。社区可贡献。Plugin ≠ Tool — Plugin 在生命周期 Hook 自动触发，Tool 是 Agent 主动调用的 MCP 资源。

| # | Plugin | 状态 | Hook | 描述 |
|---|--------|------|------|------|
| P-1 | ✅ `compaction` | DONE | `round_end` | Token 感知上下文压缩，75% 阈值 + LLM 摘要 |
| P-2 | ✅ `checkpoint` | DONE | `round_end`, `session_end` | Round 快照 + session 归档，支持恢复 |
| P-3 | ✅ `trace` | DONE | 全部 9 个 hook | JSONL 事件记录，用于调试、回放、评估 |
| P-4 | ✅ `eval` | DONE | 离线 | Trace 回放 + 指标计算 + diff 报告 |
| P-5 | ✅ `memory` | DONE | `round_end` | 长期记忆提取，system model 驱动，原子写入 `memory.md` |
| P-6 | ✅ `todo` | DONE | `round_start`, `round_end` | 任务列表追踪 + 提醒注入 |
| P-7 | ✅ `undo` | DONE | `round_end`, `sandbox_persist` | Round 级状态 + 文件回滚 |
| ~~P-8~~ | ~~`model_router`~~ | **已弃用** | `pre_model_call` | TwoTierRouter 快/慢分发 — 已弃用 |
| P-9 | ✅ `human_loop` | DONE | `post_permission` | SSE 审批通道，60s 超时 |
| P-10 | `bash` | P1 | `pre_tool_exec` | Shell 执行器，注入安全审计 |
| P-11 | `code_interpreter` | P1 | `pre_tool_exec` | Python 沙箱 |

### 演进方向

参见各模块设计文档：
- [上下文管理](docs/context-management.md) — 语义单元压缩、自适应阈值、跨会话摘要复用
- [Memory 插件](docs/plugins/memory.md) — 多轮次触发、自定义 prompt 模板
- [资源注册](docs/resource-registry.md) — 层次化覆盖合并、MCP 多源 Provider
- [工具沙箱](docs/tool-sandbox.md) — Per-invocation sandbox、内容感知扫描
- [Skill Pipeline](docs/skill-pipeline.md) — 多 Agent DAG、Worktree 隔离
- [中断](docs/interrupt.md) — 暂停/重定向、空闲超时
- [Trace](docs/trace.md) — SQLite Trace DB、OpenTelemetry 导出
- [回归测评](docs/eval-benchmark.md) — CLI 集成、语义相似度指标

<br/>

---

<p align="center">
  <sub>MIT — 详见 <a href="./LICENSE">LICENSE</a></sub>
  <br/>
  <sub><a href="https://gitee.com/dalaydata/open_deepseek_arf">Gitee</a> &nbsp;·&nbsp; <a href="https://github.com/Wang-hubber/open_deepseek_arf">GitHub</a></sub>
</p>
