# 双 Agent 架构

ARF 支持 User Agent + System Agent 双 Agent 架构。User Agent 处理用户任务（对话、文件操作、搜索），System Agent 负责内部操作（资源创建、工具生成、配置管理）。独立执行，共享工作区。用户看到的是一个连贯的助手，双 Agent 是实现细节。

---

## 为什么需要双 Agent

单 Agent 的问题：
- 用户对话和系统管理共享同一上下文，token 消耗大
- 权限难以分离——用户能用的工具和系统管理需要的工具混在一起
- 系统操作（创建工具、注册资源）容易污染用户对话历史

双 Agent 解决：
- **User Agent**：处理用户请求，拥有对话所需的工具
- **System Agent**：处理系统请求，拥有资源创建/管理的工具
- 两个 Agent 通过 **handoff** 机制交接任务

---

## 配置双 Agent

```yaml
# agent.yaml
name: arf_assistant
description: 主助手

# User Agent 的工具（内核即对话所需）
tools:
  - name: file_reader
    activation: kernel
  - name: file_writer
    activation: kernel
  - name: web_search
    activation: kernel
  - name: web_fetch
    activation: kernel
  - name: handoff      # 交接工具
    activation: kernel

# System Agent 的完整定义
agents:
  - name: sys_agent
    role: 系统工程师
    task: 资源创建、模型配置、工具/技能生成
    description: 处理系统级操作

    system_prompt:
      template: |
        You are the ARF System Engineer.
        {{INVENTORY}}
        ## Critical Rules
        {{CRITICAL_RULES}}
      critical_rules: |
        ### Gate 1 — Design
        先设计方案，等待用户确认（"go ahead"、"yes"、"确认"）

        ### Gate 2 — Write
        确认后才调用 file_writer 创建文件

        ### Gate 3 — Validate
        读取验证规范并检查

        ### Gate 4 — Activate
        调用 resource_loader 激活新资源

    models:
      - type: deep
        model: deepseek-v4-pro
        api_base: https://api.deepseek.com
        api_key_env: DEEPSEEK_API_KEY
        context_window: 1000000
        kwargs:
          reasoning_effort: max

    tools:
      - name: file_reader
        activation: kernel
      - name: file_writer
        activation: kernel
      - name: resource_loader
        activation: kernel
      - name: resource_scaffold
        activation: kernel
```

---

## handoff 机制

User Agent 调用 `handoff` 工具将任务移交给 System Agent：

```yaml
# tools/handoff/tool.yaml
name: handoff
description: Agent 间移交——派发任务或完成后返回
parameters:
  type: object
  properties:
    task:
      type: string
      description: 任务描述
    context:
      type: string
      description: 任务上下文
  required: [task]
activation: kernel
```

交接流程：

1. User Agent 判断任务需要系统权限，调用 `handoff(task="创建新工具", context="...")`
2. 函数返回 `{"handoff": True, ...}` → 引擎 `HandoffManager.detect()` 捕获信号 → 状态保存 → 目标解析 → 上下文构建 → Agent 切换
3. System Agent 执行后再次调用 `handoff` → 引擎反向解析 → 恢复 User Agent 状态
4. 用户感知不到 Agent 切换

详见下方 [Handoff 流程详解](#handoff-流程详解)。

### 目标解析策略 (Target Resolution)

`HandoffManager.resolve()` 采用三级递进解析，为多目标 handoff 场景预留了完整的扩展能力：

```
resolve(from_agent, handoff_data):
  candidates = rules[from_agent]         # 按 from_agent 分组

  Tier 1: len(candidates) == 1
    → 直接返回 candidates[0].to_agent   # 当前配置走这里

  Tier 2: len(candidates) > 1 && system_model 可用
    → LLM 语义匹配: 将 trigger 描述与 task 内容对比
    → 返回最佳匹配的 to_agent

  Tier 3: len(candidates) > 1 && system_model 不可用
    → 关键词 fallback: trigger 文本分词后与 task 做交集
    → 首个命中即返回；无命中则返回 candidates[0]
```

当前 `agent.yaml` 中每个 `from_agent` 只有一条规则，所以 `trigger` 字段和 Tier 2/3 逻辑尚未触发——但框架已完整实现，只需在 `handover.rules` 中为同一 `from_agent` 添加多条规则即可启用多目标调度。

---

## 共享工作区

两个 Agent 共享同一个 `workspaces/default/` 目录。System Agent 创建的资源直接放在 `tools/`、`skills/`、`models/` 目录下。FileWatcher 检测到新文件后自动热加载——User Agent 下一轮对话即可使用新工具。

---

## 路径权限分离

`file_writer` 和 `file_deleter` 根据 `_agent_mode` 参数区分权限：

- **User Agent** 模式（`_agent_mode: user`）：禁止写入 `tools/`、`skills/`、`models/` 目录——需要调用 `handoff` 交接
- **System Agent** 模式（`_agent_mode: sys`）：可以写入上述目录

```python
# tools/file_writer/function.py
USER_RESTRICTED_PREFIXES = ("/tools/", "/skills/", "/models/")

async def execute(path: str, content: str, _agent_mode: str = "sys") -> dict:
    if _agent_mode == "user":
        for prefix in USER_RESTRICTED_PREFIXES:
            if prefix in path:
                return {"error": "需要 System Agent 权限，请调用 handoff"}
    # 正常写入逻辑
```

---

## Handoff 流程详解

### 正向交接（User Agent → System Agent）

1. User Agent 调用 `handoff(task="...", context="...")` → 函数返回 `{"handoff": True, "task": ..., "context": ...}`
2. 引擎在每次工具执行后调用 `HandoffManager.detect()` 扫描 tool_results，发现 `{"handoff": True}` 信号
3. 保存当前 User Agent 状态到 `state_store`（key: `{session_id}/{from_agent}`）
4. `HandoffManager.resolve()` 根据 handover rules 解析目标 Agent（支持多候选时 LLM 匹配 trigger）
5. `HandoffManager.build_target_context()` 构建 System Agent 的初始消息：target system prompt + raw_turns 上下文 + task summary + handoff user message
6. 设置 `state["active_agent"] = "sys_agent"`，后续工具调用自动携带 `_agent_mode` 参数
7. 下一轮循环时，引擎使用 System Agent 的配置（system_prompt、tools、skills、max_turns）

### 反向交接（System Agent → User Agent）

System Agent 完成任务后再次调用 `handoff`，引擎检测到 handoff 信号后：
1. 解析目标为 `arf_assistant`（handover rules 第二条）
2. `_execute_handoff` 发现 `state_store` 中已有目标 Agent 的状态（正向交接时保存的），直接恢复
3. 子 Agent 的最后一条 assistant 消息作为 handoff 工具的结果注入原对话
4. User Agent 继续处理，用户感知不到切换

### `_agent_mode` 传递

引擎在每次工具执行前从 `state["active_agent"]` 读取 agent_mode 并注入工具参数：

```
graph.py:726 → tool_executor.execute(valid_calls, agent_mode=agent_mode)
→ function.py:30 → params["_agent_mode"] = agent_mode
```

`file_writer` / `file_deleter` 据此区分权限：`_agent_mode == "user"` 时禁止写入 `tools/`、`skills/`、`models/` 路径。

### 注意事项

- System Agent 每次被调用创建独立上下文（build_target_context 构建全新 messages），任务完成后释放——不会污染 User Agent 的对话历史
- User Agent 的状态在 handoff 前持久化，返回时完整恢复
- System Agent 创建的资源（tools/skills/models）由 FileWatcher 自动检测，User Agent 无需重启即可使用
- `trigger` 字段在当前单规则配置下不会被使用（`len(candidates) == 1` 直接返回），但框架已为多目标 handoff 预留了 LLM 匹配 + 关键词 fallback 能力（`HandoffManager.resolve()`）
