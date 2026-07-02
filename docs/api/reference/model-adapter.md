# ARF ModelAdapter — 模型适配器 API 参考

> **Phase 4** · 可插拔多供应商 · `from arf import DeepSeekProvider, ...`

## 概述

ModelAdapter 是 ARF 框架与外部大语言模型 API 之间的翻译层。它将框架内部的 `ModelMessage` 格式转换为各供应商的原生 API 格式，并通过 HTTP 调用模型。支持两种使用模式：

```
┌─────────────────────────────────────────────────────┐
│  模式一：直接调用（测试/调试）                          │
│                                                       │
│  Python 代码 ──→ Provider.chat() ──→ HTTP ──→ API    │
│                    · DeepSeekProvider                 │
│                    · OpenAIProvider                   │
│                    · AnthropicProvider                │
└─────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────┐
│  模式二：Bus 集成（生产环境）                           │
│                                                       │
│  Engine ──→ Bus ──→ ModelAdapterNode ──→ HTTP ──→ API│
│              ↑         │                              │
│              └─ model_response ──────────────────────┘│
└─────────────────────────────────────────────────────┘
```

### 供应商矩阵

| 供应商 | Python 类 | API 格式 | 端点 | 思考模式 | 工具调用 |
|--------|----------|---------|------|---------|---------|
| DeepSeek | `DeepSeekProvider` | OpenAI 兼容 | `/chat/completions` | ✅ `reasoning_content` | ✅ |
| DeepSeek (Anthropic) | `AnthropicProvider` | Anthropic Messages | `/anthropic` | ✅ `thinking` block | ✅ |
| OpenAI | `OpenAIProvider` | OpenAI 标准 | `/v1/chat/completions` | — | ✅ |
| Anthropic | `AnthropicProvider` | Anthropic Messages | `/v1/messages` | — | ✅ |
| MiniMax | `MiniMaxProvider` | OpenAI 兼容 | `/v1/chat/completions` | — | ✅ |
| 阿里百炼 (OpenAI 兼容) | `OpenAIProvider` | OpenAI 兼容 | `/compatible-mode/v1/chat/completions` | — | ✅ |

### 设计意图——为什么有两种使用模式

**直接调用模式**让你在开发阶段快速验证模型行为——无需启动 Bus、无需配置节点，直接拿到回复。**Bus 集成模式**将 Provider 注册为 Bus 上的被动节点，Engine 通过发送 `model_call` 消息自动发现并调用模型。两种模式共享同一个 Provider 实例，代码完全复用。

### 适用场景

- 需要在 Python 代码中调用大语言模型（同步/流式）
- 需要多个模型供应商共存，按模型名动态切换
- 需要通过 Bus 消息总线将模型调用接入 Agent 主循环
- 需要工具的 function calling 能力
- 需要 DeepSeek 的思考模式（reasoning）

---

## 快速上手

### 安装

ModelAdapter 是 `py-arf` 包的一部分，随 Bus 一起安装：

```bash
pip install -e "py-arf[dev]" -i https://pypi.mirrors.ustc.edu.cn/simple
```

### 第一个 Provider：直接对话

最简例子——创建 DeepSeek 配置、构造 Provider、发送一条消息、打印回复。

```python
import asyncio
from arf import DeepSeekConfig, DeepSeekProvider, ModelMessage, ModelParams

async def main():
    # 1. 配置 —— 只需要 API key 和模型列表
    config = DeepSeekConfig(
        api_key="sk-xxx",               # 你的 API key
        models=["deepseek-v4-flash"],   # 支持的模型
    )

    # 2. 创建 Provider
    provider = DeepSeekProvider(config=config)
    print(f"Provider: {provider.name}")          # Provider: deepseek
    print(f"Models: {provider.supported_models}") # Models: ['deepseek-v4-flash']

    # 3. 构造消息
    messages = [
        ModelMessage(role="system", content="You are a helpful assistant."),
        ModelMessage(role="user", content="What is 2+2?"),
    ]

    # 4. 设置参数
    params = ModelParams(temperature=0.7, max_tokens=256)

    # 5. 调用模型
    response = await provider.chat(
        model_name="deepseek-v4-flash",
        messages=messages,
        tools=[],
        params=params,
    )

    # 6. 读取回复
    print(f"Response: {response.message.content}")
    print(f"Tokens: {response.usage.input_tokens} → {response.usage.output_tokens}")
    print(f"Finish: {response.finish_reason}")

asyncio.run(main())
```

**运行输出：**（耗时 ~1.5s）

```
Provider: deepseek
Models: ['deepseek-v4-flash']
Response: 2+2 equals 4.
Tokens: 17 → 7
Finish: stop
```

### 流式响应

用 `chat_stream()` 替代 `chat()`，逐 chunk 打印——适合需要实时反馈的场景。

```python
import asyncio
from arf import DeepSeekConfig, DeepSeekProvider, ModelMessage, ModelParams

async def main():
    config = DeepSeekConfig(api_key="sk-xxx", models=["deepseek-v4-flash"])
    provider = DeepSeekProvider(config=config)

    messages = [ModelMessage(role="user", content="Count from 1 to 5.")]
    params = ModelParams(temperature=0.7, max_tokens=128)

    # chat_stream 返回 (chunks, response) 元组
    chunks, response = await provider.chat_stream(
        model_name="deepseek-v4-flash",
        messages=messages,
        tools=[],
        params=params,
    )

    for chunk in chunks:
        if chunk.chunk_type == "text":
            print(chunk.content, end="", flush=True)
        elif chunk.chunk_type == "reasoning":
            print(f"\n[思考] {chunk.reasoning}")

    print(f"\n\nTokens: {response.usage.total_tokens}")

asyncio.run(main())
```

**运行输出：**（逐字打印，耗时 ~2s）

```
1, 2, 3, 4, 5.

Tokens: 26
```

### 工具调用（Function Calling）

注册一个 `get_weather` 工具，模型识别到用户意图后返回 `tool_calls` 而非文本回复。

```python
import asyncio
from arf import DeepSeekConfig, DeepSeekProvider, ModelMessage, ModelParams, ToolDef

async def main():
    config = DeepSeekConfig(api_key="sk-xxx", models=["deepseek-v4-flash"])
    provider = DeepSeekProvider(config=config)

    # 定义工具 —— JSON Schema 格式
    tools = [
        ToolDef(
            name="get_weather",
            description="Get current weather for a city",
            parameters={
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "City name"}
                },
                "required": ["city"],
            },
        )
    ]

    messages = [ModelMessage(role="user", content="What's the weather in Beijing?")]
    params = ModelParams()

    response = await provider.chat(
        model_name="deepseek-v4-flash",
        messages=messages,
        tools=tools,
        params=params,
    )

    print(f"Finish: {response.finish_reason}")
    if response.tool_calls:
        for tc in response.tool_calls:
            print(f"Tool: {tc.name}")
            print(f"Args: {tc.arguments}")

asyncio.run(main())
```

**运行输出：**（耗时 ~2s）

```
Finish: tool_calls
Tool: get_weather
Args: {'city': 'Beijing'}
```

当 `finish_reason` 为 `"tool_calls"` 时，`response.message.content` 通常为空——模型期望你执行工具并回传结果。下一节展示完整的工具闭环。

### 工具调用闭环

第一轮拿到 `tool_calls` → 模拟执行工具 → 第二轮把结果回传 → 模型基于结果最终回复。

```python
import json
import asyncio
from arf import DeepSeekConfig, DeepSeekProvider, ModelMessage, ModelParams, ToolDef

async def main():
    config = DeepSeekConfig(api_key="sk-xxx", models=["deepseek-v4-flash"])
    provider = DeepSeekProvider(config=config)

    tools = [
        ToolDef(
            name="get_weather",
            description="Get current weather for a city",
            parameters={
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        )
    ]

    # ── 第一轮：模型决定调用工具 ──
    msgs = [ModelMessage(role="user", content="What's the weather in Beijing?")]
    r1 = await provider.chat(
        model_name="deepseek-v4-flash",
        messages=msgs,
        tools=tools,
        params=ModelParams(),
    )

    print(f"第一轮 finish: {r1.finish_reason}")
    # 第一轮 finish: tool_calls

    tc = r1.tool_calls[0]
    print(f"模型想调用: {tc.name}({tc.arguments})")

    # ── 模拟工具执行 ──
    tool_result = "Sunny, 25°C, humidity 60%"

    # ── 构造第二轮消息 ──
    # 关键：assistant 消息需要用 extra.tool_calls 保存第一轮的 tool_calls
    msgs2 = [
        ModelMessage(role="user", content="What's the weather in Beijing?"),
        ModelMessage(
            role="assistant",
            content="",
            extra={
                "tool_calls": [{
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments),
                    },
                }]
            },
        ),
        ModelMessage(
            role="tool",
            content=tool_result,
            tool_call_id=tc.id,
            name=tc.name,
        ),
    ]

    # ── 第二轮：模型基于工具结果回复 ──
    r2 = await provider.chat(
        model_name="deepseek-v4-flash",
        messages=msgs2,
        tools=[],
        params=ModelParams(),
    )

    print(f"第二轮 finish: {r2.finish_reason}")
    print(f"最终回复: {r2.message.content}")
    print(f"总 Token: {r2.usage.total_tokens}")

asyncio.run(main())
```

**运行输出：**（耗时 ~4s，两轮 HTTP 调用）

```
第一轮 finish: tool_calls
模型想调用: get_weather({'city': 'Beijing'})
第二轮 finish: stop
最终回复: The weather in Beijing is currently sunny, with a temperature of 25°C
           and a humidity level of 60%. It's a pleasant day.
总 Token: 145
```

> **注意**：回传 tool_calls 时必须将 `tc.arguments`（Python dict）用 `json.dumps()` 转为 JSON 字符串——这是 OpenAI API 格式的要求。如果直接用 dict，API 会返回 400 错误。

### 连接 Bus（生产模式）

将 Provider 注册为 Bus 节点——Engine 通过发送 `model_call` 消息来调用模型，无需直接持有 Provider 引用。

```python
import asyncio
from arf import Bus, NodeId, NodeInfo, MessageFilter, ToMatch
from arf import DeepSeekConfig, DeepSeekProvider, ModelMessage, ModelParams

async def main():
    # 1. 创建 Bus
    bus = Bus()

    # 2. 创建 Provider 并连接到 Bus
    config = DeepSeekConfig(api_key="sk-xxx", models=["deepseek-v4-flash"])
    provider = DeepSeekProvider(config=config)

    node = await provider.connect_to_bus(bus=bus, node_id=NodeId(id="model/deepseek"))
    print(f"节点已连接: {node}")

    # 3. 创建 Engine 端 —— 模拟 Engine 发 model_call
    engine_info = NodeInfo(
        node_id="engine/main",
        node_type="engine",
        capabilities={},
    )
    engine_flt = MessageFilter(
        types=["model_response", "model_response_chunk"],
        to_match=ToMatch.BroadcastAndDirectedToMe,
    )
    engine = await bus.connect(info=engine_info, filter=engine_flt)

    # 4. Engine 发送 model_call → Bus → ModelAdapterNode → DeepSeek API
    await engine.send(msg_type="model_call", to=[NodeId(id="model/deepseek")], payload={
        "messages": [
            {"role": "user", "content": "Say hello in one word."}
        ],
        "tools": [],
        "model_params": {
            "temperature": None,
            "max_tokens": None,
            "thinking_enabled": False,
            "extra": None,
        },
        "stream": False,
    })

    # 5. Engine 收到 model_response
    msg = await engine.recv()
    response = msg.payload
    print(f"Finish: {response['finish_reason']}")
    print(f"Content: {response['message']['content']}")

    # 6. 清理
    await engine.disconnect()
    await node.shutdown()
    await bus.shutdown()
    print("Done.")

asyncio.run(main())
```

**运行输出：**（耗时 ~2s）

```
节点已连接: ModelAdapterNode(node_id='model/deepseek')
Finish: stop
Content: Hello!
Done.
```

### 多供应商共存

三个 Provider 连接到同一个 Bus，Engine 根据 `provider` 名选择目标：

```python
import asyncio
from arf import Bus, NodeId
from arf import (
    DeepSeekConfig, DeepSeekProvider,
    OpenAIConfig, OpenAIProvider,
    AnthropicConfig, AnthropicProvider,
)

async def main():
    bus = Bus()

    # 创建三个 Provider 并全部连接到 Bus
    ds = DeepSeekProvider(
        config=DeepSeekConfig(api_key="sk-ds", models=["deepseek-v4-flash"])
    )
    oa = OpenAIProvider(
        config=OpenAIConfig(api_key="sk-oa", models=["gpt-4o"])
    )
    an = AnthropicProvider(
        config=AnthropicConfig(api_key="sk-an", models=["claude-sonnet-4-6"])
    )

    node_ds = await ds.connect_to_bus(bus=bus, node_id=NodeId(id="model/deepseek"))
    node_oa = await oa.connect_to_bus(bus=bus, node_id=NodeId(id="model/openai"))
    node_an = await an.connect_to_bus(bus=bus, node_id=NodeId(id="model/anthropic"))

    # 查看 Bus 上的所有模型节点
    graph = bus.graph()
    for n in graph.nodes:
        if n.node_type == "model":
            caps = n.capabilities
            print(f"  {n.node_id} → {caps['provider']} : {caps['models']}")

    # 根据 provider 路由 model_call 到对应节点
    print(f"\n在线模型节点: {len([n for n in graph.nodes if n.node_type == 'model'])}")

    # 清理
    await node_ds.shutdown()
    await node_oa.shutdown()
    await node_an.shutdown()
    await bus.shutdown()

asyncio.run(main())
```

**运行输出：**（耗时 <10ms，节点顺序取决于 Bus 内部 HashMap 迭代，不固定）

```
  model/openai → openai : ['gpt-4o']
  model/deepseek → deepseek : ['deepseek-v4-flash']
  model/anthropic → anthropic : ['claude-sonnet-4-6']

在线模型节点: 3
```

> **注意**：此例中 api_key 为占位符，实际调用模型时需要有效的 API key。但节点注册和 Bus 生命周期操作不依赖 API key 的有效性——你可以在没有 API key 的情况下验证节点连接/断开的正确性。

---

## 核心概念

### Provider 抽象

```
┌──────────────────────────────────────────┐
│              Provider trait               │
│                                            │
│  name()          → "deepseek" / "openai"  │
│  supported_models() → ["model-a", ...]    │
│  chat()          → ModelResponsePayload    │
│  chat_stream()   → (chunks, payload)       │
│  connect_to_bus() → ModelAdapterNode       │  ← Python 端扩展
└──────────────────────────────────────────┘
         ↑                ↑
    DeepSeekProvider   OpenAIProvider   AnthropicProvider
    (OpenAI 兼容)      (OpenAI 标准)    (Anthropic Messages)
```

三个 Provider 实现同一套接口，差异仅在于 `name` 返回值和 HTTP 请求格式。Python 端新增 `connect_to_bus()` 方法——它将 `Arc<ConcreteProvider>` 升级为 `Arc<dyn Provider>` 后创建 Bus 节点。

### 消息流转（Bus 模式）

```
Engine                  Bus                 ModelAdapterNode        HTTP
  │                      │                       │                   │
  │── model_call ──────→│                       │                   │
  │                      │─── model_call ──────→│                   │
  │                      │                       │── POST /chat ──→│
  │                      │                       │←── SSE/JSON ────│
  │                      │←── model_response ───│                   │
  │←── model_response ───│                       │                   │
  │                      │                       │                   │
```

`model_call` 的 payload 是 `ModelCallPayload` 的 JSON 序列化：

```python
{
    "messages": [
        {"role": "user", "content": "Hello"},
    ],
    "tools": [],
    "model_params": {
        "temperature": None,
        "max_tokens": None,
        "thinking_enabled": False,
        "extra": None,
    },
    "stream": True,   # 默认 True —— 流式优先
}
```

流式模式下，每个 SSE chunk 作为独立的 `model_response_chunk` 消息发送，最后一条是完整的 `model_response`。

### 节点生命周期

```
connect_to_bus()
  │
  ├─→ 广播 node_online (capabilities 含 provider + models)
  │
  ├─→ 循环监听 model_call 消息
  │     ├─ 收到 model_call → 调用 Provider
  │     └─ 收到 shutdown 信号 → 退出循环
  │
  └─→ shutdown()
        ├─→ 发送 node_offline
        └─→ 从 Bus graph 移除
```

`ModelAdapterNode` 不能直接构造——必须通过 `provider.connect_to_bus()` 创建。创建后立即可在 `bus.graph()` 中看到该节点（`node_type: "model"`，`capabilities` 包含 `provider` 和 `models`）。

### 思考模式（DeepSeek）

DeepSeek 的思考模式通过两个参数控制：

| 参数 | 位置 | 含义 |
|------|------|------|
| `ModelParams.thinking_enabled` | `thinking.type` | `True` → `"enabled"`，`False` → `"disabled"`。始终显式发送 |
| `ModelParams.extra["reasoning_effort"]` | 顶层 `reasoning_effort` | `"high"` / `"medium"` / `"low"`。仅 `enabled` 时有效 |

模型的推理过程通过 `ModelMessage.extra["reasoning_content"]` 返回——在 `ModelResponsePayload.message.extra` 中读取。

```python
params = ModelParams(
    thinking_enabled=True,
    extra={"reasoning_effort": "high"},
)

response = await provider.chat(
    model_name="deepseek-v4-pro",
    messages=messages,
    tools=[],
    params=params,
)

reasoning = response.message.extra.get("reasoning_content")
if reasoning:
    print(f"模型思考过程: {reasoning}")
```

---

## API 参考

### DeepSeekConfig

```python
from arf import DeepSeekConfig

# 方式 1：直接构造（最常见 — 显式传 key 和 models）
config = DeepSeekConfig(
    api_key="sk-xxx",
    models=["deepseek-v4-flash"],
    endpoint="https://api.deepseek.com/chat/completions",  # 可选 — 默认就是 DeepSeek 公共 endpoint
    timeout_secs=320,           # 可选
    max_retries=3,              # 可选
)

# 方式 2：从 DEEPSEEK_API_KEY 环境变量读 key
config = DeepSeekConfig.from_env()  # 需设置 DEEPSEEK_API_KEY

# 方式 3：取默认（空 key + 空 models，调试用）
config = DeepSeekConfig.default()  # endpoint 已是 DeepSeek 公共
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `api_key` | `str` | 必填 | DeepSeek API key |
| `models` | `list[str]` | 必填 | 支持的模型名列表 |
| `endpoint` | `str` | `"https://api.deepseek.com/chat/completions"` | **完整请求 URL（含 path）**；任意 OpenAI 兼容服务都可指 |
| `timeout_secs` | `int` | `320` | HTTP 请求超时（秒） |
| `max_retries` | `int` | `3` | 可重试错误（429/5xx）的最大重试次数 |

**静态方法：**

| 方法 | 说明 |
|------|------|
| `DeepSeekConfig.default() -> Self` | 返回默认配置（空 key + 空 models + 默认 endpoint） |
| `DeepSeekConfig.from_env() -> Result<Self, ProviderError>` | 从 `DEEPSEEK_API_KEY` 读 key；缺 key 时报 `"DEEPSEEK_API_KEY not set"` |

**属性：** 所有构造参数均可通过同名属性读取（`config.api_key`、`config.models` 等）。

```python
config = DeepSeekConfig(api_key="sk-xxx", models=["deepseek-v4-flash", "deepseek-v4-pro"])
print(config.models)        # ['deepseek-v4-flash', 'deepseek-v4-pro']
print(config.timeout_secs)  # 320
print(config)               # DeepSeekConfig(endpoint='https://api.deepseek.com/chat/completions', models=[...])
```

---

### OpenAIConfig

```python
from arf import OpenAIConfig

config = OpenAIConfig(
    api_key="sk-xxx",
    models=["gpt-4o"],
    endpoint="https://api.openai.com/v1/chat/completions",  # 可选
    timeout_secs=320,           # 可选
    max_retries=3,              # 可选
)
```

参数和属性与 `DeepSeekConfig` 一致，仅默认 `endpoint` 不同。`endpoint` 也可指向任何 OpenAI 兼容的服务（如 vLLM、Ollama、阿里百炼兼容模式）：

```python
# 本地 Ollama — 用 OpenAI 兼容协议
config = OpenAIConfig(
    api_key="ollama",           # Ollama 不需要真实 key，但必须非空
    models=["llama3.2"],
    endpoint="http://localhost:11434/v1/chat/completions",
)

# 阿里百炼兼容模式
config = OpenAIConfig(
    api_key=os.environ["DASHSCOPE_API_KEY"],
    endpoint="https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
    models=["qwen3.7-max-preview"],
)
```

**静态方法：**

| 方法 | 说明 |
|------|------|
| `OpenAIConfig.default() -> Self` | 返回默认配置 |
| `OpenAIConfig.from_env() -> Result<Self, ProviderError>` | 从 `OPENAI_API_KEY` 读 key |

---

### AnthropicConfig

```python
from arf import AnthropicConfig

config = AnthropicConfig(
    api_key="sk-xxx",
    models=["claude-sonnet-4-6"],
    endpoint="https://api.anthropic.com/v1/messages",  # 可选 — 默认就是 Anthropic 公共 endpoint
    timeout_secs=320,           # 可选
    max_retries=3,              # 可选
)
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `api_key` | `str` | 必填 | API key |
| `models` | `list[str]` | 必填 | 支持的模型名列表 |
| `endpoint` | `str` | `"https://api.anthropic.com/v1/messages"` | **完整请求 URL（含 path）**；DeepSeek Anthropic 兼容端点直接 `endpoint="https://api.deepseek.com/anthropic"` |
| `timeout_secs` | `int` | `320` | HTTP 请求超时（秒） |
| `max_retries` | `int` | `3` | 最大重试次数 |

**静态方法：**

| 方法 | 说明 |
|------|------|
| `AnthropicConfig.default() -> Self` | 返回默认配置 |
| `AnthropicConfig.from_env() -> Result<Self, ProviderError>` | 从 `ANTHROPIC_API_KEY` 读 key |

`endpoint` 直接传完整 URL — DeepSeek 的 Anthropic 兼容端点示例：

```python
# DeepSeek 的 Anthropic 兼容端点（注意 endpoint 是完整 URL，不再分 base_url + api_path）
config = AnthropicConfig(
    api_key="sk-xxx",
    models=["deepseek-chat"],
    endpoint="https://api.deepseek.com/anthropic",  # 完整 URL
)
```

---

### MiniMaxConfig

```python
from arf import MiniMaxConfig

# 方式 1：从 MINIMAX_API_KEY（或 fallback MINIMAX_TOKEN）环境变量读
config = MiniMaxConfig.from_env()

# 方式 2：直接构造
config = MiniMaxConfig(
    api_key="sk-xxx",
    models=["MiniMax-M3"],
    endpoint="https://api.minimaxi.com/v1/chat/completions",  # 可选
    timeout_secs=320,
    max_retries=3,
)

# 方式 3：默认配置
config = MiniMaxConfig.default()
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `api_key` | `str` | 必填 | MiniMax API key |
| `models` | `list[str]` | 必填 | 支持的模型名列表 |
| `endpoint` | `str` | `"https://api.minimaxi.com/v1/chat/completions"` | **完整请求 URL** |
| `timeout_secs` | `int` | `320` | HTTP 请求超时（秒） |
| `max_retries` | `int` | `3` | 最大重试次数 |

**静态方法：**

| 方法 | 说明 |
|------|------|
| `MiniMaxConfig.default() -> Self` | 默认配置（`models=["MiniMax-M3"]`） |
| `MiniMaxConfig.from_env() -> Result<Self, ProviderError>` | 读 `MINIMAX_API_KEY`，fallback 到 `MINIMAX_TOKEN` |

---

### DeepSeekProvider / OpenAIProvider / AnthropicProvider

三个 Provider 的 API 完全一致（实现 `Provider` trait），以下以 `DeepSeekProvider` 为例。

```python
from arf import DeepSeekConfig, DeepSeekProvider

config = DeepSeekConfig(api_key="sk-xxx", models=["deepseek-v4-flash"])
provider = DeepSeekProvider(config=config)
```

**属性：**

| 属性 | 类型 | 说明 |
|------|------|------|
| `name` | `str` | 供应商标识：`"deepseek"` / `"openai"` / `"anthropic"` |
| `supported_models` | `list[str]` | 从 config 读取的模型列表 |

**方法：**

#### `await provider.chat(model_name, messages, tools, params)`

非流式对话，返回 `ModelResponsePayload`。

| 参数 | 类型 | 说明 |
|------|------|------|
| `model_name` | `str` | 模型名——必须在 `supported_models` 中或 API 支持 |
| `messages` | `list[ModelMessage]` | 对话历史 |
| `tools` | `list[ToolDef]` | 工具定义列表，空列表 `[]` 表示无工具 |
| `params` | `ModelParams` | 温度/max_tokens/thinking 等参数 |

| 返回值 | 类型 | 说明 |
|--------|------|------|
| `response` | `ModelResponsePayload` | 完整模型回复 |

| 异常 | 触发场景 |
|------|---------|
| `Exception` | 网络错误 / API 错误 (4xx/5xx) / 重试耗尽 / 响应解析失败 |

```python
response = await provider.chat(
    "deepseek-v4-flash",
    [ModelMessage(role="user", content="Hi")],
    [],
    ModelParams(temperature=0.7),
)
print(response.message.content)
print(response.usage.total_tokens)
```

#### `await provider.chat_stream(model_name=model_name, messages=messages, tools=tools, params=params)`

流式对话，返回 `(list[ModelResponseChunk], ModelResponsePayload)` 元组。

参数同 `chat()`。返回的 chunk 列表包含每个 SSE 事件（text/reasoning/tool_call/usage），`ModelResponsePayload` 是聚合后的完整回复。

```python
chunks, response = await provider.chat_stream(
    model_name="deepseek-v4-flash",
    messages=messages,
    tools=tools,
    params=params,
)
for c in chunks:
    if c.chunk_type == "text":
        print(c.content, end="")
```

#### `await provider.connect_to_bus(bus, node_id)`

将 Provider 注册为 Bus 上的 ModelAdapter 节点，返回 `ModelAdapterNode`。

| 参数 | 类型 | 说明 |
|------|------|------|
| `bus` | `Bus` | 目标 Bus 实例 |
| `node_id` | `NodeId` | 节点 ID，如 `NodeId(id="model/deepseek")` |

| 返回值 | 类型 | 说明 |
|--------|------|------|
| `node` | `ModelAdapterNode` | 已连接的节点，在后台监听 `model_call` 消息 |

```python
bus = Bus()
node = await provider.connect_to_bus(bus=bus, node_id=NodeId(id="model/deepseek"))
# node 现在在后台运行

# ... 通过 Bus 发送 model_call ...

await node.shutdown()
```

---

### ModelParams

```python
from arf import ModelParams

params = ModelParams(
    temperature=0.7,       # 可选：0.0–2.0，None = 供应商默认
    max_tokens=4096,       # 可选：输出 token 上限，None = 供应商默认
    thinking_enabled=False, # 可选：默认 False
    extra=None,            # 可选：供应商特定参数（Python dict）
)
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `temperature` | `float` or `None` | `None` | 采样温度，0.0–2.0 |
| `max_tokens` | `int` or `None` | `None` | 输出 token 硬限制 |
| `thinking_enabled` | `bool` | `False` | 是否开启思考模式（DeepSeek） |
| `extra` | `dict` or `None` | `None` | 供应商特定参数。DeepSeek: `{"reasoning_effort": "high"}` |

**属性：** 所有构造参数均可通过同名属性读取。

```python
params = ModelParams(temperature=0.7, thinking_enabled=True)
print(params.temperature)      # 0.7
print(params.thinking_enabled) # True
print(params.extra)            # None
```

> **注意**：`thinking_enabled` 是 Python `bool` 类型——传入 `"false"` 字符串不会工作（Python 中非空字符串为 truthy）。始终使用 `True`/`False`。

---

### ModelMessage

```python
from arf import ModelMessage

# 最简构造
msg = ModelMessage(role="user", content="Hello")

# 完整构造（带 tool_call_id、name、extra）
msg = ModelMessage(
    role="tool",
    content="file content here",
    tool_call_id="call_abc123",
    name="read_file",
    extra={"result_type": "text"},
)
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `role` | `str` | 必填 | `"user"` / `"assistant"` / `"system"` / `"tool"` |
| `content` | `str` | 必填 | 消息文本 |
| `tool_call_id` | `str` or `None` | `None` | `role="tool"` 时必填——对应的工具调用 ID |
| `name` | `str` or `None` | `None` | 发送者名称（如函数名） |
| `extra` | `dict` or `None` | `None` | 供应商特定数据。Provider 用它传递 `reasoning_content`、`tool_calls` 等 |

**属性：** 所有构造参数均可通过同名属性读取。

```python
msg = ModelMessage(
    role="assistant",
    content="",
    extra={"reasoning_content": "Let me think...", "citations": [1, 2, 3]},
)
print(msg.role)            # assistant
print(msg.content)         # (空字符串)
print(msg.extra["reasoning_content"])  # Let me think...
print(msg.tool_call_id)   # None
```

---

### ToolDef

```python
from arf import ToolDef

tool = ToolDef(
    name="get_weather",
    description="Get current weather for a city",
    parameters={
        "type": "object",
        "properties": {
            "city": {"type": "string", "description": "City name"}
        },
        "required": ["city"],
    },
)
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `name` | `str` | 必填 | 工具名——模型据此选择调用哪个工具 |
| `description` | `str` | 必填 | 自然语言描述——帮助模型理解工具用途 |
| `parameters` | `dict` | 必填 | JSON Schema 格式的参数定义 |

```python
print(tool.name)         # get_weather
print(tool.description)  # Get current weather for a city
print(tool.parameters["required"])  # ['city']
```

---

### ModelResponsePayload

> **只读类型**。由 `chat()` / `chat_stream()` 返回，不可直接构造。

| 属性 | 类型 | 说明 |
|------|------|------|
| `message` | `ModelMessage` | 模型回复消息。`role="assistant"`，content 为文本 |
| `tool_calls` | `list[ToolCall]` or `None` | 工具调用列表。`finish_reason="tool_calls"` 时非空 |
| `finish_reason` | `str` | `"stop"` / `"tool_calls"` / `"length"` / `"error"` |
| `usage` | `Usage` or `None` | Token 用量统计 |
| `id` | `str` | API 返回的响应 ID |
| `model` | `str` | 实际使用的模型名 |

```python
response = await provider.chat(model_name="deepseek-v4-flash", messages=messages, tools=[], params=params)

print(response.finish_reason)   # stop
print(response.model)           # deepseek-v4-flash
print(response.message.content) # (model text)

if response.tool_calls:
    for tc in response.tool_calls:
        print(f"  {tc.id}: {tc.name}({tc.arguments})")
```

---

### ToolCall

> **只读类型**。由 Provider 内部创建。

| 属性 | 类型 | 说明 |
|------|------|------|
| `id` | `str` | 工具调用 ID——回传结果时用 `ModelMessage(tool_call_id=id)` |
| `name` | `str` | 工具名 |
| `arguments` | `dict` | 解析后的参数（Python dict） |

```python
for tc in response.tool_calls:
    if tc.name == "get_weather":
        city = tc.arguments["city"]
        result = fetch_weather(city)  # 执行工具
        msgs.append(
            ModelMessage(role="tool", content=result, tool_call_id=tc.id, name=tc.name)
        )
```

---

### Usage

> **只读类型**。由 Provider 内部创建。

| 属性 | 类型 | 说明 |
|------|------|------|
| `input_tokens` | `int` | 输入 token 数 |
| `output_tokens` | `int` | 输出 token 数（含 reasoning） |
| `total_tokens` | `int` | 总计 = input + output |

```python
if response.usage:
    print(f"Tokens: {response.usage.input_tokens} + {response.usage.output_tokens} = {response.usage.total_tokens}")
```

---

### ModelResponseChunk

> **只读类型**。流式过程中由 `chat_stream()` 逐块产生。

| 属性 | 类型 | 说明 |
|------|------|------|
| `chunk_type` | `str` | `"text"` / `"reasoning"` / `"tool_call"` / `"usage"` |
| `content` | `str` or `None` | 文本增量（`chunk_type="text"`） |
| `reasoning` | `str` or `None` | 推理增量（`chunk_type="reasoning"`，DeepSeek 思考模式） |
| `tool_call` | `ToolCallDelta` or `None` | 工具调用增量（`chunk_type="tool_call"`） |
| `usage` | `Usage` or `None` | Token 统计（`chunk_type="usage"`，流的最后一个 chunk） |

```python
chunks, response = await provider.chat_stream(model_name=model, messages=msgs, tools=tools, params=params)

for c in chunks:
    match c.chunk_type:
        case "text":
            print(c.content, end="")
        case "reasoning":
            print(f"[思考中: {c.reasoning[:50]}...]")
        case "tool_call":
            print(f"[调用工具: {c.tool_call.name}]")
        case "usage":
            print(f"[Token: {c.usage.total_tokens}]")
```

---

### ToolCallDelta

> **只读类型**。流式过程中工具调用的增量更新。

| 属性 | 类型 | 说明 |
|------|------|------|
| `index` | `int` | 工具调用序号（从 0 开始，多个工具时区分） |
| `id` | `str` or `None` | 工具调用 ID（首 chunk 出现） |
| `name` | `str` or `None` | 工具名（首 chunk 出现） |
| `arguments_delta` | `str` or `None` | JSON 片段——需跨 chunk 累积拼接后 `json.loads()` |

---

### ModelAdapterNode

> **只读类型**。由 `provider.connect_to_bus()` 创建，不可直接构造。

**属性：**

| 属性 | 类型 | 说明 |
|------|------|------|
| `node_id` | `NodeId` | 节点 ID |

**方法：**

#### `await node.shutdown()`

关闭后台监听循环，从 Bus 断开连接。**幂等性：** 第二次调用抛出 `RuntimeError("node already shut down")`。

```python
node = await provider.connect_to_bus(bus=bus, node_id=NodeId(id="model/test"))
print(node.node_id)  # model/test

await node.shutdown()

# 二次 shutdown —— 抛出 RuntimeError
try:
    await node.shutdown()
except RuntimeError as e:
    print(e)  # node already shut down
```

---

## 常见模式

### 异常处理与重试

Provider 内置了对 429（限流）和 5xx（服务端错误）的自动重试（默认 3 次，指数退避）。但对于 4xx 错误（如 401 鉴权失败）不会重试，直接抛出异常。

```python
import asyncio
from arf import DeepSeekConfig, DeepSeekProvider, ModelMessage, ModelParams

async def safe_chat(provider, model, messages, max_attempts=3):
    """应用层兜底重试 —— 处理 Provider 内重试耗尽后的场景."""
    for attempt in range(1, max_attempts + 1):
        try:
            return await provider.chat(model_name=model, messages=messages, tools=[], params=ModelParams())
        except Exception as e:
            error_msg = str(e)
            if "401" in error_msg or "403" in error_msg:
                raise  # 鉴权错误，不重试
            if attempt == max_attempts:
                raise  # 重试耗尽
            wait = 2 ** attempt
            print(f"[Attempt {attempt}] {error_msg[:80]}... retrying in {wait}s")
            await asyncio.sleep(wait)

async def main():
    config = DeepSeekConfig(api_key="sk-xxx", models=["deepseek-v4-flash"])
    provider = DeepSeekProvider(config=config)

    msgs = [ModelMessage(role="user", content="Hello")]
    response = await safe_chat(provider=provider, model="deepseek-v4-flash", messages=msgs)
    print(response.message.content)

asyncio.run(main())
```

### 带思考模式的问题推理

对于需要深度推理的问题，开启 DeepSeek 思考模式并读取推理过程：

```python
import asyncio
from arf import DeepSeekConfig, DeepSeekProvider, ModelMessage, ModelParams

async def main():
    config = DeepSeekConfig(api_key="sk-xxx", models=["deepseek-v4-flash", "deepseek-v4-pro"])
    provider = DeepSeekProvider(config=config)

    # 用 Pro 模型 + thinking enabled
    params = ModelParams(
        thinking_enabled=True,
        extra={"reasoning_effort": "high"},
    )

    msgs = [
        ModelMessage(
            role="user",
            content="A bat and a ball cost $1.10 in total. "
                     "The bat costs $1.00 more than the ball. "
                     "How much does the ball cost?",
        )
    ]

    response = await provider.chat(model_name="deepseek-v4-pro", messages=msgs, tools=[], params=params)

    # 输出思考过程
    extra = response.message.extra
    if extra and "reasoning_content" in extra:
        print("=== 模型思考过程 ===")
        print(extra["reasoning_content"])
        print()

    print("=== 最终回复 ===")
    print(response.message.content)
    print(f"\nToken: {response.usage.input_tokens} + "
          f"{response.usage.output_tokens} = {response.usage.total_tokens}")

asyncio.run(main())
```

**运行输出：**（耗时 ~8s，推理 + 回复）

```
=== 模型思考过程 ===
We are asked: "A bat and a ball cost $1.10 in total. The bat costs $1.00
more than the ball. How much does the ball cost?" This is a classic trick
question. Many people quickly say 10 cents, but if the ball costs $0.10
and the bat costs $1.00 more, the bat would be $1.10, and total would be
$1.20. The correct answer is 5 cents: ball = $0.05, bat = $1.05,
total = $1.10. The answer should be $0.05. I'll provide the reasoning
step by step.

=== 最终回复 ===
The ball costs **$0.05**.

Let \( b \) be the cost of the ball. The bat costs $1.00 more than the
ball, so the bat costs \( b + 1.00 \).
The total cost is $1.10, giving the equation:

\[ b + (b + 1.00) = 1.10 \]
\[ 2b + 1.00 = 1.10 \]
\[ 2b = 0.10 \]
\[ b = 0.05 \]

So the ball is $0.05, and the bat is $1.05—together they sum to $1.10.

Token: 36 + 280 = 316
```

### Anthropic 格式调用 DeepSeek

通过 `AnthropicProvider` 以 Anthropic Messages API 格式调用 DeepSeek：

```python
import asyncio
from arf import AnthropicConfig, AnthropicProvider, ModelMessage, ModelParams

async def main():
    # 关键配置：endpoint 直接传 DeepSeek 提供的完整 URL（无 base_url + api_path 拼接）
    config = AnthropicConfig(
        api_key="sk-xxx",
        models=["deepseek-v4-flash"],
        endpoint="https://api.deepseek.com/anthropic",
    )
    provider = AnthropicProvider(config=config)

    # system 消息会被提取为顶层 system 参数
    messages = [
        ModelMessage(role="system", content="Respond in one sentence."),
        ModelMessage(role="user", content="What is the capital of France?"),
    ]

    response = await provider.chat(
        "deepseek-v4-flash", messages, [], ModelParams()
    )

    print(f"Finish: {response.finish_reason}")
    print(f"Content: {response.message.content}")
    print(f"Usage: {response.usage}")

asyncio.run(main())
```

**运行输出：**（耗时 ~1.5s）

```
Finish: stop
Content: The capital of France is Paris.
Usage: Usage(input=16, output=29, total=45)
```

### 服务发现：根据模型名路由请求

Bus 上可能注册了多个 ModelAdapter 节点。Engine 通过检查 `graph()` 中的 `capabilities` 来找到支持目标模型的节点：

```python
import asyncio
from arf import Bus, NodeId
from arf import DeepSeekConfig, DeepSeekProvider

async def find_model_node(bus, model_name):
    """在 Bus 上查找支持指定模型的节点."""
    graph = bus.graph()
    for node in graph.nodes:
        if node.node_type != "model":
            continue
        caps = node.capabilities
        if model_name in caps.get("models", []):
            return node
    return None

async def main():
    bus = Bus()

    # 注册两个模型节点
    provider_flash = DeepSeekProvider(
        config=DeepSeekConfig(api_key="sk-xxx", models=["deepseek-v4-flash"])
    )
    provider_pro = DeepSeekProvider(
        config=DeepSeekConfig(api_key="sk-xxx", models=["deepseek-v4-pro"])
    )

    node_flash = await provider_flash.connect_to_bus(bus=bus, node_id=NodeId(id="model/flash"))
    node_pro = await provider_pro.connect_to_bus(bus=bus, node_id=NodeId(id="model/pro"))

    # 根据模型名查找
    target = await find_model_node(bus=bus, model_name="deepseek-v4-pro")
    if target:
        print(f"Found node for deepseek-v4-pro: {target.node_id}")
        # engine.send("model_call", [target.node_id], payload)

    await node_flash.shutdown()
    await node_pro.shutdown()
    await bus.shutdown()

asyncio.run(main())
```

**运行输出：**（耗时 <10ms）

```
Found node for deepseek-v4-pro: model/pro
```

### 优雅关闭：确保所有节点清理

生产环境中 Bus 上有多个节点。正确的关闭顺序是先关节点，再关 Bus：

```python
import asyncio
from arf import Bus, NodeId
from arf import DeepSeekConfig, DeepSeekProvider

async def main():
    bus = Bus()

    nodes = []
    for i, model in enumerate(["deepseek-v4-flash", "deepseek-v4-pro"]):
        provider = DeepSeekProvider(
        config=DeepSeekConfig(api_key="sk-xxx", models=[model])
        )
        node = await provider.connect_to_bus(bus=bus, node_id=NodeId(id=f"model/{i}"))
        nodes.append(node)

    print(f"在线节点: {len(bus.graph().nodes)}")

    # 最佳实践：先关所有节点
    for node in nodes:
        try:
            await node.shutdown()
        except RuntimeError:
            pass  # 已关闭

    # 最后关 Bus
    await bus.shutdown()

    # 验证：graph 已空
    graph = bus.graph()
    print(f"关闭后节点数: {len(graph.nodes)}")

asyncio.run(main())
```

**运行输出：**（耗时 <20ms）

```
在线节点: 2
关闭后节点数: 0
```

---

## 异常速查表

| 异常类型 | match 文本 | 触发场景 |
|----------|-----------|---------|
| `Exception` | `transport error: ...` | 网络不可达 / DNS 解析失败 / 连接超时 |
| `Exception` | `API error 401: ...` | API key 无效 |
| `Exception` | `API error 403: ...` | 无权限访问该模型 |
| `Exception` | `API error 429: ...` | 限流（已触发 3 次重试后仍失败） |
| `Exception` | `API error 500: ...` | 服务端错误（已触发重试后仍失败） |
| `Exception` | `retry exhausted after N attempts: ...` | 所有重试耗尽 |
| `Exception` | `parse error: ...` | API 响应格式变更导致解析失败 |
| `RuntimeError` | `node already shut down` | 对已关闭的 `ModelAdapterNode` 再次调用 `shutdown()` 或访问 `node_id` |
| `TypeError` | `__init__() takes 0 positional arguments` | 尝试直接构造只读类型（`ToolCall()` / `Usage()` / `ModelAdapterNode()` 等） |

---

## Python 与 Rust API 差异

| 维度 | Rust | Python |
|------|------|--------|
| Provider 构造 | `DeepSeekProvider::new(config)` | `DeepSeekProvider(config)` |
| 异步调用 | `provider.chat(...).await` | `await provider.chat(...)` |
| 创建 Bus 节点 | `ModelAdapterNode::new(provider, &bus, node_id).await` | `await provider.connect_to_bus(bus, node_id)` |
| `supported_models` | `provider.supported_models()` 返回 `&[String]` | `provider.supported_models` 属性返回 `list[str]` |
| 错误类型 | `ProviderError` enum（Transport/Api/RetryExhausted/Parse） | 统一 `Exception`，`str(e)` 含完整信息 |
| `ModelAdapterNode.shutdown()` | `node.shutdown().await` 消费 `self` | `await node.shutdown()`，内部用 `Option` 实现幂等守卫 |
| `ModelParams.extra` | `serde_json::Value` | Python `dict` / `None`（自动转换） |
| `ToolDef.parameters` | `serde_json::Value` | Python `dict`（自动转换） |
| `ToolCall.arguments` | `serde_json::Value` | Python `dict`（自动转换） |

---

## 参考

- [Phase 4 设计文档](../v1.x/phase4_model_adapter/phase4-model-adapter-design.md) — 架构设计、消息协议、API 差异附录
- [Phase 1 Bus API](./bus.md) — Bus 消息总线 API 参考
- [Task 4.7 PyO3 绑定](../v1.x/phase4_model_adapter/task-4.7-pyo3-bindings.md) — 实现细节与测试
- [集成测试报告](../v1.x/phase4_model_adapter/test-report.md) — Rust + Python 测试结果
