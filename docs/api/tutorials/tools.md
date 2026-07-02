# 加远程工具

> 🎯 Diátaxis 桶位：**Tutorials**（入门教程，第三篇 / 完整闭环）

## 为什么

在 Ch2 的本地 tool 基础上，再注册一个远程 MCP 节点。模型可以同时调本地与远程 tool — Bus / AgentConfig / Engine / ModelAdapter / MCP (Local + Remote) 全部就位，最小闭环完成。

> ⚠️ 本章需要 **一个真实可用的远程 MCP server URL**。代码里 `REMOTE_MCP_URL` 是占位符，请替换为你自己的 URL。URL 不可达时 Engine 会捕获 tool 失败并继续（fail gracefully），不会 unhandled exception。

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

## 代码

完整可运行脚本（保存为 `/tmp/ch3.py`；替换 `REMOTE_MCP_URL` 后跑）：

```python
import asyncio
import os
from arf import (
    Bus, NodeId, Route,
    MiniMaxConfig, MiniMaxProvider,
    McpNode, RemoteConfig,
    AgentConfig, EngineBuilder, EngineState,
)


# 替换为你的真实远程 MCP server URL。本教程默认占位符 — 跑通需替换。
REMOTE_MCP_URL = "https://your-remote-mcp.example.com/mcp"


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

    # 远程 MCP 节点 — 替换 REMOTE_MCP_URL 为你自己的 URL
    mcp_remote = None
    try:
        mcp_remote = await McpNode.remote(
            namespace="weather-api",
            config=RemoteConfig(transport="http", url=REMOTE_MCP_URL, timeout_secs=10),
        )
        await mcp_remote.connect(bus=bus)
    except Exception as e:
        print(f"remote MCP unavailable (skipped): {type(e).__name__}: {e}")
        # 占位 URL 下 mcp_remote 保持 None；engine.run 仍会跑（只调本地 tool）

    engine = await EngineBuilder.new(buses=[bus]).build(
        config=AgentConfig(
            agent_id="tutorial-ch3",
            system_prompt_template="你是一个简洁的中文助手。",
            routes={
                "model_call": Route.discovery(requirements=[("provider", "minimax")]),
                "tool_exec": Route.strict(ids=[NodeId("mcp/tools")]),
                # tool_exec_weather 只在 mcp_remote 在线时才有意义；占位 URL 下
                # engine.build 时会因 NodeId 不在 graph 而失败，所以这里动态构造。
                **(
                    {"tool_exec_weather": Route.strict(ids=[NodeId("mcp/weather-api")])}
                    if mcp_remote is not None else {}
                ),
            },
        ),
    )

    state = EngineState()
    try:
        out = await engine.run(
            state=state,
            user_input="查一下现在北京时间，再告诉我上海今天天气怎么样。",
        )
        print(f"out={out!r}")
    except Exception as e:
        # URL 不可达 / tool 失败时 Engine 可能在这里抛 — 我们捕获并继续
        print(f"engine.run raised (expected if REMOTE_MCP_URL is unreachable): {type(e).__name__}: {e}")
    print(f"messages={len(state.messages)}, turn_count={state.turn_count}")
    print(f"roles={[m['role'] for m in state.messages]}")
    await bus.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
```

## 运行

```bash
# 替换 REMOTE_MCP_URL 后再跑（占位 URL 跑不通远程 tool，本地 tool 仍能跑）
export MINIMAX_API_KEY='sk-...'
cd /path/to/repo
.venv/bin/python /tmp/ch3.py
unset MINIMAX_API_KEY
rm -rf ./tools/get_time
```

预期 stdout（占位 URL 场景下，远程 tool 失败但脚本不崩）：

```text
remote MCP unavailable (skipped): ConnectionError: remote MCP server unreachable (https://your-remote-mcp.example.com/mcp): ...
out='**当前北京时间：2026年5月15日 18:37:09（星期四）**\n\n关于上海的天气情况，很抱歉，我目前没有查询天气的工具可用...'
messages=5, turn_count=3
roles=['system', 'user', 'assistant', 'tool', 'assistant']
```

> 注：占位 URL 下远程 tool 必失败；脚本中 `try/except` 让 main 不 unhandled-exit。本地 `get_time` 仍可被调用；模型会基于可用 tool 给出真实回答（不会编造天气）。**完整跑通 Ch3（本地 + 远程都通）需要真实远程 MCP URL。**

## 下一节

本系列三章覆盖了 ARF 最小闭环：Bus / AgentConfig / Engine / ModelAdapter / MCP (Local & Remote)。

后续可探索：`Route.strict` vs `Route.discovery` 的取舍、Checkpoint 与 CheckpointRule、ModelAdapterPool 容量管理、Phase 6 全栈。详见 [`docs/api/reference/`](../reference/)。

## 切换 Provider

模型节点的 Provider 与 ch1 完全相同 — 见 [hello.md § 切换 Provider](hello.md#切换-provider)。如果换成 DeepSeek，相应 `Route.discovery` 同步改为 `("provider", "deepseek")`。