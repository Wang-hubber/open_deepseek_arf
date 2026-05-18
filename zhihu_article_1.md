# 开源 Agent 框架深度解析：如何用"提示词 + 控制平面"双重约束，让 LLM 真正听话

> 一个能自我演进的 AI Agent 框架，如何在 800 token 系统提示词内实现指令遵循、工具调用和任务管理？

---

## 引言：Agent 的"失控"问题

写过 Agent 的人都遇到过这些场景：

- 你告诉它"不要编造文件内容"，它依然自信地描述一个不存在的目录结构
- 你让它切换到深度思考模型，它回答"已切换"，实际上根本没调用工具
- 它在同一轮对话里连续 3 次调用同一个工具——每次参数一模一样，每次结果都一样

这些不是"模型不够聪明"的问题，而是**控制平面缺失**的问题。

ARF（Agent Resources & RunTime FrameWork）是我最近开源的一个 AI Agent 框架。在这篇文章里，我会详解它是如何通过**提示词工程 + 图状态控制**的双重约束，让 DeepSeek V4 这类 LLM 严格遵循指令的。

---

## 一、提示词不是一段话，而是一条流水线

大多数 Agent 框架把系统提示词写成一整段文本。ARF 的做法不同——它把提示词拆成**一条可插拔的流水线**：

```python
PROMPT_PIPELINE = [
    (10, "workspace",        "_workspace_section"),    # 工作区信息
    (15, "long_term_memory", "_long_term_memory"),      # 长期记忆
    (20, "memory",           "_memory_section"),         # 会话上下文
    (25, "critical_rules",   "_critical_rules"),         # ← 硬规则，夹在中间
    (30, "identity",         "_identity_section"),       # 身份和能力描述
    (50, "inventory",        "_inventory_section"),      # 工具和技能清单
    (60, "language",         "_language_instruction"),   # 语言偏好
]
```

每一段是一个独立方法，按 `priority` 排序组装。这带来了什么好处？

**优先级 25 的硬规则（critical_rules）夹在"会话记忆"（20）和"身份描述"（30）之间。** LLM 在读取自己的能力和工具清单之前，先被灌输了行为边界——先立规矩，再给能力。

当你想增加一段自定义提示词时，不需要纠结"放在哪里"，只需要给它一个合适的优先级数字。新规则会把旧规则挤到合适的相对位置。这比手动拼接字符串强太多。

### 四个硬规则（R0-R3）

来看看这组"硬规则"：

**R0: 每次回复前自检**
> 在写任何回复文字之前，问自己：我调用了工具来验证我即将说的话吗？如果答案是否定的，而你陈述了任何关于当前状态的事实（模型、工具、文件、配置），立即停止。先调用工具验证，然后用验证过的信息回复。

**R1: 先验证，再回答**
> 永远不要凭记忆陈述当前模型、活跃工具、文件内容或任何运行时状态。先调用相关工具，然后根据工具结果回答。猜测永远是错的。

**R2: 工具调用 ≠ 口头声称**
> 要切换模型，必须调用 model_switch 或 model_manager。说"已切换到 X"而没有调用工具，这是违规行为。这个规则适用于所有状态变更操作。

**R3: 行动后验证**
> 调用改变状态的工具后，验证结果。如果工具说成功了，报告成功。如果失败或显示非预期状态，告知用户实际结果。

其中 R2 是整个设计里最有意思的一条——它直接针对 LLM 的一个已知失效模式：**口头声称替代实际行为**。你告诉它"切换到深度思考"，它可能只是说"好的，已切换"，而没有真的调用任何工具。

这些规则很短——总共不到 200 字——但它们建立了一个"不信任循环"：LLM 应该默认怀疑自己的记忆，主动调用工具去验证。

---

## 二、控制平面：提示词说了不算，运行时再查一遍

提示词工程只能做到"告诉模型规则"。但如果模型就是不遵守呢？

ARF 的答案是：在 LangGraph 的图引擎里，**用代码再查一遍**。这叫"双重约束"——提示词是软约束（LLM 可以忽略），图状态检测是硬约束（代码强制执行）。

### 矛盾检测：揪出"口头声称但没做"的 LLM

在 `execute_tools_node` 执行完工具后，代码会扫描结果：

```python
# 检查：助手声称的模型 vs 工具实际返回的模型
for claimed_name in ("quick_thinking", "deep_thinking"):
    if claimed_name in last_assistant_text and claimed_name != actual_active:
        notes.append("Assistant claimed X but actual model is Y. Correct your response.")
```

如果 LLM 说"我使用了深度思考模型"，但 `model_switch` 工具返回的实际模型不是 `deep_thinking`，系统会**注入一条 `[CONTRADICTION]` 消息**到对话流里：

```
[user]: [CONTRADICTION] Assistant claimed 'deep_thinking' but actual active
        model is 'quick_thinking'. Correct your response.
```

这意味着 LLM 会看到自己的违规被标记出来——它被迫面对错误并纠正。不是沉默地忽略，不是后台日志记录，而是直接打在脸上。

### 工具调用去重：防止 LLM 的"重试强迫症"

另一个常见问题：LLM 调用工具后看到相同结果，以为"我没调用成功"，然后又调用一次——一模一样的参数。如果不管，3 次、5 次、10 次都可能。

ARF 在同一轮对话内做去重：

```python
call_key = (tool_name, arguments)
if call_key in seen_calls:
    return {"deduplicated": True, "note": "Duplicate call skipped"}
```

同一轮内相同的 `(工具名, 参数)` 组合只执行一次，后续调用直接返回 "已去重"。对 LLM 透明——它看到的是工具返回了结果，但实际没有浪费 API 调用。

### 连续失败 3 次 → 提示升级模型

还有一个更细腻的设计：当同一个工具连续失败 3 次，系统会自动注入一条提示：

```
[user]: 工具 'xxx' 已连续失败 3 次。建议使用 model_switch 切换到 deep_thinking 后重试。
```

这利用了 ARF 的两级模型路由——如果一个快速模型反复调同一个工具都失败了，可能是因为任务超出了它的能力范围，需要升级到更强的模型。

---

## 三、模型路由：让 LLM 自己决定自己该用哪个脑子

ARF 使用两级模型路由：`quick_thinking`（快速推理）和 `deep_thinking`（深度推理）。

### 分类器：用模型来分类模型

有趣的设计选择：分类器本身就是一个 LLM 调用。

```python
CLASSIFY_SYSTEM_PROMPT = """
你是任务复杂度分类器。分析用户消息，回复一个词：medium 或 complex。

- medium: 问候、简单问答、文件读取、代码生成、调试、工具编排
- complex: 系统设计、多文件重构、架构决策、安全分析

示例:
"hello" → medium
"重构认证模块以使用 OAuth2" → complex
"""
```

分类器的系统提示词非常短——只有 9 个示例，不到 100 字。它不需要长篇大论地描述每个分类的定义。

**而且分类不是一次性的。** 会话中间的每一条用户消息都可以触发重新分类。如果用户从"你好"切换到"帮我设计一个微服务架构"，路由器会自动检测到"架构"、"设计"等关键词（需要 2+ 个匹配），重新运行分类器。

### 降级链：优雅降级而非崩溃

如果目标模型不可用（比如用户只配了一个模型），系统不会报错——它走降级链：

```python
DEGRADATION = {
    "deep_thinking":  ["quick_thinking"],   # 深度不可用时用快速
    "quick_thinking": ["deep_thinking"],    # 快速不可用时升级到深度
}
```

双向降级——没有深度模型时用快速顶上，没有快速模型时用深度代替。总有一个能工作。

---

## 四、状态同步：工具改了文件，图状态要知道

一个容易被忽视但致命的问题：工具修改了配置文件（比如 `model_switch` 写入 `arf_agent.yaml`），但 LangGraph 的状态机还在用旧的模型。

ARF 在工具执行后做了一件事——**状态同步**：

```python
# 工具执行后，读取工具结果，更新图状态
if tool_name == "model_switch" and result.get("ok"):
    state["current_model"] = result["model_type"]
    state["_needs_tools_refresh"] = True
```

这样，工具说"已切换到 deep_thinking"，图状态里的 `current_model` 就真的变成了 `deep_thinking`。下一次 `call_model` 会用新的模型——不会出现工具说切换了但实际没切的尴尬。

---

## 五、成果：800 token 提示词 + 硬约束 = 稳定的 Agent

整套设计的结果是：

- **系统提示词约 800 token**（渐进式披露：内核工具始终激活，其余按需加载）
- **4 条硬规则**全量不到 200 字
- **矛盾检测**在每次工具执行后自动运行
- **工具去重**防止重复调用浪费 token
- **自动降级**保证用户体验不间断
- **状态同步**保证 Agent 的自我认知与实际一致

这套架构已经在 DeepSeek V4 上稳定运行，支持 Windows + Linux 双平台。

---

## 开源地址

- **Gitee**: [https://gitee.com/dalaydata/open_deepseek_arf](https://gitee.com/dalaydata/open_deepseek_arf)
- **GitHub**: [https://github.com/Wang-hubber/open_deepseek_arf](https://github.com/Wang-hubber/open_deepseek_arf)

如果这些设计思路对你有启发，欢迎 Star 和贡献代码。下一篇会讲 ARF 的另一个核心特性——**上下文压缩**：如何在 1M token 的窗口中自动管理 Agent 的上下文，让长会话不丢失信息的同时不浪费 token。
