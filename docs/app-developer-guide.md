# ARF App 开发者指南

> 框架 v0.2 配置/API 大全。覆盖 A2A Plugin、Skill 系统、HITL 中断、冲突检测——App 开发者只需配置 + 监听事件，框架接管全部 mechanism。

## 1. 快速入门

```yaml
# agent.yaml
name: my-agent
description: 我的 Agent
model: deepseek-v4
plugins:
  - a2a           # 开启 A2A（子 agent 委派 + HITL + 冲突检测）
  - tool_guard
  - approval
```

App 侧只需三件事：
1. **配置 agent.yaml** — 启用插件、定义子 agent
2. **监听 EventBus 事件** — `task_completed`、`human_decision_required`
3. **处理 HITL 恢复** — 人类回答 → 注入子 agent state → `astream(child_session_id)`

---

## 2. 消息结构（v0.2）

```
messages = [
  {role: "system", content: "<Structured System Prompt>"},   # [0]: Agent 身份+硬规则
  {role: "system", content: "<system-reminder>"},             # [1]: Skills + Tools + Memory
  {role: "user", content: "..."},
  ...
]
```

**迁移影响**：agent.yaml 的 `prefix` / `suffix` 字段已废弃。System Prompt 现在是固定模板，不再从 YAML 拼装。`system-reminder` 由框架自动构建。

---

## 3. A2A Plugin

### 3.1 启用

```yaml
# agent.yaml
plugins:
  - a2a

plugins_config:
  a2a:
    max_concurrent_tasks: 3     # 最大并发子 agent 数
    max_task_timeout: 600       # 子 agent 硬超时（秒）
```

启用后自动获得 4 个 tool：`delegate_task`、`queue_status`、`await_task`、`cancel_task`。

### 3.2 Tool 一览

| Tool | 参数 | 返回 | 说明 |
|------|------|------|------|
| `delegate_task` | `task` (str), `agent` (str=""), `timeout` (int=0), `context` (dict) | `{dispatched/queued, task_id}` | 派发子 agent。agent 空=临时继承 |
| `queue_status` | 无 | `{running, queued, completed}` | 查询任务状态 |
| `await_task` | `task_id`, `timeout` (int=0) | `{result}` | 阻塞等待任务完成（非消费性读取） |
| `cancel_task` | `task_id` | `{cancelled}` | 取消排队中的任务 |

### 3.3 事件

App 需监听 EventBus：

| 事件 | 触发时机 | App 动作 |
|------|---------|---------|
| `task_completed` | 子 agent 完成 | 展示通知；如果父 agent 在等人类输入，调 `chat()` 发起新 round |
| `human_decision_required` | 子 agent 调了 `ask_user` | 展示 UI 弹窗；收集人类回答；恢复子 agent（见 §4） |

`task_completed` 事件结构：
```json
{
  "type": "task_completed",
  "parent_session_id": "...",
  "child_session_id": "...",
  "task_id": "task_3",
  "result": {"content": "...", "turn_count": 5, "file_changes": {...}}
}
```

### 3.4 子 Agent 配置

子 agent 是普通 agent.yaml：

```yaml
# agents/code-reviewer/agent.yaml
name: code-reviewer
description: 代码审查专家
model: deepseek-v4
plugins:
  - tool_guard    # 子 agent 不需要 a2a plugin（自动有 _tool_blacklist）
```

子 agent 自动不能调 `delegate_task`（深度限制 = 2），但可以调 `ask_user`。

---

## 4. HITL 中断（Human-in-the-Loop）

### 4.1 Flow

```
子 agent 调 ask_user("选方案?", ["A","B"])
  → 子 agent round 自然结束
  → A2APlugin emit "human_decision_required" 事件
  → App 展示 UI，收人类回答
  → App 调用恢复:
      state_store.get(child_session_id)
      → messages.append({role:"user", content:"[Human] B"})
      → engine.astream(child_session_id)
  → 子 agent 下一轮继续
```

### 4.2 App 恢复代码

```python
# 监听事件
async def on_human_decision_required(event: AgentEvent):
    child_sid = event.data["child_session_id"]
    question = event.data["question"]
    options = event.data["options"]

    # 展示 UI，拿到回答
    answer = await show_ui_dialog(question, options)

    # 恢复子 agent
    state = await state_store.get(child_sid)
    state["messages"].append({
        "role": "user",
        "content": f"[Human] {answer}"
    })
    await state_store.put(child_sid, state)

    # 继续执行（不创建新 session）
    async for event in engine.astream(state):
        yield event
```

### 4.3 冲突检测与解决

当两个子 agent 修改同一文件时，第二个的变更暂存到磁盘：

```
data/{parent_session_id}/conflicts/{task_id}/
├── manifest.json
└── files/...
```

父 agent 消息中看到冲突警告，可调 `resolve_conflict(task_id)` 应用或 `cancel_held(task_id)` 丢弃。

---

## 5. Skill 系统

### 5.1 Skill 目录结构

```
skills/                        # 项目级 skill
  └── react-component/
      ├── skill.yaml           # {name, description, tools_sequence?}
      └── skill.md             # 领域知识正文（Markdown）

arf/plugins/{name}/skills/     # 插件级 skill（同名时覆盖项目级）
  └── ...
```

### 5.2 skill.yaml

```yaml
name: react-component
description: 创建符合项目规范的 React 组件
tools_sequence:          # 可选
  - plan_create
  - plan_dispatch
  - plan_summarize
```

### 5.3 skill.md

自由格式 Markdown，框架不做解析，原样返回给模型。写领域知识、编码规范、最佳实践。

### 5.4 使用方式

1. 启动时自动索引，索引出现在 `system-reminder`（messages[1]）
2. 模型调 `use_skill("react-component")` → 返回完整 body → 进入消息流
3. 无需手动注册，无需重启——新增/修改 skill 文件后 FileWatcher 自动重载

### 5.5 Agent 配置

```yaml
# agent.yaml
skills:
  auto_index: true      # 默认 true，自动注入索引到 system-reminder
```

---

## 6. 事件总线 API

App 应该监听的事件：

```python
from arf.core.events import AgentEvent

# 在 BaseAgent 初始化后获取 event_bus
event_bus = agent.event_bus

# 事件类型一览（v0.2 新增）
EVENTS_TO_LISTEN = [
    "task_completed",           # 子 agent 完成
    "human_decision_required",  # 子 agent 需要人类决策
    "session_policy_switch",    # 权限模式切换
    "gate_exceeded",            # Turn/token 预算超限
    "error",                    # 错误事件
]
```

---

## 7. 配置速查

```yaml
# agent.yaml 完整配置（v0.2）
name: my-agent
description: 我的 Agent
model: deepseek-v4

plugins:
  - a2a
  - tool_guard
  - approval

plugins_config:
  a2a:
    max_concurrent_tasks: 3
    max_task_timeout: 600
  approval:
    ask: []
    allow: []
    deny: []

skills:
  auto_index: true

max_turns: 50
workspace_dir: "./workspace"
data_dir: "./data"
```

---

## 8. 迁移检查清单（v0.1 → v0.2）

- [ ] agent.yaml 的 `prefix` / `suffix` 移除（改为框架固定模板）
- [ ] 如需 A2A，`plugins` 加 `"a2a"`
- [ ] 监听 `task_completed` 事件替代轮询
- [ ] 监听 `human_decision_required` 事件实现 HITL UI
- [ ] 领域知识写入 `skills/{name}/skill.md`
- [ ] system prompt 用 `$INVENTORY` 和 `$MEMORY` 占位符（框架填充）
