# Plugin 体系

> **Plugin ≠ Tool.** Tool 是 Agent 调用的 MCP 资源。Plugin 是挂载在 Hook 点上的行为——在框架生命周期事件时自动触发。
>
> 框架无 Plugin 也能运行完整的 Agent Loop。Plugin 添加可插拔的能力。

---

## 当前 Plugin（10 个）

| Plugin | 类型 | Hook 挂载点 | 说明 |
|--------|------|------------|------|
| `tool_guard` | blocking | `pre_action` | 双层安全：PermissionRegistry deny→ask→allow + PathSandbox 路径遍历拦截 |
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

### 控制平面集成

部分能力已从 Plugin 吸收到 `ControlPlane` 内建：
- **session_mode** — `SessionModeManager` 内建于 ControlPlane，`set_session_mode()` 发射 `session_policy_switch` 事件
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
