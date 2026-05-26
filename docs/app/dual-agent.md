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
  - name: handoff_to_sys      # 交接工具
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

User Agent 调用 `handoff_to_sys` 工具将任务移交给 System Agent：

```yaml
# tools/handoff_to_sys/tool.yaml
name: handoff_to_sys
description: 将资源创建/修改操作移交给 SysAgent
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
1. User Agent 判断任务需要系统权限，调用 `handoff_to_sys(task="创建新工具", context="...")`
2. 框架将任务上下文传递给 System Agent
3. System Agent 执行后通过 handoff 返回结果或待确认事项
4. 用户感知不到 Agent 切换

---

## 共享工作区

两个 Agent 共享同一个 `workspaces/default/` 目录。System Agent 创建的资源直接放在 `tools/`、`skills/`、`models/` 目录下。FileWatcher 检测到新文件后自动热加载——User Agent 下一轮对话即可使用新工具。

---

## 路径权限分离

`file_writer` 和 `file_deleter` 根据 `_agent_mode` 参数区分权限：

- **User Agent** 模式（`_agent_mode: user`）：禁止写入 `tools/`、`skills/`、`models/` 目录——需要调用 `handoff_to_sys` 交接
- **System Agent** 模式（`_agent_mode: sys`）：可以写入上述目录

```python
# tools/file_writer/function.py
USER_RESTRICTED_PREFIXES = ("/tools/", "/skills/", "/models/")

async def execute(path: str, content: str, _agent_mode: str = "sys") -> dict:
    if _agent_mode == "user":
        for prefix in USER_RESTRICTED_PREFIXES:
            if prefix in path:
                return {"error": "需要 System Agent 权限，请调用 handoff_to_sys"}
    # 正常写入逻辑
```

---

## 注意事项

- **当前实现状态**：`agent.yaml` 中的 `agents:` 和 `handover:` 段已被解析但**未被框架运行时完整接入**。参考 app 中通过 `handoff_to_sys` 工具 + `_agent_mode` 参数实现了基本的双 Agent 分离，但多 Agent 调度器（Dispatcher）尚未集成到主循环中
- **System Agent 的上下文成本**：System Agent 每次被调用都创建独立的上下文，任务完成后释放——不会污染 User Agent 的对话历史
- **FileWatcher**：System Agent 创建资源后，FileWatcher 自动检测新文件，User Agent 无需重启即可使用新工具
