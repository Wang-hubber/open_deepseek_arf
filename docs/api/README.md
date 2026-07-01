# ARF Python API 参考

> **`pip install arf`** · `from arf import ...` · Python 3.11+
>
> ARF（AI Resources & Runtime Framework）—— Agent 运行所需的全部基础设施。框架通过 Protocol 定义接口隔离，依赖注入组装全部能力。

## 文档地图

按使用频率和依赖顺序排列——上层的 API 依赖下层。

### 入门

| 文档 | 一句话 | 推荐起点 |
|------|--------|---------|
| **[engine.md](engine.md)** | ReAct 循环引擎——把模型 + 工具 + 状态串成完整 Agent | ⭐ 第一次用 ARF |
| **[bus.md](bus.md)** | CAN 总线模型消息总线——多节点广播 + 接收侧过滤 | ⭐ 理解通信模型 |

### 组件层

| 文档 | 一句话 |
|------|--------|
| **[model-adapter.md](model-adapter.md)** | 多供应商 LLM 适配（DeepSeek / OpenAI / Anthropic / MiniMax）|
| **[mcp.md](mcp.md)** | Model Context Protocol 工具节点（本地文件系统 + 远程 HTTP）|

### 底层（Rust 数据模型）

| 文档 | 一句话 | 何时读 |
|------|--------|--------|
| **[agent-config.md](agent-config.md)** | Rust `AgentConfig` 数据结构 + `validate()` | 写自定义 Config loader / 序列化 |
| **[state.md](state.md)** | Rust `State` / `Task` / `TaskStatus` | 写持久化 / 写 A2A 协调逻辑 |

## 5 分钟起步

### 安装

```bash
# 1. 拉代码 + 装 Python 依赖
git clone <repo> && cd open_deepseek_arf
python -m venv .venv
source .venv/bin/activate
pip install -e "py-arf[dev]"

# 2. 跑 hello world
cd py-arf
.venv/bin/python -c "
import asyncio
from arf import Bus, NodeInfo, MessageFilter, AgentConfig, EngineBuilder, EngineState

async def main():
    bus = Bus()
    mock = await bus.connect(info=NodeInfo('model/mock', 'model', {'provider': 'mock', 'models': ['mock-v1']}), filter=MessageFilter(types=['model_call']))
    asyncio.create_task(_responder(mock))
    engine = await EngineBuilder.new(buses=[bus]).build(config=AgentConfig())
    print(await engine.run(state=EngineState(), user_input='hi'))

async def _responder(h):
    while True:
        try:
            m = await h.recv()
            await h.send('model_response', to=[m.sender], payload={'correlation_id': m.payload.get('correlation_id'), 'message': {'role': 'assistant', 'content': 'pong'}, 'finish_reason': 'stop'})
        except: break

asyncio.run(main())
"
```

完整可运行例子 + 实测耗时见 [engine.md → 快速上手](engine.md#快速上手)。

## API 速查表

### Bus 层（Phase 1）

```python
from arf import Bus, NodeId, NodeInfo, MessageFilter, ToMatch, Message, NodeHandle, SendReceipt, BusGraph
```

| 类 | 一句话 |
|----|--------|
| `Bus` | CAN 总线；广播 + 接收侧过滤 |
| `NodeId` | 节点唯一标识（基于字符串的 hashable 包装） |
| `NodeInfo` | 节点身份 + 能力（`{node_id, node_type, capabilities, online_since}`） |
| `MessageFilter` | 节点订阅过滤（`types=[]` + `to_match=ToMatch.*`） |
| `ToMatch` | 4 种目标匹配策略（`All` / `BroadcastOnly` / `DirectedToMe` / `BroadcastAndDirectedToMe`） |
| `NodeHandle` | 节点收发句柄（由 `Bus.connect()` 返回） |
| `Message` | 收到的消息（`msg_type`, `sender`, `to`, `payload`, `timestamp`, `is_broadcast()`, `is_for(node_id)`） |
| `SendReceipt` | 发送回执（`message_id`, `online_nodes`, `matching_nodes`） |
| `BusGraph` | Bus 状态快照（`nodes`, `message_count`, `uptime_ms`） |

### Model Adapter 层（Phase 4 + 6.20）

```python
from arf import (
    ModelMessage, ModelParams, ToolDef, ToolCall, ToolCallDelta, Usage,
    ModelResponsePayload, ModelResponseChunk,
    DeepSeekConfig, DeepSeekProvider,
    OpenAIConfig, OpenAIProvider,
    AnthropicConfig, AnthropicProvider,
    MiniMaxConfig, MiniMaxProvider,         # Phase 6 task 6.20
    ModelAdapterNode,
)
```

| 类 | 一句话 |
|----|--------|
| `ModelMessage` | 对话消息（`role`, `content`, `tool_call_id`, `name`, `extra`） |
| `ModelParams` | 推理参数（`temperature`, `top_p`, `max_tokens`, `stream`） |
| `ToolDef` / `ToolCall` / `ToolCallDelta` | 工具定义 / 工具调用 / 流式增量 |
| `Usage` | token 统计（`input_tokens`, `output_tokens`, `total_tokens`） |
| `ModelResponsePayload` | LLM 完整响应（`message`, `tool_calls`, `finish_reason`, `usage`） |
| `ModelResponseChunk` | 流式响应的一个 chunk |
| `{Provider}Config` | 4 个供应商的配置（API key, base_url, models, timeout_secs, max_retries） |
| `{Provider}Provider` | 4 个供应商的 Provider（实现 `Provider` trait） |
| `ModelAdapterNode` | 把 Provider 注册为 Bus 节点（`ModelAdapterNode.new(provider, bus, node_id)`） |

### MCP 层（Phase 5）

```python
from arf import McpNode, RemoteConfig, RetryConfig
```

| 类 | 一句话 |
|----|--------|
| `McpNode` | MCP 节点（`McpNode.local(namespace, root)` 本地 / `McpNode.remote(namespace, config)` 远程） |
| `RemoteConfig` | 远程 MCP 配置（`url`, `timeout_secs`） |
| `RetryConfig` | 重试策略（`max_retries`, `backoff_ms`） |

### Engine 层（Phase 6 task 6.10）

```python
from arf import (
    AgentConfig, EngineBuilder, Engine, EngineState,
    WaitStrategy, ModelCall,
)
```

| 类 | 一句话 |
|----|--------|
| `AgentConfig` | Engine 声明式配置（`agent_id`, `provider`, `model`, `system_prompt_template`, `max_turns`） |
| `EngineBuilder` | 构建工厂（`EngineBuilder.new(buses=[...])` staticmethod → `await build(config)`） |
| `Engine` | ReAct 循环驱动（`await engine.run(state, user_input)`） |
| `EngineState` | 对话状态（`round_count`, `turn_count`, `context_tokens`, `messages`） |
| `WaitStrategy` | 多响应等待策略（`All` / `Any` / `Count(n)`） |
| `ModelCall` | Engine 发的 model_call ActionMessage（`msg_type="model_call"`, `correlation_id`） |
| `Checkpoint` / `CheckpointRule` / `ActionMessage` | 自定义 checkpoint 触发（BeforeModelCall / AfterModelCall / BeforeToolExec / AfterToolExec / RoundEnd） |
| `Route` / `Capability` | Engine 路由策略（`Route.strict(ids=[...])` / `Route.discovery(requirements=[...])`），通过 `AgentConfig(routes={...})` 注入 |
| `PoolConfig` / `Overflow` / `PoolError` / `Lease` | 通用资源池化原语 |
| `ModelAdapterResource` / `ModelAdapterPool` | 池化 LLM 调用（限流 / 配额） |
| `McpResource` / `McpPool` | 池化 MCP 工具调用（串行 / 限流） |

## 模块依赖图

```
                ┌─────────────────────────────────────────────┐
                │              应用层 (Python)                 │
                │  asyncio + 你的 Agent 逻辑                    │
                └────────────────────┬────────────────────────┘
                                     │
       ┌─────────────────────────────┼─────────────────────────────┐
       │                             │                             │
       ▼                             ▼                             ▼
┌──────────────┐           ┌──────────────────┐          ┌────────────────┐
│   Engine     │ ──────→  │     Bus          │ ←──────  │  ModelAdapter   │
│ (ReAct 循环) │           │  (CAN 总线)      │          │  (LLM 适配)     │
└──────┬───────┘           └────────┬─────────┘          └────────┬───────┘
       │                            │                              │
       │                   ┌────────┴────────┐                     │
       └─────────────────→ │  State          │ ←───────────────────┘
                           │ (对话历史)       │
                           └────────┬────────┘
                                    │
                           ┌────────┴────────┐
                           │  McpNode        │
                           │  (工具)         │
                           └─────────────────┘
```

## 运行示例代码

所有 `docs/api/*.md` 中的例子都可以这样跑：

```bash
cd /home/wangxie/open_deepseek_arf
.venv/bin/python -c "$(cat <<'EOF'
# 把文档里的代码块原样粘贴
import asyncio
from arf import Bus
# ... 你的代码 ...
EOF
)"
```

或保存为脚本：

```bash
.venv/bin/python my_example.py
```

需要真实 LLM 调用的例子：

```bash
export MINIMAX_API_KEY=sk-xxx   # 或 DEEPSEEK_API_KEY / OPENAI_API_KEY / ANTHROPIC_API_KEY
.venv/bin/python my_example.py
```

未配 key 的例子会 `print("[skip] KEY not set")` 而非失败。

## 测试覆盖

`py-arf/tests/` 下覆盖：

- `tests/lifecycle.py`、`tests/filters.py`、`tests/shutdown.py`、`tests/multi_consumer.py`、`tests/reconnect.py`、`tests/resource_leak.py`、`tests/boundary.py`、`tests/concurrency.py`、`tests/imports.py` — Bus 行为
- `tests/test_mcp.py` — MCP
- `tests/test_model_adapter_node.py` / `test_model_adapter_imports.py` / `test_model_adapter_live.py` — ModelAdapter
- `tests/e2e/` — Phase 6 task 6.22.2，20 个 E2E 测试覆盖 py-arf 全 surface

跑全套：

```bash
cd py-arf
.venv/bin/pytest tests/ -v
```

## 参考

- [V1.x 路线图](../v1.x/2026-06-26-arfv1-roadmap.md) — 8 Phase 概览
- [Phase 1 Bus 设计](../v1.x/phase1/phase1-bus-design.md)
- [Phase 4 ModelAdapter 设计](../v1.x/phase4/phase4-model-adapter-design.md)
- [Phase 5 MCP 设计](../v1.x/phase5/phase5-mcp-design.md)
- [Phase 6 Engine 设计](../v1.x/phase6/phase6-engine-design.md) + [E2E wrap-up](../v1.x/phase6/task-6.20-6.22-e2e-testing.md)
- [V1.x Agent 配置参考](../agent.md)
- [A2A 通信参考](../a2a-communication.md)
