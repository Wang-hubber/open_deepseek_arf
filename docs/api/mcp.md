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
├── skills/                 # Skill 目录（Phase 5 预留）
│   └── react-component/
│       ├── SKILL.md
│       └── references/
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

### 模式四：script 工具三种 runtime

```toml
# Python — JSON 原生支持，最自然
# tool.toml: runtime = "python", entrypoint = "main.py"
```

```toml
# Bash — 用 python3 做 JSON 胶水，核心操作用原生命令
# tool.toml: runtime = "bash", entrypoint = "main.sh"
```

```toml
# Rust — rustc 编译，无外部 crate，适合性能关键 + 输入简单的工具
# tool.toml: runtime = "rust", entrypoint = "main.rs"
# ⚠️ 限制：无 serde_json（手写 JSON 解析）、无 regex（仅 str::contains）
```

| Runtime | JSON 支持 | 首次延迟 | 适合场景 |
|---------|----------|---------|---------|
| Python | 原生 `json` 模块 | 即时 | 通用工具，复杂 JSON 操作 |
| Bash | python3 胶水（~5行） | 即时 | 文件/系统操作密集 |
| Rust | 手写提取器（~60行） | rustc 编译 ~1-3s | 计算密集，简单 JSON 输入 |

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
