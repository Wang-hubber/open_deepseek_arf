# ARF MCP — Model Context Protocol 节点 API 参考

> **Phase 5** · 工具注册与执行 · `from arf import McpNode, RemoteConfig, RetryConfig`

## 概述

MCP（Model Context Protocol）是 ARF 框架的工具注册与执行层。一个 MCP 节点 = Bus 上的一个 namespace，Engine 通过 `mcp/{namespace}` 路由工具调用。本地 MCP 扫描文件夹发现 Tool/Skill，远程 MCP 通过 HTTP 协议发现和代理执行——Engine 对两者无区别。

```
                    ┌─────────────────────────────┐
                    │           Bus               │
                    │                             │
  Engine ──────────┤→ mcp/filesystem (Local)      │
    │               │   · read_file, write_file    │
    │               │   · search_content           │
    │               │                             │
    └───────────────┤→ mcp/codetidy   (Remote)     │
                    │   · base64_encode, hash...   │
                    │   · json_format, jwt...      │
                    └─────────────────────────────┘

  Engine 视角：
    监听 node_online → 发现 MCP 节点 → 按 node_id 路由
    tool_call_set → mcp/{ns} → tool_result_set
```

### 设计意图——执行权归属注册方

谁注册了这个 Tool，谁就决定这个 Tool 如何被运行。Engine 只看到 `node_online`（能力广播）和 `tool_result_set`（执行结果），不关心宿主机/Docker/Firecracker：

```
Engine                              MCP (mcp/filesystem)
  │                                     │
  │  发出 tool_call_set                  │  我注册了 read_file，我决定它怎么跑
  │  [call_0: read_file, ...]           │    ├── LocalRuntime → python3 (宿主机)
  │                                     │    └── SandboxRuntime → docker   (容器)
  │                                     │
  │  ←── tool_result_set ────────────── │  执行细节对 Engine 不可见
```

### 适用场景

- 在 Python 中注册本地工具脚本（Python/Bash/Rust），通过文件夹约定自动发现
- 接入外部 MCP 服务（如 CodeTidy），通过 HTTP 代理执行
- 多 namespace 共存——同名 tool 在不同 namespace 下互不冲突
- 与 MiniEngine（或 Phase 6 Engine）配合，构建完整的 ReAct Agent 工具层

---

## 快速上手

### 安装

MCP 是 `py-arf` 包的一部分：

```bash
pip install -e "py-arf[dev]" -i https://pypi.mirrors.ustc.edu.cn/simple
```

### 第一个本地 MCP：扫描文件夹，连接 Bus

```python
import tempfile, os, asyncio
from arf import Bus, McpNode

# 1. 准备工具目录（按约定组织）
root = tempfile.mkdtemp()
tool_dir = os.path.join(root, "tools", "echo")
os.makedirs(name=tool_dir)

# tool.toml — 工具元数据
with open(os.path.join(tool_dir, "tool.toml"), "w") as f:
    f.write('''
name = "echo"
description = "Echo back the input params as JSON"
runtime = "bash"
entrypoint = "main.sh"
''')

# main.sh — stdin JSON → stdout JSON
with open(os.path.join(tool_dir, "main.sh"), "w") as f:
    f.write("#!/bin/bash\nread p\necho '{\"msg\":\"ok\"}'\n")

# 2. 创建节点 + 连接 Bus
bus = Bus()
node = McpNode.local(namespace="greeter", root=root)  # 同步构造，扫描 {root}/tools/*/tool.toml
# McpNode.local() → 0.3ms
await node.connect(bus=bus)                # 广播 node_online
# node.connect() → 1.2ms

# 3. 验证：Bus 上能看到这个 MCP 节点
graph = bus.graph()
mcp_nodes = [n for n in graph.nodes if n.node_type == "mcp"]
print(f"Namespace: {node.namespace}")          # Namespace: greeter
print(f"Node ID:   {node.node_id}")            # Node ID:   mcp/greeter
print(f"Tools:     {mcp_nodes[0].capabilities['tools']}")
# Tools: [{'name': 'echo', 'description': 'Echo back the input params as JSON'}]

await bus.shutdown()
# 总耗时 ~0.1s
```

### 第一个远程 MCP：连接 CodeTidy

```python
from arf import Bus, McpNode, RemoteConfig

bus = Bus()
config = RemoteConfig(
    url="https://mcp.codetidy.dev",
    timeout_secs=30,
)
node = await McpNode.remote(namespace="codetidy", config=config)  # 异步构造，HTTP 握手
# McpNode.remote() → 1.7-2.4s（取决于网络延迟）
await node.connect(bus=bus)                          # 广播 node_online
# node.connect() → 0.9ms

graph = bus.graph()
ct = [n for n in graph.nodes if n.node_type == "mcp"][0]
tools = ct.capabilities["tools"]
print(f"CodeTidy 提供 {len(tools)} 个工具")      # CodeTidy 提供 62 个工具

await bus.shutdown()
# 总耗时 ~2.3s
```

> **注意**：`McpNode.local(namespace=..., root=...)` 是同步构造（文件扫描），`McpNode.remote(namespace=..., config=...)` 是异步构造（需 HTTP 握手）。两者都通过 `await node.connect(bus=bus)` 注册到 Bus。

---

## 核心概念

### 一个 MCP 实例 = 一个 namespace = Bus 上一个节点

```
Bus
 │
 ├── mcp/filesystem  (LocalMcpNode) root=/mcp/stable
 │       ├── [内部] FsDiscovery → 扫描 {root}/tools/*/tool.toml
 │       └── [内部] LocalRuntime → 宿主机直接 spawn subprocess
 │
 ├── mcp/codetidy    (RemoteMcpNode) url=https://mcp.codetidy.dev
 │       ├── [内部] HttpDiscovery → HTTP tools/list 发现
 │       └── [内部] RemoteRuntime  → HTTP tools/call 代理
 │
 └── mcp/another     (LocalMcpNode) root=/mcp/experimental
```

Engine 只看到三个节点，各 namespace 独立，同名 tool 不冲突。路由 key 是 `node_id`（格式 `mcp/{namespace}`），如 `mcp/filesystem`。

### 文件夹约定——本地 MCP 唯一注册路径

```python
{root}/
├── tools/                  # 工具目录
│   ├── read_file/
│   │   ├── tool.toml       # name, description, runtime, entrypoint
│   │   └── main.py         # stdin JSON → stdout JSON
│   └── write_file/
│       ├── tool.toml
│       └── main.py
├── skills/                 # Skill 目录
│   └── react-component/
│       ├── SKILL.md         # YAML frontmatter + Markdown body（L1→L2→L3 渐进披露）
│       ├── tools/           # (可选) skill 专属工具，与顶层 tools/ 结构统一
│       ├── references/      # (可选) 参考文档
│       └── assets/          # (可选) 静态资源
```

`tool.toml` 格式：

```toml
name = "read_file"
description = "Read the contents of a file at the given path"
runtime = "python"           # "python" | "bash" | "rust"
entrypoint = "main.py"       # 入口文件名（相对 tool 目录）
timeout_ms = 10000           # 可选：单次执行超时

[params_schema]              # JSON Schema（供 LLM function calling）
type = "object"
properties.path = { type = "string", description = "Absolute path to the file" }
required = ["path"]
```

### stdin/stdout JSON 协议

所有脚本工具遵循同一协议：

```
ScriptTool.execute(params)
  ├── spawn: python3 main.py / bash main.sh / ./main（rustc 编译产物）
  ├── stdin  ← JSON params
  ├── stdout → JSON result
  └── stderr → error message
```

脚本只需：**从 stdin 读 JSON，往 stdout 写 JSON**：

```python
import sys, json
params = json.loads(sys.stdin.read())
# ... 业务逻辑 ...
print(json.dumps({"ok": True, "result": "..."}))
```

### 本地 vs 远程生命周期

```
                    McpNode.local()                         McpNode.remote()
                    ──────────────                         ──────────────
构造 (sync)          FsDiscovery::scan(root)                HTTP initialize + tools/list
                    失败 → RuntimeError("discovery...")     失败 → RuntimeError("remote...")

connect(bus)        bus.connect() → node_online             bus.connect() → node_online
                    失败 → RuntimeError("bus...")            失败 → RuntimeError("bus...")

运行时               executor(tool_map)                     executor(tool_map)
                    → ToolResultSet                        → ToolResultSet
```

> **关键**：远程 MCP 的 `remote()` 是 async——它需要 HTTP 握手。本地 MCP 的 `local()` 是 sync——它只扫描文件系统。`connect()` 对两者都是 async。

---

## API 参考

### `McpNode`

```python
from arf import McpNode
```

统一的 MCP 节点类型——本地和远程只是构造方式不同，对 Bus 其余节点看到相同的接口。

**两种构造方式**：

| 方法 | 类型 | 网络 | 说明 |
|------|------|------|------|
| `McpNode.local(namespace=..., root=...)` | sync | 否 | 扫描 `{root}/tools/*/tool.toml`，默认 LocalRuntime |
| `McpNode.remote(namespace=..., config=...)` | async | 是 | HTTP initialize + tools/list，RemoteRuntime |

#### `McpNode.local()`

```python
@classmethod
def local(cls, namespace: str, root: str) -> McpNode:
    ...
```

**Args:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `namespace` | `str` | *required* | 唯一命名空间标识，kebab-case（如 `"filesystem"`）。Bus 上的 `node_id` 自动推导为 `mcp/{namespace}` |
| `root` | `str` | *required* | 工具根目录路径，内含 `tools/` 子目录 |

**Returns:** `McpNode` — 已完成文件扫描的节点实例

**Raises:**

| Exception | Match text | Trigger |
|-----------|-----------|---------|
| `RuntimeError` | `"discovery error"` | `root` 不存在或不可读 |

**Example:**

```python
# 最小用法 — root 可为空目录（无工具）
node = McpNode.local(namespace="my-ns", root="/path/to/tools")

# 含工具 — tools/echo/tool.toml + main.py
node = McpNode.local(namespace="filesystem", root="/mcp/stable")
print(node.namespace)  # "filesystem"
print(node.node_id)    # "mcp/filesystem"
```

#### `McpNode.remote()`

```python
@classmethod
async def remote(cls, namespace: str, config: RemoteConfig) -> McpNode:
    ...
```

**Args:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `namespace` | `str` | *required* | 唯一命名空间标识，Bus 上的 `node_id` 自动推导为 `mcp/{namespace}` |
| `config` | `RemoteConfig` | *required* | 远程 MCP 连接配置（URL、超时、headers、TLS、重试） |

**Returns:** `McpNode` — 已完成 HTTP 握手和工具发现的节点实例

**Raises:**

| Exception | Match text | Trigger |
|-----------|-----------|---------|
| `ConnectionError` | `"unreachable"` | DNS 解析失败、连接被拒、TLS 错误、超时 |
| `ConnectionError` | `"rejected"` | MCP 握手被服务器拒绝（错误的协议版本等） |

**Example:**

```python
config = RemoteConfig(
    url="https://mcp.codetidy.dev",
    timeout_secs=30,
    headers={"Authorization": "Bearer sk-xxx"},
    retry=RetryConfig(max_retries=3),
)
node = await McpNode.remote(namespace="codetidy", config=config)
```

#### `McpNode.connect()`

```python
async def connect(self, bus: Bus) -> None:
    ...
```

将节点注册到 Bus，广播 `node_online` 消息。Engine 和其他节点通过此消息发现 MCP 的能力。

**Args:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `bus` | `Bus` | *required* | 要连接的 Bus 实例 |

**Raises:**

| Exception | Match text | Trigger |
|-----------|-----------|---------|
| `RuntimeError` | `"bus connect"` | Bus 连接失败（channel 关闭等） |

**Example:**

```python
node = McpNode.local(namespace="tools", root="/path/to/root")
await node.connect(bus=bus)  # 广播 node_online，含 tools + skills L1 + runtime capabilities
```

#### 属性

| Attribute | Type | Access | Description |
|-----------|------|--------|-------------|
| `namespace` | `str` | read-only | 构造时传入的 namespace |
| `node_id` | `str` | read-only | Bus 节点 ID，格式 `mcp/{namespace}` |

```python
node = McpNode.local(namespace="filesystem", root="/root")
assert node.namespace == "filesystem"
assert node.node_id == "mcp/filesystem"
```

---

### `RemoteConfig`

```python
from arf import RemoteConfig

RemoteConfig(
    url: str,
    transport: str = "http",
    timeout_secs: int | None = None,
    headers: dict[str, str] | None = None,
    tls_ca_cert: str | None = None,
    retry: RetryConfig | None = None,
)
```

远程 MCP 服务器的连接配置。

**Args:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `url` | `str` | *required* | MCP 服务端 URL（如 `"https://mcp.example.com"`） |
| `transport` | `str` | `"http"` | 传输协议，当前仅支持 `"http"`（`"streamable-http"` 亦可） |
| `timeout_secs` | `int \| None` | `None` | HTTP 请求超时秒数。`None` = 无超时 |
| `headers` | `dict[str,str] \| None` | `None` | 注入到每个 HTTP 请求的自定义头部（如 `{"Authorization": "Bearer tok"}`） |
| `tls_ca_cert` | `str \| None` | `None` | 自签 TLS 证书的 CA 证书路径 |
| `retry` | `RetryConfig \| None` | `None` | 重连配置。`None` = 不重试，网络错误直接 fail |

**属性（只读）**：所有构造参数均可通过同名 property 读取。

**Example:**

```python
# 最小配置
cfg = RemoteConfig(url="https://mcp.codetidy.dev")

# 完整配置
cfg = RemoteConfig(
    url="https://mcp.internal.dev",
    timeout_secs=60,
    headers={"Authorization": "Bearer sk-xxx"},
    tls_ca_cert="/etc/ssl/internal-ca.pem",
    retry=RetryConfig(max_retries=3, initial_backoff_ms=1000),
)
```

---

### `RetryConfig`

```python
from arf import RetryConfig

RetryConfig(
    max_retries: int = 3,
    initial_backoff_ms: int = 1000,
    max_backoff_ms: int = 30000,
)
```

远程 MCP 网络故障重连配置。仅在 `RemoteConfig.retry` 设为非 `None` 时生效。

**Args:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `max_retries` | `int` | `3` | 最大重试次数 |
| `initial_backoff_ms` | `int` | `1000` | 初始退避时间（毫秒），每次重试指数增长 |
| `max_backoff_ms` | `int` | `30000` | 最大退避时间（毫秒），退避时间封顶值 |

**重连流程**：

```
网络故障 / 5xx / 429
  ┌→ 等待 backoff (initial → 指数增长 → max_backoff)
  ├→ HTTP initialize（重新握手）
  ├→ HTTP tools/list（刷新工具列表，server 可能已更新）
  ├→ 重新广播 node_online
  ├→ 重试 tools/call
  ├─ 成功 → 正常返回
  └─ 超过 max_retries → error
```

**可重试 vs 不可重试**：

| 可重试 | 不可重试 |
|--------|---------|
| DNS 解析失败、连接被拒、TLS 错误 | HTTP 401/403（认证/权限） |
| HTTP 超时 | HTTP 400（参数错误） |
| HTTP 5xx（服务端临时故障） | HTTP 404（端点不存在） |
| HTTP 429（限流，等 Retry-After） | — |

**属性（只读）**：

| Attribute | Type | Description |
|-----------|------|-------------|
| `max_retries` | `int` | 最大重试次数 |
| `initial_backoff_ms` | `int` | 初始退避毫秒 |
| `max_backoff_ms` | `int` | 最大退避毫秒 |

**Example:**

```python
# 默认值
retry = RetryConfig()
assert retry.max_retries == 3
assert retry.initial_backoff_ms == 1000

# 自定义
retry = RetryConfig(max_retries=5, initial_backoff_ms=2000, max_backoff_ms=60000)
```

---

## 常见模式

### 模式一：本地工具 + Bus + 模拟 Engine 调用

完整的本地 MCP 工具注册 → 发 `tool_call_set` → 收 `tool_result_set` 链路：

```python
import tempfile, os, asyncio
from arf import Bus, McpNode, NodeId, NodeInfo, MessageFilter, ToMatch

async def main():
    # 1. 准备工具
    root = tempfile.mkdtemp()
    tool_dir = os.path.join(root, "tools", "echo")
    os.makedirs(name=tool_dir)
    with open(os.path.join(tool_dir, "tool.toml"), "w") as f:
        f.write('name = "echo"\ndescription = "Echo"\nruntime = "bash"\nentrypoint = "main.sh"\n')
    with open(os.path.join(tool_dir, "main.sh"), "w") as f:
        f.write('#!/bin/bash\nread p\necho \'{"msg":"hello from mcp"}\'\n')

    # 2. 启动 Bus + MCP 节点
    bus = Bus()
    node = McpNode.local(namespace="demo", root=root)
    await node.connect(bus=bus)

    # 3. 模拟 Engine — 连接 Bus + 发送 tool_call_set
    engine_info = NodeInfo(
        node_id="engine/s1",
        node_type="engine",
        capabilities={},
    )
    engine_filter = MessageFilter(
        types=["tool_result_set"],
        to_match=ToMatch.All,
    )
    engine = await bus.connect(info=engine_info, filter=engine_filter)

    engine.send(
        msg_type="tool_call_set",
        to=[NodeId(id=node.node_id)],
        payload={
            "session_id": "s1",
            "calls": [{"id": "c0", "tool": "echo", "params": {}}],
        },
    )

    # 4. 接收结果
    resp = await engine.recv()
    result = resp.payload["results"][0]
    print(f"Status: {result['status']}")  # Status: success
    print(f"Name:   {result['name']}")    # Name:   echo
    print(f"Result: {result['result']}")  # Result: {'msg': 'hello from mcp'}
    # tool_call_set → tool_result_set: ~2ms

    await bus.shutdown()

asyncio.run(main())
```

### 模式二：多 namespace — 同名 tool 不冲突

```python
# 两个 namespace，各有同名 echo 工具
node_a = McpNode.local(namespace="alpha", root="/mcp/alpha")
node_b = McpNode.local(namespace="beta",  root="/mcp/beta")

await node_a.connect(bus=bus)
await node_b.connect(bus=bus)

# node_a.node_id == "mcp/alpha"
# node_b.node_id == "mcp/beta"
# 调用时按 node_id 路由，同名 echo 互不干扰
engine.send(msg_type="tool_call_set", to=[NodeId(id=node_a.node_id)], payload={...})  # → alpha
engine.send(msg_type="tool_call_set", to=[NodeId(id=node_b.node_id)], payload={...})  # → beta
```

### 模式三：远程 MCP + 本地 MCP 共存

```python
# 本地文件工具 + 远程 CodeTidy 工具并存
local = McpNode.local(namespace="fs", root="/mcp/stable")
remote = await McpNode.remote(namespace="codetidy",
    config=RemoteConfig(url="https://mcp.codetidy.dev", timeout_secs=30))

await local.connect(bus=bus)
await remote.connect(bus=bus)

# Bus 上有两个 MCP 节点
graph = bus.graph()
mcp_nodes = [n for n in graph.nodes if n.node_type == "mcp"]
for n in mcp_nodes:
    print(f"{n.node_id}: {len(n.capabilities['tools'])} tools")
# mcp/fs: 3 tools
# mcp/codetidy: 62 tools
```

### 工具编写示例

同一个 `read_file` 工具，用 Python、Bash、Rust 分别实现。三个版本共享相同的 `tool.toml` 结构（仅 `runtime` 和 `entrypoint` 不同），输出完全一致的 JSON。

> **runtime 选择建议**：性能不敏感的工具优先使用 **Python** 或 **Bash**——JSON 支持好、即时启动、无编译延迟。Rust 仅用于计算密集或输入极其简单的场景（手写 JSON 解析有维护成本，`rustc` 编译有首次延迟）。

**文件结构**：

```
{root}/tools/
├── read_file/
│   ├── tool.toml       # runtime = "python"
│   └── main.py
├── read_file_bash/
│   ├── tool.toml       # runtime = "bash"
│   └── main.sh
└── read_file_rust/
    ├── tool.toml       # runtime = "rust"
    └── main.rs
```

#### Python — `runtime = "python"`

最自然的写法。`json` 模块 + `pathlib` 现代 API，完整的异常分类：

```toml
# tool.toml
name = "read_file"
description = "Read the contents of a file at the given path. Returns the file content as a string."
runtime = "python"
entrypoint = "main.py"
timeout_ms = 10000

[params_schema]
type = "object"
properties.path = { type = "string", description = "Absolute path to the file to read" }
required = ["path"]
```

```python
# main.py
#!/usr/bin/env python3
import sys, json
from pathlib import Path

def main():
    try:
        params = json.loads(sys.stdin.read())
    except json.JSONDecodeError as e:
        print(json.dumps({"ok": False, "error": f"invalid JSON params: {e}"}))
        sys.exit(0)

    path_str = params.get("path", "")
    if not path_str:
        print(json.dumps({"ok": False, "error": "missing required param: path"}))
        sys.exit(0)

    file_path = Path(path_str)
    if not file_path.exists():
        print(json.dumps({"ok": False, "error": f"file not found: {path_str}"}))
        sys.exit(0)
    if not file_path.is_file():
        print(json.dumps({"ok": False, "error": f"not a file: {path_str}"}))
        sys.exit(0)

    try:
        content = file_path.read_text(encoding="utf-8")
    except PermissionError:
        print(json.dumps({"ok": False, "error": f"permission denied: {path_str}"}))
        sys.exit(0)
    except UnicodeDecodeError:
        print(json.dumps({"ok": False, "error": f"binary file not supported: {path_str}"}))
        sys.exit(0)
    except OSError as e:
        print(json.dumps({"ok": False, "error": f"read error: {e}"}))
        sys.exit(0)

    print(json.dumps({"ok": True, "content": content, "path": path_str}))

if __name__ == "__main__":
    main()
```

**设计要点**：
- `sys.exit(0)` 而非非零退出码——错误通过 JSON 字段传递，非进程退出码。保证 ScriptTool 始终能解析 stdout
- `UnicodeDecodeError` 单独捕获——二进制文件不应被当作文本读取，返回明确错误
- `pathlib.Path` 比 `os.path` 更简洁，Python 3.6+ 内置

```bash
$ echo '{"path":"/etc/hostname"}' | python3 main.py
{"ok": true, "content": "iZbp1hzrvnradlsiut7vanZ\n", "path": "/etc/hostname"}

$ echo '{"path":"/nonexistent"}' | python3 main.py
{"ok": false, "error": "file not found: /nonexistent"}
```

#### Bash — `runtime = "bash"`

Bash 没有原生 JSON 支持——用 `python3 -c` 做编解码胶水（每处 ~3 行），核心文件操作用原生命令：

```toml
# tool.toml — 与 Python 版仅 runtime 和 entrypoint 不同
runtime = "bash"
entrypoint = "main.sh"
```

```bash
# main.sh
#!/usr/bin/env bash
set -euo pipefail

# JSON input parsing (bash has no native JSON — python3 glue)
PATH_VAL=$(python3 -c "
import json,sys
d = json.loads(sys.stdin.read())
print(d.get('path', ''))
" 2>/dev/null || true)

if [ -z "$PATH_VAL" ]; then
    python3 -c "import json; print(json.dumps({'ok': False, 'error': 'missing required param: path'}))"
    exit 0
fi

# File operations (native bash)
if [ ! -e "$PATH_VAL" ]; then
    python3 -c "import json; print(json.dumps({'ok': False, 'error': 'file not found: $PATH_VAL'}))"
    exit 0
fi

if [ ! -f "$PATH_VAL" ]; then
    python3 -c "import json; print(json.dumps({'ok': False, 'error': 'not a file: $PATH_VAL'}))"
    exit 0
fi

if [ ! -r "$PATH_VAL" ]; then
    python3 -c "import json; print(json.dumps({'ok': False, 'error': 'permission denied: $PATH_VAL'}))"
    exit 0
fi

# Use python3 for JSON encoding (guarantees correct escaping of special chars)
python3 -c "
import json
content = open('$PATH_VAL').read()
print(json.dumps({'ok': True, 'content': content, 'path': '$PATH_VAL'}))
"
```

**设计要点**：
- `set -euo pipefail` — 未定义变量/管道失败立即退出
- `[ -e ]` / `[ -f ]` / `[ -r ]` 三级检查 — 存在性 → 类型 → 权限
- JSON 编解码用 `python3 -c` 胶水——不假装 Bash 能做 JSON，也不因为 JSON 限制而放弃 Bash 在文件操作上的表达力

```bash
$ echo '{"path":"/etc/hostname"}' | bash main.sh
{"ok": true, "content": "iZbp1hzrvnradlsiut7vanZ\n", "path": "/etc/hostname"}

$ echo '{"path":"/nonexistent"}' | bash main.sh
{"ok": false, "error": "file not found: /nonexistent"}
```

#### Rust — `runtime = "rust"`

`rustc` 直接编译（无 Cargo），不能用 `serde_json`——需手写最小 JSON 字符串提取器。适合性能敏感且输入结构简单的工具：

```toml
# tool.toml
runtime = "rust"
entrypoint = "main.rs"
```

```rust
// main.rs
use std::fs;
use std::io::{self, Read};

/// Extract a string field value from flat JSON like {"key": "value"}.
/// Handles \\", \\n, \\t, \\r escapes. No serde_json needed.
fn extract_str(json: &str, key: &str) -> Option<String> {
    let search = format!("\"{}\"", key);
    let pos = json.find(&search)?;
    let after_key = &json[pos + search.len()..];
    let colon = after_key.find(':')?;
    let after_colon = &after_key[colon + 1..];
    let trimmed = after_colon.trim_start();
    if !trimmed.starts_with('"') {
        return None;
    }
    let inner = &trimmed[1..];
    let mut result = String::new();
    let mut chars = inner.chars();
    loop {
        match chars.next() {
            None => return None,
            Some('\\') => match chars.next() {
                Some('"') => result.push('"'),
                Some('\\') => result.push('\\'),
                Some('n') => result.push('\n'),
                Some('t') => result.push('\t'),
                Some('r') => result.push('\r'),
                Some(c) => { result.push('\\'); result.push(c); }
                None => return None,
            },
            Some('"') => break,
            Some(c) => result.push(c),
        }
    }
    Some(result)
}

fn main() {
    let mut input = String::new();
    io::stdin().read_to_string(&mut input).unwrap();
    let input = input.trim();

    let path_str = match extract_str(input, "path") {
        Some(p) if !p.is_empty() => p,
        _ => {
            println!(r#"{{"ok": false, "error": "missing required param: path"}}"#);
            return;
        }
    };

    let metadata = match fs::metadata(&path_str) {
        Ok(m) => m,
        Err(_) => {
            println!(r#"{{"ok": false, "error": "file not found: {}"}}"#, path_str);
            return;
        }
    };
    if !metadata.is_file() {
        println!(r#"{{"ok": false, "error": "not a file: {}"}}"#, path_str);
        return;
    }

    match fs::read_to_string(&path_str) {
        Ok(content) => {
            let escaped = content.replace('\\', "\\\\").replace('"', "\\\"")
                .replace('\n', "\\n").replace('\r', "\\r").replace('\t', "\\t");
            println!(r#"{{"ok": true, "content": "{}", "path": "{}"}}"#,
                escaped, path_str);
        }
        Err(e) => {
            println!(r#"{{"ok": false, "error": "read error: {}"}}"#, e);
        }
    };
}
```

**设计要点**：
- `extract_str()` — ~40 行最小 JSON 字符串提取器，处理 `\"`、`\n`、`\t`、`\r` 转义。仅覆盖扁平对象，不处理嵌套/数组
- 手动 `replace()` 做 JSON 转义——`\` → `\\`、`"` → `\"`、换行 → `\n` 等
- `rustc` 编译：首次 ~1-3s，ScriptTool 按 mtime 缓存，后续直接执行 binary

```bash
$ rustc main.rs -o main && echo '{"path":"/etc/hostname"}' | ./main
{"ok": true, "content": "iZbp1hzrvnradlsiut7vanZ\n", "path": "/etc/hostname"}

$ echo '{"path":"/nonexistent"}' | ./main
{"ok": false, "error": "file not found: /nonexistent"}
```

#### Runtime 选择速查

| 维度 | Python | Bash | Rust |
|------|--------|------|------|
| JSON 支持 | 原生 `json` 模块 | python3 胶水 (~3 行/处) | 手写提取器 (~40 行) |
| 首次延迟 | 即时 | 即时 | rustc ~1-3s（后续缓存） |
| 正则 | `re` 模块 | `grep -rnI` | 无（仅 `str::contains`） |
| 推荐场景 | **首选**——通用工具，复杂 JSON | **首选**——文件/系统操作为主 | 仅计算密集且输入简单的场景 |

---

## 前瞻：Skill Inventory 模式（渐进式披露）

当前 MCP 同时支持顶层 `tools/` 和 `skills/{name}/tools/` 两种目录。一种更激进的设计是**弃用顶层 `tools/`，让 Skill 成为唯一的 MCP 资源**——所有 Tool 归属某个 Skill，通过渐进式披露发现：L1 看有哪些 Skill → L2 看 Skill 里有什么工具 → L3 看工具的参数和脚本 → 执行。

```
{root}/
└── skills/                    # 唯一顶层资源
    └── filesystem/
        ├── SKILL.md           # L1: name + description（何时用）
        ├── tools/             # L2→L3: skill 内部的工具
        │   ├── read_file/
        │   │   ├── tool.toml  # L3: params_schema + entrypoint
        │   │   └── main.py    # L3: 工具脚本
        │   ├── write_file/
        │   └── search_content/
        ├── references/        # L3: 参考文档
        └── assets/            # L3: 静态资源
```

**与当前模式的关键差异**：`node_online` 的 `tools` 为**零**——Engine 初始只看到 Skill 列表。LLM 根据 Skill 描述决定加载哪个 Skill，然后通过 L2/L3 渐进获取工具细节。

以下示例演示完整链路——仅用 `skills/` 目录，无顶层 `tools/`：

```python
import tempfile, os, asyncio
from arf import Bus, McpNode, NodeId, NodeInfo, MessageFilter, ToMatch

# 1. 准备：仅 skills/ 目录，无顶层 tools/
root = tempfile.mkdtemp()
skill_dir = os.path.join(root, "skills", "filesystem")
os.makedirs(name=os.path.join(skill_dir, "tools/read_file"))
os.makedirs(name=os.path.join(skill_dir, "tools/write_file"))
os.makedirs(name=os.path.join(skill_dir, "tools/search_content"))
os.makedirs(name=os.path.join(skill_dir, "references"))

# SKILL.md — L1 元数据 + L2 说明文档
with open(os.path.join(skill_dir, "SKILL.md"), "w") as f:
    f.write("""---
name: filesystem
description: >
  File operations — reading, writing, searching. Use when user
  asks to read/write files or search for patterns in text.
---

# Filesystem Skill

## When to use
- Read a file → `read_file`
- Create/overwrite a file → `write_file`
- Search code/text → `search_content`
""")

# 工具在 skill 内部 — 与顶层 tools/ 结构完全一致
for tool_name, desc, schema_extra, script in [
    ("read_file", "Read file contents with UTF-8 encoding",
     'properties.path = { type = "string", description = "Absolute file path" }\nrequired = ["path"]',
     "import sys, json\nfrom pathlib import Path\nparams = json.loads(sys.stdin.read())\np = Path(params['path'])\nprint(json.dumps({'ok': True, 'content': p.read_text()[:200], 'path': str(p)}))"),
    ("write_file", "Write content to file, creating parent directories",
     'properties.path = { type = "string" }\nproperties.content = { type = "string" }\nrequired = ["path", "content"]',
     "import sys, json\nfrom pathlib import Path\nparams = json.loads(sys.stdin.read())\np = Path(params['path'])\np.parent.mkdir(parents=True, exist_ok=True)\np.write_text(params['content'])\nprint(json.dumps({'ok': True, 'path': str(p), 'bytes': len(params['content'])}))"),
    ("search_content", "Regex search in text files under a directory",
     'properties.pattern = { type = "string", description = "Regex pattern" }\nproperties.path = { type = "string", description = "Directory to search" }\nrequired = ["pattern", "path"]',
     "import sys, json, re\nfrom pathlib import Path\nparams = json.loads(sys.stdin.read())\np = Path(params['path'])\npat = re.compile(params['pattern'])\nmatches = []\nfor f in p.rglob('*'):\n if f.is_file():\n  try:\n   for i,line in enumerate(f.read_text().splitlines(),1):\n    if pat.search(line): matches.append({'file':str(f),'line':i,'content':line.strip()})\n  except: pass\nprint(json.dumps({'ok':True,'matches':matches[:10]}))"),
]:
    tdir = os.path.join(skill_dir, "tools", tool_name)
    with open(os.path.join(tdir, "tool.toml"), "w") as f:
        f.write(f'name = "{tool_name}"\ndescription = "{desc}"\nruntime = "python"\nentrypoint = "main.py"\n[params_schema]\ntype = "object"\n{schema_extra}\n')
    with open(os.path.join(tdir, "main.py"), "w") as f:
        f.write(script)


async def main():
    bus = Bus()
    node = McpNode.local(namespace="inventory", root=root)
    await node.connect(bus=bus)

    engine_info = NodeInfo(node_id="engine/s1", node_type="engine", capabilities={})
    engine = await bus.connect(
        info=engine_info,
        filter=MessageFilter(
            types=["skill_loaded", "skill_resource_loaded",
                   "skill_script_result", "skill_error",
                   "skill_resource_error"],
            to_match=ToMatch.All,
        ),
    )

    # ── L1: node_online → 只有 skills，没有 top-level tools ──
    graph = bus.graph()
    mcp = [n for n in graph.nodes if str(n.node_id) == node.node_id][0]
    caps = mcp.capabilities
    print(f"top-level tools: {len(caps['tools'])}")   # 0 — 全部在 skill 内
    print(f"skills: {[s['name'] for s in caps['skills']]}")  # ['filesystem']

    # ── L2: use_skill → body + 资源清单 ──
    engine.send(
        msg_type="use_skill",
        to=[NodeId(id=node.node_id)],
        payload={"name": "filesystem"},
    )
    resp = await engine.recv()
    resources = resp.payload["resources"]
    print(f"body: {len(resp.payload['body'])} chars ({resp.payload['description'][:50]}...)")
    print(f"tools inside skill: {resources['tools']}")        # ['read_file', 'search_content', 'write_file']
    print(f"references: {resources['references']}")            # ['api-guide.md']

    # ── L3: load_skill_resource → 工具脚本 + params_schema ──
    engine.send(
        msg_type="load_skill_resource",
        to=[NodeId(id=node.node_id)],
        payload={"skill_name": "filesystem",
                 "resource_path": "tools/write_file/main.py"},
    )
    resp = await engine.recv()
    print(f"description: {resp.payload['description']}")
    print(f"params_schema: {list(resp.payload['params_schema'].keys())}")
    # ['properties', 'required', 'type']

    # ── 执行: run_skill_script → 工具结果 ──
    test_file = os.path.join(skill_dir, "tools/read_file/main.py")
    engine.send(
        msg_type="run_skill_script",
        to=[NodeId(id=node.node_id)],
        payload={"skill_name": "filesystem", "tool_name": "read_file",
                 "call_id": "c1", "params": {"path": test_file}},
    )
    resp = await engine.recv()
    print(f"status: {resp.payload['status']}")   # success
    print(f"name: {resp.payload['name']}")       # filesystem/read_file

    await bus.shutdown()

asyncio.run(main())
```

**实际运行输出**：

```
top-level tools: 0
skills: ['filesystem']
body: 337 chars (File operations — reading, writing, searching...)
tools inside skill: ['read_file', 'search_content', 'write_file']
references: ['api-guide.md']
description: Write content to file, creating parent directories
params_schema: ['properties', 'required', 'type']
status: success
name: filesystem/read_file
```

**设计权衡**：

| 维度 | 当前模式（tools/ + skills/） | Skill Inventory 模式 |
|------|---------------------------|---------------------|
| 简单工具 | 直接放 `tools/`，零 ceremony | 必须创建 Skill 包裹 |
| 工具发现 | 启动时全部列出 | L1→L2→L3 渐进，按需加载 |
| context 利用率 | 所有工具描述始终在 system prompt | 仅 L1 skill 描述在 prompt，工具详情按需注入 |
| 适用场景 | 工具数量少（<20），全量注册无压力 | 工具数量多（50+），需要分层组织 |

> **当前状态**：框架已支持 Skill Inventory 模式（`skills/{name}/tools/` 路径）。顶层 `tools/` 保留用于快速原型和简单场景。未来可考虑弃用顶层 `tools/`，统一到 Skill 模型——取决于实际使用中的 context 压力和工具组织需求。

---

## Error Reference

| Exception | Match text | Trigger |
|-----------|-----------|---------|
| `RuntimeError` | `"discovery error"` | `McpNode.local()` — root 路径不存在或不可读 |
| `ConnectionError` | `"unreachable"` | `McpNode.remote()` — 远端不可达（DNS/连接拒/超时/TLS） |
| `ConnectionError` | `"rejected"` | `McpNode.remote()` — MCP 握手被服务端拒绝（协议版本不匹配等） |
| `RuntimeError` | `"bus connect"` | `node.connect()` — Bus 连接失败 |

> **注意**：工具执行错误（如文件不存在、脚本返回 error）不会抛异常——它们通过 `ToolResultItem.status = "error"` 和 `error` 字段返回。Engine 通过 `tool_result_set` 消息获取结构化错误，并转换为 `ModelMessage` 注入 LLM 上下文。

---

## Python vs Rust API 差异

| 维度 | Rust | Python |
|------|------|--------|
| **Local 构造** | `McpNode::local(ns, root)` | `McpNode.local(namespace=ns, root=root)` — sync classmethod |
| **Remote 构造** | `McpNode::remote(ns, config).await` | `await McpNode.remote(namespace=ns, config=config)` — async classmethod |
| **connect** | `node.connect(&bus).await` | `await node.connect(bus=bus)` — async method |
| **自定义 runtime** | `McpNode::local_with_runtime(ns, root, rt)` | 暂未暴露（延后到 SandboxRuntime 需求驱动） |
| **namespace** | `node.namespace`（String 字段） | `node.namespace`（str property） |
| **node_id** | `node.node_id`（NodeId 类型） | `node.node_id`（str property，自动转换） |

> **未实现**：`RuntimeModule` trait 的 Python 子类化（PyO3 trampoline 复杂度高）。当前 Python 用户通过 `McpNode.local()` / `McpNode.remote()` 使用内置 runtime（LocalRuntime / RemoteRuntime），`local_with_runtime()` 构造函数暂不可用。

---

## 性能说明

| 操作 | 实测耗时 | 说明 |
|------|---------|------|
| `McpNode.local()` | ~0.3ms | 文件系统扫描 `tools/*/tool.toml` |
| `McpNode.remote()` | ~1.7-2.4s | HTTP initialize + tools/list（取决于网络延迟） |
| `node.connect()` | ~1ms | 注册到 Bus + 广播 `node_online` |
| 本地 tool 执行 | ~2ms | Bash subprocess spawn + stdin/stdout JSON（冷启动 ~50ms） |
| 远程 tool 执行 | ~500ms-2s | HTTP tools/call 往返 |
| Rust tool 首次编译 | ~1-3s | `rustc source -o binary`；后续使用 mtime 缓存 |
| 本地完整示例（Quickstart） | ~0.1s | Bus 创建 + scan + connect + shutdown |
| 远程完整示例（CodeTidy） | ~2.3s | Bus 创建 + HTTP 握手 + connect + shutdown |

---

## See Also

- [Phase 5 设计文档](../v1.x/phase5_mcp/phase5-mcp-design.md) — 完整架构设计、消息协议、执行模型
- [Task 5.2 — ScriptTool](../v1.x/phase5_mcp/task-5.2-script-tool.md) — 脚本工具实现细节
- [Task 5.9 — Python API](../v1.x/phase5_mcp/task-5.9-python-api.md) — PyO3 绑定实现记录
- [Task 5.12 — 集成测试](../v1.x/phase5_mcp/task-5.12-integration-tests.md) — MiniEngine + CodeTidy 集成
- [ARP Bus API 参考](bus.md) — Bus、NodeHandle、MessageFilter 详解
- [ARF ModelAdapter API 参考](model-adapter.md) — `tool_result_to_model_message()` 转换
