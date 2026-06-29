# 任务 5.8：RemoteMcpNode

> Phase 5 — MCP 第八项任务
> 父文档：`docs/v1.x/phase5_mcp/phase5-mcp-design.md`
> 依赖：Task 5.1 (类型定义), Phase 1 (Bus)

## 设计思路

`RemoteMcpNode` 代理外部 MCP server——通过 JSON-RPC 2.0 over HTTP 协议握手、发现工具、代理执行。生命周期与 `LocalMcpNode` 对齐：`new()` 不联网，`connect()` 握手 + 上线。

```
RemoteMcpNode::new(ns, config)  → 创建 struct，不联网
RemoteMcpNode::connect(bus)     → HTTP initialize → tools/list → bus.connect() → node_online
```

| 文件 | 操作 | 内容 |
|------|------|------|
| `Cargo.toml` | 更新 | 添加 `reqwest` |
| `config.rs` | 更新 | `RemoteConfig` 扩展字段 + `RetryConfig` |
| `remote.rs` | 新建 | 协议类型 + `RemoteMcpNode` |

---

## 代码实现

### `Cargo.toml`

```toml
reqwest = { version = "0.12", default-features = false, features = ["json", "rustls-tls"] }
```

### `config.rs` — RemoteConfig 扩展 + RetryConfig

```rust
use std::collections::HashMap;
use std::path::PathBuf;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RemoteConfig {
    pub transport: String,
    pub url: String,
    #[serde(default)]
    pub timeout_secs: Option<u64>,
    #[serde(default)]
    pub headers: HashMap<String, String>,
    #[serde(default)]
    pub tls_ca_cert: Option<PathBuf>,
    #[serde(default)]
    pub retry: Option<RetryConfig>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RetryConfig {
    #[serde(default = "default_max_retries")]
    pub max_retries: u32,
    #[serde(default = "default_initial_backoff_ms")]
    pub initial_backoff_ms: u64,
    #[serde(default = "default_max_backoff_ms")]
    pub max_backoff_ms: u64,
}

fn default_max_retries() -> u32 { 3 }
fn default_initial_backoff_ms() -> u64 { 1000 }
fn default_max_backoff_ms() -> u64 { 30000 }
```

### `remote.rs` — RemoteMcpNode

```rust
pub struct RemoteMcpNode {
    pub namespace: String,
    pub node_id: NodeId,
    config: RemoteConfig,
    http_client: reqwest::Client,
    known_tools: HashMap<String, RemoteToolDef>,
    handle: Mutex<Option<arf_bus::NodeHandle>>,
}

impl RemoteMcpNode {
    pub fn new(namespace: impl Into<String>, config: RemoteConfig) -> Self { ... }
    pub async fn connect(self: &Arc<Self>, bus: &Bus) -> Result<(), McpError> { ... }
}
```

`connect()` 流程：
1. HTTP POST `initialize` → 获取 server 信息
2. HTTP POST `tools/list` → 构建 `known_tools`
3. `bus.connect()` → 广播 `node_online`
4. `tokio::spawn(message_loop)` → 处理 `tool_call_set`

`message_loop` 处理 `tool_call_set`：对每个 call → HTTP POST `tools/call` → 组装 `ToolResultItem`。

错误处理：
- 网络不可达 → `McpError::RemoteUnreachable`
- 握手被拒 → `McpError::RemoteRejected`
- 运行时错误 → `ToolResultItem { status: "error" }`
- skill 消息（`use_skill`/`load_skill_resource`/`run_skill_script`）→ `skill_error`

重试（`config.retry` 启用时）：
- 可重试：网络错误、5xx、429
- 不可重试：4xx 非 429
- 指数退避：`initial_backoff_ms * 2^attempt`，上限 `max_backoff_ms`
- 重试前：重新 initialize + tools/list（刷新工具列表）+ 重新广播 node_online

---

## 测试

| 测试 | 覆盖 |
|------|------|
| 类型序列化往返 | RemoteToolDef, CallToolResult, ToolContent, JsonRpcError serde |
| RetryConfig 默认值 | max_retries=3, backoff defaults |
| RemoteConfig 反序列化 | 含 headers/timeout/retry |
| new() 不联网 | 无需 HTTP server 即可创建 |
| 协议常量 | JSON-RPC version = "2.0" |

使用 mock HTTP server 测试 connect 和 tools/call 流程（需要引入 mock server 依赖，或使用 `wiremock`）。

---

## 测试覆盖摘要

| 文件 | 新增测试 | 累计 arf-mcp |
|------|---------|-------------|
| `config_tests.rs` | +5 (RetryConfig + RemoteConfig 扩展) | |
| `remote_tests.rs` | +8 (类型序列化 + new + 协议) | |
| **合计** | **13** | 175 + 13 = **188 tests** |
