# Dual Agent Split Design

## Motivation

当前 ARF 是单 Agent 架构，同时处理两类垂直任务：

- **sys 类**：资源创建、工具/skill/model 自编排（Coding 角色）
- **user 类**：用户任务处理（个人助手角色）

单 Agent 承担所有职责导致：任务复杂度高、上下文臃肿（所有工具描述全量加载）、系统 prompt 冗长（混合两种身份的职责说明）。

目标：拆分为 User Agent 和 Sys Agent，各自只加载所需资源和擅长任务，降低单 Agent 上下文和复杂度。

## Architecture

```
User Message
    │
    ▼
Dispatcher (新增)
    │
    ├─ Phase 1: User Graph
    │   system_prompt: user persona
    │   tools: file_reader, file_writer*, file_deleter*, file_download,
    │          memory_store, web_fetch, web_search,
    │          image_understanding, ocr, speech_*, video_understanding,
    │          handoff_to_sys
    │   model: quick_thinking (default), classifier → [quick_thinking, deep_thinking]
    │   max_turns: 6
    │
    ├─ handoff? → Phase 2: Sys Graph
    │   system_prompt: sys persona
    │   tools: ALL tools (kernel + task tools)
    │   model: deep_thinking (固定, 无 classifier)
    │   max_turns: 10 (total, shared with Phase 1)
    │
    └─ → final response
```

**核心组件：**

| 组件 | 新增/修改 | 职责 |
|------|-----------|------|
| `Dispatcher` | 新增 | 编排两阶段执行，管理 handoff，合并结果 |
| `UserAgent` | 拆分自 ARFAgent | User persona prompt + 受限工具集 |
| `SysAgent` | 拆分自 ARFAgent | Sys persona prompt + 全量工具集 |
| `GraphEngine` | 复用 | 每个 Agent 各持一个实例 |
| `AgentState` | 扩展 | 新增 `agent_mode: "user" \| "sys"` |
| `handoff_to_sys` | 新增 | 系统工具，User Agent 触发切换的信号 |
| `file_download` | 新增 | 系统工具，生成文件下载链接 |

**前端与 WebSocket 层无需改动** — Dispatcher 对外暴露与现有 `chat_with_tools` / `chat_stream_with_tools` 一致的接口。

## Agent Identities & Prompts

### User Agent Prompt

聚焦个人助手角色。相比原 ARF Agent prompt：

**移除：**
- 资源创建流程 (Gate 1-4, resource_scaffold, validate_tool)
- Model management 指引 (model_manager, model_switch)
- Resource registration (resource_registrar)
- Hook management (manage_hooks)

**保留：**
- `file_reader`, `memory_store` 使用说明
- `file_writer`, `file_deleter` 使用说明（含路径限制）
- `file_download` 使用说明
- Error handling (error_handler skill)
- Language requirement

**新增 — Intent Translation & Handoff：**

```
## Intent Translation
用户很少直接说出技术动作。你需要将用户话术翻译为潜在行为：
- "能不能..." / "帮我想个..." → 可能涉及创建资源
- "我想要一个..." / "有没有办法..." → 可能涉及发现或创建资源
- "改一下..." / "加个功能..." → 涉及修改资源
- "为什么这个工具..." / "怎么用..." → 只读，可处理
- "这个结果帮我改一下..." → 文件修改，可用 file_writer

逐一判断每个潜在动作需要的工具是否在你的工具集中。
任一动作需要 sys 工具 → call handoff_to_sys。

## File Writer / Deleter 路径限制
你可以用 file_writer 和 file_deleter 操作用户的常规文件
(uploads/, output/, data/ 等) 以及对工具生成结果做精细修改。
但如果写入目标路径在 tools/, skills/, models/ 下，
或涉及资源创建/注册/激活，必须调用 handoff_to_sys。
```

### Sys Agent Prompt

聚焦 Coding/R&D 角色：

**保留：**
- 资源创建完整流程 (Gate 1-4)
- Model management, model switching
- 全部 kernel tools 使用说明
- Error handling, memory management

**精简：** 一般性任务处理指引（Sys Agent 只处理 handoff 来的具体任务，不需要闲聊能力）。

### Prompt Pipeline

`PROMPT_PIPELINE` 机制保留，两个 Agent 各自定义自己的 pipeline：

```
UserAgent.PROMPT_PIPELINE:
  workspace, long_term_memory, memory, critical_rules,
  user_identity, user_inventory, language

SysAgent.PROMPT_PIPELINE:
  workspace, long_term_memory, memory, critical_rules,
  sys_identity, sys_inventory, language
```

共享 sections: `_workspace_section`, `_memory_section`, `_long_term_memory_section`, `_critical_rules_section`, `_language_instruction`。

## Tool Partitioning

### Framework System Tools (`@sys/tools/`)

所有工具仍为 `@sys/tools/` 下的框架实现，与用户自建工具区分。

| 工具 | User Agent | Sys Agent | 备注 |
|------|:---:|:---:|------|
| `file_reader` | ✓ | ✓ | |
| `file_writer` | ✓* | ✓ | *路径限制 |
| `file_deleter` | ✓* | ✓ | *路径限制 |
| `file_download` | ✓ | ✓ | 新增，生成下载链接 |
| `memory_store` | ✓ | ✓ | |
| `resource_loader` | ✗ | ✓ | sys 专属 |
| `resource_registrar` | ✗ | ✓ | sys 专属 |
| `model_manager` | ✗ | ✓ | sys 专属 |
| `model_switch` | ✗ | ✓ | sys 专属 |
| `manage_hooks` | ✗ | ✓ | sys 专属 |
| `handoff_to_sys` | ✓ | ✗ | 仅 User Agent 可见 |
| `web_fetch` | ✓ | ✓ | |
| `web_search` | ✓ | ✓ | |
| `image_understanding` | ✓ | ✓ | |
| `ocr` | ✓ | ✓ | |
| `speech_*` | ✓ | ✓ | |
| `video_understanding` | ✓ | ✓ | |

### 双重约束

User Agent 的 file_writer/file_deleter 路径限制：

1. **Prompt 层**：User Agent 的 identity 明确列出禁止写入的路径前缀
2. **运行时**：file_writer/file_deleter 的 execute() 检查 caller identity，User Agent 身份时拒绝写入 `tools/`, `skills/`, `models/` 前缀路径

复用已有的 workspace 沙箱（file_writer 已限制在 workspace 目录内），仅叠加路径黑名单。

### handoff_to_sys 工具

```yaml
name: handoff_to_sys
description: |
  当用户需要创建/修改/删除资源，或需要写入 tools/skills/models 路径，
  或当前工具集无法满足请求时调用。
  调用此工具表明任务将被转交给系统工程师 Agent 处理。
parameters:
  - name: intent
    type: string
    required: true
    description: "翻译后的用户意图，用中文描述"
  - name: required_actions
    type: array
    items: string
    description: "需要的具体动作列表"
  - name: reason
    type: string
    description: "无法处理的原因"
```

这是一个轻量工具，不执行实际操作。Dispatcher 检测到其 tool_result 即触发 handoff。

### file_download 工具

```yaml
name: file_download
description: "将工作区文件生成可下载链接，用户点击即可下载查看"
parameters:
  - name: path
    type: string
    required: true
    description: "文件路径（相对于工作区）"
  - name: label
    type: string
    description: "链接显示名称，默认使用文件名"
```

## Handoff Mechanism

### Flow

```
User Agent 最后一轮
    │
    ├─ tool_calls 中有 handoff_to_sys?
    │
    ├─ YES → Dispatcher:
    │    1. 提取 handoff 参数 (intent, required_actions, reason)
    │    2. 构建 Sys Graph 参数 (sys prompt + all tools)
    │    3. 注入 handoff 上下文消息:
    │       [role: user]
    │       content: |
    │         [Handoff from User Agent]
    │         意图: {intent}
    │         需要动作: {actions}
    │         原因: {reason}
    │         原始用户消息: {original_user_message}
    │    4. User Agent 阶段的完整历史保留
    │    5. 运行 Sys Graph → 返回 response
    │
    └─ NO → 返回 User Agent response
```

### max_turns

总 max_turns 在两个阶段间共享。Phase 1 消耗 N 轮，Phase 2 可用 `max_turns - N` 轮。总计不超过配置上限。

### 无 handoff 场景

如果 User Agent 不需要 handoff，直接返回 User Agent 的 response，流程与现有单 Agent 一致。

## User Experience

### 流式输出中的 Handoff

```
用户: "我想要一个能查天气的功能"
  ↓
[User Agent 处理 — 正常流式输出]
  ↓
→ 前端收到 handoff 事件:
  {"type": "handoff", "from": "user_agent", "to": "sys_agent",
   "intent": "创建天气查询工具，支持输入城市名返回实时天气"}
  ↓
[前端显示过渡标记]
  ↓
[Sys Agent 处理 — 继续流式输出]
  ↓
→ 最终 response
```

**前端展现：**
- 用户看到一条连续对话，中间有轻量过渡标记
- 不是两条分开的消息，而是同一消息内部切换处理角色
- 过渡标记示例：一条灰色系统提示 "🔄 系统工程师接手处理..."

**体验原则：**
- User Agent 阶段要快 — 只做意图翻译和判断，正常速度
- Handoff context 信息充足，Sys Agent 第一轮就直接开始干活，不重复询问用户
- Sys Agent 完成后用户直接看到结果，无需二次确认

## Context Sharing

- 两个 Agent 共享同一份 `messages` 列表
- User Agent 阶段的 assistant 消息、tool_call、tool_result 全量保留
- Handoff 时注入特殊 user 消息（handoff 上下文），Sys Agent 接续
- 最终返回前端的 history 合并去重

## Model Strategy

| Agent | 默认模型 | 动态路由 | 路由范围 |
|-------|---------|:---:|---------|
| User Agent | quick_thinking | 启用 | quick_thinking ↔ deep_thinking |
| Sys Agent | deep_thinking | 禁用 | 固定 deep_thinking |

User Agent 复用现有 classifier 机制判断用户消息复杂度。Sys Agent 由于处理的是资源创建/编排任务，固定 deep_thinking。

## Error Handling

- **User Agent 失败**：返回错误信息给用户，不触发 handoff（handoff 只由 User Agent 主动触发）
- **Sys Agent 失败**：错误信息返回给用户，附带"系统工程师处理失败"标记
- **Handoff 后 Sys Agent 不可用**（无 deep_thinking 模型）：Dispatcher 返回友好错误，建议用户配置 deep_thinking 模型
- **max_turns 耗尽**：在耗尽前给出明确提示，建议用户开新对话或简化需求

## Implementation Steps

| # | 步骤 | 涉及文件 |
|---|------|----------|
| 1 | 新增 `handoff_to_sys` 系统工具 | `resources/system/tools/handoff_to_sys/` |
| 2 | 新增 `file_download` 系统工具 | `resources/system/tools/file_download/` |
| 3 | 拆分 ARFAgent → UserAgent + SysAgent，提取共享基类 | `agent/base.py`, `agent/user_agent.py`, `agent/sys_agent.py` |
| 4 | AgentState 增加 `agent_mode` | `engine/state.py` |
| 5 | 新增 Dispatcher（两阶段编排 + 流式 handoff 事件） | `engine/dispatcher.py` |
| 6 | file_writer/file_deleter 增加 caller identity 检查 | 现有工具 function.py |
| 7 | 整合到 SessionManager | `server/session_manager.py` |
| 8 | 测试 | `tests/` |

## Testing Strategy

- **单元测试**：Dispatcher 路由逻辑、handoff 触发条件、工具分区
- **集成测试**：User → Sys handoff 完整流程、上下文传递正确性
- **边界场景**：
  - User Agent 无需 handoff 的正常任务
  - User Agent 需要 handoff 的资源创建任务
  - 用户消息模糊，User Agent 意图翻译后 handoff
  - Sys Agent deep_thinking 模型不可用
  - max_turns 在 Phase 1 耗尽
  - 流式输出中 handoff 事件的正确发射
