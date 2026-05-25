# Framework-App Fact-Check — 2026-05-25

对 `arf/` 框架层、`app/arf_default_assistant/` 应用层及全部文档的系统交叉对比。共发现 10 条代码问题 + 18 条文档不一致。

---

## 第一部分：代码问题 (10 条)

### 1. ResourceCache 定义但从未使用

**类型**：死代码 | **位置**：`arf/resources/cache.py`

`ResourceCache`（`_FrozenDict` + kernel/dynamic 分离）已完整实现，但**没有任何 Provider、Resolver 或 BaseAgent 代码导入它**。每个 Provider 在内部独立实现了相同的 kernel/dynamic dict 分离逻辑。

**建议**：重构 Provider 统一使用 `ResourceCache`，或删除 `cache.py` 避免维护歧义。

---

### 2. CLI 路由与 server 路由不匹配

**类型**：Bug | **位置**：`app/arf_default_assistant/cli.py`

| CLI 命令 | 请求路径 | 实际 server 路由 | 结果 |
|----------|---------|-----------------|------|
| `cmd_chat()` | `POST /chat` | `POST /api/chat` | 404 |
| `cmd_stop()` | `POST /save` | `POST /api/save` | 404 |
| `cmd_run()` | `GET /resources/skills` | `GET /api/resources/{res_type}` | 404 |
| `cmd_list()` | `GET /resources/{t}` | `GET /api/resources/{res_type}` | 404 |

四个 CLI 命令缺少 `/api/` 前缀。

---

### 3. agent.yaml 大量字段未被运行时读取

**类型**：死配置 | **位置**：`arf/agent/config.py`，`arf/core/config_base.py`

以下字段已被 Pydantic 解析，但**从未被任何框架或 app 代码读取**：

| 字段 | 所在类 / 配置段 |
|------|--------|
| `AgentConfig.role` | 已解析，从不读取 |
| `AgentConfig.task` | 已解析，从不读取 |
| `AgentConfig.agents` | 已解析，多 Agent 运行时未接线 |
| `AgentConfig.handover` | 已解析，handoff 规则未接线 |
| `AgentConfig.supervisor` | 已解析，调度器未接线 |
| `AdvancedConfig.guardrails` | BaseAgent 硬编码 DefaultGuardRunner |
| `AdvancedConfig.human_loop` | BaseAgent 始终使用 AlwaysAutoApprove |
| `AdvancedConfig.streaming` | 无运行时接线 |
| `AdvancedConfig.sandbox` | SandboxConfig 已定义但未接入任何 guard |
| `AdvancedConfig.tool_retrieval` | 引擎硬编码 `top_k=10` |
| `AdvancedConfig.reload` | FileWatcher 始终用 `poll_interval=5`，ReloadConfig.watch 默认 false 但从未被读取 |

---

### 4. lazy_persistence.py 填补框架缺口

**类型**：App 填补框架缺口 | **位置**：`app/arf_default_assistant/lazy_persistence.py`

框架 `StateStore` 协议的唯一内置实现 `InMemoryStateStore` 使用纯内存字典——进程重启即丢失。App 通过 `save_archive_async()` / `load_archive()` 手动将 `AgentState` 序列化到 `memory/archive.json`，在 lifespan 的 startup/shutdown 中恢复/归档。

这不是重叠，但表明 `StateStore` 协议缺少一个 `FileStateStore` 实现。

---

### 5. FileWatcher 生命周期由 App 管理，非框架

**类型**：责任泄漏 | **位置**：`arf/resources/file_watcher.py`，`app/arf_default_assistant/server.py:93-98`

框架在 `BaseAgent.__init__()` 中创建 `FileWatcher` 并注册 watch 回调，但**从未调用 `start()` 或 `stop()`**。App 通过直接访问 `_agent._file_watcher.start()` / `stop()` 管理生命周期。

**建议**：框架应在事件循环就绪后自动启动 FileWatcher，或通过协议暴露 `start()`/`stop()` 接口。

---

### 6. App 频繁访问框架私有内部

**类型**：脆弱耦合 | **位置**：`app/arf_default_assistant/server.py`

App 通过 `_agent._engine`（line 147, 196, 216, 222, 236）、`_agent._file_watcher`（line 93, 97）、`_agent._resource_resolver`（line 557-563, 569, 631-632）直接访问框架的私有属性。框架对这些属性的修改会直接破坏 App。

---

### 7. 3 个工具在文件系统上但不在 agent.yaml 中

**类型**：孤儿资源 | **位置**：`app/arf_default_assistant/tools/`

| 工具 | 位置 | agent.yaml 中？ |
|------|------|----------------|
| `manage_hooks` | `tools/manage_hooks/` | 否 |
| `text_to_upper` | `tools/text_to_upper/` | 否 |
| `resource_scaffold` | `tools/resource_scaffold/` | 仅在 sys_agent 的 tools 中 |

框架的 ToolProvider 会扫描文件系统找到所有 15 个工具并传给 LLM，但 agent.yaml 主 tools 段只声明了 12 个——system prompt 中不会列出未声明的工具。

---

### 8. set_agent() 重复调用

**类型**：Minor bug | **位置**：`app/arf_default_assistant/server.py:69-70`

```python
set_agent(_agent)
set_agent(_agent)  # 重复
```

功能上无害，但属于无意义的重复。

---

### 9. ToolConfig 多个字段已解析但未强制执行

**类型**：死字段 | **位置**：`arf/core/config_base.py` ToolConfig

| 字段 | 运行时行为 |
|------|----------|
| `provider` | 始终为 `static_yaml`，未切换 |
| `backend` | 始终为 `function`，未切换（`FunctionBackend` 硬编码） |
| `execution.sandbox` | 已解析，未执行 |
| `execution.timeout` | 已解析，未在 FunctionBackend 中使用 |
| `source` | 从不读取 |

---

### 10. 多 Agent 运行时未接线

**类型**：未实现 | **位置**：`arf/agent/config.py` AgentConfig.agents

`agent.yaml` 中声明了完整的 `sys_agent` 定义（models、tools、skills、system_prompt），但框架没有 Dispatcher 或 Supervisor 调度多个 Agent 实例。当前通过 app 层的 `handoff_to_sys` 工具 + `_agent_mode` 参数手动实现基本的 Agent 分离。

---

## 第二部分：文档不一致 (18 条)

### D1. 事件类型数量：文档 13 vs 代码 15

**位置**：`docs/trace.md`，`README.md` line 202，`README.zh-CN.md` line 202

文档说"13 种事件类型"，`EventType` Literal 实际定义了 15 种。文档列出的 13 种之外的 3 种是：`tool_call_result`、`approval_required`、`approval_resolved`（定义了但引擎当前未 emit）。

此外，文档列出的 `user_input` 不在 `EventType` Literal 中——它作为字符串 emit 但不被类型系统校验。

---

### D2. README 中的并发模型描述不准确

**位置**：`README.md` line 68，`README.zh-CN.md` line 68

总览表中并发行写"Sequential execution"（顺序执行）。实际上 Agent 循环是顺序的，但单轮内的工具调用通过 `ConcurrentToolExecutor` **并行**执行（`asyncio.gather` + semaphore=5）。Hook 通过 `asyncio.create_subprocess_shell` + `asyncio.gather` 并行触发。

`docs/skill-pipeline.md` 已正确指出了这个问题。

---

### D3. Python 版本声明不一致

**位置**：`README.md` line 226，`README.zh-CN.md` line 226，`pyproject.toml` line 9

- README 开头 badge 显示 `python-3.10+`
- README Quick Start 说 "Python ≥ 3.11"
- 底部 "Core stack" 说 "Python 3.10+"
- `pyproject.toml` 定义 `requires-python = ">=3.11"`

应以 `pyproject.toml` 为准：**≥3.11**。

---

### D4. ResourceCache 被文档大量引用但代码中从未使用

**位置**：`docs/resource-registry.md` lines 91-96, 102, 120

资源注册文档将 `ResourceCache` 描述为架构的缓存层组件（行 91-96, 102, 120），并引用了 `_FrozenDict` 的行为。实际上，没有任何 Provider、Resolver 或 BaseAgent 代码导入 `ResourceCache`。每个 Provider 内部自己管理 kernel/dynamic dict。

---

### D5. 文档中大量行数与实际代码不符

**位置**：多份设计文档

| 文件 | 文档声称行数 | 实际行数 |
|------|------------|---------|
| `tools.md` (App docs) | 15 个工具 | 15 个 ✅ |
| `app/README.md` | 14 个工具 | 15 个 ❌ |
| `tool_provider.py` | 119 | 118 |
| `skill_provider.py` | 60 | 59 |
| `model_provider.py` | 60 | 59 |
| `resolver.py` | 147 | 146 |
| `file_watcher.py` | 200 | 199 |
| `llm_writer.py` | 155 | 154 |
| `llm_retriever.py` | 113 | 112 |
| `guardrails/runner.py` | 41 | 40 |
| `path_check.py` | 33 | 32 |
| `permissions.py` | 77 | 76 |
| `tool_executor.py` | 40 | 39 |
| `pipeline.py` | ~80 | 125 |
| `file_trace.py` | 63 | 62 |
| `usage_tracker.py` | 92 | 91 |
| `replay.py` | 46 | 45 |

大多数差 1 行（文档计入尾行或计法不同），`pipeline.py` 偏差较大（~80 vs 125）。

---

### D6. Summarizer 代码位置引用偏移

**位置**：`docs/memory-management.md` line 99

文档说 LLM Summarizer 位于 `base.py:129-157`，实际在 `base.py:174-203`（`_summarize` 闭包）。文档计入了 `_build_system_prompt`（line 26-66）之前的区域。

---

### D7. ReloadConfig.watch 默认值文档与代码不一致

**位置**：`docs/resource-registry.md` line 225，`docs/app/advanced.md` line 117

两处文档都说 `reload.watch` 默认为 `true`（"默认 true"、"启用 FileWatcher（默认 true）"）。实际上 `ReloadConfig` Pydantic 模型定义的是 `watch: bool = False`。

更关键的是：`AdvancedConfig.reload` 字段在 `BaseAgent.__init__()` 中**从未被读取**——`watch_enabled` 参数始终为 `True`（`base.py:82`：`watch_enabled = override_protocols.pop("watch_enabled", True)`）。所以表面上"默认 true"的行为是对的，但原因是 app 绕过配置直接传了 override，而非读取配置。

---

### D8. 压缩策略 `summarization` 选项无实际行为差异

**位置**：`arf/core/config_base.py` line 62

`CompactionConfig.strategy` 枚举包含 `"summarization"` 选项，但 `base.py:171` 只判断 `!= "none"`——选择 `summarization` 的结果与 `sliding_window` 完全相同。这是一个信息值为零的配置选项。

---

### D9. `user_input` 事件 emit 但不在 EventType 类型定义中

**位置**：`arf/core/events.py` line 7，`arf/engine/graph.py` line 256

引擎在 `invoke()` 和 `astream()` 中通过 `_emit("user_input", ...)` 发射 `user_input` 事件，但 `EventType` Literal 联合类型不包含 `"user_input"`。运行时通过（因为没有类型强制校验），但类型系统不保证安全。

---

### D10. `approval_required` / `approval_resolved` / `tool_call_result` 事件类型未使用

**位置**：`arf/core/events.py` lines 12-13

这三个事件类型在 `EventType` Literal 中定义，但 GraphEngine 在任何路径都不 emit 它们。属于预留但未实现的接口。

---

### D11. README 的双 Agent 架构描述已实现但实际未接线

**位置**：`README.md` lines 206-219，`README.zh-CN.md` lines 206-219

README Part II "双智能体架构" 将 sys_agent 描述为"独立执行，共享工作区"的架构。实际上，框架层的多 Agent 调度尚未接线（见代码问题 #10）。当前能工作是因为 app 在 `server.py` 和 `handoff_to_sys` 工具中手动实现了 Agent 切换。

`docs/app/dual-agent.md` 已正确标注"当前实现状态：agents: 和 handover: 段已被解析但未被框架运行时完整接入"。

---

### D12. `docs/app/tools.md` 说"15 个工具"，`app/README.md` 说"14 个工具"

**位置**：`docs/app/tools.md` line 110，`app/arf_default_assistant/README.md` line 63

app README 目录结构注释说"14 个工具"，实际文件系统有 15 个工具目录。App docs 的 tools.md 正确写了 15。

---

### D13. `docs/app/hooks.md` 说"同一事件类型的多个 Hook 按 agent.yaml 声明顺序执行"

**位置**：`docs/app/hooks.md` line 84

`SubprocessHookRunner.fire()` 使用 `asyncio.gather` 并行启动所有匹配的 Hook，而非按声明顺序串行执行。文档描述的行为与实际实现的并行模型不匹配。

---

### D14. 贡献者须知.md 中链接的 app docs 文件经过验证全部存在

`贡献者须知.md` 链接到 `docs/app/models.md`、`docs/app/tools.md`、`docs/app/skills.md`、`docs/app/hooks.md`、`docs/app/dual-agent.md`、`docs/app/advanced.md` ——全部存在 ✅。

---

### D15. agent.yaml 中 models 只声明了 `deep`，但路由引用 `quick`

**位置**：`app/arf_default_assistant/agent.yaml`

`agent.yaml` 主 `models:` 段只声明了 `deep`，但 `advanced.routing` 配置引用了 `quick`，且 `advanced.memory.model` 也指定了 `quick`。`quick` 模型实际存在于 `models/quick.yaml` 文件系统中，通过 ModelProvider 的文件系统扫描加载。这种"隐式引用"对阅读 agent.yaml 的人不直观——看到 routing 配置引用 quick 但在 models 列表中找不到。

---

### D16. CLI `cmd_stop` 路径缺少 `/api/` 前缀（追加）

**位置**：`app/arf_default_assistant/cli.py` line 121

除已报告的 3 个命令外，`cmd_stop()` 也使用了错误路径：`_httpx_post("/save")` 应为 `_httpx_post("/api/save")`。

---

### D17. CLI `cmd_config_generate` 与 `ResourceResolver.generate_config()` 功能重复

**位置**：`app/arf_default_assistant/cli.py` lines 212-232

CLI 的 `cmd_config_generate` 创建独立的 `ToolProvider`/`SkillProvider`/`ModelProvider` 实例并包装在 `ResourceResolver` 中，与框架已有的 `ResourceResolver.generate_config()` 功能完全重叠。Server 端有 `GET /api/resources/generate-config` 使用 `_agent._resource_resolver.generate_config()`。CLI 应复用框架能力而非直接调用 Provider。

---

### D18. memory-management.md 中引擎集成路径引用

**位置**：`docs/memory-management.md` lines 138-141

文档说"invoke:352-358, astream:628-632"用于文本响应路径的记忆写入。实际 `graph.py` 的 `invoke()` 文本响应路径的记忆写入在 lines 352-358 ✅，`astream()` 在 lines 624-632 ✅。

但文档说工具执行路径在"invoke:469-475, astream:721-727"——`invoke()` 的工具路径记忆写入实际在 lines 469-475 ✅，`astream()` 在 lines 721-727 ✅。这些引用正确。

---

## 第三部分：APP/框架边界问题

### B1. 框架缺少会话持久化层

App 通过 `lazy_persistence.py` 手动将 `AgentState` 序列化到 `memory/archive.json`，因为框架的 `StateStore` 协议只有 `InMemoryStateStore` 实现。框架应提供 `FileStateStore`。

### B2. FileWatcher 生命周期需 App 手动管理

框架创建 `FileWatcher` 但不启动。App 必须访问 `_agent._file_watcher` 来管理 start/stop。启动/停止点与 FastAPI lifespan 耦合。

### B3. 模型 API key 管理完全在 App 层

`server.py` 中的 `_load_dotenv()`、`_save_api_key()`、`_api_key_cache`、`_verify_api_key()`、`POST /api/config/register-deepseek` 全部在 App 层实现。框架的 `ModelConfig.api_key_env` 只是声明字段名，实际从 `os.environ` 读取。框架没有 key 验证/持久化/刷新机制。

### B4. Session 管理在 App 层手动实现

`server.py` 中的 `_active_cancel_events`、`/api/sessions` 系列端点、`/ws` stub 在 App 层手动管理，框架不提供 session API。

### B5. Trace API 端点全在 App 层

`/api/trace`、`/api/traces/*`、`/api/trace/stream` 系列端点全部在 `server.py` 中实现，而非框架提供的可复用路由。只有 `FileTraceStore` 和 `UsageTracker` 在框架层。

### B6. SSE 事件翻译在 App 层

`_sse_chat()` 函数（139 行）手动将框架的 `AgentEvent` 翻译为前端 SSE 格式（`chunk`/`tool_call`/`tool_result`/`error`/`cancelled`/`done`）。这是框架事件到传输协议的映射——应由框架提供可复用的 SSE 适配器。

---

## 汇总

| 类别 | 数量 |
|------|------|
| 代码 Bug | 2 (CLI 路由, set_agent 重复) |
| 死代码/死配置 | 4 (ResourceCache, ToolConfig 字段, agent.yaml 字段, EventType 未用) |
| 未实现功能 | 2 (多 Agent, SandboxConfig) |
| 框架缺口 (app 填补) | 6 (持久化, FileWatcher 生命周期, key 管理, session, trace API, SSE) |
| 文档不一致 | 18 |
| 总数 | 32 |
