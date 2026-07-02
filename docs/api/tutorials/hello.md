# 你好，ARF — 连接真实模型

> 🎯 Diátaxis 桶位：**Tutorials**（入门教程，真实 LLM 版本）

## 入门：两个核心问题

跑一个 ARF Agent 之前，每个开发者都会问两个问题：

1. **如何接入模型？** — 用哪个 LLM provider？API key 怎么传？能不能接自部署 / 第三方？
2. **如何定义我的 agent？** — 系统的 prompt、路由、参数怎么写？

这一章先给一个**完整可跑示例**（看完就能跑通），再拆成三段回答这两个问题 + 解释 Engine 怎么把它们组装起来。

## 一个完整示例（先跑起来看效果）

> 完整可跑脚本（保存为 `/tmp/hello.py`）。**先跑通，再回来看下面三段拆解** — 没有体感的话，下面的字段解释就是抽象的。

```python
import asyncio
from arf import (
    Bus, NodeId, Route,
    MiniMaxConfig, MiniMaxProvider,
    AgentConfig, EngineBuilder, EngineState,
)


async def main():
    bus = Bus()

    # 1. 接入模型：注册一个真实的 MiniMax provider 到 bus
    provider = MiniMaxProvider(config=MiniMaxConfig.from_env())
    await provider.connect_to_bus(bus=bus, node_id=NodeId("model/main"))

    # 2. 定义 agent：AgentConfig 是声明式骨架
    engine = await EngineBuilder.new(buses=[bus]).build(
        config=AgentConfig(
            agent_id="tutorial-hello",
            system_prompt_template="你是一个简洁的中文助手。",
            routes={
                "model_call": Route.discovery(requirements=[("provider", "minimax")]),
            },
        ),
    )

    # 3. 跑通最小循环：user_input → Engine → model → response
    state = EngineState()
    out = await engine.run(state=state, user_input="用一句话介绍北京。")
    print(f"out={out!r}")
    print(f"messages={len(state.messages)}, turn_count={state.turn_count}")
    await bus.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
```

### 跑起来

```bash
export MINIMAX_API_KEY='sk-...'   # 你的 MiniMax key，inline 填入
.venv/bin/python /tmp/hello.py
unset MINIMAX_API_KEY              # 跑完立即清掉
```

预期 stdout（实际 LLM 回复会有变化）：

```text
out='北京是中华人民共和国的首都，是一座拥有三千多年建城史和八百多年建都史的历史文化名城，也是当代中国的政治、文化、国际交往和科技创新中心。'
messages=3, turn_count=1
```

> 注：`messages=3` 是 user + assistant + 引擎内部条；`turn_count=1` 是 1 次模型调用。

跑通后回到下面三段，看每一行是干什么的。

## 拆解 1：如何接入模型 — ModelAdapter Provider

**回答第一个问题**："用哪个 LLM provider？API key 怎么传？"

ARF 的 `ModelAdapter` 抽象统一了不同 LLM 提供方的差异。所有 provider 都用同一种方式注册到 Bus：`provider.connect_to_bus(bus=bus, node_id=NodeId(...))`。

### 4 个内置供应商

ARF Python 绑定导出 4 个 Config + Provider 对。每对都通过 `from_env()` 读环境变量（也支持直接构造传参）。

| Provider | Config 类 | 构造示例 | 环境变量 | 备注 |
|---|---|---|---|---|
| DeepSeek | `DeepSeekConfig` | `DeepSeekConfig.from_env()` | `DEEPSEEK_API_KEY` | 推荐新手起手；充 10 块够用很久 |
| OpenAI | `OpenAIConfig` | `OpenAIConfig.from_env()` | `OPENAI_API_KEY` | 同时是 OpenAI-API 兼容协议入口（见下） |
| MiniMax | `MiniMaxConfig` | `MiniMaxConfig.from_env()` | `MINIMAX_API_KEY` | TokenPlan 包月；用量大时省钱 |
| Anthropic | `AnthropicConfig` | `AnthropicConfig.from_env()` | `ANTHROPIC_API_KEY` | Claude 系列；API 协议自成一派 |

### OpenAI / Anthropic API 兼容协议

理论上 ARF 可以接入任何实现 **OpenAI Chat Completions 协议** 或 **Anthropic Messages 协议** 的模型服务：

- **OpenAI 兼容**（推荐用于国产 / 自部署模型）：用 `OpenAIConfig` + 自定义 `endpoint`（完整 URL）。
  - 阿里百炼兼容模式：`https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions`
  - Moonshot：`https://api.moonshot.cn/v1/chat/completions`
  - vLLM / Ollama 自部署：`http://localhost:8000/v1/chat/completions` 等
- **Anthropic 兼容**：用 `AnthropicConfig` + 自定义 `endpoint`（完整 URL，参考 [Anthropic API 文档](https://docs.anthropic.com/)）。
  - DeepSeek Anthropic 兼容端点：`endpoint="https://api.deepseek.com/anthropic"`

由于各服务具体支持的 `models` 列表不同，直接构造时需明确传入 `models=[...]`。

> 详细切换示例见文末 `## 切换 Provider` 段。

## 拆解 2：如何定义你的 agent — AgentConfig

**回答第二个问题**："系统的 prompt、路由、参数怎么写？"

`AgentConfig` 是 agent 的"声明式配置"，决定 agent 的身份、行为、消息路由。本节拆解 hello 示例里的 `AgentConfig` 各字段。

### 核心字段

```python
AgentConfig(
    agent_id="tutorial-hello",            # 标识；出现在消息 from 字段，方便调试
    system_prompt_template="你是一个简洁的中文助手。",  # 注入到 system role 的预制 prompt
    routes={                              # 消息路由表（见下）
        "model_call": Route.discovery(requirements=[("provider", "minimax")]),
    },
)
```

| 字段 | 必填 | 作用 |
|---|---|---|
| `agent_id` | ✓ | Agent 唯一标识；engine 在 bus 上注册为 `engine/<agent_id>` |
| `system_prompt_template` |  | 注入到对话前缀的 system prompt；**原样发送**（不再做 `{{skills}}` 占位符替换）。Engine 在每轮 `do_model_turn` 时按 [template, *initial_memory, skills, *conversation] 拼装 prefix，详见 [explanation/上下文拼装机制.md](../../explanation/上下文拼装机制.md) |
| `initial_memory` |  | 会话内相对稳定的记忆条目；每条作为独立 system message 注入到 template 之后、skills 之前。例：`initial_memory=["你是 MiniMax 的助手", "用户偏好中文"]` |
| `max_turns` |  | 单轮最大 ReAct 步数（默认 10） |
| `routes` |  | msg_type → Route 路由表（见下） |
| `checkpoint_rules` |  | 检查点规则列表（ch2+ 用） |

### 路由 routes：把消息路由到正确节点

`routes` 是 `msg_type → Route` 的字典。本章 `model_call` 用 `Route.discovery` — Engine 按节点能力自动选：

```python
"model_call": Route.discovery(requirements=[("provider", "minimax")]),
```

`Route.discovery(requirements=[...])` 的意思是："找一个节点，其 `capabilities` 里 `provider == "minimax"`，把 `model_call` 路由给它。" 后续 ch2/ch3 会用到另一种 — `Route.strict(ids=[...])` — 严格指定 NodeId（用于 `tool_exec`）。

> 完整的 `Route` 语义与 Engine 消息流详见 [docs/api/reference/bus.md](../reference/bus.md) 与 [engine.md](../reference/engine.md)。

## 拆解 3：如何组装起来跑通 — Engine 装配

前两段是"零件"：Provider 是模型接入、AgentConfig 是 agent 定义。这一段把它们装成一个能跑的对象。

### Bus 是消息总线

```python
bus = Bus()
```

所有 node（模型、工具、engine 本身）都挂在 Bus 上。Bus 不关心 node 的具体类型 — 它只做消息路由。

### `provider.connect_to_bus` 注册模型节点

```python
provider = MiniMaxProvider(config=MiniMaxConfig.from_env())
await provider.connect_to_bus(bus=bus, node_id=NodeId("model/main"))
```

这一步把 provider 包装成一个 `model` 类型的 node 注册到 bus，节点 ID 是 `model/main`。注册后，Engine 就能通过 `Route.discovery(requirements=[("provider", "minimax")])` 找到它。

### `EngineBuilder.new(...).build(...)` 装配 Engine

```python
engine = await EngineBuilder.new(buses=[bus]).build(config=agent_config)
```

`EngineBuilder` 在 build 时做 fail-fast 校验：Strict routes 引用的 NodeId 都 online、Discovery routes 的 capability 至少一个节点匹配。校验不通过会立刻 `BuildError`，不会到运行时才崩。

### `engine.run(state, user_input)` 跑通一个 ReAct round

```python
state = EngineState()
out = await engine.run(state=state, user_input="用一句话介绍北京。")
```

`EngineState` 持有对话历史（`state.messages`），跨多次 `engine.run()` 累积（ch2 会演示）。`engine.run` 内部跑一轮 ReAct 循环：广播 `model_call` → 等待 `model_response` → 模型有 `tool_calls` 则继续（ch2+）→ 纯文本则收尾。`turn_count` 计数模型调用次数。

## 下一节

→ [conversation.md](conversation.md) — 注册本地 MCP 工具节点，让模型能调本地 tool（`McpNode.local`）。

## 切换 Provider

把 MiniMax 换成其他 provider 通常改 2-3 行。**本节 4 个示例都实跑过**（除 Anthropic 因本地无 key 仅展示代码）。

### 公共注意

- `OpenAIProvider` 报 `provider="openai"`。多 OpenAI 兼容厂商共存时（如同时挂 DeepSeek + 阿里百炼），需用 `Route.strict(ids=[NodeId(...)])` 给每个 provider 唯一 NodeId；不要用 `Route.discovery`，否则多 OpenAI 节点会全匹配
- 早期版本曾有 `base_url` 字段 + 隐式拼接 `{/v1/chat/completions}`；2026-07-02 起改为 `endpoint`（完整 URL），无拼接 — 见 [explanation/上下文拼装机制.md](../../explanation/上下文拼装机制.md) 的"不变量 & 限制"段
- `DeepSeekProvider` 对 `reasoning_content` 块有专门处理；`OpenAIProvider` 走兼容模式也能识别但只把内容当主回答

### DeepSeek — 用 `DeepSeekProvider`（推荐）

`DeepSeekConfig` 是 Python dataclass（不是 pyo3 wrap），无 `from_env`，直接构造：

```python
import os
from arf import DeepSeekConfig, DeepSeekProvider

provider = DeepSeekProvider(config=DeepSeekConfig(
    api_key=os.environ["DEEPSEEK_API_KEY"],
    models=["deepseek-chat"],
))
# 用唯一 NodeId + strict route
await provider.connect_to_bus(bus=bus, node_id=NodeId("model/deepseek"))

engine = await EngineBuilder.new(buses=[bus]).build(
    config=AgentConfig(
        agent_id="tutorial-hello",
        system_prompt_template="你是一个简洁的中文助手。",
        routes={
            "model_call": Route.strict(ids=[NodeId("model/deepseek")]),
        },
    ),
)
```

跑（key inline 传，不入任何文件）：

```bash
export DEEPSEEK_API_KEY='sk-...'
.venv/bin/python /tmp/hello.py
unset DEEPSEEK_API_KEY
```

实测输出（用户 prompt "用一句话介绍北京。"）：

```text
out (34 chars): '北京是中华人民共和国的首都，是一座融合古老历史与现代文明的文化名城。'
```

### DeepSeek — 用 `OpenAIProvider`（OpenAI 兼容模式）

`OpenAIConfig` 接受完整 URL 作为 `endpoint`，不拼接路径。用 DeepSeek 时显式写完整 endpoint：

```python
from arf import OpenAIConfig, OpenAIProvider

provider = OpenAIProvider(config=OpenAIConfig(
    api_key=os.environ["DEEPSEEK_API_KEY"],
    endpoint="https://api.deepseek.com/v1/chat/completions",  # 完整 URL
    models=["deepseek-chat"],
))
await provider.connect_to_bus(bus=bus, node_id=NodeId("model/deepseek"))
# Route.strict ids=[NodeId("model/deepseek")]
```

实测输出（同样 prompt）：

```text
out (30 chars): '北京是中国的首都，是一座融合古老历史与现代文明的国际大都市。'
```

### 阿里百炼 — 用 `OpenAIProvider`（兼容模式）

阿里百炼兼容 OpenAI 协议，直接传完整 endpoint：

```python
from arf import OpenAIConfig, OpenAIProvider

provider = OpenAIProvider(config=OpenAIConfig(
    api_key=os.environ["DASHSCOPE_API_KEY"],
    endpoint="https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",  # 完整 URL
    models=["qwen3.7-max-preview"],  # 有免费额度
))
await provider.connect_to_bus(bus=bus, node_id=NodeId("model/bailian"))
# Route.strict ids=[NodeId("model/bailian")]
```

跑（推荐用 `qwen3.7-max-preview` 等有免费额度的模型；首次调用前先在控制台"免费模型"列表确认）：

```bash
export DASHSCOPE_API_KEY='sk-...'
.venv/bin/python /tmp/hello.py
unset DASHSCOPE_API_KEY
```

实测输出（`qwen3.7-max-preview`）：

```text
out (41 chars): '北京是中国的首都，一座完美融合了深厚历史底蕴与现代都市活力的政治、文化和科技中心。'
```

### Anthropic — 用 `AnthropicProvider`

```python
from arf import AnthropicConfig, AnthropicProvider

provider = AnthropicProvider(config=AnthropicConfig.from_env())
# 默认 endpoint="https://api.anthropic.com/v1/messages"
# Route.discovery 同步改：("provider", "anthropic")
```

> 未实跑（本地无 Anthropic key）；Claude 系列需 `ANTHROPIC_API_KEY` 环境变量

### DeepSeek Anthropic 兼容端点

`AnthropicConfig.endpoint` 直接传 DeepSeek 提供的 `/anthropic` 端点（完整 URL）：

```python
from arf import AnthropicConfig, AnthropicProvider

provider = AnthropicProvider(config=AnthropicConfig(
    api_key=os.environ["DEEPSEEK_API_KEY"],
    endpoint="https://api.deepseek.com/anthropic",  # 完整 URL
    models=["deepseek-chat"],
))
# Route.discovery 同步改：("provider", "anthropic")
```

> 未实跑（DeepSeek 的 Anthropic 兼容端点可能与 AnthropicConfig 默认 schema 不完全一致）。如需 Claude 系模型，推荐用上面原生 AnthropicProvider。