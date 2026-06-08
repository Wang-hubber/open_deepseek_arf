# Plugin 体系

> **Plugin ≠ Tool.** Tool 是 Agent 调用的 MCP 资源。Plugin 是挂载在 Hook 点上的行为——在框架生命周期事件时自动触发，如同生物的反射弧。
>
> 框架无 Plugin 也能运行完整的 Agent Loop。Plugin 添加预设或可定制的能力。当 Plugin 需要智能（记忆提取、上下文摘要），它通过标准 `_call_model` 接口调用模型——**智能来自模型，而非 Plugin**。

---

## 一、架构机制

### 自动发现

`PluginLoader` 扫描 `arf/plugins/{name}/` 目录，每个子目录即一个 Plugin。无需手动注册。发现规则：

1. 查找 `plugin.yaml`（fallback `config.yaml`）获取元数据和配置
2. 查找 `plugin.py`，动态导入，定位以 `Plugin` 结尾的类（实现了 `PluginProtocol`）
3. 若只有 `plugin.yaml` 而无 `plugin.py`，该 Plugin 为 Tool/Skill 提供者（如 `file_tools`、`planner`），不参与生命周期 Hook

### 注册与加载

`PluginProvider`（`arf/resources/providers/plugin_provider.py`）统一管理 Plugin 的发现和加载。`BaseAgent` 初始化时自动调用 `load_all_plugins()`，按 `enabled: true/false` 过滤。

### Hook 挂载

每个 Plugin 通过 `hooks` 字段声明挂载点：

```yaml
hooks:
  round_end: blocking
  session_start: side
```

- **blocking**：引擎等待 Hook 执行完毕才继续。用于状态修改、安全检查、审批。
- **side**：引擎不等待，异步执行。用于日志、指标、记忆提取等不阻塞主循环的操作。

9 个 Hook 注入点构成控制平面的挂载面：

| Hook | 触发时机 | 典型 Plugin |
|------|---------|------------|
| `session_start` | 会话初始化 | checkpoint、validate_messages |
| `round_start` | 每轮开始 | strategy、memory_retrieval、checkpoint |
| `turn_start` | 每次迭代开始 | cancellation、metrics |
| `pre_dispatch` | 调度前（模型调用或工具执行前） | session_mode、tool_guard、approval、validate_messages |
| `post_dispatch` | 调度后 | sandbox_persist、metrics |
| `post_permission` | 权限检查后 | approval |
| `pre_tool_exec` | 工具执行前 | — |
| `post_tool_exec` | 工具执行后 | — |
| `turn_end` | 迭代结束 | metrics |
| `round_end` | 轮次结束 | compaction、checkpoint、memory、todo、undo |
| `session_end` | 会话结束 | checkpoint、memory_extraction、metrics |
| `sandbox_persist` | 沙箱持久化 | undo |
| `error` | 异常发生时 | error_handler |

---

## 二、内置 Plugin

### 核心安全

| Plugin | Hook | 说明 |
|--------|------|------|
| `tool_guard` | `pre_dispatch` | 双层防护：`PermissionRegistry` deny→ask→allow 检查 + `PathSandbox` 路径遍历扫描 |
| `approval` | `pre_dispatch`, `post_permission` | 人机审批通道。`ask_list` 中的工具需人工确认，默认 60s 超时 |
| `session_mode` | `pre_dispatch` | 解析全局 `session_mode`（auto/ask/plan）与 per-agent policy，决定有效模式 |
| `validate_messages` | `pre_dispatch` | 模型调用前校验消息列表（首条必须 user、role 合法） |
| `error_handler` | `error` | 五动作恢复路由：fallback（compact/repair）、retry、skip、abort |

### 记忆与上下文

| Plugin | Hook | 说明 |
|--------|------|------|
| `memory` | `round_end`, `session_end` | 定期长期记忆提取。每 N 轮将对话写入临时 JSON，子进程调用模型提取事实，原子写入 `memory.md` |
| `memory_extraction` | `session_end` | 会话结束时写入长期记忆（需注入 writer） |
| `memory_retrieval` | `round_start` | 每轮开始时检索相关记忆注入 `context_summary`（需注入 retriever） |
| `compaction` | `round_end` | Token 感知上下文压缩。达到阈值时保留最近 N 条消息，旧轮次 LLM 摘要。冷却 2 轮 |
| `todo` | `round_end` | 周期性提醒 Agent 同步 TODO 列表 |

### 状态与回滚

| Plugin | Hook | 说明 |
|--------|------|------|
| `checkpoint` | `round_start`, `round_end` | Round 级状态快照。委托 `RoundManager` 进行 begin/close round。支持 undo |
| `undo` | `round_end`, `sandbox_persist` | 声明 undo 的 Hook 挂载点（实际快照由 checkpoint 完成） |
| `cancellation` | `turn_start` | 检查外部注入的 `cancel_event`，支持会话中断 |

### 可观测

| Plugin | Hook | 说明 |
|--------|------|------|
| `trace` | 全部 9 个 Hook（side） | 跨切面 JSONL 事件记录。每个 Hook 调用写入 `{trace_dir}/{session_id}.jsonl` |
| `metrics` | `turn_start`, `post_dispatch`（side） | 采集 turn 级别耗时指标 |
| `eval` | 离线 | 回放 trace，计算指标，diff 报告。通过 `EvalRunner` 执行 |

### 调度与策略

| Plugin | Hook | 说明 |
|--------|------|------|
| `strategy` | `round_start` | 选择 Loop 策略（默认 `"react"`），可注册自定义策略 |
| `planner` | Tool 提供者 | 提供 `planner` 工具和 `plan_execute.yaml` Skill |

### 工具型 Plugin

以下 Plugin 无 `plugin.py`，仅提供 Tool/Skill 定义：

| Plugin | 提供内容 |
|--------|---------|
| `file_tools` | glob、grep、read、subagent 工具 + `file_tools.yaml` Skill |
| `subagent` | `subagent` 工具（max_turns: 10, timeout: 120） |

---

## 三、Plugin 开发

### 最小 Plugin

```
arf/plugins/my_plugin/
├── plugin.yaml     # name, hooks, config
└── plugin.py       # PluginProtocol 实现
```

**plugin.yaml**：

```yaml
name: my_plugin
version: "1.0"
enabled: true
hooks:
  round_end: side
config:
  threshold: 0.5
```

**plugin.py**：

```python
from arf.core.protocols.plugin import PluginProtocol

class MyPlugin(PluginProtocol):
    name = "my_plugin"
    hooks = {"round_end": "side"}

    def __init__(self, config: dict | None = None):
        self.config = config or {}

    async def on_hook(self, hook_name: str, context: dict) -> None:
        if hook_name == "round_end":
            # Plugin logic here
            ...
```

### 关键约束

- Plugin 不应做认知判断。需要"理解"时，调用 `context["call_model"]()` 让模型处理
- blocking Hook 抛异常会中止当前 turn。side Hook 异常被静默吞掉
- 配置文件中的参数通过 `config` dict 自动注入构造函数

---

## 四、当前状态

| 状态 | 数量 | Plugin |
|------|------|--------|
| **完整实现** | 18 | approval, cancellation, checkpoint, compaction, error_handler, eval, memory, memory_extraction, memory_retrieval, metrics, session_mode, strategy, todo, tool_guard, trace, undo, validate_messages, memory |
| **工具/Skill 提供者** | 3 | file_tools, planner, subagent |
| **Stub（空实现）** | 2 | sandbox_persist, secure_archive |

---

## 五、演进方向

- **参数化记忆插件**：将 `memory_extraction`/`memory_retrieval` 从文件读写演进到 LoRA 权重更新（见 [演进方向](../paper/reading_summary/parameterized-memory.md)）
- **异步 Plugin 执行**：当前 blocking Hook 是同步阻塞的。可通过双缓冲机制让 side Hook 在后台更高效地执行 SFT 更新
- **sandbox_persist 实现**：工具执行后的沙箱差异持久化，当前为空 stub
