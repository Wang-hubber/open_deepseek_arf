# Plugin 体系

> **Plugin ≠ Tool.** Tool 是 Agent 调用的 MCP 资源。Plugin 是挂载在 Hook 点上的行为——在框架生命周期事件时自动触发。
>
> 框架无 Plugin 也能运行完整的 Agent Loop。Plugin 添加可插拔的能力。

---

## 当前 Plugin（10 个）

| Plugin | 类型 | Hook 挂载点 | 说明 |
|--------|------|------------|------|
| `tool_guard` | blocking | `pre_action` | 模式感知安全：auto 放行、plan 只读（readOnlyHint）、ask 列表匹配 + PathSandbox |
| `approval` | blocking | `pre_action` | 人机审批。ask_list 中的工具需人工确认，60s 超时，支持内联 chat |
| `error_handler` | blocking | `error` | 五动作恢复路由：fallback(compact/repair)、retry(指数退避)、skip、abort |
| `compaction` | blocking | `round_end` | Token 感知上下文压缩。达阈值时 LLM 摘要旧轮次，带冷却机制 |
| `memory` | side | `round_end`, `session_end` | 长期记忆提取。子进程调用模型，原子写入 memory.md |
| `trace` | side | 全部 9 个 hook | 跨切面 JSONL 事件记录。内容寻址配置快照 |
| `undo` | blocking | `round_start`, `round_end` | Round 级 checkpoint + 回滚。状态深拷贝 + 工作区文件快照 |
| `plan_solve` | blocking | `pre_action`, `round_start` | DAG 依赖校验 + 断点检测。提供 plan_create/dispatch/summarize/status 工具族 |
| `filesystem` | 工具/Skill 提供者 | 无（纯工具插件） | 14 个 MCP 对齐文件操作工具（read/write/edit/search/list/delete/move） |
| `eval` | 离线 | 无（显式调用） | 回放 trace、6 种 LLM 指标、diff 报告 |

---

## 9 个 Hook 注入点

| Hook | 触发时机 | 挂载 Plugin |
|------|---------|------------|
| `session_start` | 会话初始化 | trace |
| `round_start` | 每轮开始 | undo（begin_round）、plan_solve、trace |
| `turn_start` | 每次迭代开始 | trace |
| `pre_action` | 模型调用 / 工具执行前 | tool_guard、approval、plan_solve、trace |
| `post_action` | 模型调用 / 工具执行后 | trace |
| `turn_end` | 迭代结束 | trace |
| `round_end` | 轮次结束 | compaction、memory、undo（close_round）、trace |
| `session_end` | 会话结束 | memory、trace |
| `error` | 异常发生时 | error_handler、trace |

**blocking** — 引擎等待执行完毕，异常传播到 error_handler。用于安全、状态修改。
**side** — 引擎不等待，异常被静默吞掉。用于日志、指标、记忆提取。

---

## 架构

### 自动发现

`PluginProvider` 扫描 `arf/plugins/{name}/`，每个子目录即一个 Plugin：

1. 查找 `plugin.yaml` 获取元数据和配置
2. 查找 `plugin.py`，动态导入，定位 `*Plugin` 类（实现 `PluginProtocol`）
3. 若只有 `tools/` 和 `skills/` 而无 `plugin.py`，该 Plugin 为工具/Skill 提供者（如 `filesystem`）

### 注册与加载

`BaseAgent.build_engine()` 自动通过 `PluginProvider` 发现和加载 Plugin。`AgentConfig.plugins: [...]` 白名单控制激活。Plugin 按 hook mode 分为 blocking / side 两组，分别交给 `InProcessHookRunner` 和 `SubprocessHookRunner`。

### 配置

所有 Plugin 统一通过 `AgentConfig.plugins_config` 配置，`PluginProvider` 自动合并 `plugin.yaml` 默认值。工具名使用裸名，框架在运行时通过 `set_name_resolver()` 注入解析器。

```yaml
plugins:
  - tool_guard
  - approval
  - compaction

plugins_config:
  tool_guard:
    deny: [rm, bash]
    ask: [write_file, delete_file]
    sandbox_check: true
  compaction:
    threshold: 0.8
    keep_count: 10
```

不再有 `_SPECIAL_PLUGINS` — 所有 Plugin 待遇一致。

### 数据路径与操作边界

`data_path` 和 `allow_paths` 是 `AgentConfig` 的顶层字段，控制数据存储位置和文件操作范围。

```yaml
# agent.yaml
data_path: ./runtime          # 运行时数据（state/trace/memory），默认 = app root
allow_paths:                   # 允许操作的文件路径，默认 = [data_path]
  - /project/root
  - /shared/workspaces
```

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `data_path` | app root | 运行时数据根目录。初始化时自动创建 state/traces/memory/files 子目录 |
| `allow_paths` | `[data_path]` | 文件操作沙箱边界。`DirectoryBoundary` 多路径支持，`PathCheckToolGuard` 只放行 resolve 后落在列表内的路径 |

不传则回退到 `AppContext.root`，完全向后兼容。典型场景：

```yaml
data_path: ./builtin               # 运行时数据隔离
allow_paths: [.]                   # 操作范围覆盖项目根
```

#### 路径自动注入

框架启动时将 computed path 注入对应 Plugin，确保一致性：

| Plugin | 注入项 | 来源 |
|--------|--------|------|
| `trace` | `set_trace_dir()` | `{data_path}/traces/` |
| `compaction` | `set_model_context_window()` | `ModelConfig.context_window` |
| `undo` | `set_undo_plugin()` | ControlPlane 引用 |

#### Eval 产物

Eval 是永久产物，不属于运行时数据，默认路径独立：

```
eval/                          # 可通过 plugins_config.eval.eval_dir 覆盖
├── snapshots/
│   └── a1b2c3d4.xml           # 配置快照（内容寻址，同配置复用）
├── my_benchmark.json           # benchmark 定义
└── report_my_benchmark.json    # eval report（含 snapshot_hash）
```

### 控制平面集成

部分能力已从 Plugin 吸收到 `ControlPlane` 内建：
- **session_mode** — `SessionModeManager` 内建于 ControlPlane，`set_session_mode()` 发射 `session_policy_switch` 事件。`effective_mode` 在 `pre_action` 前注入 `ctx.hook_data`
- **validate_messages** — `ControlPlane._validate_messages()` 在每次 call_model 前校验消息合约

---

## Plugin 开发

### 最小 Plugin

```
arf/plugins/my_plugin/
├── plugin.yaml     # name, hooks, config
└── plugin.py       # PluginProtocol 实现
```

**plugin.yaml**:

```yaml
name: my_plugin
enabled: true
hooks:
  - round_end
config:
  threshold: 0.5
```

**plugin.py**:

```python
class MyPlugin:
    def __init__(self, config: dict | None = None):
        self._cfg = config or {}

    @property
    def name(self) -> str:
        return "my_plugin"

    @property
    def hooks(self) -> dict[str, str]:
        return {"round_end": "side"}

    async def on_hook(self, hook_name: str, ctx: PluginContext) -> None:
        pass
```

### 关键约束

- Plugin 不应做认知判断。需要智能时通过 `_call_model` 接口调用模型
- blocking Hook 抛异常会中止当前 turn，路由到 error_handler
- side Hook 异常静默吞掉，仅日志记录
- 工具型 Plugin 不需要 `plugin.py`，只需 `tools/` + `skills/` 目录
- **每个 Tool 的 `tool.yaml` 必须声明 `annotations.readOnlyHint`**（`true`/`false`）。plan 模式下未声明的工具会被拒绝
