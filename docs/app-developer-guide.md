# ARF App 开发者参考 (v0.2)

> 框架暴露给 App 层的完整配置/API 参考。框架提供 mechanism，App 通过 configuration 决定 policy。

---

## 目录

1. [Agent 配置 (agent.yaml)](#1-agent-配置-agentyaml)
2. [消息结构](#2-消息结构)
3. [Plugin 系统](#3-plugin-系统)
4. [Tool 系统](#4-tool-系统)
5. [Skill 系统](#5-skill-系统)
6. [Hook 生命周期](#6-hook-生命周期)
7. [EventBus 事件](#7-eventbus-事件)
8. [BaseAgent API](#8-baseagent-api)
9. [内置 Tool 一览](#9-内置-tool-一览)
10. [A2A Plugin](#10-a2a-plugin)
11. [HITL 中断](#11-hitl-中断)
12. [目录约定](#12-目录约定)
13. [迁移 v0.1 → v0.2](#13-迁移-v01--v02)

---

## 1. Agent 配置 (agent.yaml)

### 1.1 完整配置

```yaml
# agent.yaml
schema_version: "1.0"
session_mode: ask                 # auto | ask | plan

# --- Agent 身份 ---
name: my-agent                    # 必填，唯一标识
description: 我的 AI 助手

# --- 数据路径 ---
data_path: "./data"               # state/trace/memory/conflicts 根目录
allow_paths: []                   # 额外允许文件操作的路径（空=同 data_path）

# --- 模型 ---
model_defs:                       # 模型定义（框架统一管理）
  - model: deepseek-chat          # DeepSeek-V4 系列
    api_base: https://api.deepseek.com/v1
    api_key_env: DEEPSEEK_API_KEY
  - model: deepseek-chat-pro      # 高能力，适合复杂推理
    api_base: https://api.deepseek.com/v1
    api_key_env: DEEPSEEK_API_KEY
  - model: deepseek-chat-flash    # 低延迟，适合简单任务
    api_base: https://api.deepseek.com/v1
    api_key_env: DEEPSEEK_API_KEY

agent_models:                     # 本 agent 可用的模型列表（可引用 model_defs 中任意模型）
  - model: deepseek-chat
  - model: deepseek-chat-pro
  - model: deepseek-chat-flash

# --- 插件 ---
plugins:                          # 启用的插件名称列表
  - a2a
  - approval
  - tool_guard
  - memory
  - error_handler

plugins_config:                   # 每个插件的配置
  a2a:
    max_concurrent_tasks: 3
    max_task_timeout: 600
  approval:
    ask: []
    allow: ["read_file", "search_content"]
    deny: []
  memory:
    workspace: "./data/memory"
    resident_file: "memory.md"
    max_size_kb: 300

# --- 高级配置 ---
advanced:
  max_turns: 50                   # 每 round 最大 turn 数
  max_tokens: null                # session token 上限（null=无限制）
  tool_timeout: 300.0             # 单个 tool 执行超时（秒）
  call_timeout: 120.0             # 模型调用超时（秒）
  session_timeout: null           # session 总超时（null=无限制）
  max_undo_depth: 3               # Undo 最大步数
  recovery:
    max_continuation: 3
    max_compaction: 3
    backoff_base: 1.0
    backoff_max: 30.0
```

### 1.2 子 Agent 配置

与主 agent 相同格式，放在独立目录：

```yaml
# agents/code-reviewer/agent.yaml
name: code-reviewer
description: 代码审查专家
model: deepseek-chat-pro    # 可引用主 agent model_defs 中任意模型
plugins:
  - tool_guard              # 子 agent 不需要 a2a plugin
```

---

## 2. 消息结构

### 2.1 多层 System Message (v0.2)

框架按顺序注入 system 消息——每类上下文独立一条：

```
messages = [
  {role: "system", content: "<System Prompt>"},     # [0]: agent.yaml 定义的身份+规则
  {role: "system", content: "## Available Skills\n..."}, # [1]: Skills 索引
  {role: "system", content: "## Available Tools\n..."},  # [2]: Tool 清单
  {role: "system", content: "## Memory\n..."},            # [3]: 常驻记忆
  {role: "user", content: "..."},
  ...
]
```

| 层 | 来源 | 内容 | 变动频率 |
|---|---|---|---|
| **System Prompt** | `agent.yaml` (DefaultSystemPromptProvider) | Agent 身份、核心规则 | 固定 |
| **Skills** | `skills/` + `plugins/*/skills/` 索引 | Skill name + description | 每 session |
| **Tools** | MCP inventory | Tool 清单 | 每 session |
| **Memory** | `data/memory/memory.md` | 常驻记忆 | 每 session |

### 2.2 System Prompt

System Prompt 来自 `agent.yaml`，由 `DefaultSystemPromptProvider` 构建：

```yaml
# agent.yaml
system_prompt:
  prefix:
    role: "你是资深软件工程师"
    critical_rules: "始终先理解需求再动手"
  suffix: |
    ## 注意事项
    $INVENTORY
    $MEMORY
```

`$INVENTORY` 和 `$MEMORY` 占位符保留向后兼容——如果 suffix 中包含则框架填充。建议不再使用这两个占位符，让框架以独立 system 消息注入 skills/tools/memory（更清晰的分层）。

---

## 3. Plugin 系统

### 3.1 Plugin 目录结构

```
arf/plugins/{plugin_name}/
├── plugin.yaml          # 元数据 + 默认配置
├── plugin.py            # Plugin 类（hook 处理逻辑）
├── tools/               # Plugin 提供的工具
│   └── {tool_name}/
│       ├── tool.yaml    # 工具定义
│       └── function.py  # async def execute(...) -> dict
└── skills/              # Plugin 提供的技能
    └── {skill_name}/
        ├── skill.yaml
        └── skill.md
```

### 3.2 plugin.yaml

```yaml
name: my_plugin
description: 我的插件
config:                    # 默认配置（可被 agent.yaml plugins_config 覆盖）
  option1: value1
  option2: 10
```

### 3.3 Plugin 类接口

```python
class MyPlugin:
    @property
    def name(self) -> str:       # 必填，与 plugin.yaml 一致
        return "my_plugin"

    @property
    def hooks(self) -> dict[str, str]:  # 订阅的 hook 事件 → 模式
        return {
            "pre_action": "blocking",
            "round_end": "side",
        }

    async def on_hook(self, hook_name: str, ctx: PluginContext) -> None:
        """Hook 回调入口。ctx 提供 session_id, state, messages, event_bus 等。"""
        ...
```

### 3.4 Hook 模式

| 模式 | 说明 |
|------|------|
| `"blocking"` | 阻塞执行，engine 等待 hook 返回。可修改 state、emit 事件 |
| `"side"` | 旁路执行，engine 不等待。用于 trace、logging 等 |

### 3.5 启用 Plugin

```yaml
# agent.yaml
plugins:
  - my_plugin          # PluginProvider 自动从 arf/plugins/my_plugin/ 加载

plugins_config:
  my_plugin:
    option1: custom_value    # 覆盖 plugin.yaml 默认值
```

工具自动以 `{plugin_name}__` 前缀注册（如 `my_plugin__my_tool`）。

---

## 4. Tool 系统

### 4.1 Tool 目录结构

```
tools/                         # App 层工具（user__ 命名空间）
└── {tool_name}/
    ├── tool.yaml              # 工具定义
    └── function.py            # async def execute(...) -> dict

arf/plugins/{name}/tools/      # Plugin 工具（{plugin}__ 命名空间）
└── ...

arf/skills/*_tool.py           # 框架内核工具（无前缀）
```

### 4.2 tool.yaml

```yaml
name: my_tool
description: 工具描述（模型可见，必填）
parameters:
  type: object
  properties:
    file_path:
      type: string
      description: 文件路径
      format: path            # 标记为路径参数，框架做安全检查
    query:
      type: string
      description: 搜索关键词
  required:
    - file_path
execution:
  sandbox: none               # none | inherit | strict
  timeout: 30s                # 执行超时
activation: kernel            # kernel | dynamic | on-demand
```

### 4.3 function.py

```python
async def execute(
    file_path: str,        # tool.yaml 定义的参数
    query: str,
    _engine=None,          # 框架注入：ControlPlane 引用
    _workspace: str = "",  # 框架注入：workspace 根目录
    _state_store=None,     # 框架注入：StateStore
    session_id: str = "",  # 框架注入：当前 session_id
    **kwargs,
) -> dict:
    """返回 {ok: True, ...} 或 {ok: False, error: "..."}"""
    return {"ok": True, "result": "..."}
```

框架注入参数（`_` 前缀）由 `ConcurrentToolExecutor` 自动传入。

### 4.4 Tool 命名空间

| 前缀 | 来源 | 示例 |
|------|------|------|
| `user__` | `tools/` 目录 | `user__my_tool` |
| `{plugin}__` | `arf/plugins/{name}/tools/` | `a2a__delegate_task` |
| 无前缀 | 内核工具（ControlPlane 注册） | `use_skill`, `ask_user` |

---

## 5. Skill 系统

### 5.1 Skill 目录结构

```
skills/                          # 项目级（裸名，如 "react-component"）
└── {skill_name}/
    ├── skill.yaml               # 元数据：name, description, tools_sequence?
    └── skill.md                 # 领域知识正文（Markdown，自由格式）

arf/plugins/{name}/skills/       # 插件级（namespaced: {plugin}__{skill_name}）
└── ...                          # 遵循 MCP 命名约定，与 Tool 一致
```

### 5.2 skill.yaml

```yaml
name: react-component
description: 创建符合项目规范的 React 组件
tools_sequence:          # 可选。有则激活 SkillPipeline 时序约束
  - plan_create
  - plan_dispatch
  - plan_summarize
```

### 5.3 skill.md

自由格式 Markdown。框架不做解析，`use_skill` 调用时原样返回给模型。

```markdown
# React 组件规范

## 状态管理
本项目使用 Zustand，禁止 useState 跨组件传递。

## 文件结构
src/components/{name}/index.tsx

## 样式
使用 CSS Modules。
```

### 5.4 生命周期

```
session_start → SkillIndex 扫描 skills/ + plugins/*/skills/
  → 构建索引 → 注入 system-reminder (messages[1]) 的 "## Available Skills" 段

模型调 use_skill("react-component")
  → 读取 skill.md → 返回 {name, description, body, tools_sequence}
  → tool result 进入消息流，模型自然看到正文
```

### 5.5 配置

```yaml
# agent.yaml
skills:
  auto_index: true     # 默认 true
```

---

## 6. Hook 生命周期

### 6.1 事件时序

```
session_start  (side)     ← 会话开始（仅首次）
  └─ round_start  (side)  ← 每轮 chat() 入口
       └─ turn_start  (side)  ← 每轮 model call 前
            ├─ pre_action  (blocking)  ← model call 前，可注入额外指令
            ├─ [model call]
            ├─ pre_action  (blocking)  ← tool 执行前，可拦截
            ├─ [execute tools]
            ├─ tool_output  (blocking) ← tool 执行后，可修改结果
            ├─ post_action  (side)     ← turn 结束，trace 落盘
            └─ turn_end  (side)
       └─ round_end  (blocking)  ← chat() 结束，compaction 在此触发
  └─ session_end  (side)  ← 会话结束
```

### 6.2 Hook 订阅

Plugin 在 `hooks` 属性中声明订阅：

```python
@property
def hooks(self) -> dict[str, str]:
    return {
        "pre_action": "blocking",    # 阻塞：可修改 state、emit 事件
        "round_end": "side",         # 旁路：trace、logging
    }
```

---

## 7. EventBus 事件

### 7.1 所有事件类型

| 事件 | 数据 | 触发时机 |
|------|------|---------|
| `session_start` | `{session_id}` | 会话开始 |
| `session_end` | `{session_id, reason}` | 会话结束（completed/cancelled/aborted/error） |
| `user_input` | `{content}` | 用户输入 |
| `thinking_delta` | `{content, reasoning}` | 模型流式输出 |
| `model_call_start` | `{model, turn}` | 模型调用开始 |
| `model_call_end` | `{model, turn, content, usage}` | 模型调用结束 |
| `tool_call_start` | `{tool_name, id, arguments}` | 工具调用开始 |
| `tool_call_end` | `{tool_name, id, success, result}` | 工具调用结束 |
| `gate_exceeded` | `{reason, current_turn}` | Turn/token 超限 |
| `session_policy_switch` | `{mode, previous_mode}` | 会话模式切换 |
| **`task_completed`** (v0.2) | `{parent_session_id, child_session_id, task_id, result}` | 子 agent 完成 |
| **`human_decision_required`** (v0.2) | `{parent_sid, child_sid, task_id, agent_name, question, options}` | 子 agent 需要人类决策 |
| `error` | `{phase, detail, exception, message}` | 错误 |

### 7.2 App 监听事件

```python
from arf.core.events import AgentEvent

event_bus = agent.event_bus  # 从 BaseAgent 获取

# App 应监听的事件
async def on_event(event: AgentEvent):
    if event.type == "task_completed":
        # 展示通知；如果父 agent 在等人类，自动发起新 round
        ...
    elif event.type == "human_decision_required":
        # 展示 UI 弹窗，收集人类回答
        ...
```

---

## 8. BaseAgent API

### 8.1 初始化

```python
from arf.agent.base import BaseAgent
from arf.agent.config import AgentConfig

config = AgentConfig(**yaml.safe_load(open("agent.yaml")))
agent = BaseAgent(config=config)
await agent.start()     # 初始化 MCP、插件、FileWatcher
```

### 8.2 核心方法

```python
# 流式对话
async for event in agent.stream(user_message, session_id="..."):
    yield event         # AgentEvent

# 非流式对话
state = await agent.chat(user_message, session_id="...")

# 恢复子 agent 执行（HITL 场景）
async for event in agent.stream(
    user_message="[Human] 选B",
    session_id="parent_sid--task_3"   # 子 agent session_id
):
    yield event

# 关闭会话
await agent.close(session_id="...")

# 停止 Agent
await agent.stop()
```

### 8.3 属性

```python
agent.engine           # ControlPlane 引用
agent.event_bus        # EventBus（InMemoryEventBus）
agent.state_store      # StateStore（FileStateStore）
agent.session_mode     # 当前会话模式
await agent.set_session_mode("ask")    # 切换会话模式
```

---

## 9. 内置 Tool 一览

### 9.1 内核工具（框架自动注册，无前缀）

| Tool | 参数 | 返回 | 说明 |
|------|------|------|------|
| `use_skill` | `name` (str) | `{skill: {name, description, body, tools_sequence}}` | 加载 Skill 领域知识 |
| `ask_user` | `question` (str), `options` (list[str]?) | `{pending: true}` | 请求人类决策，子 agent round 结束 |

### 9.2 A2A Plugin 工具（`plugins: [a2a]`启用）

| Tool | 参数 | 返回 | 说明 |
|------|------|------|------|
| `delegate_task` | `task`, `agent`?, `timeout`?, `context`? | `{dispatched/queued, task_id}` | 派发子 agent |
| `queue_status` | 无 | `{running, queued, max_concurrent}` | 查询任务队列 |
| `await_task` | `task_id`, `timeout`? | `{result}` | 阻塞等待（非消费性读取） |
| `cancel_task` | `task_id` | `{cancelled}` | 取消排队任务 |
| `resolve_conflict` | `task_id` | `{applied}` | 应用冲突暂存变更 |
| `cancel_held` | `task_id` | `{discarded}` | 丢弃冲突暂存变更 |

---

## 10. A2A Plugin

### 10.1 架构

```
主 agent 调 delegate_task
  → QueuedTaskDelegator 插槽调度（FIFO，每 session 最多 N 个并发）
  → 子 agent astream(stop_on_text=True)
  → round_end hook → complete() + emit task_completed
  → 父 agent pre_action → 注入结果到父 messages
```

### 10.2 事件

| 事件 | 触发 | App 动作 |
|------|------|---------|
| `task_completed` | 子 agent 完成 | 通知；父 agent 等人类时自动发起新 round |
| `human_decision_required` | 子 agent 调 `ask_user` | UI 弹窗 → 人类回答 → 恢复子 agent（见 §11） |

### 10.3 冲突检测

```
子 agent 启动 → workspace snapshot (hash all files)
子 agent 完成 → diff snapshot → file_changes {added, modified, deleted}
父 agent pre_action:
  一批 completed tasks → cross-task overlap 检查
  无冲突 → 正常注入
  有冲突 → first-writer-wins → 后完成者的冲突变更暂存磁盘:
    data/{sid}/conflicts/{task_id}/
      ├── manifest.json
      └── files/
  父 agent 消息注入冲突警告 → 调 resolve_conflict/cancel_held
```

### 10.4 深度限制

子 agent 自动被注入 `_tool_blacklist: ["delegate_task"]`，ControlPlane 过滤 tool 列表——子 agent 无法再 spawn 子 agent。

---

## 11. HITL 中断

### 11.1 Flow

```
子 agent 执行中 → 调 ask_user("选方案?", ["A","B"])
  → state["_pending_human_decision"] = {question, options}
  → 子 agent round 自然结束
  → A2APlugin.round_end 检测 → emit "human_decision_required" 事件

App:
  1. 监听事件，展示 UI 弹窗
  2. 收人类回答
  3. state_store.get(child_session_id)
     → messages.append({role:"user", content:"[Human] B"})
     → engine.astream(child_session_id)
  4. 子 agent 下一轮继续，看到人类回答
```

### 11.2 App 示例

```python
async def handle_human_decision(event: AgentEvent):
    child_sid = event.data["child_session_id"]
    question = event.data["question"]
    options = event.data["options"]

    # 展示 UI
    answer = await show_dialog(question, options)

    # 注入并恢复
    state = await state_store.get(child_sid)
    state["messages"].append({
        "role": "user",
        "content": f"[Human] {answer}"
    })
    await state_store.put(child_sid, state)

    async for event in engine.astream(state):
        yield event
```

---

## 12. 目录约定

```
项目根目录/
├── agent.yaml                    # Agent 配置
├── agents/                       # 预定义子 Agent 配置
│   └── {name}/
│       └── agent.yaml
├── skills/                       # 项目级 Skill
│   └── {name}/
│       ├── skill.yaml
│       └── skill.md
├── tools/                        # App 级 Tool（user__ 命名空间）
│   └── {name}/
│       ├── tool.yaml
│       └── function.py
├── arf/plugins/                  # 框架插件
│   └── {name}/
│       ├── plugin.yaml
│       ├── plugin.py
│       ├── tools/
│       └── skills/
├── data/                         # 运行时数据
│   └── {session_id}/
│       ├── traces/
│       ├── state/
│       ├── tool_outputs/
│       └── conflicts/            # A2A 冲突暂存
└── workspace/                    # Agent 文件操作工作区
```

---

## 13. 迁移 v0.1 → v0.2

### 变更

1. **消息结构变更**
   messages 从 `[system_prompt, ...]` 变为 `[system_prompt, skills, tools, memory, ...]`。框架按顺序注入独立 system 消息。

2. **Skill 命名空间**
   插件 skills 加 `{plugin}__` 前缀，不再覆盖同名项目 skill。

### 新增

- `plugins_config.a2a` 配置段
- `model_defs` + `agent_models` 模型定义
- `task_completed` / `human_decision_required` 事件
- `use_skill` / `ask_user` 内核工具
- `skills/` 目录（Skill 懒加载）
- `_tool_blacklist` 深度限制
- 文件冲突检测 + `resolve_conflict` / `cancel_held` 工具

### 无需修改

- `plugins` 列表
- `tools/` 目录结构
- `plugins_config.{approval,tool_guard,memory}` 等
- `advanced.*` 高级配置
- `mcp_servers`
