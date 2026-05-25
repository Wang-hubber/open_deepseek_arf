# Framework-App Fact-Check — 2026-05-25

对 `arf/` 框架层与 `app/arf_default_assistant/` 应用层的系统交叉对比，共发现 10 条不一致、死代码或功能缺口。

---

## 1. ResourceCache 定义但从未使用

**类型**：死代码 | **位置**：`arf/resources/cache.py`

`ResourceCache`（`_FrozenDict` + kernel/dynamic 分离）已完整实现，但**没有任何 Provider、Resolver 或 BaseAgent 代码导入它**。每个 Provider 在内部独立实现了相同的 kernel/dynamic dict 分离逻辑。

**建议**：重构 Provider 统一使用 `ResourceCache`，或删除 `cache.py` 避免维护歧义。

---

## 2. CLI 路由与 server 路由不匹配

**类型**：Bug | **位置**：`app/arf_default_assistant/cli.py`

| CLI 命令 | 请求路径 | 实际 server 路由 | 结果 |
|----------|---------|-----------------|------|
| `cmd_chat()` | `POST /chat` | `POST /api/chat` | 404 |
| `cmd_run()` | `GET /resources/skills` | `GET /api/resources/{res_type}` | 404 |
| `cmd_list()` | `GET /resources/{t}` | `GET /api/resources/{res_type}` | 404 |

三个 CLI 命令缺少 `/api/` 前缀。

---

## 3. agent.yaml 大量字段未被运行时读取

**类型**：死配置 | **位置**：`arf/agent/config.py`，`arf/core/config_base.py`

以下字段已被 Pydantic 解析，但**从未被任何框架或 app 代码读取**：

| 字段 | 所在类 |
|------|--------|
| `AgentConfig.role` | 已解析，从不读取 |
| `AgentConfig.task` | 已解析，从不读取 |
| `AgentConfig.agents` | 已解析，多 Agent 运行时未接线 |
| `AgentConfig.handover` | 已解析，handoff 规则未接线 |
| `AgentConfig.supervisor` | 已解析，调度器未接线 |
| `AdvancedConfig.guardrails` | BaseAgent 硬编码 DefaultGuardRunner |
| `AdvancedConfig.human_loop` | BaseAgent 始终使用 AlwaysAutoApprove |
| `AdvancedConfig.streaming` | 无运行时接线 |
| `AdvancedConfig.sandbox` | 无运行时接线 |
| `AdvancedConfig.tool_retrieval` | 引擎硬编码 `top_k=10` |
| `AdvancedConfig.reload` | FileWatcher 始终开启 |

---

## 4. lazy_persistence.py 填补框架缺口

**类型**：App 填补框架缺口 | **位置**：`app/arf_default_assistant/lazy_persistence.py`

框架 `StateStore` 协议的唯一内置实现 `InMemoryStateStore` 使用纯内存字典——进程重启即丢失。App 通过 `save_archive_async()` / `load_archive()` 手动将 `AgentState` 序列化到 `memory/archive.json`，在 lifespan 的 startup/shutdown 中恢复/归档。

这不是重叠（框架根本没有磁盘持久化），但表明 `StateStore` 协议缺少一个 `FileStateStore` 实现。

---

## 5. FileWatcher 生命周期由 App 管理，非框架

**类型**：责任泄漏 | **位置**：`arf/resources/file_watcher.py`，`app/arf_default_assistant/server.py:93-98`

框架在 `BaseAgent.__init__()` 中创建 `FileWatcher` 并注册 watch 回调，但**从未调用 `start()` 或 `stop()`**。App 通过直接访问 `_agent._file_watcher.start()` / `stop()` 管理生命周期。

**建议**：框架应在 GraphEngine 启动时自动启动 FileWatcher，或在 lifespan hook 中集成。

---

## 6. App 频繁访问框架私有内部

**类型**：脆弱耦合 | **位置**：`app/arf_default_assistant/server.py`

App 通过 `_agent._engine`、`_agent._file_watcher`、`_agent._resource_resolver` 直接访问框架的私有属性（`_` 前缀）。框架对这些属性的修改会直接破坏 App。

---

## 7. 3 个工具在文件系统上但不在 agent.yaml 中

**类型**：孤儿资源 | **位置**：`app/arf_default_assistant/tools/`

| 工具 | 位置 | agent.yaml 中？ |
|------|------|----------------|
| `manage_hooks` | `tools/manage_hooks/` | 否 |
| `text_to_upper` | `tools/text_to_upper/` | 否 |
| `resource_scaffold` | `tools/resource_scaffold/` | 仅在 sys_agent 的 tools 中 |

框架的 ToolProvider 会扫描文件系统找到所有 15 个工具并传给 LLM，但 agent.yaml 只声明了 12 个——system prompt 中不会列出未声明的工具。

---

## 8. set_agent() 重复调用

**类型**：Minor bug | **位置**：`app/arf_default_assistant/server.py:70`

```python
set_agent(_agent)
set_agent(_agent)  # 重复
```

功能上无害（第二次调用覆盖第一次），但属于无意义的重复。

---

## 9. ToolConfig 多个字段已解析但未强制执行

**类型**：死字段 | **位置**：`arf/core/config_base.py` ToolConfig

| 字段 | 运行时行为 |
|------|----------|
| `provider` | 始终为 `static_yaml`，未切换 |
| `backend` | 始终为 `function`，未切换 |
| `execution.sandbox` | 已解析，未执行 |
| `execution.timeout` | 已解析，未在 FunctionBackend 中使用 |
| `source` | 从不读取 |

---

## 10. 多 Agent 运行时未接线

**类型**：未实现 | **位置**：`arf/agent/config.py` AgentConfig.agents

`agent.yaml` 中声明了完整的 `sys_agent` 定义（models、tools、skills、system_prompt），但框架没有 Dispatcher 或 Supervisor 调度多个 Agent 实例。当前通过 app 层的 `handoff_to_sys` 工具 + `_agent_mode` 参数手动实现基本的 Agent 分离。
