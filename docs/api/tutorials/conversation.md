# 加本地工具

> 🎯 Diátaxis 桶位：**Tutorials**（入门教程，第二篇）

## 为什么

上一章模型只会"说话"。本章节注册一个本地 MCP 工具节点（扫描 `./tools/*/tool.toml`），Engine 在模型返回 `tool_calls` 时自动路由 `tool_exec` 到 MCP，把 `tool_result` 喂回模型 — 完成 Reason → Act → Observe 局部闭环。

## 本地工具约定

`McpNode.local(namespace, root)` 启动时扫描 `{root}/tools/*/tool.toml`，每个 `tool.toml` 对应一个工具。

`tool.toml` 字段：

| 字段 | 必填 | 说明 |
|---|---|---|
| `name` | ✓ | 工具名（模型用此名调用） |
| `description` | ✓ | 工具功能描述（决定模型何时调它） |
| `runtime` | ✓ | 入口脚本的运行时：`python` / `bash` / `rust` |
| `entrypoint` | ✓ | 入口脚本文件名（在 `tool.toml` 同目录） |
| `params_schema` |  | JSON Schema，描述工具参数 |
| `timeout_ms` |  | 工具执行超时 |

入口脚本通过 `print(json.dumps({...}))` 输出 `tool_result`，MCP runtime 捕获。Exit code 0 表示成功，非 0 表示失败。

## 代码

完整可运行脚本（保存为 `/tmp/ch2.py`，会运行时写 `./tools/get_time/` 下的清单和入口脚本）：

```python
import asyncio
import os
from arf import (
    Bus, NodeId, Route,
    MiniMaxConfig, MiniMaxProvider,
    McpNode,
    AgentConfig, EngineBuilder, EngineState,
)


def ensure_local_tool():
    """第一次跑时把 get_time 工具的清单和入口写到 ./tools/get_time/。"""
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

    # 模型节点（与 ch1 相同）
    provider = MiniMaxProvider(config=MiniMaxConfig.from_env())
    await provider.connect_to_bus(bus=bus, node_id=NodeId("model/main"))

    # 本地 MCP 节点：扫描 ./tools/*/tool.toml
    # 注意：McpNode.local(root) 内部在 {root}/tools/ 下找 tool.toml，
    # 所以 root 用 "."（项目根），让 ./tools/get_time/tool.toml 能被发现。
    mcp = McpNode.local(namespace="tools", root=".")
    await mcp.connect(bus=bus)

    engine = await EngineBuilder.new(buses=[bus]).build(
        config=AgentConfig(
            model=ModelDecl(provider="minimax", model_name="MiniMax-M2"),
            system_prompt_template="你是一个简洁的中文助手。",
            # 声明 MCP 资源：Engine 自动解析 to，注入 tools → model_call
            resources=[
                ResourceSpec(name="tools", node_type="mcp", capabilities={"tools": ["get_time"]}),
            ],
            engine=EngineConfig(
                max_turns=10,
            ),
        ),
    )

    state = EngineState()
    out = await engine.run(state=state, user_input="现在北京时间几点？")
    print(f"out={out!r}")
    print(f"messages={len(state.messages)}, turn_count={state.turn_count}")
    print(f"roles={[m['role'] for m in state.messages]}")
    await bus.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
```

## 运行

```bash
export MINIMAX_API_KEY='sk-...'
cd /path/to/repo   # 切到仓库根，让 ./tools/ 写到仓库下
.venv/bin/python /tmp/ch2.py
unset MINIMAX_API_KEY
# 跑完可清理：
rm -rf ./tools/get_time
```

预期 stdout（实际 LLM 文本会有变化；时间戳、turn_count、roles 稳定）：

```text
out='现在是北京时间 **2026年5月13日 16:31:35**，星期三。'
messages=5, turn_count=3
roles=['system', 'user', 'assistant', 'tool', 'assistant']
```

> 注：`turn_count=3` 跨 3 步 ReAct — 模型产 `tool_call` → MCP 返回 `tool_result` → 模型给最终回答。`roles` 列表里出现 `'tool'` 即证明本地 tool 真的被调到了。

## 下一节

→ [tools.md](tools.md) — 再注册一个远程 MCP 节点，让模型能同时调本地 + 远程 tool。

## 切换 Provider

模型节点的 Provider 与 ch1 完全相同 — 见 [hello.md § 切换 Provider](hello.md#切换-provider)。如果换成 DeepSeek，相应 `Route.discovery` 同步改为 `("provider", "deepseek")`。