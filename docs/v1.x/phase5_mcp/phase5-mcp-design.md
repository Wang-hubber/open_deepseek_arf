# Phase 5 — MCP 设计

> 父文档：`docs/v1.x/2026-06-26-arfv1-roadmap.md`
> 依赖：Phase 1 (Bus) — 已完成
> 状态：✅ 设计完成（含 RuntimeModule trait 抽象）

## 定位

**一个 MCP 实例 = 一个 namespace = Bus 上一个节点。** Engine 只和 `mcp/{namespace}` 通讯。本地 MCP 扫描文件夹发现 Tool/Skill，远程 MCP 通过 HTTP 协议发现和代理执行。Engine 对两者无区别——都是 `node_online` 广播 + 响应 `tool_call_set`。

> **核心设计意图 — 执行权归属注册方**
>
> ```
> Engine                              MCP (mcp/filesystem)
>   │                                     │
>   │  发出 tool_call_set                  │  我注册了 read_file，我决定它怎么跑
>   │  [call_0: read_file, ...]           │    ├── LocalRuntime  → python3 script.py  (宿主机)
>   │                                     │    └── SandboxRuntime → docker run ...    (容器)
>   │                                     │
>   │  ←── tool_result_set ────────────── │  执行细节对 Engine 不可见
>   │  [call_0: {status:"success", ...}]  │
>   │                                     │
>   │  Engine 只看到：                      │
>   │  - node_online 时知道 capabilities    │
>   │  - tool_result_set 时知道结果         │
>   │  - 不关心宿主机/Docker/Firecracker    │
> ```
>
> **一句话：谁注册了这个 Tool，谁就决定这个 Tool 如何被运行。Engine 侧完全没有认知负担——只获取最终结果。**
>
> 封装边界在 `RuntimeModule` trait。执行策略（本地进程 / Docker 容器 / Firecracker uVM）是 MCP 节点的内部实现细节，通过 `capabilities()` 自描述，通过 `run_single()` 执行。Engine 不需要理解、也无法触及这一层。

```
Bus
 │
 ├── mcp/filesystem  (LocalMcpNode) root=/mcp/stable
 │       │
 │       ├── [内部] discovery → 扫描 {root}/tools + {root}/skills
 │       └── [内部] runtime   → RuntimeModule trait (构造时绑定)
 │             ├── LocalRuntime (默认) → 宿主机直接 spawn
 │             └── SandboxRuntime (用户定义) → Bus → sandbox node
 │
 ├── mcp/codetidy    (RemoteMcpNode) url=https://mcp.codetidy.dev
 │       │
 │       ├── [内部] 无文件系统，HTTP tools/list 发现
 │       └── [内部] HTTP tools/call 代理执行
 │
 └── mcp/another     (LocalMcpNode) root=/mcp/experimental
         ├── [内部] discovery
         └── [内部] runtime
```

Engine 视角——**多个 MCP 节点，统一入口**：

```
Engine
  │
  │ 看到 Bus 上有三个 MCP 节点：
  │   mcp/filesystem   → node_online{tools: [read_file, write_file, search_content], skills: [...]}
  │   mcp/codetidy     → node_online{tools: [json_format, base64_encode, url_decode, ...], skills: []}
  │   mcp/another      → node_online{tools: [code_review_v2, ...], skills: [...]}
  │
  │ 需要读文件    → tool_call_set 发给 mcp/filesystem
  │ 需要 JSON 格式化 → tool_call_set 发给 mcp/codetidy
  │ 需要 skill      → use_skill 发给 mcp/filesystem
  │
  │ 各 namespace 独立，同名 tool 不冲突
```

**MCP 内部消息分发**：

```
Engine 发消息给 mcp/{namespace}
  │
  ▼
LocalMcpNode / RemoteMcpNode (Bus NodeHandle)
  │
  │ 按 msg_type 分发：
  │
  ├── msg_type = tool_call_set        → [internal] runtime / HTTP proxy 处理
  ├── msg_type = run_skill_script     → [internal] discovery 的 run_tool 处理 (Remote 返回 error)
  ├── msg_type = use_skill            → [internal] discovery 处理 (Remote 返回 error)
  └── msg_type = load_skill_resource  → [internal] discovery 处理 (Remote 返回 error)
```

## 节点结构

### 两种节点，统一接口

```
               ┌──────────────────────┐
               │     Bus Node 接口     │
               │  node_online / tool_  │
               │  call_set / use_skill │
               └──────┬───────────────┘
                      │
          ┌───────────┴───────────┐
          │                       │
  ┌───────┴────────┐    ┌────────┴──────────┐
  │ LocalMcpNode   │    │  RemoteMcpNode    │
  │                │    │                   │
  │ root_dir       │    │ RemoteConfig      │
  │ DiscoveryModule│    │ HTTP transport     │
  │ RuntimeModule  │    │ tools/list proxy   │
	  │  (trait)       │    │                   │
  │ SkillIndex     │    │ tools/call proxy   │
  └────────────────┘    └───────────────────┘
      本地 Tool+Skill         外部 Tool (无 Skill)
```

| | new() | connect(bus) | 运行时 | Skill |
|---|---|---|---|---|---|
| **LocalMcpNode** | `new(ns, root_dir) -> Result<Self, McpError>` | `connect(bus) -> Result<(), McpError>` → `node_online` | ScriptTool subprocess → `ToolResultItem` | ✅ `skills/*/SKILL.md` |
| **RemoteMcpNode** | `new(ns, config) -> Self` (不联网) | `connect(bus) -> Result<(), McpError>` → HTTP init + `node_online` | HTTP `tools/call` proxy → `ToolResultItem` | ❌ → `skill_error` |

两者对 Engine 完全透明 — 都是 `node_online` 广播能力，都响应 `tool_call_set`。

### 统一生命周期 + 错误类型

LocalMcpNode 和 RemoteMcpNode 共享相同的生命周期分界和错误类型。Engine 只看到连接成功后的 `node_online` 广播——连接失败意味着节点不存在。

```rust
/// Unified error type for MCP node creation and connection.
pub enum McpError {
    /// Resource discovery failed (local: scan error; remote: tools/list failed).
    Discovery { reason: String },
    /// Remote MCP server unreachable (RemoteMcpNode only).
    RemoteUnreachable { url: String, reason: String },
    /// MCP handshake rejected (RemoteMcpNode only).
    RemoteRejected { url: String, code: i32, message: String },
    /// Bus connection failed.
    BusConnect { reason: String },
}
```

```
                    LocalMcpNode                         RemoteMcpNode
                    ─────────────                        ─────────────
new()              scan(root_dir) + 自检                仅创建 struct，不联网
                   失败 → McpError::Discovery

connect(bus)       bus.connect() +                     HTTP initialize
                   广播 node_online                     → tools/list
                   失败 → McpError::BusConnect          → bus.connect()
                                                         → 广播 node_online
                                                        失败 → McpError::Remote*

运行时             tool_call_set → executor             tool_call_set → HTTP proxy
                    ↓                                    ↓
                   ToolResultItem                       ToolResultItem
                   { status: "success"                  { status: "success"
                   | "error"                            | "error"
                   | "cancelled",                       | "cancelled",
                     error: Option<String> }              error: Option<String> }
                                                        + use_skill → skill_error
```

> **Engine 视角**：连接成功 → 收到 `node_online` → 发 `tool_call_set` → 收 `tool_result_set`。不管本地还是远程，运行时错误统一为 `ToolResultItem.error: String`。零认知负担。

### LocalMcpNode

```rust
/// A local MCP node — discovers tools and skills from the filesystem.
///
/// RuntimeModule is a trait object bound at construction time:
/// - Default: `LocalRuntime` — spawns subprocesses directly on the host
/// - Custom:  `SandboxRuntime` — forwards execution to a sandbox Bus node
pub struct LocalMcpNode {
    pub namespace: String,
    pub node_id: NodeId,
    root_dir: PathBuf,
    discovery: DiscoveryModule,
    runtime: Box<dyn RuntimeModule>,
}

impl LocalMcpNode {
    /// Scan the filesystem with the default local runtime.
    /// Returns Err(McpError::Discovery) if the root_dir doesn't exist
    /// or contains no valid tools/skills.
    pub fn new(namespace: impl Into<String>, root_dir: PathBuf) -> Result<Self, McpError> {
        todo!()
    }

    /// Scan the filesystem with a custom RuntimeModule (e.g. Docker sandbox).
    /// The runtime is bound at construction — its execution strategy is fixed
    /// before the node goes online.
    pub fn with_runtime(
        namespace: impl Into<String>,
        root_dir: PathBuf,
        runtime: Box<dyn RuntimeModule>,
    ) -> Result<Self, McpError> {
        todo!()
    }

    /// Connect to the Bus and broadcast node_online.
    /// The node_online payload includes runtime.capabilities() so Engine
    /// can see execution characteristics (local vs sandbox, image, etc.).
    pub async fn connect(&self, bus: &Bus) -> Result<(), McpError> {
        todo!()
    }
}
```

### RuntimeModule trait — 执行后端抽象

**定义时绑定执行方式，capability 自描述。** `RuntimeModule` 不关心内部 DAG 调度（由 executor 统一处理），只负责单个 tool call 的实际执行。框架默认提供 `LocalRuntime`（宿主机直接 spawn），开发者可实现 `SandboxRuntime` 转发到 Bus 上的 sandbox 节点。

```rust
use async_trait::async_trait;
use std::collections::HashMap;
use std::sync::Arc;
use serde_json::Value;

use crate::tool::Tool;
use crate::types::{ToolCallSet, ToolResultSet};

/// Execution backend for tool calls — bound at LocalMcpNode construction.
///
/// The RuntimeModule trait decouples DAG scheduling from subprocess execution.
/// The executor handles topology, concurrency, and cascade cancel — it calls
/// `run_single()` for each individual tool invocation.
///
/// Implementations:
/// - `LocalRuntime` (framework default): spawn subprocesses on the host
/// - `SandboxRuntime` (user-defined): forward to a sandbox Bus node
#[async_trait]
pub trait RuntimeModule: Send + Sync {
    /// Self-describing capabilities — injected into `node_online.payload.capabilities`.
    ///
    /// Engine sees this and knows the execution environment without understanding
    /// sandbox internals. Examples:
    /// ```json
    /// {"runtime": "local", "concurrency": "layer-parallel"}
    /// {"runtime": "sandbox", "engine": "docker", "image": "python:3.11-slim"}
    /// ```
    fn capabilities(&self) -> Value;

    /// Execute a full `tool_call_set`. The default implementation delegates
    /// DAG scheduling to the executor and calls `run_single()` per call.
    /// Override this only if you need to replace the entire execution model.
    async fn execute(
        &self,
        call_set: &ToolCallSet,
        tools: &HashMap<String, Arc<dyn Tool>>,
    ) -> ToolResultSet;

    /// Execute a single tool call. Called by the default `execute()` impl
    /// (after DAG scheduling) or directly for single-call sets.
    async fn run_single(
        &self,
        call_id: &str,
        tool: &dyn Tool,
        params: Value,
    ) -> (String, Value, Option<String>); // (status, result, error)
}
```

**框架默认实现**：

```rust
/// Default RuntimeModule — spawns subprocesses directly on the host.
///
/// This is what `LocalMcpNode::new()` uses. Zero configuration, zero overhead.
pub struct LocalRuntime;

#[async_trait]
impl RuntimeModule for LocalRuntime {
    fn capabilities(&self) -> Value {
        serde_json::json!({"runtime": "local", "concurrency": "layer-parallel"})
    }

    async fn execute(
        &self,
        call_set: &ToolCallSet,
        tools: &HashMap<String, Arc<dyn Tool>>,
    ) -> ToolResultSet {
        // Delegates to the DAG executor (Task 5.4), which calls run_single()
        // per call after topological sort and layer construction.
        todo!() // Task 5.4 + 5.7
    }

    async fn run_single(
        &self,
        call_id: &str,
        tool: &dyn Tool,
        params: Value,
    ) -> (String, Value, Option<String>) {
        match tool.execute(params).await {
            Ok(val) => ("success".into(), val, None),
            Err(e) => ("error".into(), Value::Null, Some(e.message)),
        }
    }
}
```

**SandboxRuntime 示例（用户实现，Phase 5 后置）**：

```rust
/// User-defined RuntimeModule — forwards execution to a sandbox Bus node.
///
/// Does NOT spawn subprocesses itself. Instead, sends a `sandbox_exec`
/// message to `sandbox/{engine}` and waits for `sandbox_result`.
pub struct SandboxRuntime {
    /// Name of the sandbox Bus node (e.g., "docker", "firecracker").
    engine: String,
    /// Container image (e.g., "python:3.11-slim").
    image: String,
    /// Bus handle for sending sandbox_exec messages.
    bus: Arc<Bus>,
}

#[async_trait]
impl RuntimeModule for SandboxRuntime {
    fn capabilities(&self) -> Value {
        serde_json::json!({
            "runtime": "sandbox",
            "engine": self.engine,
            "image": self.image,
        })
    }

    async fn run_single(
        &self,
        call_id: &str,
        tool: &dyn Tool,
        params: Value,
    ) -> (String, Value, Option<String>) {
        // Send sandbox_exec message to sandbox/{engine} node
        // Wait for sandbox_result response
        // Return (status, result, error)
        todo!()
    }
}
```

**构造对比**：

```rust
// 默认：宿主机执行，零配置
let mcp = LocalMcpNode::new("filesystem", root_dir)?;

// 沙箱：Docker 隔离执行
let runtime = Box::new(SandboxRuntime::new("docker", "python:3.11-slim", &bus));
let mcp = LocalMcpNode::with_runtime("filesystem", root_dir, runtime)?;
```

**Python 侧等价**：

```python
# 默认
mcp = LocalMcpNode(namespace="filesystem", root_dir="/root")

# 沙箱：用户自定义 RuntimeModule
class DockerSandbox(RuntimeModule):
    def capabilities(self) -> dict:
        return {"runtime": "sandbox", "engine": "docker", "image": self.image}

    async def run_single(self, call_id, tool, params):
        # 发 sandbox_exec → 收 sandbox_result
        ...

runtime = DockerSandbox(image="python:3.11-slim", bus=bus)
mcp = LocalMcpNode.with_runtime("filesystem", "/root", runtime)
```

### RemoteMcpNode

```rust
/// Streamable HTTP transport configuration for a remote MCP server.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RemoteConfig {
    /// Transport protocol: `"streamable-http"`.
    pub transport: String,
    /// Base URL of the remote MCP server.
    pub url: String,
    /// HTTP request timeout in seconds. `None` = no timeout (the default).
    /// Set this when the remote service has known latency characteristics.
    #[serde(default)]
    pub timeout_secs: Option<u64>,
    /// Optional HTTP headers injected into every request.
    /// Common use: Authorization, X-API-Key, custom tenant IDs.
    #[serde(default)]
    pub headers: HashMap<String, String>,
    /// Optional path to a custom CA certificate bundle (PEM format).
    /// For internal deployments with self-signed certificates.
    #[serde(default)]
    pub tls_ca_cert: Option<PathBuf>,
    /// Retry configuration for transient failures. `None` = no retry.
    #[serde(default)]
    pub retry: Option<RetryConfig>,
}

/// Retry policy for transient HTTP failures during tools/call.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RetryConfig {
    /// Maximum retry attempts. Default = 3.
    #[serde(default = "default_max_retries")]
    pub max_retries: u32,
    /// Initial backoff in milliseconds. Default = 1000.
    #[serde(default = "default_initial_backoff_ms")]
    pub initial_backoff_ms: u64,
    /// Maximum backoff in milliseconds. Default = 30000.
    #[serde(default = "default_max_backoff_ms")]
    pub max_backoff_ms: u64,
}

fn default_max_retries() -> u32 { 3 }
fn default_initial_backoff_ms() -> u64 { 1000 }
fn default_max_backoff_ms() -> u64 { 30000 }

/// A remote MCP node — discovers tools via HTTP, proxies execution.
pub struct RemoteMcpNode {
    pub namespace: String,
    pub node_id: NodeId,
    config: RemoteConfig,
    http_client: reqwest::Client,
    known_tools: HashMap<String, RemoteToolDef>,
}

impl RemoteMcpNode {
    /// Create the node struct without networking. Always succeeds.
    pub fn new(namespace: impl Into<String>, config: RemoteConfig) -> Self {
        todo!()
    }

    /// HTTP initialize → tools/list → Bus connect → broadcast node_online.
    /// Returns Err(McpError::RemoteUnreachable|RemoteRejected|BusConnect).
    pub async fn connect(&self, bus: &Bus) -> Result<(), McpError> {
        todo!()
    }
}
```

**RemoteMcpNode 工作流（JSON-RPC 2.0）**：

```
new() → 创建 struct（参数校验，不联网）
  │
connect(bus):
  ├→ HTTP POST → initialize (MCP 握手)
  │    → {"jsonrpc":"2.0","id":1,"method":"initialize","params":{...}}
  │    ← {"jsonrpc":"2.0","id":1,"result":{...}}
  │
  ├→ 失败:
  │    ├─ 网络不可达 → Err(McpError::RemoteUnreachable { url, reason })
  │    ├─ 握手被拒   → Err(McpError::RemoteRejected { url, code, message })
  │    └─ HTTP 超时  → Err(McpError::RemoteUnreachable { url, reason: "timeout after Ns" })
  │
  ├→ HTTP POST → tools/list
  │    → {"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}
  │    ← {"jsonrpc":"2.0","id":2,"result":{"tools":[{name,description,inputSchema}]}}
  │    → 构建 known_tools (HashMap<String, RemoteToolDef>)
  │
  ├→ bus.connect() → 广播 node_online (携带全部 tools)
  │   失败 → Err(McpError::BusConnect { reason })
  │
  └→ 成功

### Runtime: tool_call_set 处理 + 重连

```
收到 tool_call_set:
  └→ 对每个 ToolCallItem:
       ├─ tool 不在 known_tools → ToolResultItem { status: "error", error: "tool not found: {name}" }
       ├─ HTTP POST tools/call
       │    → {"jsonrpc":"2.0","id":N,"method":"tools/call","params":{"name":"X","arguments":{...}}}
       │    ← 成功: {"result":{"content":[{type:"text","text":"..."}]}}
       │       → ToolResultItem { status: "success", result: text, name: tool }
       │    ← MCP 协议错误: {"error":{"code":-32000,"message":"..."}}
       │       → ToolResultItem { status: "error", error: "MCP error [{code}]: {message}" }
       │    ← 非 text content_type:
       │       → ToolResultItem { status: "error", error: "unsupported content type: {type}" }
       │    ← 网络层错误 / 5xx / timeout
       │       → 如果 config.retry = Some → 触发重连
       └─ name 从 ToolCallItem.tool 回填

重连流程（仅当 config.retry 启用且错误可重试）:
  ┌→ 等待 backoff (initial_backoff_ms → 指数退避 → max_backoff_ms)
  ├→ HTTP initialize (重新握手，验证 server 在线)
  ├→ HTTP tools/list (刷新工具列表，server 可能已更新)
  ├→ 更新 known_tools，重新广播 node_online
  ├→ 重试 tools/call
  ├─ 成功 → ToolResultItem { status: "success" }
  └─ 超过 max_retries → ToolResultItem { status: "error", error: "retry exhausted after {N} attempts" }

可重试 vs 不可重试:
  网络错误 (DNS/connection refused/TLS/timeout) → 重试
  HTTP 5xx (服务端临时故障) → 重试
  HTTP 429 (限流, 等 Retry-After) → 重试
  HTTP 4xx 非 429 (认证/权限/参数错误) → 不重试，直接 error
```

### use_skill / load_skill_resource

```
收到 use_skill / load_skill_resource:
  → 返回 skill_error: { error: "skills not supported by remote MCP node: {namespace}" }
```

**namespace 约定**：仅含小写字母、数字和连字符（如 `filesystem`、`code-review`、`codetidy`）。MCP 构造时内部去重——同一 namespace 内 tool/skill name 冲突直接 panic（开发期错误）。跨 namespace 同名无影响。

**文件夹扫描**：LocalMcpNode 的 `root_dir` 传给 `DiscoveryModule` 和 `SkillIndex`。`DiscoveryModule::scan()` 扫描 `{root}/tools/*/tool.toml` → ScriptTool + `{root}/skills/*/SKILL.md` → SkillEntry，生成统一的 `node_online` 广播。框架不内置任何 Tool——所有本地 Tool 通过文件夹约定发现。

**Engine 路由逻辑**（极简）：
1. 监听 `node_online` → 看到 `mcp/filesystem`、`mcp/network` 等节点
2. 所有与 MCP 的通讯都发给 `mcp/{namespace}` 这一个 NodeId
3. MCP 内部按 `msg_type` 分发给 discovery 或 runtime 模块

## 依赖关系

```
arf-core: NodeId, Message, NodeInfo, ModelMessage
    ↑                   ↑
    │                   │
arf-bus: Bus,     arf-mcp ─── depends on: arf-core + arf-bus + serde + serde_json + tokio + toml + reqwest
NodeHandle,             ↑
MessageFilter           │
    ↑           arf-model-adapter ─── depends on: arf-core + arf-bus + arf-mcp + reqwest
    │
arf-engine ─── depends on: arf-core + arf-bus + arf-state (Phase 6)
```

不依赖 `arf-state`、不依赖 `arf-agent`。

**关键**：`arf-model-adapter` 依赖 `arf-mcp`，因为它需要将 `ToolResultItem`（MCP 产出）转换为 `ModelMessage`（模型 API 格式）。转换函数 `tool_result_to_model_message()` 在 `arf-model-adapter/src/convert.rs` 中定义。MCP 不感知 ModelAdapter——它只产出纯数据。

---

## 资源注册与发现

> 开发者如何注册 Tool/Skill？MCP 如何发现它们？

### 唯一注册路径：文件夹约定

**所有 Tool 和 Skill 通过文件夹扫描发现。** 框架只提供发现和执行机制，不内置任何具体实现。开发者按约定组织目录，MCP 自动扫描注册。

| 资源 | 目录 | 入口文件 | 实现方式 |
|------|------|---------|---------|
| **ScriptTool** | `{root}/tools/{name}/` | `tool.toml` | `DiscoveryModule` 扫描 → `ScriptTool` 包裹 |
| **Skill** | `{root}/skills/{name}/` | `SKILL.md` | `SkillIndex` 扫描 → `SkillEntry` 索引 |

### LocalMcpNode 的 root 目录

`LocalMcpNode` 构造时接收一个 root 目录，内部按约定组织：

```
{root}/
├── skills/                 # SkillIndex 扫描
│   ├── react-component/
│   │   ├── SKILL.md
│   │   ├── scripts/
│   │   ├── references/
│   │   └── assets/
│   └── ...
├── tools/                  # ScriptTool 扫描
│   ├── cleanup_logs/
│   │   ├── tool.toml
│   │   └── main.sh
│   └── ...
```

**Skill**：开发者建文件夹 + 写 `SKILL.md`，`SkillIndex::scan()` 自动发现。

**ScriptTool**：开发者建文件夹 + 写 `tool.toml` + 入口脚本，`DiscoveryModule::scan()` 自动发现并创建 `ScriptTool` 实例。

```rust
// 本地 MCP — 构造即扫描，连接即上线
let local = LocalMcpNode::new("filesystem", PathBuf::from("/path/to/root"))?;
local.connect(&bus).await?; // → node_online 广播

// 远程 MCP — 构造不联网，连接时握手，支持重连
let remote = RemoteMcpNode::new("codetidy", RemoteConfig {
    transport: "streamable-http".into(),
    url: "https://mcp.codetidy.dev".into(),
    timeout_secs: None,  // 不设超时，由开发者按服务特性决定
    headers: HashMap::from([
        ("Authorization".into(), "Bearer sk-xxx".into()),
    ]),
    tls_ca_cert: None,
    retry: Some(RetryConfig {
        max_retries: 3,
        initial_backoff_ms: 1000,
        max_backoff_ms: 30000,
    }),
});
remote.connect(&bus).await?; // → HTTP initialize → tools/list → node_online 广播
```

### 脚本 Tool

#### 语言支持与场景

| 语言 | 场景 | 执行方式 |
|------|------|---------|
| **Python** | AI 生态、数据处理、LLM 工具链 | `python3 {entrypoint}` |
| **Rust** | 性能敏感的解析、并发操作 | 首次/变更时自动编译为 binary |
| **Bash** | 定时任务、系统操作、CI/CD 集成 | `bash {entrypoint}` |

#### tool.toml — 脚本 Tool 元数据

```toml
name = "cleanup_logs"
description = "Delete log files older than N days"
runtime = "bash"             # "python" | "bash" | "rust"
entrypoint = "main.sh"       # 入口文件
timeout_ms = 30000           # 可选：单次执行超时

[params_schema]               # JSON Schema
type = "object"
properties.days = { type = "integer", default = 30 }
properties.path = { type = "string" }
```

#### 执行协议：stdin/stdout JSON

MCP 通过 `ScriptTool`（通用的 `Tool` trait 实现）包裹所有脚本：

```
MCP executor
  → ScriptTool.execute(params)
    → 按 runtime 选择执行器
    → stdin 写入 JSON params
    → stdout 读取 JSON result
    → stderr 捕获为 error
```

脚本只需要：**从 stdin 读 JSON，往 stdout 写 JSON**。

```python
import sys, json
params = json.loads(sys.stdin.read())
# ... 业务逻辑 ...
print(json.dumps({"ok": True, "deleted": 42}))
```

#### Tool 来源唯一化

`DiscoveryModule::scan()` 只从一个来源发现 Tool：**`{root}/tools/*/tool.toml`**。每个合法目录生成一个 `ScriptTool` 实例。不区分"内置"和"脚本"——所有 Tool 都是 ScriptTool，通过 `node_online` 统一广播。

### 持久化选择：文件夹 vs SQLite

**文件夹胜出**：
- Skill 本质是 Markdown 文件，放数据库里反而别扭
- Git 友好，开发者的 skill 可以版本控制
- FileWatcher 可直接监听文件变更做热加载
- Skill 数量级最多几十个，不需要查询引擎
- 如果未来有全文搜索需求，在内存中建 `HashMap<String, SkillEntry>` 索引即可

---

## 数据结构

### ToolError

```rust
/// Error returned by a tool's execute().
///
/// The executor catches this and packages it into the ToolResultItem.
/// Tool authors don't need to follow any error convention — just return Err.
#[derive(Debug, Clone)]
pub struct ToolError {
    pub message: String,
}

impl std::fmt::Display for ToolError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.message)
    }
}

impl std::error::Error for ToolError {}

impl From<&str> for ToolError {
    fn from(s: &str) -> Self {
        Self { message: s.to_string() }
    }
}

impl From<String> for ToolError {
    fn from(s: String) -> Self {
        Self { message: s }
    }
}
```

### Tool trait

```rust
/// A tool that MCP can execute.
///
/// Implementations are Send + Sync so they can be shared across tokio tasks.
/// Tool authors only need to implement `execute()` — error handling, panic
/// catching, and result packaging are centralized in the executor.
/// Sandboxing and approval are handled by separate Nodes on the Bus — not here.
pub trait Tool: Send + Sync {
    /// Unique tool name: "read_file", "write_file", "search_content".
    fn name(&self) -> &str;

    /// Human-readable description for LLM function calling.
    fn description(&self) -> &str;

    /// JSON Schema for the tool's parameters.
    fn parameters_schema(&self) -> serde_json::Value;

    /// Execute the tool with the given parameters.
    ///
    /// Returns Ok(result) on success, Err(ToolError) on failure.
    /// The executor also wraps this in catch_unwind for panic safety —
    /// tool authors can use plain `unwrap()` / `expect()` without breaking MCP.
    async fn execute(
        &self,
        params: serde_json::Value,
    ) -> Result<serde_json::Value, ToolError>;

    /// Cancel an in-progress execution.
    ///
    /// Called by the executor when:
    /// - A dependency fails (cascade cancel)
    /// - Engine sends a cancellation for this tool_call_set
    ///
    /// Default implementation is a no-op. Tools with long-running or
    /// resource-holding operations should override this to release
    /// resources and set a cancellation flag.
    async fn cancel(&self) {
        // no-op by default
    }
}
```

### ScriptRuntime — 脚本语言

```rust
/// Supported script runtimes.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum ScriptRuntime {
    Python,
    Bash,
    Rust,
}
```

### ToolConfig — tool.toml 解析结果

```rust
/// Parsed tool.toml — script tool metadata.
///
/// Each directory under `{root}/tools/` with a valid tool.toml is
/// registered as a ScriptTool. The `runtime` field selects the executor.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolConfig {
    /// Unique tool name (kebab-case).
    pub name: String,
    /// Human-readable description for LLM function calling.
    pub description: String,
    /// Script runtime: "python", "bash", or "rust".
    pub runtime: ScriptRuntime,
    /// Entry point script filename relative to the tool directory.
    pub entrypoint: String,
    /// Per-call timeout in milliseconds. None = no timeout.
    #[serde(default)]
    pub timeout_ms: Option<u64>,
    /// JSON Schema for the tool's parameters.
    #[serde(default)]
    pub params_schema: serde_json::Value,
}
```

### ScriptTool — 实现 Tool trait 的通用脚本包裹器

```rust
/// A Tool implementation that wraps an external script.
///
/// ScriptTool implements the Tool trait, so it works with the standard
/// DAG executor — no special path. The `execute()` method:
/// 1. Starts a child process (python3 / bash / compiled binary)
/// 2. Writes params as JSON to stdin
/// 3. Reads result as JSON from stdout
/// 4. Captures stderr for error reporting
/// 5. Kills the process on timeout or cancel
pub struct ScriptTool {
    /// Tool name from tool.toml.
    name: String,
    /// Tool description from tool.toml.
    description: String,
    /// Which runtime to use.
    runtime: ScriptRuntime,
    /// Path to the tool directory (contains entrypoint script).
    tool_dir: PathBuf,
    /// Entry point filename (e.g. "main.sh").
    entrypoint: String,
    /// Per-call timeout. None = no timeout.
    timeout_ms: Option<u64>,
    /// JSON Schema for parameters.
    params_schema: serde_json::Value,
}
```

### ToolCallItem — 单个工具调用

```rust
/// A single tool invocation within a tool_call_set.
///
/// Bidirectional dependency lock (same pattern as task State):
///   blocked_by — who blocks me (I depend on them)
///   blocking  — who I block (they depend on me)
///
/// Engine sets the dependencies; executor derives the reverse edges
/// during DAG construction for efficient bidirectional traversal.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolCallItem {
    /// Unique ID within this tool_call_set (e.g., "call_0", "call_1").
    pub id: String,
    /// Tool name to invoke.
    pub tool: String,
    /// Parameters for the tool call.
    pub params: serde_json::Value,
    /// IDs of calls that must complete before this one.
    /// Empty = no dependencies, can execute immediately.
    #[serde(default)]
    pub blocked_by: Vec<String>,
    /// IDs of calls that depend on this one.
    /// Empty = nothing waits for this call.
    #[serde(default)]
    pub blocking: Vec<String>,
}
```

### ToolCallSet — Engine → MCP

```rust
/// A set of 1-N tool calls dispatched together.
///
/// Engine assembles this after receiving a model_response with tool_calls.
/// MCP builds a DAG from the bidirectional lock (blocked_by / blocking),
/// topologically sorts, and executes:
/// - Calls without dependencies: concurrent execution
/// - Calls with blocked_by: serialized by dependency order
/// - Any call fails → cascade cancel along blocking (forward)
/// - Any call cancelled by upstream → cascade cancel along blocking (forward)
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolCallSet {
    /// The session this tool call set belongs to.
    pub session_id: String,
    /// 1-N tool calls in this set.
    pub calls: Vec<ToolCallItem>,
    /// Per-call timeout in milliseconds. None = no timeout (Engine's choice).
    /// MCP does not impose a default or hidden deadline.
    /// When a call exceeds this timeout, the executor cancels it and
    /// cascades to its dependents.
    #[serde(default)]
    pub timeout_ms: Option<u64>,
}
```

### ToolResultItem — 单个工具结果

```rust
/// Result of a single tool call.
///
/// All error packaging is centralized in the executor — tool authors
/// never construct this struct directly. The executor:
/// 1. Calls tool.execute() inside catch_unwind
/// 2. On Ok(val) → status: "success", result: val, name: from ToolCallItem.tool
/// 3. On Err(e) → status: "error", error: e.message
/// 4. On panic → status: "error", error: "panic: {message}"
/// 5. On cancel (cascade or timeout) → calls tool.cancel(), status: "cancelled"
///
/// The `name` field is backfilled from `ToolCallItem.tool` by the executor.
/// It travels with the result so downstream consumers (ModelAdapter) can
/// construct the model-specific tool-result message without a call_id→name lookup.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolResultItem {
    /// Matches ToolCallItem.id.
    pub call_id: String,
    /// Tool name, matches ToolCallItem.tool. Backfilled by executor.
    pub name: String,
    /// "success" or "error" or "cancelled".
    pub status: String,
    /// The tool's return value. Null on error/cancelled.
    pub result: serde_json::Value,
    /// Error message populated by executor on error/cancelled.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}
```

### ToolResultSet — MCP → Engine

```rust
/// Aggregated results for a tool_call_set.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolResultSet {
    /// The session this result set belongs to.
    pub session_id: String,
    /// Results, one per call in the original ToolCallSet.
    pub results: Vec<ToolResultItem>,
}
```

---

### MCP 协议类型 (remote)

> RemoteMcpNode 与外部 MCP server 通讯的 JSON-RPC 2.0 类型。仅定义需要结构化提取的响应类型，请求侧用 `serde_json::json!()` 构造。

```rust
/// A tool entry parsed from an MCP `tools/list` response.
///
/// Represents a single tool available on the remote server.
/// Stored in `RemoteMcpNode.known_tools` for dispatch by name.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RemoteToolDef {
    /// Tool name — matches the key in `known_tools` HashMap.
    pub name: String,
    /// Human-readable description for LLM function calling.
    pub description: String,
    /// JSON Schema for the tool's parameters (MCP spec field name: `inputSchema`).
    #[serde(default)]
    pub input_schema: serde_json::Value,
}

/// Successful result from an MCP `tools/call` response.
///
/// The `content` array carries the tool's output. Phase 5 only handles
/// `type: "text"` content items; `image` and `resource` return an error.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CallToolResult {
    pub content: Vec<ToolContent>,
}

/// A single content item within `CallToolResult.content`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolContent {
    /// MCP content type: `"text"`, `"image"`, or `"resource"`.
    #[serde(rename = "type")]
    pub content_type: String,
    /// Text content. Present when `content_type` is `"text"`.
    /// For `"image"` / `"resource"` — Phase 5 returns an error.
    #[serde(default)]
    pub text: Option<String>,
}

/// JSON-RPC error from an MCP method call.
///
/// Extracted from the response envelope on `tools/call` failure.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct JsonRpcError {
    /// Numeric error code (MCP defines -32700 to -32000 range).
    pub code: i32,
    /// Human-readable error message.
    pub message: String,
}
```

**映射关系**：

```
RemoteToolDef ──→ node_online.capabilities.tools[]
  { name, description, input_schema }  → { name, description, parameters_schema: input_schema }

JsonRpcError ──→ ToolResultItem
  { code, message }  → { status: "error", error: "MCP error [-32000]: {message}" }
```

**ToolContent 处理规则**：

| content_type | 处理 |
|--------------|------|
| `"text"` | 取 `text` 字段 → `ToolResultItem.result` |
| `"image"` | Phase 5 返回 `ToolResultItem { status: "error", error: "unsupported content type: image" }` |
| `"resource"` | Phase 5 返回 `ToolResultItem { status: "error", error: "unsupported content type: resource" }` |

所有 MCP 协议类型定义在 `crates/arf-mcp/src/remote.rs` 中，与 `RemoteMcpNode` 实现同文件。

---

## Skill 数据模型

**Skill 是纯数据——"给 AI 的工作手册"。** 不包含可执行逻辑，不自带 Tool trait。LLM 读取 body 后自己决定调什么工具、用什么资源。MCP 只负责存储、索引和按需出货。

### 目录结构

每个 skill 是标准化的文件夹，`SKILL.md` 为唯一入口：

```
skills/{name}/           # name 为 kebab-case（如 react-component）
├── SKILL.md             # (必选) YAML frontmatter + Markdown body — "何时用"
├── tools/               # (可选) 可执行工具，与顶层 tools/ 结构统一
│   ├── generate-component/
│   │   ├── tool.toml    # "怎么调"（params_schema 为主，description 轻量）
│   │   └── main.py      # 入口脚本
│   └── validate/
│       ├── tool.toml
│       └── main.sh
├── references/          # (可选) 参考文档
└── assets/              # (可选) 静态资源（模板、图片等）
```

**Skill 内 tools 与顶层 tools 结构完全一致**：每个工具独占目录，`tool.toml` + 入口脚本。`name` 和 `entrypoint` 在 toml 中正常声明。`description` 由 `SKILL.md` 承载（说明何时使用），`tool.toml` 聚焦 `params_schema`（说明怎么调用）。`load_skill_resource` 返回脚本内容时附带来自 `tool.toml` 的 `description` 和 `params_schema`。

### SKILL.md 格式

```markdown
---
name: react-component
description: >
  Use when asked to build React components with TypeScript.
  Keywords: react, component, tsx, frontend.
compatibility: node>=18
---

# React Component Skill

## Prerequisites
- Ensure tsconfig.json has strict mode enabled.

## Main Flow
1. Read existing component files to understand project conventions.
2. Run the `generate-component` tool to scaffold the component.
...
```

YAML frontmatter 定义：
- **`name`** (必填)：唯一标识，kebab-case，仅含小写字母、数字和连字符
- **`description`** (必填)：含触发短语、使用时机和关键词，LLM 据此判断是否加载
- **`compatibility`** (可选)：运行环境或依赖声明

### 渐进式披露（L1 → L2 → L3）

| 层级 | 触发时机 | 内容 | 暴露方式 |
|------|---------|------|---------|
| L1 元数据 | Agent 启动 | `{name, description}` | `node_online` 广播 |
| L2 说明文档 | LLM 决定使用此 Skill | body (SKILL.md 全文) + `resources` (文件清单) | `use_skill` → `skill_loaded` |
| L3 资源文件 | LLM 需要具体脚本/模板 | 工具脚本：content + description + params_schema（来自 tool.toml）；普通文件：content | `load_skill_resource` → `skill_resource_loaded` |

### SkillEntry

> 纯数据 struct，无 trait，无 execute。

```rust
/// A skill registered by MCP — L1 metadata only.
///
/// The full body and resources are loaded on demand via
/// use_skill (L2) and load_skill_resource (L3).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SkillEntry {
    /// Unique skill name (kebab-case). Parsed from SKILL.md frontmatter.
    pub name: String,
    /// Human-readable description with trigger phrases and keywords.
    /// LLM uses this to decide whether to load the skill.
    pub description: String,
    /// Optional compatibility constraint (e.g. "node>=18").
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub compatibility: Option<String>,
    /// Path to skill directory (for body/resource loading).
    /// Not serialized — MCP-internal.
    #[serde(skip)]
    pub source_dir: String,
}

/// File manifest for a skill's resource directories.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SkillResources {
    /// Tool names under tools/ (e.g. ["generate-component", "validate"]).
    #[serde(default)]
    pub tools: Vec<String>,
    /// Files under references/ (e.g. ["api-guide.md"]).
    #[serde(default)]
    pub references: Vec<String>,
    /// Files under assets/ (e.g. ["template.tsx"]).
    #[serde(default)]
    pub assets: Vec<String>,
}
```

### SkillIndex

> MCP 内部组件，不对外暴露。Engine 通过 Bus 消息获取 skill 内容，不直接扫描文件系统。

```rust
/// Scan, index, and retrieve lazy-loaded skills.
///
/// Scans `<root>/skills/*/SKILL.md`, parses YAML frontmatter for L1 metadata.
/// Body and resources loaded on demand. MCP-internal — Engine never sees this.
pub struct SkillIndex {
    root: PathBuf,
    entries: HashMap<String, SkillEntry>,
}
```

方法：
- `scan()` — 扫描 `skills/*/SKILL.md`，解析 YAML frontmatter 构建 L1 索引；同时遍历 `tools/`、`references/`、`assets/` 构建 `SkillResources`
- `resolve(name) -> Option<&SkillEntry>` — 按名查找
- `load_body(name) -> Option<String>` — 读取 `SKILL.md` 全文（L2）
- `load_resources(name) -> SkillResources` — 列出三个子目录的内容清单（L2 附带）
- `load_resource_file(name, resource_path) -> Option<LoadedResource>` — 读取文件内容 + 工具元数据（L3）
- `load_tool_config(name, tool_name) -> Option<ToolConfig>` — 读取 `tools/{tool_name}/tool.toml`，返回标准 `ToolConfig`
- `run_tool(name, tool_name, params) -> Result<Value, String>` — 执行 skill 工具（复用 ScriptTool subprocess 机制）
- `list_index() -> Vec<&SkillEntry>` — 列出全部 L1 元数据

`LoadedResource` 结构：
```rust
pub struct LoadedResource {
    pub content: String,
    /// Present only for tools/ files with a tool.toml.
    pub description: Option<String>,
    /// Present only for tools/ files with a tool.toml.
    pub params_schema: Option<serde_json::Value>,
}
```
```

### MCP 自检

`scan()` 时交叉校验：
- `SKILL.md` 不存在 → 跳过该目录（非 skill）
- `name` 不符合 kebab-case → warning 日志，仍注册
- body 中引用的 `tools/`、`references/`、`assets/` 下的文件缺失 → warning 日志，不阻断
- 不强制资源完整性——Skill 可以只有 `SKILL.md`，无子目录

---

## Bus 消息协议

### node_online — MCP 节点注册

MCP 节点上线时广播一个 `node_online`，携带本 namespace 的全部能力——tools（含描述，供 LLM）+ skills（L1 元数据）。Engine 据此构建 system prompt 并路由后续请求。

```json
{
  "msg_type": "node_online",
  "from": "mcp/filesystem",
  "to": [],
  "payload": {
    "node_type": "mcp",
    "node_id": "mcp/filesystem",
    "namespace": "filesystem",
    "capabilities": {
      "runtime": {"runtime": "local", "concurrency": "layer-parallel"},
      "tools": [
        {"name": "read_file", "description": "Read file contents"},
        {"name": "write_file", "description": "Write content to a file"},
        {"name": "search_content", "description": "Search files for a pattern"}
      ],
      "skills": [
        {
          "name": "react-component",
          "description": "Use when asked to build React components with TypeScript. Keywords: react, component, tsx, frontend."
        }
      ]
    },
    "online_since": 1719500000000
  }
}
```

> **Engine 发现逻辑**：
> 1. 启动时 `bus.graph()` 主动获取当前所有在线 MCP 节点（覆盖 MCP 先于 Engine 上线的时序）
> 2. 订阅 `node_online` / `node_offline` ——感知运行期间的动态增删
> 3. 工具描述 + skill L1 注入 system prompt
> 4. 所有后续请求都发给 `mcp/{namespace}`——MCP 内部按 `msg_type` 分派，Engine 不知道细节

### tool_call_set — Engine → MCP

Engine 按 tool 的 namespace 前缀路由到对应 MCP 节点，MCP 内部分发给 runtime 模块执行。

```json
{
  "msg_type": "tool_call_set",
  "from": "engine/session-1",
  "to": ["mcp/filesystem"],
  "payload": {
    "namespace": "filesystem",
    "session_id": "session-1",
    "calls": [
      {
        "id": "call_0",
        "tool": "read_file",
        "params": {"path": "/workspace/src/main.rs"},
        "blocking": ["call_2"]
      },
      {
        "id": "call_1",
        "tool": "search_content",
        "params": {"pattern": "fn main", "path": "/workspace/src"},
        "blocking": ["call_2"]
      },
      {
        "id": "call_2",
        "tool": "write_file",
        "params": {"path": "/workspace/out.txt", "content": "..."},
        "blocked_by": ["call_0", "call_1"]
      }
    ]
  }
}
```

### tool_result_set — MCP → Engine

定向消息（返回给 Engine）：

```json
{
  "msg_type": "tool_result_set",
  "from": "mcp/filesystem",
  "to": ["engine/session-1"],
  "payload": {
    "session_id": "session-1",
    "results": [
      {
        "call_id": "call_0",
        "name": "read_file",
        "status": "success",
        "result": "fn main() {\n    println!(\"Hello\");\n}\n"
      },
      {
        "call_id": "call_1",
        "name": "search_content",
        "status": "success",
        "result": {"matches": [{"line": 1, "content": "fn main() {"}]}
      },
      {
        "call_id": "call_2",
        "name": "write_file",
        "status": "success",
        "result": {"ok": true, "path": "/workspace/out.txt", "bytes": 42}
      }
    ]
  }
}
```

### 级联取消示例

当 `call_1` 失败时，executor 沿 `blocking` 正向遍历：call_1.blocking → call_2 → cancelled。同时沿 `blocked_by` 回查可确认 call_2 的所有上游状态。

```json
{
  "msg_type": "tool_result_set",
  "payload": {
    "session_id": "session-1",
    "results": [
      {"call_id": "call_0", "name": "read_file", "status": "success", "result": "..."},
      {
        "call_id": "call_1",
        "name": "search_content",
        "status": "error",
        "result": null,
        "error": "pattern syntax error: unmatched ("
      },
      {
        "call_id": "call_2",
        "name": "write_file",
        "status": "cancelled",
        "result": null,
        "error": "cancelled: dependency call_1 failed"
      }
    ]
  }
}
```

### 逆向取消示例

当 `call_0` 被 Engine 直接取消（非执行失败，如用户中断），沿 `blocking` 正向级联：

```
Engine 发 cancel 消息 → executor 标记 call_0 = cancelled
  → call_0.blocking = ["call_2"]
    → call_2 沿 blocked_by 回查：call_1 仍在跑，但 call_0 已取消
      → call_2 仍有一组上游未全部成功 → call_2 = cancelled
        → call_2.blocking = ["call_3"] → call_3 = cancelled
```

### use_skill — Engine → MCP (L2)

```json
{
  "msg_type": "use_skill",
  "from": "engine/session-1",
  "to": ["mcp/filesystem"],
  "payload": {
    "namespace": "filesystem",
    "name": "react-component"
  }
}
```

### skill_loaded — MCP → Engine (L2)

返回 `SKILL.md` 全文 + 资源文件清单。Engine 将 body 注入 LLM 上下文，`resources` 供 LLM 按需请求 L3。

```json
{
  "msg_type": "skill_loaded",
  "from": "mcp/filesystem",
  "to": ["engine/session-1"],
  "payload": {
    "namespace": "filesystem",
    "name": "react-component",
    "description": "Use when asked to build React components with TypeScript...",
    "body": "---\nname: react-component\ndescription: >\n  Use when asked to build React components...\n---\n\n# React Component Skill\n\n## Prerequisites\n...\n## Main Flow\n1. Run the `generate-component` tool to scaffold the component.\n",
    "resources": {
      "scripts": ["generate-component.py"],
      "references": ["design-system.md"],
      "assets": ["component-template.tsx"]
    }
  }
}
```

### load_skill_resource — Engine → MCP (L3)

```json
{
  "msg_type": "load_skill_resource",
  "from": "engine/session-1",
  "to": ["mcp/filesystem"],
  "payload": {
    "namespace": "filesystem",
    "skill_name": "react-component",
    "resource_path": "tools/generate-component/main.py"
  }
}
```

### skill_resource_loaded — MCP → Engine (L3)

返回文件内容。对于 `tools/` 下的脚本，额外携带来自 `tool.toml` 的 `description` 和 `params_schema`，使 LLM 能理解工具的用途和调用方式。

```json
{
  "msg_type": "skill_resource_loaded",
  "from": "mcp/filesystem",
  "to": ["engine/session-1"],
  "payload": {
    "namespace": "filesystem",
    "skill_name": "react-component",
    "resource_path": "tools/generate-component/main.py",
    "content": "#!/usr/bin/env python3\n\nimport sys\n...",
    "description": "Generate a React component with TypeScript props",
    "params_schema": {
      "type": "object",
      "properties": {
        "component_name": {"type": "string", "description": "Name of the component"}
      },
      "required": ["component_name"]
    }
  }
}
```

普通文件（`references/`、`assets/`）无 `tool.toml` 时，`description` 和 `params_schema` 为 `null`。

### run_skill_script — Engine → MCP

Engine 请求执行 skill `tools/` 下的一个工具。与 `tool_call_set` 使用相同的数据结构（`ToolCallItem` / `ToolResultItem`），但走独立的 `msg_type` 通道——skill 工具不作为全局 Tool 注册，执行权归属 skill 所属的 MCP。

```json
{
  "msg_type": "run_skill_script",
  "from": "engine/session-1",
  "to": ["mcp/filesystem"],
  "payload": {
    "namespace": "filesystem",
    "session_id": "session-1",
    "skill_name": "react-component",
    "tool_name": "generate-component",
    "call_id": "call_5",
    "params": {"component_name": "Button"}
  }
}
```

### skill_script_result — MCP → Engine

返回工具执行结果，shape 与 `ToolResultItem` 一致。

```json
{
  "msg_type": "skill_script_result",
  "from": "mcp/filesystem",
  "to": ["engine/session-1"],
  "payload": {
    "session_id": "session-1",
    "call_id": "call_5",
    "name": "react-component/generate-component",
    "status": "success",
    "result": {"ok": true, "files_created": ["src/components/Button.tsx"]},
    "error": null
  }
}
```

**设计约束**：`run_skill_script` 不支持 DAG（无 `blocked_by`/`blocking`），一次只执行一个工具。Skill 工具的运行时继承 MCP 的 `RuntimeModule`。

MCP 安全约束：`resource_path` 必须在 `{skill_dir}/tools/`、`references/` 或 `assets/` 下，路径穿越（`../`）和绝对路径拒绝返回 error。

---

## 执行模型

### DAG 构建与执行

```
ToolCallSet.calls
  → 构建双向链：
       blocked_by → edges（正向：谁阻塞我 — 入度）
       blocking   → reverse edges（逆向：我阻塞谁 — 出度）
       Engine 同时提供 blocked_by + blocking，executor 交叉验证：
         A.blocking 包含 B ⇔ B.blocked_by 包含 A
         不一致 → 全部返回 error（数据错误，不静默修复）
    → 检测环（DFS，有环则全部返回 error）
      → 拓扑排序（Kahn 算法，基于 blocked_by 入度）
        → 按层级分批：
            Layer 0: [call_0, call_1]   ← 入度 = 0，concurrent
            Layer 1: [call_2]           ← 依赖 0,1 都完成
            Layer 2: [call_3]           ← 依赖 2 完成
          → 每层内部并发执行（tokio::spawn）
            → 任一步失败 → 沿 blocking 正向级联取消
              → 组装 ToolResultSet 返回
```

### 集中式错误处理

每个 tool call 执行时，executor 统一包裹三层保护。Tool 作者不需要遵守任何错误约定——抛异常、返回 Err、或 unwrap panic 都会被捕获并封装为标准的 tool_result message。

```
spawn per call:
  ┌─ tokio::spawn(async {
  │    // Layer 1: catch_unwind — panic safety
  │    let result = catch_unwind(AssertUnwindSafe(|| async {
  │        // Layer 2: Result<_, ToolError> — type-safe errors
  │        tool.execute(params).await
  │    })).await;
  │
  │    // Layer 3: timeout (if ToolCallSet.timeout_ms is set)
  │    // tokio::time::timeout wraps the entire execution
  │
  │    match result {
  │        Ok(Ok(val))            → ToolResultItem { status: "success", result: val }
  │        Ok(Err(tool_err))      → ToolResultItem { status: "error", error: tool_err.message }
  │        Err(panic_payload)     → ToolResultItem { status: "error", error: "panic: {msg}" }
  │    }
  │  })
```

### 级联取消策略

双向锁支持正向和逆向遍历，与 Phase 2 Task 状态的双向锁模式一致：

```
              blocked_by (I depend on them)
       call_0 ──────────────────→ call_2 ──→ call_3
       call_1 ──────────────────→ call_2
              blocking (they depend on me)
       call_0 ←────────────────── call_2 ←── call_3
       call_1 ←────────────────── call_2
```

**正向取消（沿 blocking）**：call X 执行失败或超时 → 沿 `blocking` 链标记所有直接/间接下游为 `cancelled`
**逆向查找（沿 blocked_by）**：需要知道"call Y 被谁阻塞"时，沿 `blocked_by` 回查上游

- 当 call X 返回 `status: "error"` 或超时 → 沿 `blocking` 正向遍历，标记所有下游为 `cancelled`
- 对每个 cancelled call，调用 `tool.cancel()` 通知工具释放资源，跳过执行
- 如果 call X 的所有上游（`blocked_by`）中任一失败或取消，call X 也被级联取消
- 最终 ToolResultSet 包含所有 call 的结果（含 cancelled）

### 超时

- `ToolCallSet.timeout_ms` 由 Engine 设置，MCP 不做默认值、不设兜底
- 超时只由 Engine 的业务需求驱动——Engine 不传就是无超时
- 超时触发时：executor 调用 `tool.cancel()` → 级联取消 dependents → 标记 `status: "cancelled"`

### 取消机制

`Tool::cancel()` 是优雅退出的钩子：
- 默认实现为空（no-op），简单工具不需要关心
- 持有文件句柄、网络连接等资源的工具应覆盖 `cancel()` 设置标志位
- `cancel()` 被调用后，正在运行的 `execute()` 应尽快返回
- 取消不等待——executor 调用 `cancel()` 后 abort tokio task

### 并发模型

- 每个 tool call 在独立的 `tokio::spawn` 任务中执行
- 层内并发数无硬性限制（由 tokio runtime 调度）
- 所有 tool 实现必须是 `Send + Sync`
- 执行期间 executor 持有每个 task 的 `JoinHandle`，用于取消

---

## MCP ↔ ModelAdapter 集成

> MCP 产出纯数据（`ToolResultItem`），ModelAdapter 负责转换为模型 API 格式（`ModelMessage`）。MCP 不感知 ModelMessage 的存在。

### 数据流

```
Engine 收到 model_response (含 tool_calls)
  → Engine 构建 ToolCallSet { calls: [ToolCallItem { id: "call_0", tool: "read_file", params: {...} }] }
  → Engine 发送 tool_call_set 给 mcp/{namespace}
  → MCP 执行，executor 将 ToolCallItem.tool 回填到 ToolResultItem.name
  → MCP 返回 ToolResultSet { results: [ToolResultItem { call_id: "call_0", name: "read_file", ... }] }
  → Engine 调用 tool_result_to_model_message(&item) → ModelMessage
  → Engine 将 ModelMessage 追加到 conversation history
  → 下一轮 model_call
```

**关键**：`ToolResultItem.name` 由 MCP executor 回填，ModelAdapter 直接使用。Engine 不做 call_id→name 查表——这个设计消除了 Engine 的中间状态。

### 转换规则

转换函数位于 `arf-model-adapter/src/convert.rs`，由 ModelAdapter 持有：

```rust
/// Convert a ToolResultItem (from MCP) into a ModelMessage for the LLM conversation.
///
/// Clean boundary: MCP produces data, ModelAdapter converts to model API format.
/// MCP never knows about ModelMessage.
pub fn tool_result_to_model_message(item: &ToolResultItem) -> ModelMessage {
    let content = match item.status.as_str() {
        "success" => item.result.to_string(),
        "error" => serde_json::json!({
            "error": item.error.as_deref().unwrap_or("unknown error")
        }).to_string(),
        "cancelled" => serde_json::json!({
            "error": item.error.as_deref().unwrap_or("cancelled")
        }).to_string(),
        other => serde_json::json!({"error": format!("unknown status: {other}")}).to_string(),
    };
    ModelMessage::new("tool", content)
        .with_tool_call_id(&item.call_id)
        .with_name(&item.name)
}
```

| status | content | 示例 |
|--------|---------|------|
| `"success"` | `result.to_string()` | `"{\"ok\": true, \"bytes\": 42}"` |
| `"error"` | `{"error": message}` | `"{\"error\": \"file not found\"}"` |
| `"cancelled"` | `{"error": "cancelled: ..."}` | `"{\"error\": \"cancelled: dependency call_1 failed\"}"` |
| other | `{"error": "unknown status: X"}` | (防御性兜底) |

所有变体统一产出 `role = "tool"`，`tool_call_id = call_id`，`name = item.name`。

### 职责边界

```
┌─────────────────────────────────────────────────────────┐
│                       MCP                               │
│  ToolResultItem { call_id, name, status, result, error }│
│  纯数据 struct，无任何 ModelMessage 知识                   │
└──────────────────────┬──────────────────────────────────┘
                       │ item: &ToolResultItem
                       ▼
┌─────────────────────────────────────────────────────────┐
│                  ModelAdapter                           │
│  tool_result_to_model_message(item) → ModelMessage      │
│  唯一转换入口，了解模型 API 格式                            │
└──────────────────────┬──────────────────────────────────┘
                       │ ModelMessage { role, content, ... }
                       ▼
                   [LLM API]
```

### 依赖

```
arf-model-adapter ──→ arf-mcp (需要 ToolResultItem 类型)
                    ──→ arf-core (需要 ModelMessage 类型)
```

MCP **不**依赖 ModelAdapter。依赖方向：`adapter → mcp`，单向。

---

## 测试基准工具

以下三个工具作为 ScriptTool 测试 fixtures，验证 MCP 资源注册、发现和运行机制。它们以脚本形式放在测试数据目录下，通过 `tool.toml` 声明，由 `ScriptTool` 包裹执行 — 与开发者自定义工具的注册路径完全一致。

### read_file

| 字段 | 值 |
|------|-----|
| name | `read_file` |
| runtime | `python` |
| params | `{"path": "/workspace/src/main.rs"}` |
| result | 文件内容字符串 |
| error | `{"ok": false, "error": "file not found: ..."}` |

### write_file

| 字段 | 值 |
|------|-----|
| name | `write_file` |
| runtime | `python` |
| params | `{"path": "/workspace/out.txt", "content": "..."}` |
| result | `{"ok": true, "path": "...", "bytes": 42}` |
| error | `{"ok": false, "error": "permission denied: ..."}` |

### search_content

| 字段 | 值 |
|------|-----|
| name | `search_content` |
| runtime | `python` |
| params | `{"pattern": "fn main", "path": "/workspace/src"}` |
| result | `{"matches": [{"line": 1, "content": "...", "file": "..."}]}` |
| error | `{"ok": false, "error": "invalid regex: ..."}` |

> 框架不内置任何 Tool。以上三个是集成测试的 ScriptTool fixtures，验证 MCP 的完整链路（扫描 → 发现 → 执行 → 结果）。未来可选包可以按同样约定提供 Tool 包——到那时只需 focus 在工具逻辑，注册机制已通用。

---

## 目录结构

```
crates/arf-mcp/
├── Cargo.toml
└── src/
    ├── lib.rs              # pub mod, re-exports
    ├── tool.rs             # Tool trait
    ├── types.rs            # ToolError, ToolCallItem, ToolCallSet, ToolResultItem, ToolResultSet
    ├── config.rs           # ScriptRuntime, ToolConfig, RemoteConfig
    ├── script.rs           # ScriptTool: implements Tool trait via subprocess + stdin/stdout JSON
    ├── remote.rs           # RemoteToolDef, CallToolResult, ToolContent, JsonRpcError + RemoteMcpNode
    ├── skill.rs            # SkillEntry, SkillResources, SkillIndex
    ├── executor.rs         # DAG builder, cycle detection, topological sort, parallel exec
    ├── node.rs             # LocalMcpNode: Bus lifecycle + msg_type dispatch + discovery/runtime
    ├── discovery.rs        # DiscoveryModule: scans {root}/tools + {root}/skills, L2/L3
    ├── runtime.rs          # RuntimeModule trait + LocalRuntime: execution backend (构造时绑定)
    └── tests/
        ├── tool_tests.rs       # Tool trait tests
        ├── config_tests.rs     # tool.toml + RemoteConfig parsing
        ├── script_tests.rs     # ScriptTool execute + cancel + timeout
        ├── remote_tests.rs     # RemoteMcpNode: mock HTTP server + tools/list + tools/call
        ├── skill_tests.rs      # SkillIndex scan/resolve/load
        ├── executor_tests.rs   # DAG build, cycle detect, topo sort, cascade cancel
        ├── node_tests.rs       # LocalMcpNode: msg_type dispatch + internal routing
        ├── discovery_tests.rs  # DiscoveryModule: scan + L2/L3
        ├── runtime_tests.rs    # RuntimeModule: tool execution + sandbox boundary
        └── integration_tests.rs # E2E: LocalMcpNode + RemoteMcpNode + multi-namespace + Bus
```

### Python bindings

```
py-arf/src/mcp/           # Python package (在 py-arf crate 内)
├── __init__.py            # from py_arf.mcp import LocalMcpNode, RemoteMcpNode
├── local.py               # LocalMcpNode Python wrapper
├── remote.py              # RemoteMcpNode Python wrapper
└── types.py               # RemoteConfig python dataclass
```

---

## 任务拆解

| # | 任务 | 内容 | 产出 |
|---|------|------|------|
| 5.1 | 脚手架 + 类型定义 | `Cargo.toml`、`tool.rs`（Tool trait）、`types.rs`（ToolError, ToolCallItem, ToolCallSet, ToolResultItem, ToolResultSet）、`config.rs`（ScriptRuntime, ToolConfig, RemoteConfig）、`lib.rs` | `crates/arf-mcp/` |
| 5.1a | ModelAdapter 集成 | `arf-model-adapter` 依赖 `arf-mcp`，`convert.rs` 新增 `tool_result_to_model_message()` + 9 个测试 | `crates/arf-model-adapter/` |
| 5.2 | ScriptTool + tool.toml 解析 | `tool.toml` 反序列化 → `ToolConfig`、`ScriptTool` 实现 `Tool` trait（spawn subprocess + stdin/stdout JSON + stderr 捕获 + 超时/取消 kill） | `config.rs`, `script.rs` |
| 5.3 | SkillIndex | 扫描 `skills/*/SKILL.md` YAML frontmatter → L1 索引、`load_body` (L2)、`load_resource_file` (L3 含工具 description+params_schema，来自 `tools/{name}/tool.toml`)、`load_tool_config`、`run_tool`（复用 ScriptTool subprocess）。Skill 工具不注册为全局 Tool，通过独立的 `run_skill_script` / `skill_script_result` 消息执行。`skills/{name}/tools/` 与顶层 `tools/` 结构统一（每工具独立目录 + tool.toml） | `skill.rs` |
| 5.4 | DAG 执行器 | 邻接表、环检测、拓扑排序、分层并发、`catch_unwind` + `Result` 集中错误处理、超时、`cancel()` 级联取消 | `executor.rs` |
| 5.5 | LocalMcpNode | `LocalMcpNode::new(namespace, root_dir)` → Bus 连接 + 内部 msg_type 分发（`tool_call_set` → runtime, `use_skill`/`load_skill_resource` → discovery） | `node.rs` |
| 5.6 | DiscoveryModule | 扫描 `{root}/tools/*/tool.toml` → ScriptTool + `{root}/skills/*/SKILL.md` → SkillEntry、`node_online` 广播、L2/L3 查询处理 | `discovery.rs` |
| 5.7 | RuntimeModule | `RuntimeModule` trait（`capabilities()` 自描述 + `execute()` / `run_single()`）+ `LocalRuntime` 默认实现（宿主机直接 spawn ScriptTool subprocess）。trait 对象在 `LocalMcpNode` 构造时绑定，执行方式在定义阶段固定。Python 用户可实现 `RuntimeModule` 子类注入自定义执行后端（如 `SandboxRuntime` → Bus → sandbox node） | `runtime.rs` |
| 5.8 | RemoteMcpNode | MCP 协议类型（`RemoteToolDef`, `CallToolResult`, `ToolContent`, `JsonRpcError`）+ `RetryConfig` + `RemoteMcpNode` JSON-RPC 握手/发现/代理执行/重连 + headers 注入 + 自定义 TLS CA + `node_online` 广播 | `remote.rs` |
| 5.9 | Python API | PyO3 绑定：`LocalMcpNode` 和 `RemoteMcpNode` + `RemoteConfig` + `RetryConfig` 暴露给 Python | `py-arf/src/mcp/` |
| 5.10 | 测试 fixtures | 三个 ScriptTool fixtures（read_file/write_file/search_content）以 py 脚本 + tool.toml 形式放在测试数据目录 | `tests/fixtures/` |
| 5.11 | Workspace 注册 | 根 `Cargo.toml` 添加 `arf-mcp` | `Cargo.toml` |
| 5.12 | 集成测试 | LocalMcpNode + RemoteMcpNode + 多 namespace 隔离 + Bus + mock Engine E2E (用 5.10 fixtures 验证本地链路; mock HTTP server 验证远程链路) | `tests/` |
| 5.13 | 全链路集成测试 | MCP + ModelAdapter + 极简 Engine + Bus 全链路 ReAct 场景测试。Engine 监听 `node_online` 发现 MCP → 构建 system prompt（含 tool 描述）→ 用户发消息 → LLM 决定 tool call → Engine 发 `tool_call_set` → MCP 执行 → `tool_result_set` 返回 → LLM 继续 → 完整 ReAct 闭环。覆盖：本地 Tool 调用、远程 Tool 调用、Skill 加载后使用、多 namespace 路由。使用真实 LLM API KEY（用户提供） | `tests/full_chain/` |

## 交付标准

- [ ] `cargo test --workspace` 全部通过
- [ ] `arf-mcp` 仅依赖 `arf-core` + `arf-bus` + `tokio` + `serde_json` + `toml` + `reqwest`
- [ ] 框架不内置任何 Tool — 所有本地 Tool 通过 `{root}/tools/*/tool.toml` 扫描发现
- [ ] 三个测试 fixture（read_file/write_file/search_content）作为 ScriptTool 实例完成实际文件读写和内容搜索
- [ ] `LocalMcpNode::new(ns, root_dir) -> Result<Self, McpError>` — 构造即扫描，失败返回 Discovery error
- [ ] `LocalMcpNode::connect(bus) -> Result<(), McpError>` — 连接 Bus + 广播 node_online
- [ ] `RemoteMcpNode::new(ns, config) -> Self` — 纯构造，不联网
- [ ] `RemoteMcpNode::connect(bus) -> Result<(), McpError>` — HTTP initialize + tools/list → bus.connect() → 广播 node_online
- [ ] `RemoteMcpNode` 远端不可达/握手被拒/超时时 `connect()` 返回 `McpError::RemoteUnreachable` 或 `RemoteRejected`
- [ ] `RemoteMcpNode` 运行时错误统一为 `ToolResultItem { status: "error", error: String }`
- [ ] `RemoteMcpNode` 不支持 Skill（`use_skill`/`load_skill_resource` 返回 skill_error）
- [ ] `RemoteConfig.headers` 注入到每个 HTTP 请求（开发者提供值，框架负责注入）
- [ ] `RemoteConfig.tls_ca_cert` 为自签证书场景提供 CA 证书路径
- [ ] `RemoteConfig.retry` 启用时，网络故障自动指数退避重连并刷新工具列表，成功或 max_retries 耗尽后结束
- [ ] 重连成功后自动重新握手 + 重新广播 `node_online`（工具列表可能变更）
- [ ] `RemoteConfig.retry = None` 时不重试，网络故障直接返回 error
- [ ] `McpError` 统一 LocalMcpNode 和 RemoteMcpNode 的错误类型
- [ ] `LocalMcpNode` 正确广播一条 `node_online`（含全部 tools 描述 + skills L1 + namespace）
- [ ] `LocalMcpNode` 内部按 `msg_type` 正确分发：`tool_call_set` → runtime，`use_skill`/`load_skill_resource` → discovery
- [ ] `LocalMcpNode` 正确响应 `tool_call_set` → executor 调度 → `tool_result_set`
- [ ] `LocalMcpNode` 正确响应 `use_skill` → `skill_loaded` (L2: body + resources)
- [ ] `LocalMcpNode` 正确响应 `load_skill_resource` → `skill_resource_loaded` (L3: 脚本含 description + params_schema，普通文件含 content)
- [ ] `LocalMcpNode` 正确响应 `run_skill_script` → 调用 `SkillIndex::run_tool()` → 返回 `skill_script_result`
- [ ] `run_skill_script` / `skill_script_result` 使用与 `ToolCallItem` / `ToolResultItem` 一致的数据结构（call_id, name, status, result, error）
- [ ] `skills/{name}/tools/` 与顶层 `tools/` 结构统一——每工具独占目录，`tool.toml` + 入口脚本
- [ ] Skill 工具元数据从 `tools/{tool_name}/tool.toml` 读取，使用标准 `ToolConfig`（不再需要 `ScriptMeta`）
- [ ] Skill 工具无 `tool.toml` 时，`load_skill_resource` 返回 description=null, params_schema=null
- [ ] `SkillIndex::run_tool()` 复用 `ScriptTool` 的 subprocess 执行机制，继承 MCP 的 `RuntimeModule`
- [ ] `run_skill_script` 不支持 DAG（单工具执行），需要编排时 Engine 逐个发出调用
- [ ] Python API：`from py_arf.mcp import LocalMcpNode, RemoteMcpNode` 可正常 import 并使用
- [ ] 内部去重：同一 namespace 内 tool/skill name 冲突 → panic
- [ ] 跨 namespace 重名工具不冲突（`filesystem/read_file` ≠ `network/read_file`），Engine 只需按 NodeId 路由
- [ ] `RuntimeModule` trait：`capabilities()` 自描述执行环境 + `execute(call_set, tools) → ToolResultSet` + `run_single(call_id, tool, params) → (status, result, error)`
- [ ] `LocalRuntime`（框架默认）：`capabilities()` 返回 `{"runtime": "local", "concurrency": "layer-parallel"}`，`run_single()` 直接调用 `tool.execute(params)`
- [ ] `LocalMcpNode::new()` 默认使用 `LocalRuntime`，零配置即宿主机执行
- [ ] `LocalMcpNode::with_runtime(ns, root, runtime)` 接受 `Box<dyn RuntimeModule>`，构造时绑定执行后端
- [ ] RuntimeModule 对 Python 用户暴露——可通过 PyO3 子类化 `RuntimeModule` 实现自定义执行方式（如 DockerSandbox）
- [ ] `node_online.payload.capabilities` 包含 `runtime` 字段，Engine 可看到执行环境特征
- [ ] L3 资源读取有路径安全校验（拒绝 `../` 和绝对路径）
- [ ] `tool.toml` 正确解析为 `ToolConfig`（含 `name`, `description`, `runtime`, `entrypoint`, `params_schema`, `timeout_ms`）
- [ ] `ScriptTool` 实现 `Tool` trait：spawn subprocess → stdin 写入 JSON params → stdout 读取 JSON result → stderr 捕获为 error
- [ ] `ScriptTool` 支持 Python (`python3`) 和 Bash (`bash`) 两种 runtime
- [ ] Rust 脚本：首次或源文件变更时自动编译为 binary，后续使用编译产物
- [ ] `ScriptTool` 超时/取消时 kill 子进程，保证资源不泄漏
- [ ] DAG 执行器：无依赖并发、有依赖拓扑排序、失败级联取消、`catch_unwind` panic 安全
- [ ] 超时机制：Engine 传入 `timeout_ms` → executor 按 `tokio::time::timeout` 终止
- [ ] `Tool::cancel()` 被调用后才 abort task，保证优雅退出
- [ ] `Tool` trait 可 mock（用于 Engine 单测）
- [ ] `ToolResultItem.name` 由 executor 从 `ToolCallItem.tool` 回填，随结果携带
- [ ] `ToolResultItem` 是纯数据 struct——无 `to_model_message()` 方法，不感知 `ModelMessage`
- [ ] `arf-model-adapter` 依赖 `arf-mcp`，`convert.rs` 提供 `tool_result_to_model_message()` 转换函数
- [ ] `tool_result_to_model_message()` 正确处理 success/error/cancelled 三种 status，统一产出 role="tool" 的 ModelMessage
- [ ] MCP 不依赖 ModelAdapter——依赖方向 `adapter → mcp`，单向
- [ ] `SkillIndex` 正确扫描 `SKILL.md` YAML frontmatter → L1 索引，不对外暴露
- [ ] MCP 自检：kebab-case 校验、资源文件存在性检验（warning 不阻断）
- [ ] LocalMcpNode + RemoteMcpNode 多实例并存（不同 namespace），各自独立广播 `node_online`
- [ ] `DiscoveryModule::scan()` 合并两个来源：`tools/*/tool.toml` → ScriptTool + `skills/*/SKILL.md` → SkillEntry，统一通过 `node_online` 广播
- [ ] 文件夹约定是本地 MCP 唯一资源定义机制——文件系统即注册中心、即持久化
- [ ] 全链路集成：Engine 发现 MCP 节点 → 构建 system prompt（含 tool 描述 + skill L1）→ LLM 决定 tool call → `tool_call_set` 正确路由到目标 namespace
- [ ] 全链路集成：MCP 执行结果通过 `tool_result_set` 返回 → Engine 注入 LLM 上下文 → LLM 继续推理 → ReAct 闭环
- [ ] 全链路集成：Skill 加载链路 — `use_skill` → `skill_loaded` → LLM 根据 body 决定工具调用 → 执行 → 结果
- [ ] 全链路集成：多 namespace 场景 — LocalMcpNode + RemoteMcpNode 共存，Engine 按 namespace 正确路由

---

## Python API

### 设计原则

验证对称性：Rust API 和 Python API 暴露同等的概念模型。Python 开发者应能使用与 Rust 开发者相同的构造模式，不做类型桥接的妥协。

### API

```python
from py_arf.mcp import LocalMcpNode, RemoteMcpNode, RemoteConfig, RuntimeModule

# 本地 MCP — 默认 LocalRuntime，零配置宿主机执行
mcp_local = LocalMcpNode(
    namespace="filesystem",
    root_dir="/path/to/tools",
)
await mcp_local.connect(bus)  # → node_online 广播

# 本地 MCP — 自定义 RuntimeModule，沙箱执行
class DockerSandbox(RuntimeModule):
    def __init__(self, image: str, bus):
        self.image = image
        self._bus = bus

    def capabilities(self) -> dict:
        return {"runtime": "sandbox", "engine": "docker", "image": self.image}

    async def run_single(self, call_id: str, tool, params: dict):
        # 转发 tool.execute 请求到 sandbox/docker Bus 节点
        msg = Message(type="sandbox_exec", payload={
            "call_id": call_id,
            "command": tool.build_command_spec(),
            "params": params,
        })
        result = await self._bus.send_and_wait("sandbox/docker", msg)
        return (result["status"], result["result"], result.get("error"))

sandbox_runtime = DockerSandbox(image="python:3.11-slim", bus=bus)
mcp_sandboxed = LocalMcpNode.with_runtime(
    namespace="filesystem",
    root_dir="/path/to/tools",
    runtime=sandbox_runtime,
)
await mcp_sandboxed.connect(bus)  # node_online 包含 runtime capabilities

# 远程 MCP — URL 驱动，构造不联网
mcp_remote = RemoteMcpNode(
    namespace="codetidy",
    config=RemoteConfig(
        transport="streamable-http",
        url="https://mcp.codetidy.dev",
        timeout_secs=None,
        headers={"Authorization": "Bearer sk-xxx"},
        retry=RetryConfig(max_retries=3, initial_backoff_ms=1000, max_backoff_ms=30000),
    ),
)
await mcp_remote.connect(bus)  # → HTTP init → tools/list → node_online 广播
```

### 转换规则

| Rust | Python |
|------|--------|
| `LocalMcpNode::new(namespace, root_dir)` | `LocalMcpNode(namespace=str, root_dir=str)` |
| `LocalMcpNode::with_runtime(ns, root, runtime)` | `LocalMcpNode.with_runtime(namespace=str, root_dir=str, runtime=RuntimeModule)` |
| `LocalMcpNode::connect(bus)` | `await mcp.connect(bus)` |
| `RuntimeModule` trait (async_trait) | `RuntimeModule` base class (PyO3 子类化) |
| `LocalRuntime` (default impl) | 无需显式构造，`LocalMcpNode()` 默认使用 |
| `RemoteMcpNode::new(namespace, config)` | `RemoteMcpNode(namespace=str, config=RemoteConfig)` |
| `RemoteMcpNode::connect(bus)` | `await mcp.connect(bus)` |
| `RemoteConfig { transport, url, timeout_secs: Option<u64>, headers, tls_ca_cert, retry }` | `RemoteConfig(transport=str, url=str, timeout_secs=int\|None, headers=dict, tls_ca_cert=str\|None, retry=RetryConfig\|None)` |
| `RetryConfig { max_retries, initial_backoff_ms, max_backoff_ms }` | `RetryConfig(max_retries=int, initial_backoff_ms=int, max_backoff_ms=int)` |

Python 类型通过 PyO3 从 Rust struct 自动导出，不做手动类型桥接。

---

## 与其他 Phase 的关系

| Phase | 如何使用 MCP |
|-------|-------------|
| Phase 6 Engine | 监听 `node_online` → AgentConfig 匹配 → 所有 MCP 请求发 `mcp/{namespace}` → MCP 内部按 msg_type 分发 |
| Phase 7 集成 | E2E：Engine + ModelAdapter + MCP 完整 ReAct 循环 |
| 未来 RemoteMCP | 同消息格式，只是工具执行转发到外部进程 |
