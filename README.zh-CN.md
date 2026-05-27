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
  &nbsp;·&nbsp;
  <a href="./docs/SELF_REVIEW.md">自评报告</a>
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

| 问题 | OS 方案 | 当前实现 | 演进方向 |
|------|--------|----------|----------|
| **[内存管理 →](docs/memory-management.md)**<br>OOM + 持久化 | 虚拟内存 + 文件系统 | Token 感知滑动窗口压缩（75% 阈值），LLM 摘要换出轮次。事实/偏好/决策自动抽取去重，语义检索注入。长工具输出落盘。 | 语义单元检索；知识图谱索引 |
| **[多模型调度 →](docs/model-routing.md)**<br>KV cache | 多级缓存 + big.LITTLE 调度 | 二级 LLM 分类器（中等→quick，复杂→deep）。专用模型（v4-flash with no thinking）处理框架后台任务。KV cache 由推理侧处理，框架有意不介入。 | 模型硬件化；LLM as hardware |
| **[资源注册与发现 →](docs/resource-registry.md)**<br>注册与生命周期 | 注册表 + 服务管理器（systemd/udev/launchd） | 约定优于配置：每工具 `tool.yaml`+`function.py`，`skills/*.yaml`，`models/*.yaml`。内核/动态分离 + 冻结只读。FileWatcher inotify+轮询双轨自动热加载。ResourceResolver 覆盖合并 + `generate_config()` dump。 | 层次化覆盖合并；MCP 多源 Provider；交叉引用验证 |
| **[工具沙箱 →](docs/tool-sandbox.md)**<br>安全边界 | 系统调用 + 保护环（Ring 0–3）+ ACL | `PathCheckToolGuard` 递归扫描（..、symlink、深度/数量配额）。权限 deny→ask→allow。人工审批通道（SSE 推送 + 60s 超时）。 | 每次调用独立沙箱；MCP 协议 |
| **[并发与死锁 →](docs/skill-pipeline.md)**<br>Skill Pipeline | 超标量执行 + 依赖图 | Agent 循环顺序执行，单轮内工具调用通过 `ConcurrentToolExecutor` 并行。Skill 声明工具流水线与显式依赖——引擎强制执行顺序。Hook `asyncio.gather` 并发。 | 多 Agent DAG 分析；Worktree 隔离 |
| **[外部中断 →](docs/interrupt.md)**<br>用户干预 | 硬件中断：保存现场 → ISR → 恢复 | `asyncio.Event` 异步取消。Round 级 undo（`RoundManager`）— 可配快照窗口（默认 3），状态+文件跨 handoff 回滚，round 元数据持久化落盘。`undo_executed` trace 事件。 | 暂停/重定向向量；空闲超时 |
| **[Trace →](docs/trace.md)**<br>可观测性 | 系统监控 + 结构化事件日志 | EventType Literal 18 种事件类型 → `FileTraceStore`（JSON）+ `UsageTracker`。前端瀑布流按交互轮次分组。独立查看器。 | SQLite Trace 数据库；OpenTelemetry 导出 |

### 框架 vs. 应用

**边界原则**：框架提供 mechanism（怎么做），应用通过 configuration + instantiation 决定做什么。`agent.yaml` 是桥接点——框架读取它自动装配全部能力；应用只需声明"用什么"，不需要知道"怎么实现"。

| 层级 | 范畴 | 能力 |
|------|------|------|
| **框架** (`arf/`) | **执行引擎** | `GraphEngine`（invoke + astream 双模式）、状态修复、checkpoint/undo 机制、cancel 取消令牌、Memory 提取→检索→写入管道、Compaction 上下文压缩、Guardrails 三道防线、ModelRouter 路由调用、Transaction 事务回滚 |
| | **资源系统** | `ResourceResolver`（统一解析入口）、`ToolProvider` / `SkillProvider` / `ModelProvider`、`ResourceCache`（kernel/dynamic 双缓存）、`FileWatcher`（inotify/polling 文件变更检测）、双源加载（文件系统 + `agent.yaml` override 合并） |
| | **Agent 组装** | `BaseAgent` — DI 注入全部协议实现、`AgentConfig` — YAML 驱动配置、`ModelAdapter` — 自动注入 call/stream、`LoopStrategy` — ReAct 策略 |
| | **基础设施** | `EventBus`（`InMemoryEventBus`）、`FileTraceStore`（session 级 JSON 持久化）、`FileStateStore` / `InMemoryStateStore`、`UsageTracker`（用量统计）、`SubprocessHookRunner`（退出码契约：rc=2 → 消息注入）、`PathSandbox`（路径沙箱）、`TwoTierRouter`（LLM 分类路由）、`SlidingWindowCompactor`（滑动窗口压缩）、`SkillPipeline`（技能流水线排序）、`DefaultErrorPolicy` / `SnapshotRollback`（事务回滚）、`GuardDefaults`（PathCheck / Regex / None 三道防线） |
| | **协议层** | Protocol 类（`core/protocols/`）——定义 `MemoryStore`、`MemoryWriter`、`HookRunner`、`GuardRunner`、`EventBus`、`LoopStrategy` 等全部抽象接口 |
| **应用** (`app/`) | **前端** | Vue 3 + TypeScript + Vite SPA、Pinia 状态管理 / VueRouter 路由、ECharts 图表 / i18n 中英双语、ChatPanel / TraceView / ResourcePanel 等组件 |
| | **HTTP 服务** | FastAPI + Uvicorn + SSE streaming、REST 端点（chat / trace / resources / config / usage …）、WebSocket 端点、CORS / SPA fallback / StaticFiles |
| | **CLI 工具** | init / start / stop / chat / list / validate / config |
| | **配置与数据** | `agent.yaml` — agent 行为 + 路由策略 + 记忆策略 + 压缩策略、`models/deep.yaml` + `models/quick.yaml`、自定义 `tools/`（undo, file_*, web_*, python_exec …）、自定义 `skills/`（code_review, debug, file_ops …）、自定义 `hooks/`、DeepSeek API key 管理 |

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

引擎每轮检查 `asyncio.Event` 取消令牌。`POST /api/chat/cancel` 或客户端断开即可停止 Agent。`RoundManager` 维护 3 个 Round 级滚动快照——undo 恢复到任意最近轮次开始时的状态+文件，跨 agent handoff 边界也生效。`undo_executed` trace 事件标记回滚边界但不删除历史。Hook 退出码 2 的消息注入对话流。

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

> 基于 [自评报告](docs/SELF_REVIEW.md) 逐项代码验证。

| # | 标题 | 代码路径 | 功能域 | 类型 | 详情 |
|---|------|---------|--------|------|------|
| 1 | Engine `invoke`/`astream` 代码重复 | `arf/engine/graph.py:446-1195` | 进程调度 | 框架 | `invoke()`(446行) 与 `astream()`(791行) 约 400 行结构完全相同的 Agent Loop 逻辑，仅事件发射方式不同（`self._emit` vs `yield`）。**风险**：修改 Loop 逻辑需同步两处，遗漏即产生不一致行为 |
| 2 | `BaseAgent.__init__` 巨型构造 | `arf/agent/base.py` (636行) | 进程创建 | 框架 | 构造函数内直接实例化 20+ 个默认实现（EventBus、StateStore、Memory、Guardrails、Hooks、ToolExecutor 等），无工厂方法拆分。**风险**：新增协议实现需修改 `__init__`，测试注入依赖 `**override_protocols` 隐式传参 |
| 3 | `server.py` 单文件混杂 | `app/arf_default_assistant/server.py` (843行) | 用户界面 | App | REST 路由、WebSocket、SSE 流、CORS、文件服务、状态管理、配置 API 全在一个文件。`ChatReq` Pydantic 模型混在路由文件中。**风险**：加新接口易触碰到已有逻辑，测试无法隔离 |
| 4 | `SnapshotRollback` 状态快照为空 | `arf/errors/transaction.py:10` | 故障恢复 | 框架 | `begin()` 中 `"state_snapshot": None` 始终不存快照，`rollback()` 只标记未决工具，不恢复任何状态。`TransactionContext` 协议定义了 commit/rollback 语义但实现不完整。**风险**：工具调用中途失败时无真实回滚能力 |
| 5 | `EvalRunner` 指标空转 | `arf/evaluation/runner.py:17` | 质量保证 | 框架 | `run()` 调用 `agent.chat()` 拿到回复后，`trace` 字段硬编码为 `{"turns": []}`，未通过 `EventBus` 或 `StateStore` 收集真实的 turn-by-turn 执行轨迹。`ToolAccuracyMetric` / `TurnEfficiencyMetric` 始终计算空数据。**风险**：框架迭代无自动化回归检测，重构无法证明行为未退化。"覆盖率 60%"目标无评估手段支撑 |
| 6 | 全局状态 `registry._agent` | `arf/agent/registry.py:6` | 进程隔离 | 框架 | `_agent: Any = None` 模块级单例，`set_agent()` / `get_agent()` 全局读写。`server.py` 等上层代码直接 `import` 引用。**风险**：同一进程只能跑一个 Agent 实例；测试顺序敏感（全局状态泄漏）；与"框架"定位冲突——框架不应强制单例 |
| 7 | `PromptBasedPlanner` 返回空计划 | `arf/engine/loop_strategies/planner.py:10,19` | 任务规划 | 框架 | `generate_plan()` 始终返回 `{"steps": []}`，`detect_divergence()` 始终返回 `{"diverged": False}`。Engine 注入了 `_call_model` 但从未调用 LLM 生成计划。**风险**：`Planner` 协议是自主 Agent 任务分解的核心扩展点，当前对外传达虚假能力——调用者获得空结果可能误认为"任务无需分解" |
| 8 | SSE 监听器泄漏 | `arf/streaming/adapters/sse.py:21,26` | 通信协议 | 框架 | `listen()` 将回调 `_cb` 追加到 `self._listeners`（21行），仅在 generator `finally` 块中移除（26行）。若 `listen()` 的调用方提前丢弃 async generator 或异常退出，回调永久残留。**风险**：长时间运行的 SSE 服务中监听器累积，导致内存泄漏和无效回调被调用 |
| 9 | 代码规范不统一 | 14 个文件缺模块 docstring；`graph.py` 13 处裸 `dict`，`planner.py` 3 处 | 文档系统 | 框架 | 14 个 `.py` 文件无模块级 docstring；核心文件（`graph.py`、`base.py`、`planner.py`）共 18 处函数签名用裸 `dict` 而非 `TypedDict` 或具体类型；中英 docstring 混用。**风险**：贡献者上手速度降低；mypy 严格模式无法通过；代码观感与架构水平不匹配 |
| 10 | 无 Rate Limiting / Circuit Breaker | `arf/engine/graph.py` 模型调用路径 | 进程调度 | 框架 | LLM API 调用无速率限制、无断路器保护。`ModelAdapter` 有重试逻辑但框架层无跨调用的保护机制。**风险**：高频使用场景下可能触发 API 限流；持久故障模型无自动熔断，反复重试浪费资源 |
| 11 | 开源基建缺失 | — | 打包分发 | 框架 | 无 `CONTRIBUTING.md`、PR/Issue 模板、`CHANGELOG.md`、版本发布流程。文档丰富但缺乏外部贡献的流程指引。**风险**：潜在贡献者不知道提交标准；无 changelog 则用户无法评估升级影响 |

### 演进方向

### 演进方向

参见各模块设计文档第三章：
- [Memory](docs/memory-management.md#3-演进方向) — 语义单元检索、知识图谱索引、记忆衰减
- [Model Routing](docs/model-routing.md#3-演进方向) — 三级分类器、连续负载跟踪、模型硬件化
- [Resource Registry](docs/resource-registry.md#3-演进方向) — 层次化覆盖合并、MCP 多源 Provider
- [Tool Sandbox](docs/tool-sandbox.md#3-演进方向) — Per-invocation sandbox、MCP 协议
- [Skill Pipeline](docs/skill-pipeline.md#3-演进方向) — 多 Agent DAG 分析、Worktree 隔离
- [Interrupt](docs/interrupt.md#3-演进方向) — 暂停/重定向、空闲超时
- [Trace](docs/trace.md#3-演进方向) — SQLite Trace DB、OpenTelemetry 导出

<br/>

---

<p align="center">
  <sub>MIT — 详见 <a href="./LICENSE">LICENSE</a></sub>
  <br/>
  <sub><a href="https://gitee.com/dalaydata/open_deepseek_arf">Gitee</a> &nbsp;·&nbsp; <a href="https://github.com/Wang-hubber/open_deepseek_arf">GitHub</a></sub>
</p>
