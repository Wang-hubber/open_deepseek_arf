# 连接真实模型 — ModelAdapter 供应商配置

> 🎯 Diátaxis 桶位：**Tutorials**（入门教程，真实 LLM 版本）

## 为什么

ARF 的 `ModelAdapter` 抽象了 4 个内置供应商，并支持任何 OpenAI / Anthropic API 兼容的模型服务。本章介绍全部 4 个供应商的最小配置方式，并跑一个完整端到端示例（用 MiniMax）。`McpNode` 与工具调用放在后续章节。

## ModelAdapter 内置供应商

ARF Python 绑定导出 4 个 Config + Provider 对。每对都通过 `from_env()` 读环境变量（也支持直接构造传参）。

| Provider | Config 类 | 构造示例 | 环境变量 | 备注 |
|---|---|---|---|---|
| DeepSeek | `DeepSeekConfig` | `DeepSeekConfig.from_env()` | `DEEPSEEK_API_KEY` | 推荐新手起手；充 10 块够用很久 |
| OpenAI | `OpenAIConfig` | `OpenAIConfig.from_env()` | `OPENAI_API_KEY` | 同时是 OpenAI-API 兼容协议入口（见下） |
| MiniMax | `MiniMaxConfig` | `MiniMaxConfig.from_env()` | `MINIMAX_API_KEY` | TokenPlan 包月；用量大时省钱 |
| Anthropic | `AnthropicConfig` | `AnthropicConfig.from_env()` | `ANTHROPIC_API_KEY` | Claude 系列；API 协议自成一派 |

每个 Provider 都通过 `provider.connect_to_bus(bus, NodeId("model/<name>"))` 注册到 Bus。`AgentConfig.routes` 用 `Route.discovery(requirements=[("provider", "<key>")])` 让 Engine 自动选路由（`<key>` 是 Provider 上报的能力字段之一）。

## OpenAI / Anthropic API 兼容协议

理论上 ARF 可以接入任何实现 **OpenAI Chat Completions 协议** 或 **Anthropic Messages 协议** 的模型服务：

- **OpenAI 兼容**（推荐用于国产 / 自部署模型）：用 `OpenAIConfig` + 自定义 `base_url`。
  - 阿里百炼兼容模式：`https://dashscope.aliyuncs.com/compatible-mode/v1`
  - Moonshot：`https://api.moonshot.cn/v1`
  - vLLM / Ollama 自部署：`http://localhost:8000/v1` 等
- **Anthropic 兼容**：用 `AnthropicConfig` + 自定义 `base_url`（参考 [Anthropic API 文档](https://docs.anthropic.com/)）。
  - DeepSeek Anthropic 兼容端点：`base_url="https://api.deepseek.com"`, `api_path="/anthropic"`

由于各服务具体支持的 `models` 列表不同，直接构造时需明确传入 `models=[...]`。

## 代码

完整可跑端到端示例（保存为 `/tmp/ch1.py`，用 MiniMax）：

```python
import asyncio
from arf import (
    Bus, NodeId, Route,
    MiniMaxConfig, MiniMaxProvider,
    AgentConfig, EngineBuilder, EngineState,
)


async def main():
    bus = Bus()

    # 注册真实 MiniMax 模型节点
    provider = MiniMaxProvider(config=MiniMaxConfig.from_env())
    await provider.connect_to_bus(bus=bus, node_id=NodeId("model/main"))

    engine = await EngineBuilder.new(buses=[bus]).build(
        config=AgentConfig(
            agent_id="tutorial-ch1",
            system_prompt_template="你是一个简洁的中文助手。",
            routes={
                "model_call": Route.discovery(requirements=[("provider", "minimax")]),
            },
        ),
    )

    state = EngineState()
    out = await engine.run(state=state, user_input="用一句话介绍北京。")
    print(f"out={out!r}")
    print(f"messages={len(state.messages)}, turn_count={state.turn_count}")
    await bus.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
```

## 运行

```bash
export MINIMAX_API_KEY='sk-...'   # 你的 MiniMax key，inline 填入
.venv/bin/python /tmp/ch1.py
unset MINIMAX_API_KEY              # 跑完立即清掉，不留痕
```

预期 stdout（实际 LLM 回复会有变化）：

```text
out='北京是中华人民共和国的首都，是一座拥有三千多年建城史和八百多年建都史的历史文化名城，也是中国的政治、文化、国际交往和科技创新中心。'
messages=3, turn_count=1
```

> 注：MiniMax 默认模型带思考模式，回复里可能出现 `<think>...</think>` 推理段；`messages=3` 是 user + assistant + 引擎内部条，`turn_count=1` 是 1 次模型调用。

## 下一节

→ [conversation.md](conversation.md) — 注册本地 MCP 工具节点，让模型能调本地 tool。

## 切换 Provider

4 个内置供应商 + 任意 OpenAI / Anthropic 兼容服务，构造方式都是 2-3 行：

```python
# DeepSeek（推荐新手）
from arf import DeepSeekConfig, DeepSeekProvider
provider = DeepSeekProvider(config=DeepSeekConfig.from_env())
# Route.discovery 同步改：("provider", "deepseek")

# OpenAI
from arf import OpenAIConfig, OpenAIProvider
provider = OpenAIProvider(config=OpenAIConfig.from_env())
# Route.discovery 同步改：("provider", "openai")

# Anthropic
from arf import AnthropicConfig, AnthropicProvider
provider = AnthropicProvider(config=AnthropicConfig.from_env())
# Route.discovery 同步改：("provider", "anthropic")

# OpenAI-API 兼容服务（如阿里百炼兼容模式 / Moonshot / vLLM）
from arf import OpenAIConfig, OpenAIProvider
provider = OpenAIProvider(config=OpenAIConfig(
    api_key="...",
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    models=["qwen-plus"],
))
# Route.discovery 同步改：("provider", "openai")

# Anthropic-API 兼容服务（如 DeepSeek anthropic 端点）
from arf import AnthropicConfig, AnthropicProvider
provider = AnthropicProvider(config=AnthropicConfig(
    api_key="...",
    base_url="https://api.deepseek.com",
    api_path="/anthropic",
    models=["deepseek-chat"],
))
# Route.discovery 同步改：("provider", "anthropic")
```