# 加远程工具

> 🎯 Diátaxis 桶位：**Tutorials**（入门教程，第三篇 / 完整闭环）

## 为什么

在 Ch2 的本地 tool 基础上，再注册一个远程 MCP 节点。模型可以同时调本地与远程 tool — Bus / AgentConfig / Engine / ModelAdapter / MCP (Local + Remote) 全部就位，最小闭环完成。

## 远程 MCP 节点约定

`McpNode.remote(namespace, config)` 通过 HTTP/HTTPS 与远程 MCP server 通信。`config` 是 `RemoteConfig`：

| 字段 | 必填 | 说明 |
|---|---|---|
| `transport` | ✓ | `"http"`（推荐先试） / `"sse"`（Server-Sent Events） |
| `url` | ✓ | 远程 MCP server 端点 URL |
| `timeout_secs` |  | 请求超时（默认 10s） |
| `headers` |  | 自定义 HTTP header（鉴权 token 等） |
| `tls_ca_cert` |  | 自签 CA 证书路径（自部署 server 用） |
| `retry` |  | `RetryConfig(max_retries, initial_backoff_ms, max_backoff_ms)` |

`McpNode.remote` 在 `connect(bus)` 时通过 `HttpDiscovery` 远程拉 tool/skill 列表，本地缓存。后续 `tool_exec` 走相同 wire 协议（`tool_exec` → `tool_result`），Engine 无需区分本地/远程。

### "谁注册谁响应" 的设计意图

Engine 把 `tool_exec` 广播到所有 MCP 节点。**只有注册了该 tool 的 MCP 节点会响应**；非 owner 节点收到 `tool_exec` 后静默忽略（不发响应）。这意味着多 MCP 共存时不会出现"wrong-node responds first"的竞争条件。

## 真实远程 MCP 服务（推荐测试用）

教程需要一个能跑通真实 tool 调用的远程 MCP。推荐 [CodeTidy](https://mcp.codetidy.dev) — 62 个开发者工具（JSON 格式化/校验/转换、Base64、UUID、哈希、Semver 等），无需注册、无需 API key，HTTP 传输。

**Endpoint**: `https://mcp.codetidy.dev/`

> 测试时本地不再需要任何远程 MCP server；本教程的代码直接用 CodeTidy 跑通（假设有网络）。

## 代码

完整可运行脚本（保存为 `/tmp/ch3.py`）：

```python
import asyncio
import os
from arf import (
    Bus, NodeId, Route,
    MiniMaxConfig, MiniMaxProvider,
    McpNode, RemoteConfig,
    AgentConfig, EngineBuilder, EngineState,
)


# 真实远程 MCP — CodeTidy（无需 key）。可替换为你自己的 URL。
REMOTE_MCP_URL = "https://mcp.codetidy.dev/"


def ensure_local_tool():
    tool_dir = "./tools/get_time"
    os.makedirs(tool_dir, exist_ok=True)

    manifest = os.path.join(tool_dir, "tool.toml")
    if not os.path.exists(manifest):
        with open(manifest, "w", encoding="utf-8") as f:
            f.write(
                'name = "get_time"\n'
                'description = "返回当前北京时间"\n'
                'runtime = "python"\n'
                'entrypoint = "tool.py"\n'
                '\n'
                '[params_schema]\n'
                'type = "object"\n'
                'properties = {}\n'
            )

    entry = os.path.join(tool_dir, "tool.py")
    if not os.path.exists(entry):
        with open(entry, "w", encoding="utf-8") as f:
            f.write(
                'import json, datetime, sys\n'
                'result = {"ok": True, "content": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\n'
                'print(json.dumps(result))\n'
                'sys.exit(0)\n'
            )


async def main():
    ensure_local_tool()

    bus = Bus()

    # 模型节点
    provider = MiniMaxProvider(config=MiniMaxConfig.from_env())
    await provider.connect_to_bus(bus=bus, node_id=NodeId("model/main"))

    # 本地 MCP 节点：root="." 让 FsDiscovery 扫 ./tools/
    mcp_local = McpNode.local(namespace="tools", root=".")
    await mcp_local.connect(bus=bus)

    # 远程 MCP 节点 — CodeTidy（62 个工具）
    mcp_remote = await McpNode.remote(
        namespace="codetidy",
        config=RemoteConfig(transport="http", url=REMOTE_MCP_URL, timeout_secs=30),
    )
    await mcp_remote.connect(bus=bus)

    engine = await EngineBuilder.new(buses=[bus]).build(
        config=AgentConfig(
            model=ModelDecl(provider="minimax", model_name="MiniMax-M2"),
            system_prompt_template="你是一个简洁的中文助手。",
            # 声明 MCP 资源：两个节点，Engine 自动解析 + 注入
            resources=[
                ResourceSpec(resource_name="local_tools", node_type="mcp",
                    capabilities={"tools": ["get_time", "random_number"]}),
                ResourceSpec(resource_name="remote_tools", node_type="mcp",
                    capabilities={"tools": ["codetidy_json_format"]}),
            ],
            engine=EngineConfig(
                max_turns=10,
            ),
        ),
    )

    state = EngineState()
    out = await engine.run(
        state=state,
        user_input='用 codetidy_json_format 把这段 JSON 美化：{"name":"ARF","version":"1.0","features":["bus","model_adapter","mcp"]}',
    )
    print(f"out={out!r}")
    print(f"messages={len(state.messages)}")
    print(f"roles={[m['role'] for m in state.messages]}")
    print(f"last tool msg content[:200]: {next((m.get('content','')[:200] for m in state.messages if m['role']=='tool'), '(no tool call)')!r}")
    await bus.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
```

## 运行

```bash
export MINIMAX_API_KEY='sk-...'
cd /path/to/repo
.venv/bin/python /tmp/ch3.py
unset MINIMAX_API_KEY
rm -rf ./tools/get_time
```

预期 stdout（实际 LLM 文本会有变化）：

```text
out='<think>...我将清晰呈现结果。</think>\n\n美化后的结果：\n\n```json\n{\n  "name": "ARF",\n  "version": "1.0",\n  "features": [\n    "bus",\n    "model_adapter",\n    "mcp"\n  ]\n}\n```\n\n✅ JSON 本身是合法的...'
messages=4
roles=['user', 'assistant', 'tool', 'assistant']
last tool msg content[:200]: '{\n  "name": "ARF",\n  "version": "1.0",\n  "features": [\n    "bus",\n    "model_adapter",\n    "mcp"\n  ]\n}\n\n---\nPowered by CodeTidy — free developer tools at https://codetidy.dev\nFull interactive version: ...'
```

> 注：完整 ReAct 闭环 — `roles` 包含 `'tool'` 证明远程 MCP tool 真被调用并返回了真实结果。模型在拿到 tool_result 后包装成友好回复。CodeTidy 在返回内容里自动追加了 "Powered by CodeTidy" 链接。

### 换其他 CodeTidy 工具

CodeTidy 提供 62 个工具，模型可能因上下文截断看到不同子集。如果某个工具名 model 看不到，换个等价工具：

```python
user_input = "用 codetidy_base64_encode 把 'Hello ARF' 编码为 Base64"
user_input = "用 codetidy_json_validate 检查这段 JSON 是否合法：{...}"
user_input = "用 codetidy_uuid_generate 生成 3 个 UUID"
```

## 下一节

本系列三章覆盖了 ARF 最小闭环：Bus / AgentConfig / Engine / ModelAdapter / MCP (Local & Remote)。

后续可探索：`Route.strict` vs `Route.discovery` 的取舍、Checkpoint 与 CheckpointRule、ModelAdapterPool 容量管理、Phase 6 全栈。详见 [`docs/api/reference/`](../reference/)。

## 切换 Provider

模型节点的 Provider 与 ch1 完全相同 — 见 [hello.md § 切换 Provider](hello.md#切换-provider)。如果换成 DeepSeek，相应 `Route.discovery` 同步改为 `("provider", "deepseek")`。