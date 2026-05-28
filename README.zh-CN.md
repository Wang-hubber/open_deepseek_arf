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

### Harness 即内核——问题域架构

> **Model + Harness = Agent。CPU + Kernel = Computer。**
>
> Token 是指令。Agent 会话是进程。工具调用是系统调用。

*点击第一列的问题名称可查看深度设计文档。*

| # | 问题 | OS 类比 | 当前实现 | 演进方向 |
|---|------|--------|----------|----------|
| 1 | **[Agent 执行 →](docs/memory-management.md)**<br>生命周期 + 循环控制 | 进程管理 (fork/exec/scheduler) | `GraphEngine` invoke/astream 双模主循环。`BaseAgent` DI 组装全部协议。`LoopStrategy` ReAct 模式。`max_turns` 会话断路器。 | 多 Agent DAG 编排；暂停/恢复/检查点；plan-execute 循环策略 |
| 2 | **[LLM 调度 →](docs/model-routing.md)**<br>模型分发 + API 保护 | CPU 调度 (big.LITTLE/CFS) + 进程监管 | `TwoTierRouter` — LLM 分类器分发简单→flash、复杂→pro。`system_model` 后台任务专用。`TokenBucket` 按 API 端点限流（可配置 rps + burst）。`CircuitBreaker` 按模型指数冷却熔断——连续失败后熔断，HALF_OPEN 探测，自动恢复。`ModelAdapter` 指数退避重试。 | 自适应阈值（基于历史错误率动态调整 failure_threshold）；优先级队列（系统 vs 用户请求）；分布式限流（多 Agent 共享配额） |
| 3 | **[记忆与上下文 →](docs/memory-management.md)**<br>上下文窗口 + 长期记忆 | 虚拟内存 (paging/swapping) | `SlidingWindowCompactor` — token 感知，75% 阈值触发，保留最近 4 条 + LLM 摘要。`LLMMemoryWriter` 每轮提取事实/偏好/决策。`LLMMemoryRetriever` 语义检索。`FileMemoryStore` → `memory.json`。 | 语义单元检索；知识图谱索引；记忆衰减评分 |
| 4 | **[中断与恢复 →](docs/interrupt.md)**<br>取消 + 回退 + 回滚 | 硬件中断 (ISR) + 信号 | `asyncio.Event` 取消令牌每轮检查。`RoundManager` — 可配置快照窗口（默认 3），状态 + 文件跨 handoff 回滚。`FunctionBackend` 回滚——工具可选导出 `rollback()`，`execute()` 异常时自动调用。`SubprocessHookRunner` 退出码 2 → 消息注入。 | 暂停/重定向向量；空闲超时；中断优先级 |
| 5 | **[A2A 通信 →](docs/a2a-communication.md)**<br>Agent 间交互 | IPC (管道/信号/共享内存/消息队列) | `HandoffManager` 信号驱动 Agent 切换，集成于 invoke/astream 循环。`InMemoryAgentBus` — asyncio.Queue 消息路由（广播、定向、能力发现）。`PeerAgent` — P2P 协商/切换/发现。`DictWorkspace` 共享内存。`InMemoryLock` 同步。`MajorityVoteConsensus`。AgentBus/Supervisor/Consensus 协议层。`SkillPipeline` — 工具执行依赖声明。`ConcurrentToolExecutor` 并行执行。 | 网络 A2A (gRPC)；发布/订阅 Agent 发现；DAG 多 Agent 调度 |
| 6 | **[资源系统 →](docs/resource-registry.md)**<br>工具/技能/模型发现 | 文件系统 + udev + systemd | 约定优于配置：`tool.yaml`+`function.py` 每工具，`skills/*.yaml`，`models/*.yaml`。kernel/dynamic 分离 + 一次性冻结。`FileWatcher` inotify+轮询热加载。`ResourceResolver` 覆盖合并 + `generate_config()` 导出。 | 层次化覆盖合并；MCP 多源 Provider；交叉引用验证 |
| 7 | **[安全与沙箱 →](docs/tool-sandbox.md)**<br>访问控制 + 路径安全 | 保护环 (Ring 0-3) + ACL | `PathCheckToolGuard` — 递归扫描（..、符号链接、深度/数量配额）。`ToolPermissionChecker` deny→ask→allow 三级执行。`HumanLoop` SSE 推送审批 + 60s 超时。`GuardDefaults` 三道防线（PathCheck/Regex/None）。 | 逐次调用沙箱；MCP 协议；OAuth 范围权限 |
| 8 | **[可观测性 →](docs/trace.md)**<br>事件追踪 + 指标 | syslog / dtrace / perf | `EventType` Literal 25 种事件类型。`InMemoryEventBus` → `FileTraceStore`（每会话 JSON）。`UsageTracker` token 统计。独立 HTML trace viewer。Vue SPA 瀑布流按交互轮次分组。`SseStream` 实时事件。 | SQLite trace 数据库；OpenTelemetry 导出；Prometheus 指标 |
| 9 | **[内置工具 →](docs/api-protection.md)**<br>插件系统 | OS 内置软件 (coreutils, Notepad) | `arf/plugins/` 目录 — `agent.yaml` 的 `plugins:` 字段按名激活。`PluginProvider` 扫描插件目录，`ResourceResolver` 合并到工具/技能列表。P0 插件：`planner`（system_model 任务分解）、`todo`（工作区任务列表）、`undo`（轮次检查点回滚）。App 层可覆盖插件工具（app > plugin）。 | P1：bash、code_interpreter、file_ops；P2：web_search、web_fetch、memory_tools；社区插件仓库 |
| 10 | **[质量保证 →](docs/eval-benchmark.md)**<br>回归测试 | CI 测试套件 + 会话回放 | `BenchmarkBuilder` 从真实会话 trace 创建测试用例。`EvalRunner` 通过 `agent.chat()` 重放，`EventBus.events_since()` 采集。4 个内置指标（成功率、工具准确率、轮次效率、输出包含）。`EvalComparator` 对比运行报告。198 个单元/功能测试。 | CLI 集成；HTML 可视化报告；语义相似度指标；CI 流水线 |

**边界原则**：框架提供 mechanism（怎么做），应用通过 configuration + instantiation 决定做什么。`agent.yaml` 是桥接点——框架读取它自动装配全部能力；应用只需声明"用什么"，不需要知道"怎么实现"。

| 层级 | 范畴 | 能力 |
|------|------|------|
| **框架** (`arf/`) | **Agent 执行** | `GraphEngine`（invoke + astream 双模式）、`BaseAgent` DI 组装、`LoopStrategy` ReAct、`RoundManager` checkpoint/undo、`HandoffManager` 多 Agent 切换、`ConcurrentToolExecutor` 并行执行、`SkillPipeline` 依赖排序 |
| | **LLM 调度** | `TwoTierRouter` 快/慢分发、`ModelAdapter` 指数退避重试、`TokenBucket` 按端点限流、`CircuitBreaker` 按模型故障隔离、`ModelCallProtector` 装饰器模式注入 |
| | **记忆与上下文** | `SlidingWindowCompactor`（75% 阈值 + LLM 摘要）、`LLMMemoryWriter`/`LLMMemoryRetriever`（提取/检索管道）、`FileMemoryStore`（memory.json） |
| | **资源系统** | `ResourceResolver`（统一解析）、`ToolProvider`/`SkillProvider`/`ModelProvider`、`PluginProvider`（扫描 `arf/plugins/`）、`ResourceCache`（kernel/dynamic）、`FileWatcher`（inotify/轮询热加载） |
| | **安全** | `PathCheckToolGuard`（..、符号链接、深度/数量）、`ToolPermissionChecker` deny→ask→allow、`HumanLoop` SSE 审批 + 60s 超时、`GuardDefaults` 三道防线 |
| | **可观测性** | `InMemoryEventBus`（25 种事件类型）、`FileTraceStore`（每会话 JSON）、`UsageTracker`（token 统计）、独立 HTML trace viewer、Vue SPA 瀑布流 |
| | **基础设施** | `SubprocessHookRunner`（退出码契约）、`DefaultErrorPolicy`/`FunctionBackend` 回滚、`EvalRunner`/`BenchmarkBuilder`/`EvalComparator`（会话回放与回归） |
| | **协议层** | Protocol 类（`core/protocols/`）——定义 `MemoryStore`、`MemoryWriter`、`HookRunner`、`GuardRunner`、`EventBus`、`ModelRouter`、`LoopStrategy` 等全部抽象接口 |
| **应用** (`app/`) | **前端** | Vue 3 + TypeScript + Vite SPA、Pinia 状态管理 / VueRouter 路由、ECharts 图表 / i18n 中英双语、ChatPanel / TraceView / ResourcePanel 等组件 |
| | **HTTP 服务** | FastAPI + Uvicorn + SSE streaming、REST 端点（chat / trace / resources / config / usage …）、WebSocket 端点、CORS / SPA fallback / StaticFiles |
| | **CLI 工具** | init / start / stop / chat / list / validate / config |
| | **配置与数据** | `agent.yaml` — agent 行为 + `plugins:` 激活 + 路由 + 记忆 + 压缩、`models/deep.yaml` + `models/quick.yaml`、自定义 `tools/`（file_*, web_*, python_exec …）、自定义 `skills/`、自定义 `hooks/`、DeepSeek API key 管理 |

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

### 记忆——自动抽取与检索

应用**不实现**自己的记忆系统。框架的 `LLMMemoryWriter` 每轮对话后自动提取事实、偏好和决策。`LLMMemoryRetriever` 将相关记忆注入系统提示。统一存储在 `FileMemoryStore` → `memory/memory.json`。

```yaml
advanced:
  system_model: quick     # 系统后台模型 — 记忆、路由分类、压缩共用
  memory:
    store: file
    retriever: llm
    writer: llm
```

[设计文档 →](docs/memory-management.md)

### 压缩——Token 感知的上下文管理

`SlidingWindowCompactor` 监控上一轮的 token 用量。达到模型上下文窗口 75% 时触发：保留最近 4 条消息，旧轮次通过 LLM 摘要存入 `context_summary`。长工具输出落盘，上下文保留摘要。

```yaml
advanced:
  compaction:
    strategy: sliding_window
    threshold: 0.75
```

[设计文档 →](docs/memory-management.md)

### 模型路由——快慢分流

`TwoTierRouter` 通过廉价 LLM 对每次用户查询分类：简单 → `quick`（flash），复杂 → `deep`（pro）。后台任务（记忆、分类）使用专用模型。每轮动态切换。

```yaml
models:
  - type: quick
    model: deepseek-v4-flash
    context_window: 800000
  - type: deep
    model: deepseek-v4-pro
    context_window: 1000000

advanced:
  routing:
    strategy: two_tier
    default: quick
    classify: {medium: quick, complex: deep}
    fallback: {deep: quick}
```

[设计文档 →](docs/model-routing.md)

### 沙箱与权限

`PathCheckToolGuard` 在每次工具调用前阻断路径穿越和绝对路径。`ToolPermissionChecker` 强制执行 deny→ask→allow 规则。工具在进程内执行，守卫检查每次调用。

```yaml
advanced:
  permissions:
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

EventType Literal 定义 18 种，引擎在 invoke + astream 双路径中全部 emit → `FileTraceStore`（JSON）+ `UsageTracker`（token 统计）。每条事件携带 `round`（用户交互轮次）和 `turn`（内部迭代）。`/traces` 瀑布流按轮次分组，可展开查看：模型响应 → 工具调用 → Hook。`/trace-viewer` 提供独立 HTML 查看器。

[设计文档 →](docs/trace.md)

### 双智能体架构

User Agent 处理用户任务。System Agent 负责内部操作——资源创建、工具生成、校验。独立执行，共享工作区。用户看到一个连贯的助手；双智能体架构是实现细节。

```yaml
agents:
  - name: sys_agent
    role: 系统工程师
    task: 资源创建、模型配置、工具/技能生成
    routing:
      strategy: static
      default: deep
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
| 1 | ~~Engine `invoke`/`astream` 代码重复~~ → **已修复** | `arf/engine/graph.py` | 进程调度 | 框架 | ~~~400 行几乎相同的 Agent Loop 逻辑在两处~~ → 提取了 `_step_classify_tool_calls()` — guard pipeline、沙箱、权限、审批逻辑由两个路径共享。 |
| 2 | ~~`BaseAgent.__init__` 巨型构造~~ → **已修复** | `arf/agent/base.py` | 进程创建 | 框架 | ~~构造函数内直接实例化 20+ 个实现~~ → 提取了 `_merge_models()` 和 `_build_resource_resolver()` 工厂方法；吸收遗留的 `transaction_ctx` 覆盖。 |
| 3 | `server.py` 单文件混杂 | `app/arf_default_assistant/server.py` (843行) | 用户界面 | App | REST 路由、WebSocket、SSE 流、CORS、文件服务、状态管理、配置 API 全在一个文件。`ChatReq` Pydantic 模型混在路由文件中。**风险**：加新接口易触碰到已有逻辑，测试无法隔离 |
| 4 | ~~`SnapshotRollback` 状态快照为空~~ → **已修复** | `arf/resources/backends/function.py` | 故障恢复 | 框架 | ~~`begin()` 中 `"state_snapshot": None` 始终不存快照~~ → 改为 `FunctionBackend` 内联回滚：tool `function.py` 可选导出 `rollback()`，`execute()` 异常时自动调用。`TransactionContext` 协议和 `SnapshotRollback` 类已移除。 |
| 5 | ~~`EvalRunner` 指标空转~~ → **已修复** | `arf/evaluation/runner.py` | 质量保证 | 框架 | ~~trace 硬编码为 `{"turns": []}`~~ → 重写：`EvalRunner` 通过 `EventBus.events_since()` 采集真实 trace，`events_to_trace()` 组装结构化 turn 数据，4 个 metric 在真实数据上计算。`BenchmarkBuilder` 从 `FileTraceStore` 会话创建 benchmark，`EvalComparator` 跨运行 diff 检测回归。 |
| 6 | ~~全局状态 `registry._agent`~~ → **已修复** | `arf/agent/registry.py` 已删除 | 进程隔离 | 框架 | ~~`_agent: Any = None` 模块级单例~~ → 已删除。`_engine` 和 `_state_store` 现通过工具执行器参数注入（与 `_agent_mode` 同模式）。`undo` 工具通过函数签名接收。`server.py` 不再调用 `set_agent()`。 |
| 7 | ~~`PromptBasedPlanner` 返回空计划~~ → **已修复** | `arf/plugins/planner/` | 任务规划 | 框架 | ~~`generate_plan()` 始终返回 `{"steps": []}`~~ → 由插件系统取代。`arf/plugins/` 提供框架插件（planner、todo、undo...）。App 在 `agent.yaml` 中声明 `plugins: [planner, todo]`。`PluginProvider` 扫描插件目录，`ResourceResolver` 合并插件 tools/skills 与 App 资源。 |
| 8 | ~~SSE 监听器泄漏~~ → **已修复** | `arf/streaming/adapters/sse.py` | 通信协议 | 框架 | ~~回调移除依赖 async generator 的 `finally`，但 CPython 在 `break`/exception 时不调用。~~ → 改为 `@asynccontextmanager`：`async with stream.listen() as queue` — `__aexit__` 在所有退出路径上保证清理。 |
| 9 | ~~代码规范不统一~~ → **已修复** | 13 文件 + `graph.py` + `planner.py` | 文档系统 | 框架 | ~~14 文件缺模块 docstring；10 处裸 `dict` 类型~~ → 全部 13 个文件已加模块 docstring。核心签名用 `dict[str, Any]` 替代裸 `dict`。`test_code_style.py` 强制执行规范。 |
| 10 | ~~无 Rate Limiting / Circuit Breaker~~ → **已修复** | `arf/protection/` | 进程调度 | 框架 | ~~LLM API 调用无速率限制、无断路器保护。~~ → `ModelCallProtector` 组合 `TokenBucket`（按 api_base）+ `CircuitBreaker`（按模型，指数冷却）。在 `BaseAgent._inject_model_calls()` 中以 decorator 模式包装 `_call_model`/`_stream_model`。5 种事件通过 EventBus → trace viewer 可观测。移除了 `DefaultErrorPolicy` 中的 engine 级重试。GraphEngine/ModelAdapter 零侵入。参见 [`docs/api-protection.md`](docs/api-protection.md)。 |
| 11 | 开源基建缺失 | — | 打包分发 | 框架 | 无 `CONTRIBUTING.md`、PR/Issue 模板、`CHANGELOG.md`、版本发布流程。文档丰富但缺乏外部贡献的流程指引。**风险**：潜在贡献者不知道提交标准；无 changelog 则用户无法评估升级影响 |

**Plugins** — 框架能力包，通过 `agent.yaml` 的 `plugins:` 字段激活。`PluginProvider` 扫描 `arf/plugins/{name}/`。社区可贡献。

| # | Plugin | 状态 | 描述 |
|---|--------|------|------|
| P-1 | ✅ `planner` | DONE | 通过 system_model 做任务分解，替代空的 PromptBasedPlanner |
| P-2 | ✅ `todo` | DONE | 任务列表管理（添加/勾选/列表/清除），读写 `todo.md` |
| P-3 | ✅ `undo` 迁移 | DONE | 从 `app/tools/` → `arf/plugins/undo/` |
| P-4 | ✅ `plugin_provider` | DONE | PluginProvider 扫描插件目录，`agent.yaml` `plugins:` 字段 |
| P-5 | `bash` | P1 | Shell 执行器，社区审计注入安全 |
| P-6 | `code_interpreter` | P1 | Python 沙箱，替代 `app/tools/python_exec` |
| P-7 | `file_ops` | P1 | 读/写/列/删，从 app 工具合并到插件 |
| P-8 | `web_search` | P2 | DuckDuckGo 搜索，从 app 迁移到插件 |
| P-9 | `web_fetch` | P2 | HTTP 抓取，从 app 迁移到插件 |
| P-10 | `resource_loader` | P2 | 热加载资源，从 app 迁移到插件 |
| P-11 | `memory_tools` | P2 | LLM 可控的记忆读写/遗忘接口 |

### 演进方向

### 演进方向

参见各模块设计文档第三章：
- [Memory](docs/memory-management.md#3-演进方向) — 语义单元检索、知识图谱索引、记忆衰减
- [Model Routing](docs/model-routing.md#3-演进方向) — 三级分类器、连续负载跟踪、模型硬件化
- [Resource Registry](docs/resource-registry.md#3-演进方向) — 层次化覆盖合并、MCP 多源 Provider
- [Tool Sandbox](docs/tool-sandbox.md#3-演进方向) — Per-invocation sandbox、MCP 协议
- [Skill Pipeline](docs/skill-pipeline.md#3-演进方向) — 多 Agent DAG 分析、Worktree 隔离
- [A2A 通讯](docs/a2a-communication.md) — 网络 A2A（gRPC）、发布/订阅 Agent 发现、DAG 多 Agent 调度
- [Interrupt](docs/interrupt.md#3-演进方向) — 暂停/重定向、空闲超时
- [Trace](docs/trace.md#3-演进方向) — SQLite Trace DB、OpenTelemetry 导出

<br/>

---

<p align="center">
  <sub>MIT — 详见 <a href="./LICENSE">LICENSE</a></sub>
  <br/>
  <sub><a href="https://gitee.com/dalaydata/open_deepseek_arf">Gitee</a> &nbsp;·&nbsp; <a href="https://github.com/Wang-hubber/open_deepseek_arf">GitHub</a></sub>
</p>
