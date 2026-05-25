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

| 问题 | OS 方案 | 当前实现 | 演进方向 |
|------|--------|----------|----------|
| **[内存管理 →](docs/memory-management.md)**<br>OOM + 持久化 | 虚拟内存 + 文件系统 | Token 感知滑动窗口压缩（75% 阈值），LLM 摘要换出轮次。事实/偏好/决策自动抽取去重，语义检索注入。长工具输出落盘。 | 语义单元检索；知识图谱索引 |
| **[多模型调度 →](docs/model-routing.md)**<br>KV cache | 多级缓存 + big.LITTLE 调度 | 二级 LLM 分类器（中等→quick，复杂→deep）。专用模型（v4-flash with no thinking）处理框架后台任务。KV cache 由推理侧处理，框架有意不介入。 | 模型硬件化；LLM as hardware |
| **[资源注册与发现 →](docs/resource-registry.md)**<br>注册与生命周期 | 注册表 + 服务管理器（systemd/udev/launchd） | 约定优于配置：每工具 `tool.yaml`+`function.py`，`skills/*.yaml`，`models/*.yaml`。内核/动态分离 + 冻结只读。FileWatcher inotify+轮询双轨自动热加载。ResourceResolver 覆盖合并 + `generate_config()` dump。 | 层次化覆盖合并；MCP 多源 Provider；交叉引用验证 |
| **[工具沙箱 →](docs/tool-sandbox.md)**<br>安全边界 | 系统调用 + 保护环（Ring 0–3）+ ACL | `tool.yaml` + `function.py` 每工具。`PathCheckToolGuard` 阻断路径穿越。双源隔离：框架只读，工作区读写。权限 deny→ask→allow 管道。 | 每次调用独立沙箱；MCP 协议 |
| **[并发与死锁 →](docs/skill-pipeline.md)**<br>Skill Pipeline | 超标量执行 + 依赖图 | Agent 循环顺序执行，单轮内工具调用通过 `ConcurrentToolExecutor` 并行。Skill 声明工具流水线与显式依赖——引擎强制执行顺序。Hook `asyncio.gather` 并发。 | 多 Agent DAG 分析；Worktree 隔离 |
| **[外部中断 →](docs/interrupt.md)**<br>用户干预 | 硬件中断：保存现场 → ISR → 恢复 | `asyncio.Event` 异步取消。3 快照 undo（状态+文件双回滚），支持 API 和对话内 `undo` 工具。Hook 退出码 2 消息注入。 | 暂停/重定向向量；空闲超时 |
| **[Trace →](docs/trace.md)**<br>可观测性 | 系统监控 + 结构化事件日志 | EventType Literal 15 种事件类型 → `FileTraceStore`（JSON）+ `UsageTracker`。前端瀑布流按交互轮次分组。独立查看器。 | SQLite Trace 数据库；OpenTelemetry 导出 |

### 框架 vs. 应用

| 层级 | 范畴 | 举例 |
|------|------|------|
| **框架**（`arf/`） | 约定、引擎、资源系统、Trace 基础设施 | `GraphEngine`、`ResourceResolver`、三个 Provider（Tool/Skill/Model）、`ResourceCache`、`FileWatcher`、双源资源加载、Hook 退出码契约、`EventBus`、`FileTraceStore` |
| **参考应用**（`app/`） | 基于框架构建的具体智能体 | Vue 3 前端、模型路由、`session_archiver`、记忆管道、沙箱、undo |
| **用户工作区** | 你在框架之上的构建 | 模型配置、自定义工具和技能、`agent.yaml` |

<br/>

---

## 第二部分 — 参考应用：如何调用框架能力

参考应用 `app/arf_default_assistant/` 展示了一个应用如何调用框架的每一项能力。以下各节从应用层设计出发，链接到框架实现细节。

### 约定大于配置

四种实体类型——**model**、**tool**、**skill**、**hook**——每种遵循可预期的目录约定。框架自动发现，无需手动注册。一个工具就是两个文件：`tool.yaml`（Schema）+ `function.py`（逻辑）。

应用在 `agent.yaml` 中声明工具和技能：

```yaml
tools:
  - name: file_reader
    description: 读取文件或列出目录
    parameters: {type: object, properties: {operation: ...}, required: [operation, path]}
    activation: kernel

skills:
  - name: code_review
    description: 审查代码变更的正确性
    tools: [file_reader, file_writer]
    activation: discoverable
```

### 渐进式披露

仅必备内核工具始终激活，其余按需通过 `resource_loader` 加载、执行、停用。智能体只为实际使用的能力付费。

### 记忆——自动抽取与检索

应用**不实现**自己的记忆系统。框架的 `LLMMemoryWriter` 每轮对话后自动提取事实、偏好和决策。`LLMMemoryRetriever` 将相关记忆注入系统提示。统一存储在 `FileMemoryStore` → `memory/memory.json`。

```yaml
advanced:
  memory:
    store: file
    retriever: llm
    writer: llm
    model: quick            # 廉价模型处理记忆操作
    temperature: 0.3
    thinking_enabled: false
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
  - name: quick
    model: deepseek-v4-flash
    context_window: 800000
  - name: deep
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

引擎每轮检查 `asyncio.Event` 取消令牌。`POST /api/chat/cancel` 或客户端断开即可停止 Agent。三轮滚动快照支持状态+文件双回滚，通过 API 或对话内 `undo` 工具触发。Hook 退出码 2 的消息注入对话流。

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

EventType Literal 定义 15 种，引擎 emit 13 种 → `FileTraceStore`（JSON）+ `UsageTracker`（token 统计）。每条事件携带 `round`（用户交互轮次）和 `turn`（内部迭代）。`/traces` 瀑布流按轮次分组，可展开查看：模型响应 → 工具调用 → Hook。`/trace-viewer` 提供独立 HTML 查看器。

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

## TODO

> 以下条目记录了系统事实校验发现的不一致和文档中讨论的演进方向。欢迎贡献者认领。

### 待修复 — 代码

*确认于 2026-05-25 对源码的事实校验。*

| # | 问题 | 位置 |
|---|------|------|
| 1 | (已修复 2026-05-25) `ResourceCache` 已接入全部三个 Provider | `arf/resources/cache.py` |
| 2 | (已修复 2026-05-25) CLI 命令缺少 `/api/` 前缀——全部 5 处已修正 | `app/arf_default_assistant/cli.py` |
| 3 | `agent.yaml` 字段已解析但未读取：`role`/`task` 已接入 system prompt；`reload` 已接入；剩余 `guardrails`/`human_loop`/`streaming`/`sandbox`/`tool_retrieval` 及多 Agent 字段仍未接线 | `arf/agent/config.py` |
| 4 | `StateStore` 无磁盘后端——app 的 `lazy_persistence.py` 手动序列化到 `archive.json` | `app/arf_default_assistant/lazy_persistence.py` |
| 5 | `FileWatcher` 生命周期由 app 管理（`_agent._file_watcher.start/stop`），非框架 | `arf/resources/file_watcher.py`、`server.py` |
| 6 | App 频繁访问 `_agent._engine`、`_agent._resource_resolver` 等私有属性 | `app/arf_default_assistant/server.py` |
| 7 | (已关闭 —— 孤儿工具属约定优于配置，文件系统即真相源，agent.yaml 仅作覆盖层) | `app/arf_default_assistant/tools/` |
| 8 | (已修复 2026-05-25) `set_agent()` 在 lifespan 中重复调用——已删除 | `app/arf_default_assistant/server.py:70` |
| 9 | `ToolConfig.provider`/`backend`/`execution`/`source` 已解析但未强制执行 | `arf/core/config_base.py` |
| 10 | 多 Agent（`agents:`/`handover:`/`supervisor:`）已解析但未接入引擎 | `arf/agent/config.py` |
| 11 | (已修复 2026-05-25) `ReloadConfig` 已接入——`watch` 和 `poll_interval` 由 BaseAgent 读取 | `arf/agent/base.py`、`config_base.py` |
| 12 | `EventType` Literal 定义 15 种类型；`approval_required`/`approval_resolved` 为审批通道预留；`user_input` 已补入 Literal | `arf/core/events.py` |
| 13 | (已修复 2026-05-25) `CompactionConfig.strategy` — 移除无用的 `"summarization"` 枚举值 | `arf/core/config_base.py` |
| 14 | (已修复 2026-05-25) README 并发描述已修正——Agent 循环顺序，工具调用并行 | README 总览表 |

### 待修复 — 文档

*确认于 2026-05-25 对文档与代码的交叉校验。*

| # | 问题 | 位置 |
|---|------|------|
| D1 | (已修复 2026-05-25) 事件类型数量统一为 15 种；`user_input` 已补入 EventType Literal | `arf/core/events.py`、`docs/trace.md`、README |
| D2 | (已修复 2026-05-25) `ResourceCache` 已接入，文档描述与实际架构一致 | `docs/resource-registry.md` |
| D3 | 16 处行数与实际代码差 1 行，`pipeline.py` 偏差达 45 行（文档 ~80 实际 125） | 7 份设计文档 |
| D4 | (已修复 2026-05-25) `ReloadConfig.watch` 默认值改为 `True`，`poll_interval` 已补入模型 | `arf/core/config_base.py` |
| D5 | Summarizer 代码位置：文档说 `base.py:129-157`，实际 `base.py:174-203` | `docs/memory-management.md` |
| D6 | README 第二部分将双 Agent 架构描述为已工作状态，但多 Agent 调度器未接线 | README 第二部分 |
| D7 | App README 写"14 个工具"，实际文件系统 15 个；`docs/app/tools.md` 正确写了 15 | `app/arf_default_assistant/README.md` |
| D8 | Hook 执行文档写"按声明顺序执行"但 `SubprocessHookRunner` 使用 `asyncio.gather`（并行） | `docs/app/hooks.md` |
| D9 | `agent.yaml` 主 `models:` 段只声明 `deep`，但路由配置引用 `quick`（仅存在于文件系统）——隐式引用不直观 | `app/arf_default_assistant/agent.yaml` |

[完整事实校验报告 →](docs/fact-check-2026-05-25.md)

### 演进方向

*详见各模块设计文档第3章。*

| 模块 | 文档 | 方向 |
|------|------|------|
| 内存管理 | [memory-management.md](docs/memory-management.md) | 语义单元检索 · 知识图谱索引 · 主动预取 · 冷热分离 · 记忆衰减 |
| 多模型调度 | [model-routing.md](docs/model-routing.md) | 三级分类器 · 连续负载跟踪 · 模型硬件化 |
| 工具沙箱 | [tool-sandbox.md](docs/tool-sandbox.md) | 独立沙箱 · MCP 协议 · 审批通道 · 递归参数检查 |
| 资源注册与发现 | [resource-registry.md](docs/resource-registry.md) | 层次化覆盖合并 · MCP 多源 Provider · 交叉引用验证 · 资源版本控制 |
| 并发 | [skill-pipeline.md](docs/skill-pipeline.md) | 多 Agent DAG 调度 · Worktree 隔离 · 事务性文件操作 |
| 中断 | [interrupt.md](docs/interrupt.md) | 暂停/恢复 · 持久化检查点 · 空闲超时 · 中断优先级 |
| Trace | [trace.md](docs/trace.md) | SQLite 数据库 · OpenTelemetry 导出 · 实时告警 · 性能剖面 |

### App/框架边界

*应下沉到框架层的能力。*

| # | 问题 | 当前（App 层） | 目标（框架层） |
|---|------|---------------|---------------|
| B1 | 无磁盘状态存储 | `lazy_persistence.py` 序列化到 `archive.json` | `FileStateStore` 实现 `StateStore` |
| B2 | FileWatcher 生命周期手动管理 | `server.py` 调用 `_agent._file_watcher.start/stop` | 框架随引擎自动启动 |
| B3 | API 密钥管理 | `server.py` `.env` 解析、验证、缓存、注册端点 | 框架密钥存储 + 验证 |
| B4 | Session 管理 | `server.py` `_active_cancel_events`、session stubs | 框架 Session 管理器 |
| B5 | Trace API 端点 | `server.py` `/api/trace/*` 路由 | 框架 FastAPI router |
| B6 | SSE 事件翻译 | `server.py` `_sse_chat()` 139 行事件翻译器 | 框架 SSE 适配器 |

<br/>

## 二次开发 / 框架应用

**基于 ARF 构建 App**：详见 [App 开发者须知](./贡献者须知.md)——从零写一个 `agent.yaml`，配置模型、工具、技能、Hook，启动服务。

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

<p align="center">
  <sub>MIT — 详见 <a href="./LICENSE">LICENSE</a></sub>
  <br/>
  <sub><a href="https://gitee.com/dalaydata/open_deepseek_arf">Gitee</a> &nbsp;·&nbsp; <a href="https://github.com/Wang-hubber/open_deepseek_arf">GitHub</a></sub>
</p>
