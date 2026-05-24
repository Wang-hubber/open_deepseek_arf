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
  <a href="./pyproject.toml"><img src="https://img.shields.io/badge/python-3.10+-5fa04e?style=flat-square&labelColor=161b22&logo=python&logoColor=white" alt="Python 3.10+"/></a>
  <a href="./LICENSE"><img src="https://img.shields.io/badge/license-MIT-8b949e?style=flat-square&labelColor=161b22" alt="license"/></a>
  <a href="https://github.com/Wang-hubber/open_deepseek_arf/stargazers"><img src="https://img.shields.io/github/stars/Wang-hubber/open_deepseek_arf.svg?style=flat-square&color=dbab09&labelColor=161b22&logo=github&logoColor=white" alt="GitHub stars"/></a>
</p>

<br/>

<h3 align="center">Harness = 操作系统内核。Model = CPU。Agent = 计算机。</h3>
<p align="center">本地优先 · 约定大于配置 · 全程可追溯 · 自我演进</p>

<br/>

> **本项目由 DeepSeek V4 Pro 与 Claude Code 协作完成。** 作者仅提供设计思路与代码审核，未手写任何一行代码。

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

| 问题 | OS 方案 | 当前实现 | 演进方向 |
|------|--------|----------|----------|
| **内存管理（OOM + 持久化）** | 虚拟内存 + 文件系统 | Token 感知滑动窗口压缩（75% 阈值），LLM 摘要换出轮次。事实/偏好/决策自动抽取去重，语义检索注入。长工具输出落盘。 [压缩 →](docs/compaction.md) [记忆 →](docs/memory-pipeline.md) | 语义单元检索；知识图谱索引 |
| **模型路由与资源分配** | 多级缓存 + big.LITTLE 调度 | 二级 LLM 分类器（中等→quick，复杂→deep）。专用模型处理框架后台任务。每轮动态切换。 [模型路由 →](docs/model-routing.md) | 硬件感知调度；弱模型协作 |
| **工具沙箱与安全边界** | 系统调用 + 保护环（Ring 0–3）+ ACL | `tool.yaml` + `function.py` 每工具。`PathCheckToolGuard` 阻断路径穿越。双源隔离：框架只读，工作区读写。Hook 退出码契约（0/1/2）。权限 deny→ask→allow 管道。 [沙箱 →](docs/tool-sandbox.md) | 每次调用独立沙箱；MCP 协议 |
| **并发与死锁预防** | 超标量执行 + 依赖图 | 顺序执行。Skill 声明工具流水线与显式依赖——引擎强制执行顺序。Hook 线程池并行。 [Skill Pipeline →](docs/skill-pipeline.md) | 多 Agent DAG 分析；Worktree 隔离 |
| **外部中断与用户干预** | 硬件中断：保存现场 → ISR → 恢复 | `asyncio.Event` 异步取消。3 快照 undo（状态+文件双回滚），支持 API 和对话内 `undo` 工具。Hook 退出码 2 消息注入。 [中断 →](docs/interrupt.md) | 暂停/重定向向量；空闲超时 |
| **Trace 与可观测性** | 系统监控 + 结构化事件日志 | 13 种事件类型通过 EventBus → `FileTraceStore`（JSON）+ `UsageTracker`。前端瀑布流按交互轮次分组。资源统计 API。独立查看器。 [Trace →](docs/trace.md) | SQLite Trace 数据库；OpenTelemetry 导出 |

### 框架 vs. 应用

| 层级 | 范畴 | 举例 |
|------|------|------|
| **框架**（`arf/`） | 约定、引擎、资源系统、Trace 基础设施 | `GraphEngine`、`ResourceRegistry`、双源资源加载、Hook 退出码契约、`EventBus`、`FileTraceStore` |
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

[设计文档 →](docs/memory-pipeline.md)

### 压缩——Token 感知的上下文管理

`SlidingWindowCompactor` 监控上一轮的 token 用量。达到模型上下文窗口 75% 时触发：保留最近 4 条消息，旧轮次通过 LLM 摘要存入 `context_summary`。长工具输出落盘，上下文保留摘要。

```yaml
advanced:
  compaction:
    strategy: sliding_window
    threshold: 0.75
```

[设计文档 →](docs/compaction.md)

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

13 种事件类型通过 EventBus → `FileTraceStore`（JSON）+ `UsageTracker`（token 统计）。每条事件携带 `round`（用户交互轮次）和 `turn`（内部迭代）。`/traces` 瀑布流按轮次分组，可展开查看：模型响应 → 工具调用 → Hook。`/trace-viewer` 提供独立 HTML 查看器。

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

需要 Python ≥ 3.10 和 Node.js ≥ 18。

```bash
git clone git@gitee.com:dalaydata/open_deepseek_arf.git
cd open_deepseek_arf
pip install -e .
cd app/web && npm install && cd ..
arf init my_workspace
arf start --workspace my_workspace
```

浏览器打开 **http://localhost:5173**——输入 API 密钥即可开始。

### CLI 命令参考

| 命令 | 用途 |
|------|------|
| `arf init <name>` | 创建新工作区 |
| `arf start` | 启动后端 + 前端 |
| `arf web` | 仅启动后端（FastAPI + SSE） |
| `arf stop` | 停止运行中的进程 |
| `arf reload` | 停止 + 重启 |
| `arf list [tools\|skills\|models]` | 列出已注册资源 |
| `arf validate` | 检查工作区资源完整性 |
| `arf clone <type> <name>` | 将系统资源克隆到工作区以便定制 |

### 配置

| 环境变量 | 默认值 | 用途 |
|----------|--------|------|
| `ARF_SERVE_STATIC` | `1` | 后端托管前端静态文件 |
| `ARF_CORS_ORIGINS` | `localhost:5173` | CORS 允许来源 |
| `ARF_IDLE_TIMEOUT` | `600` | 会话空闲超时（秒） |

模型配置：`agent.yaml` — `base_url`、`api_key_env`、`model`、`context_window`、`temperature` 等。

<br/>

## 参与贡献

详见 [贡献者须知.md](./贡献者须知.md)。

```bash
git clone git@gitee.com:dalaydata/open_deepseek_arf.git
cd open_deepseek_arf
pip install -e .
cd app/web && npm install && npm run dev
```

**核心技术栈：** Python 3.10+ · FastAPI · Vue 3 · TypeScript · Vite

<br/>

---

<p align="center">
  <sub>MIT — 详见 <a href="./LICENSE">LICENSE</a></sub>
  <br/>
  <sub><a href="https://gitee.com/dalaydata/open_deepseek_arf">Gitee</a> &nbsp;·&nbsp; <a href="https://github.com/Wang-hubber/open_deepseek_arf">GitHub</a></sub>
</p>
