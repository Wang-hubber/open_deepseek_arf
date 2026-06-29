# 任务 5.1：脚手架 + 类型定义

> Phase 5 — MCP 第一项任务
> 父文档：`docs/v1.x/phase5_mcp/phase5-mcp-design.md`

## 设计思路

`arf-mcp` 是 MCP 节点 crate，依赖 `arf-core` + `arf-bus` + `tokio` + `serde_json` + `toml` + `reqwest`。本任务搭建 crate 骨架，定义所有共享类型和 `Tool` trait。

| 文件 | 内容 | 用途 |
|------|------|------|
| `Cargo.toml` | 依赖声明 | workspace member |
| `lib.rs` | `pub mod` 三层暴露 | re-export |
| `tool.rs` | `Tool` trait | 工具接口抽象 |
| `types.rs` | `ToolError`, `ToolCallItem`, `ToolCallSet`, `ToolResultItem`, `ToolResultSet` | Engine ↔ MCP 消息体 |
| `config.rs` | `ScriptRuntime`, `ToolConfig`, `RemoteConfig` | tool.toml 解析 + 远程配置 |

`Tool` trait 用 `async_trait` 宏实现动态分发——executor 需要 `Arc<dyn Tool>` 存储异构工具集。

---

## 代码实现

### `crates/arf-mcp/Cargo.toml`

```toml
[package]
name = "arf-mcp"
version.workspace = true
edition.workspace = true
license.workspace = true
repository.workspace = true
description = "ARF MCP — Model Context Protocol node on the Bus"

[dependencies]
arf-core = { path = "../arf-core" }
serde = { version = "1", features = ["derive"] }
serde_json = "1"
async-trait = "0.1"
```

逐行解释：
- `arf-core` — `NodeId`、`Message`、`NodeInfo`、`ModelMessage` 等共享类型
- `serde` feature `derive` — `#[derive(Serialize, Deserialize)]`
- `serde_json` — `Value` 类型，`params` 和 `result` 字段用
- `async-trait` — 让 `Tool` trait 的 `async fn execute()` 可用于 `Arc<dyn Tool>` 动态分发

> `arf-bus`、`tokio`、`toml`、`reqwest` 在后续任务中添加。5.1 只定义类型，不引入运行时依赖。

---

### `crates/arf-mcp/src/lib.rs`

```rust
//! ARF MCP — Model Context Protocol node.
//!
//! Each MCP instance is one namespace = one node on the Bus.
//! LocalMcpNode discovers tools/skills from the filesystem;
//! RemoteMcpNode proxies to an external MCP server via HTTP.

pub mod config;
pub mod tool;
pub mod types;
```

逐行：
- `pub mod config` — `ScriptRuntime`、`ToolConfig`、`RemoteConfig`
- `pub mod tool` — `Tool` trait（executor、ScriptTool、Engine mock 都依赖它）
- `pub mod types` — `ToolError` 和 Engine ↔ MCP 消息体

---

### `crates/arf-mcp/src/tool.rs`

```rust
use serde_json::Value;

use crate::types::ToolError;

/// A tool that MCP can execute.
///
/// Implementations are `Send + Sync` so they can be shared across tokio tasks.
/// Tool authors only need to implement `execute()` — error handling, panic
/// catching, and result packaging are centralized in the executor.
/// Sandboxing and approval are handled by separate Nodes on the Bus — not here.
#[async_trait::async_trait]
pub trait Tool: Send + Sync {
    /// Unique tool name: "read_file", "write_file", "search_content".
    fn name(&self) -> &str;

    /// Human-readable description for LLM function calling.
    fn description(&self) -> &str;

    /// JSON Schema for the tool's parameters.
    fn parameters_schema(&self) -> Value;

    /// Execute the tool with the given parameters.
    ///
    /// Returns `Ok(result)` on success, `Err(ToolError)` on failure.
    /// The executor also wraps this in `catch_unwind` for panic safety —
    /// tool authors can use plain `unwrap()` / `expect()` without breaking MCP.
    async fn execute(&self, params: Value) -> Result<Value, ToolError>;

    /// Cancel an in-progress execution.
    ///
    /// Called by the executor when:
    /// - A dependency fails (cascade cancel)
    /// - Engine sends a cancellation for this `tool_call_set`
    ///
    /// Default implementation is a no-op. Tools with long-running or
    /// resource-holding operations should override this to release
    /// resources and set a cancellation flag.
    async fn cancel(&self) {
        // no-op by default
    }
}
```

逐行解释：
- `#[async_trait::async_trait]` — 展开 `async fn execute()` 为返回 `Pin<Box<dyn Future>>` 的普通方法，使 trait 可用于 `Arc<dyn Tool>` 动态分发。底层原理：Rust 2024 edition 的 `async fn` in trait 支持静态分发，但 `dyn Trait` 仍需 `async_trait` 展开
- `Send + Sync` — `Send` 允许跨线程传递（tokio work-stealing），`Sync` 允许 `Arc<T>` 共享引用
- `name()` / `description()` / `parameters_schema()` — 元数据方法，用于构建 LLM system prompt
- `execute()` — 核心方法，接收 JSON params，返回 JSON result 或 `ToolError`
- `cancel()` — 默认空实现，不强制所有工具覆盖

---

### `crates/arf-mcp/src/types.rs`

```rust
use serde::{Deserialize, Serialize};

// ── ToolError ─────────────────────────────────────────────────────────

/// Error returned by a tool's `execute()`.
///
/// The executor catches this and packages it into the `ToolResultItem`.
/// Tool authors don't need to follow any error convention — just return `Err`.
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
        Self {
            message: s.to_string(),
        }
    }
}

impl From<String> for ToolError {
    fn from(s: String) -> Self {
        Self { message: s }
    }
}
```

逐行解释：
- `ToolError` — 只含 `message: String`，不区分错误种类。executor 统一捕获，工具作者无需约定错误格式
- `impl std::error::Error` — 满足 `anyhow`/`eyre` 等生态库的 trait 约束
- `From<&str>` + `From<String>` — 让 `"not found".into()` 和 `String::from("...").into()` 都能生成 `ToolError`，工具实现中写 `Err("timeout".into())` 即可

```rust

// ── ToolCallItem ──────────────────────────────────────────────────────

/// A single tool invocation within a `tool_call_set`.
///
/// Bidirectional dependency lock (same pattern as task State):
///   `blocked_by` — who blocks me (I depend on them)
///   `blocking`  — who I block (they depend on me)
///
/// Engine sets the dependencies; executor derives the reverse edges
/// during DAG construction for efficient bidirectional traversal.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolCallItem {
    /// Unique ID within this `tool_call_set` (e.g., "call_0", "call_1").
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

逐行解释：
- `#[serde(default)]` on `blocked_by` / `blocking` — 简单工具调用不提供依赖字段时，反序列化为空 Vec，不报错
- `id` — Engine 生成，格式 `"call_0"`、`"call_1"` 等，在同一 ToolCallSet 内唯一
- `params` — `serde_json::Value`，不提前解析 schema，由具体工具实现校验

```rust

// ── ToolCallSet ───────────────────────────────────────────────────────

/// A set of 1-N tool calls dispatched together.
///
/// Engine assembles this after receiving a model_response with tool_calls.
/// MCP builds a DAG from the bidirectional lock (`blocked_by` / `blocking`),
/// topologically sorts, and executes:
/// - Calls without dependencies: concurrent execution
/// - Calls with `blocked_by`: serialized by dependency order
/// - Any call fails → cascade cancel along `blocking` (forward)
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolCallSet {
    /// The session this tool call set belongs to.
    pub session_id: String,
    /// 1-N tool calls in this set.
    pub calls: Vec<ToolCallItem>,
    /// Per-call timeout in milliseconds. `None` = no timeout (Engine's choice).
    /// MCP does not impose a default or hidden deadline.
    #[serde(default)]
    pub timeout_ms: Option<u64>,
}
```

逐行解释：
- `session_id` — Engine 传入，关联会话
- `timeout_ms` — Engine 决定，MCP 不做默认值。None 表示无超时
- `#[serde(default)]` — Engine 不传 timeout 时默认为 None

```rust

// ── ToolResultItem ─────────────────────────────────────────────────────

/// Result of a single tool call.
///
/// All error packaging is centralized in the executor — tool authors
/// never construct this struct directly. The executor:
/// 1. Calls `tool.execute()` inside `catch_unwind`
/// 2. On `Ok(val)` → `status: "success"`, `result: val`
/// 3. On `Err(e)` → `status: "error"`, `error: e.message`
/// 4. On panic → `status: "error"`, `error: "panic: {message}"`
/// 5. On cancel (cascade or timeout) → calls `tool.cancel()`, `status: "cancelled"`
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolResultItem {
    /// Matches `ToolCallItem.id`.
    pub call_id: String,
    /// `"success"` or `"error"` or `"cancelled"`.
    pub status: String,
    /// The tool's return value. Null on error/cancelled.
    pub result: serde_json::Value,
    /// Error message populated by executor on error/cancelled.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}
```

逐行解释：
- `status` — 三态："success" / "error" / "cancelled"，Engine 据此判断是否继续
- `result` — `serde_json::Value`，错误时为 `null`
- `error` — `#[serde(skip_serializing_if = "Option::is_none")]`：成功时 `error: None` 不出现在 JSON 中，保持消息体简洁
- Tool 作者不构造此 struct — 全部由 executor 集中处理

```rust

// ── ToolResultSet ──────────────────────────────────────────────────────

/// Aggregated results for a `tool_call_set`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolResultSet {
    /// The session this result set belongs to.
    pub session_id: String,
    /// Results, one per call in the original `ToolCallSet`.
    pub results: Vec<ToolResultItem>,
}
```

逐行解释：
- `session_id` — 与原始 `ToolCallSet.session_id` 相同
- `results` — 与 `ToolCallSet.calls` 一一对应，按 `call_id` 匹配
```

---

### `crates/arf-mcp/src/config.rs`

```rust
use serde::{Deserialize, Serialize};

// ── ScriptRuntime ─────────────────────────────────────────────────────

/// Supported script runtimes.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum ScriptRuntime {
    Python,
    Bash,
    Rust,
}
```

逐行解释：
- `#[serde(rename_all = "lowercase")]` — `tool.toml` 中写 `runtime = "python"`（全小写），反序列化时自动匹配 `ScriptRuntime::Python`
- `PartialEq` — executor 按 runtime 选择执行器时需要 `==` 比较

```rust

// ── ToolConfig ─────────────────────────────────────────────────────────

/// Parsed tool.toml — script tool metadata.
///
/// Each directory under `{root}/tools/` with a valid `tool.toml` is
/// registered as a `ScriptTool`. The `runtime` field selects the executor.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolConfig {
    /// Unique tool name (kebab-case).
    pub name: String,
    /// Human-readable description for LLM function calling.
    pub description: String,
    /// Script runtime: `"python"`, `"bash"`, or `"rust"`.
    pub runtime: ScriptRuntime,
    /// Entry point script filename relative to the tool directory.
    pub entrypoint: String,
    /// Per-call timeout in milliseconds. `None` = no timeout.
    #[serde(default)]
    pub timeout_ms: Option<u64>,
    /// JSON Schema for the tool's parameters.
    #[serde(default)]
    pub params_schema: serde_json::Value,
}
```

逐行解释：
- `runtime: ScriptRuntime` — 非字符串枚举，保证合法值，避免 typo
- `entrypoint` — 相对 tool 目录的文件名（如 `"main.py"`），与 tool_dir 拼接后执行
- `timeout_ms` — `#[serde(default)]`：`tool.toml` 可省略，默认 None
- `params_schema` — `#[serde(default)]`：省略时为 `Value::Null`，表示无参数约束

```rust

// ── RemoteConfig ───────────────────────────────────────────────────────

/// Streamable HTTP transport configuration for a remote MCP server.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RemoteConfig {
    /// Transport protocol: `"streamable-http"`.
    pub transport: String,
    /// Base URL of the remote MCP server.
    pub url: String,
}
```

逐行解释：
- `transport` — 当前仅 `"streamable-http"`，预留扩展（未来可能加 `"sse"`）
- `url` — MCP server base URL（如 `"https://mcp.codetidy.dev"`）

---

## 测试

### 测试结构

```
crates/arf-mcp/src/
├── lib.rs
├── tool.rs
├── types.rs
├── config.rs
└── tests/
    ├── tool_tests.rs
    ├── types_tests.rs
    └── config_tests.rs
```

---

### `crates/arf-mcp/src/tests/tool_tests.rs`

```rust
use arf_mcp::tool::Tool;
use arf_mcp::types::ToolError;
use serde_json::Value;

/// Mock Tool implementation for testing the Trait contract.
struct MockTool {
    name: String,
    description: String,
    schema: Value,
    result: Value,
}

impl MockTool {
    fn new(name: &str, result: Value) -> Self {
        Self {
            name: name.into(),
            description: format!("Mock tool: {name}"),
            schema: serde_json::json!({"type": "object"}),
            result,
        }
    }
}

#[async_trait::async_trait]
impl Tool for MockTool {
    fn name(&self) -> &str {
        &self.name
    }

    fn description(&self) -> &str {
        &self.description
    }

    fn parameters_schema(&self) -> Value {
        self.schema.clone()
    }

    async fn execute(&self, _params: Value) -> Result<Value, ToolError> {
        Ok(self.result.clone())
    }
}

// ═══════════════════════════════════════════════════════════════
// Tool trait — 8 tests
// ═══════════════════════════════════════════════════════════════

// [构造] MockTool 返回正确的 name、description、schema
#[test]
fn tool_name_description_schema() {
    let tool = MockTool::new("mock", serde_json::json!({"ok": true}));
    assert_eq!(tool.name(), "mock");
    assert_eq!(tool.description(), "Mock tool: mock");
    assert_eq!(
        tool.parameters_schema(),
        serde_json::json!({"type": "object"})
    );
}

// [方法] execute 返回预设结果
#[tokio::test]
async fn tool_execute_returns_result() {
    let tool = MockTool::new("mock", serde_json::json!({"ok": true}));
    let result = tool.execute(serde_json::json!({"x": 1})).await.unwrap();
    assert_eq!(result, serde_json::json!({"ok": true}));
}

// [方法] cancel 默认实现不 panic（no-op）
#[tokio::test]
async fn tool_cancel_default_is_noop() {
    let tool = MockTool::new("mock", serde_json::json!(null));
    tool.cancel().await; // should not panic
}

// [边界] name 为空字符串——不 panic
#[test]
fn tool_empty_name() {
    let tool = MockTool::new("", serde_json::json!(null));
    assert_eq!(tool.name(), "");
}

// [边界] 超大 JSON result（100KB）正常返回
#[tokio::test]
async fn tool_large_result() {
    let large = serde_json::Value::String("x".repeat(100_000));
    let tool = MockTool::new("big", large.clone());
    let result = tool.execute(serde_json::json!(null)).await.unwrap();
    assert_eq!(result, large);
}

// [类型] Tool trait 可用于 Arc<dyn Tool> 动态分发
#[test]
fn tool_trait_is_object_safe_via_async_trait() {
    let tool: std::sync::Arc<dyn Tool> =
        std::sync::Arc::new(MockTool::new("mock", serde_json::json!(null)));
    assert_eq!(tool.name(), "mock");
}

// [覆盖] execute 接收空 JSON 对象 {} 不报错
#[tokio::test]
async fn tool_execute_empty_params() {
    let tool = MockTool::new("mock", serde_json::json!({"ok": true}));
    let result = tool.execute(serde_json::json!({})).await.unwrap();
    assert_eq!(result, serde_json::json!({"ok": true}));
}

// [覆盖] execute 接收 null params 不报错
#[tokio::test]
async fn tool_execute_null_params() {
    let tool = MockTool::new("mock", serde_json::json!(42));
    let result = tool.execute(serde_json::Value::Null).await.unwrap();
    assert_eq!(result, serde_json::json!(42));
}
```

---

### `crates/arf-mcp/src/tests/types_tests.rs`

```rust
use arf_mcp::types::{ToolCallItem, ToolCallSet, ToolError, ToolResultItem, ToolResultSet};
use serde_json::Value;

// ═══════════════════════════════════════════════════════════════
// ToolError — 9 tests
// ═══════════════════════════════════════════════════════════════

// [构造] From<&str> 正确设置 message
#[test]
fn tool_error_from_str() {
    let e = ToolError::from("file not found");
    assert_eq!(e.message, "file not found");
}

// [构造] From<String> 正确设置 message
#[test]
fn tool_error_from_string() {
    let e = ToolError::from(String::from("permission denied"));
    assert_eq!(e.message, "permission denied");
}

// [trait] Display 输出 message 本身
#[test]
fn tool_error_display() {
    let e = ToolError::from("timeout");
    assert_eq!(format!("{e}"), "timeout");
}

// [trait] std::error::Error 满足 trait 约束
#[test]
fn tool_error_implements_std_error() {
    fn takes_error(_e: impl std::error::Error) {}
    takes_error(ToolError::from("test"));
}

// [trait] Debug 输出包含 message
#[test]
fn tool_error_debug() {
    let e = ToolError::from("crash");
    let debug = format!("{e:?}");
    assert!(debug.contains("crash"));
}

// [trait] Clone 克隆后 message 相等
#[test]
fn tool_error_clone() {
    let e = ToolError::from("original");
    assert_eq!(e.message, e.clone().message);
}

// [边界] 空字符串 message：不 panic
#[test]
fn tool_error_empty_message() {
    let e = ToolError::from("");
    assert_eq!(e.message, "");
    assert_eq!(format!("{e}"), "");
}

// [边界] 超长 message（10KB）正常存取
#[test]
fn tool_error_long_message() {
    let long = "e".repeat(10_000);
    let e = ToolError::from(long.clone());
    assert_eq!(e.message.len(), 10_000);
}

// [边界] Unicode message（中文 + emoji）正常存取
#[test]
fn tool_error_unicode_message() {
    let e = ToolError::from("错误💥");
    assert_eq!(e.message, "错误💥");
    assert_eq!(format!("{e}"), "错误💥");
}

// ═══════════════════════════════════════════════════════════════
// ToolCallItem — 8 tests
// ═══════════════════════════════════════════════════════════════

// [构造] 所有字段正确赋值
#[test]
fn tool_call_item_all_fields() {
    let call = ToolCallItem {
        id: "call_0".into(),
        tool: "read_file".into(),
        params: serde_json::json!({"path": "/tmp/x"}),
        blocked_by: vec!["call_1".into()],
        blocking: vec!["call_2".into()],
    };
    assert_eq!(call.id, "call_0");
    assert_eq!(call.tool, "read_file");
    assert_eq!(call.params["path"], "/tmp/x");
    assert_eq!(call.blocked_by, vec!["call_1"]);
    assert_eq!(call.blocking, vec!["call_2"]);
}

// [边界] blocked_by 和 blocking 为空 Vec
#[test]
fn tool_call_item_empty_deps() {
    let call = ToolCallItem {
        id: "call_0".into(),
        tool: "search".into(),
        params: serde_json::Value::Null,
        blocked_by: vec![],
        blocking: vec![],
    };
    assert!(call.blocked_by.is_empty());
    assert!(call.blocking.is_empty());
}

// [边界] id 为空字符串：不 panic
#[test]
fn tool_call_item_empty_id() {
    let call = ToolCallItem {
        id: "".into(),
        tool: "tool".into(),
        params: serde_json::Value::Null,
        blocked_by: vec![],
        blocking: vec![],
    };
    assert_eq!(call.id, "");
}

// [序列化] serde 往返：含依赖的完整 ToolCallItem
#[test]
fn tool_call_item_serialization_roundtrip() {
    let call = ToolCallItem {
        id: "call_0".into(),
        tool: "read_file".into(),
        params: serde_json::json!({"path": "/tmp/x"}),
        blocked_by: vec!["call_1".into()],
        blocking: vec!["call_2".into(), "call_3".into()],
    };
    let json = serde_json::to_string(&call).unwrap();
    let back: ToolCallItem = serde_json::from_str(&json).unwrap();
    assert_eq!(call.id, back.id);
    assert_eq!(call.tool, back.tool);
    assert_eq!(call.params, back.params);
    assert_eq!(call.blocked_by, back.blocked_by);
    assert_eq!(call.blocking, back.blocking);
}

// [序列化] 省略 blocked_by/blocking 字段 → 反序列化为空 Vec
#[test]
fn tool_call_item_deserialize_missing_deps() {
    let json = r#"{"id":"call_0","tool":"x","params":null}"#;
    let call: ToolCallItem = serde_json::from_str(json).unwrap();
    assert!(call.blocked_by.is_empty());
    assert!(call.blocking.is_empty());
}

// [trait] Clone 克隆后字段一致
#[test]
fn tool_call_item_clone() {
    let call = ToolCallItem {
        id: "call_0".into(),
        tool: "tool".into(),
        params: serde_json::json!({"x": 1}),
        blocked_by: vec!["call_1".into()],
        blocking: vec![],
    };
    let cloned = call.clone();
    assert_eq!(call.id, cloned.id);
    assert_eq!(call.tool, cloned.tool);
    assert_eq!(call.params, cloned.params);
    assert_eq!(call.blocked_by, cloned.blocked_by);
    assert_eq!(call.blocking, cloned.blocking);
}

// [边界] params 深度嵌套（4 层）结构保留
#[test]
fn tool_call_item_deeply_nested_params() {
    let params = serde_json::json!({
        "a": {"b": {"c": {"d": [1, null, {"e": "deep"}]}}}
    });
    let call = ToolCallItem {
        id: "call_0".into(),
        tool: "deep".into(),
        params: params.clone(),
        blocked_by: vec![],
        blocking: vec![],
    };
    let json = serde_json::to_string(&call).unwrap();
    let back: ToolCallItem = serde_json::from_str(&json).unwrap();
    assert_eq!(back.params, params);
}

// [trait] Debug 输出可读
#[test]
fn tool_call_item_debug() {
    let call = ToolCallItem {
        id: "call_0".into(),
        tool: "read_file".into(),
        params: serde_json::json!({"path": "/x"}),
        blocked_by: vec![],
        blocking: vec![],
    };
    let debug = format!("{call:?}");
    assert!(debug.contains("call_0"));
    assert!(debug.contains("read_file"));
}

// ═══════════════════════════════════════════════════════════════
// ToolCallSet — 7 tests
// ═══════════════════════════════════════════════════════════════

// [构造] 含多个 call 的 ToolCallSet
#[test]
fn tool_call_set_multiple_calls() {
    let set = ToolCallSet {
        session_id: "session-1".into(),
        calls: vec![
            ToolCallItem {
                id: "call_0".into(),
                tool: "read_file".into(),
                params: serde_json::json!({"path": "/a"}),
                blocked_by: vec![],
                blocking: vec!["call_1".into()],
            },
            ToolCallItem {
                id: "call_1".into(),
                tool: "write_file".into(),
                params: serde_json::json!({"path": "/b"}),
                blocked_by: vec!["call_0".into()],
                blocking: vec![],
            },
        ],
        timeout_ms: Some(5000),
    };
    assert_eq!(set.session_id, "session-1");
    assert_eq!(set.calls.len(), 2);
    assert_eq!(set.timeout_ms, Some(5000));
}

// [边界] calls 为空 Vec
#[test]
fn tool_call_set_empty_calls() {
    let set = ToolCallSet {
        session_id: "session-1".into(),
        calls: vec![],
        timeout_ms: None,
    };
    assert!(set.calls.is_empty());
    assert_eq!(set.timeout_ms, None);
}

// [边界] timeout_ms = None（Engine 不传）
#[test]
fn tool_call_set_timeout_none() {
    let set = ToolCallSet {
        session_id: "s".into(),
        calls: vec![],
        timeout_ms: None,
    };
    assert_eq!(set.timeout_ms, None);
}

// [序列化] serde 往返：含 timeout 的完整 ToolCallSet
#[test]
fn tool_call_set_serialization_roundtrip() {
    let set = ToolCallSet {
        session_id: "session-1".into(),
        calls: vec![ToolCallItem {
            id: "call_0".into(),
            tool: "read".into(),
            params: serde_json::json!({"path": "/x"}),
            blocked_by: vec![],
            blocking: vec![],
        }],
        timeout_ms: Some(30000),
    };
    let json = serde_json::to_string(&set).unwrap();
    let back: ToolCallSet = serde_json::from_str(&json).unwrap();
    assert_eq!(back.session_id, "session-1");
    assert_eq!(back.calls.len(), 1);
    assert_eq!(back.timeout_ms, Some(30000));
}

// [序列化] 省略 timeout_ms → 反序列化为 None
#[test]
fn tool_call_set_deserialize_missing_timeout() {
    let json = r#"{"session_id":"s","calls":[]}"#;
    let set: ToolCallSet = serde_json::from_str(json).unwrap();
    assert_eq!(set.timeout_ms, None);
}

// [trait] Clone 克隆后字段一致
#[test]
fn tool_call_set_clone() {
    let set = ToolCallSet {
        session_id: "sid".into(),
        calls: vec![ToolCallItem {
            id: "c0".into(),
            tool: "t".into(),
            params: serde_json::json!(null),
            blocked_by: vec![],
            blocking: vec![],
        }],
        timeout_ms: Some(1000),
    };
    let cloned = set.clone();
    assert_eq!(set.session_id, cloned.session_id);
    assert_eq!(set.calls.len(), cloned.calls.len());
    assert_eq!(set.timeout_ms, cloned.timeout_ms);
}

// [边界] session_id 为空字符串：不 panic
#[test]
fn tool_call_set_empty_session_id() {
    let set = ToolCallSet {
        session_id: "".into(),
        calls: vec![],
        timeout_ms: None,
    };
    assert_eq!(set.session_id, "");
}

// ═══════════════════════════════════════════════════════════════
// ToolResultItem — 10 tests
// ═══════════════════════════════════════════════════════════════

// [构造] success 状态：result 有值，error 为 None
#[test]
fn tool_result_item_success() {
    let item = ToolResultItem {
        call_id: "call_0".into(),
        status: "success".into(),
        result: serde_json::json!({"ok": true, "data": [1, 2, 3]}),
        error: None,
    };
    assert_eq!(item.status, "success");
    assert_eq!(item.result["ok"], true);
    assert!(item.error.is_none());
}

// [构造] error 状态：result 为 null，error 有值
#[test]
fn tool_result_item_error() {
    let item = ToolResultItem {
        call_id: "call_1".into(),
        status: "error".into(),
        result: serde_json::Value::Null,
        error: Some("file not found".into()),
    };
    assert_eq!(item.status, "error");
    assert_eq!(item.result, serde_json::Value::Null);
    assert_eq!(item.error, Some("file not found".into()));
}

// [构造] cancelled 状态：result 为 null，error 解释原因
#[test]
fn tool_result_item_cancelled() {
    let item = ToolResultItem {
        call_id: "call_2".into(),
        status: "cancelled".into(),
        result: serde_json::Value::Null,
        error: Some("cancelled: dependency call_1 failed".into()),
    };
    assert_eq!(item.status, "cancelled");
    assert_eq!(item.result, serde_json::Value::Null);
    assert!(item.error.unwrap().contains("cancelled"));
}

// [序列化] success 不输出 error 字段
#[test]
fn tool_result_item_success_skips_error() {
    let item = ToolResultItem {
        call_id: "call_0".into(),
        status: "success".into(),
        result: serde_json::json!({"ok": true}),
        error: None,
    };
    let json = serde_json::to_string(&item).unwrap();
    assert!(!json.contains("error"));
    assert!(json.contains("success"));
}

// [序列化] error 状态输出 error 字段
#[test]
fn tool_result_item_error_includes_error_field() {
    let item = ToolResultItem {
        call_id: "call_1".into(),
        status: "error".into(),
        result: serde_json::Value::Null,
        error: Some("timeout".into()),
    };
    let json = serde_json::to_string(&item).unwrap();
    assert!(json.contains("\"error\""));
    assert!(json.contains("timeout"));
}

// [序列化] serde 往返 success
#[test]
fn tool_result_item_serialization_roundtrip_success() {
    let item = ToolResultItem {
        call_id: "call_0".into(),
        status: "success".into(),
        result: serde_json::json!({"deleted": 42}),
        error: None,
    };
    let json = serde_json::to_string(&item).unwrap();
    let back: ToolResultItem = serde_json::from_str(&json).unwrap();
    assert_eq!(back.call_id, "call_0");
    assert_eq!(back.status, "success");
    assert_eq!(back.result["deleted"], 42);
    assert_eq!(back.error, None);
}

// [序列化] serde 往返 error
#[test]
fn tool_result_item_serialization_roundtrip_error() {
    let item = ToolResultItem {
        call_id: "call_1".into(),
        status: "error".into(),
        result: serde_json::Value::Null,
        error: Some("bad input".into()),
    };
    let json = serde_json::to_string(&item).unwrap();
    let back: ToolResultItem = serde_json::from_str(&json).unwrap();
    assert_eq!(back.status, "error");
    assert_eq!(back.error, Some("bad input".into()));
}

// [trait] Clone 克隆后字段一致
#[test]
fn tool_result_item_clone() {
    let item = ToolResultItem {
        call_id: "call_0".into(),
        status: "success".into(),
        result: serde_json::json!({"x": 1}),
        error: None,
    };
    let cloned = item.clone();
    assert_eq!(item.call_id, cloned.call_id);
    assert_eq!(item.status, cloned.status);
    assert_eq!(item.result, cloned.result);
    assert_eq!(item.error, cloned.error);
}

// [边界] call_id 为空字符串：不 panic
#[test]
fn tool_result_item_empty_call_id() {
    let item = ToolResultItem {
        call_id: "".into(),
        status: "success".into(),
        result: serde_json::Value::Null,
        error: None,
    };
    assert_eq!(item.call_id, "");
}

// [边界] result 为深度嵌套 JSON：结构保留
#[test]
fn tool_result_item_deeply_nested_result() {
    let result = serde_json::json!({
        "files": [{"name": "a.rs", "matches": [{"line": 1, "col": 2}]}]
    });
    let item = ToolResultItem {
        call_id: "call_0".into(),
        status: "success".into(),
        result: result.clone(),
        error: None,
    };
    let json = serde_json::to_string(&item).unwrap();
    let back: ToolResultItem = serde_json::from_str(&json).unwrap();
    assert_eq!(back.result, result);
}

// ═══════════════════════════════════════════════════════════════
// ToolResultSet — 6 tests
// ═══════════════════════════════════════════════════════════════

// [构造] 含多个结果
#[test]
fn tool_result_set_multiple_results() {
    let set = ToolResultSet {
        session_id: "session-1".into(),
        results: vec![
            ToolResultItem {
                call_id: "call_0".into(),
                status: "success".into(),
                result: serde_json::json!("content"),
                error: None,
            },
            ToolResultItem {
                call_id: "call_1".into(),
                status: "error".into(),
                result: serde_json::Value::Null,
                error: Some("failed".into()),
            },
            ToolResultItem {
                call_id: "call_2".into(),
                status: "cancelled".into(),
                result: serde_json::Value::Null,
                error: Some("cancelled: upstream call_1 failed".into()),
            },
        ],
    };
    assert_eq!(set.session_id, "session-1");
    assert_eq!(set.results.len(), 3);
    assert_eq!(set.results[0].status, "success");
    assert_eq!(set.results[1].status, "error");
    assert_eq!(set.results[2].status, "cancelled");
}

// [边界] results 为空 Vec
#[test]
fn tool_result_set_empty_results() {
    let set = ToolResultSet {
        session_id: "session-1".into(),
        results: vec![],
    };
    assert!(set.results.is_empty());
}

// [序列化] serde 往返
#[test]
fn tool_result_set_serialization_roundtrip() {
    let set = ToolResultSet {
        session_id: "session-1".into(),
        results: vec![
            ToolResultItem {
                call_id: "call_0".into(),
                status: "success".into(),
                result: serde_json::json!({"ok": true}),
                error: None,
            },
            ToolResultItem {
                call_id: "call_1".into(),
                status: "error".into(),
                result: serde_json::Value::Null,
                error: Some("boom".into()),
            },
        ],
    };
    let json = serde_json::to_string(&set).unwrap();
    let back: ToolResultSet = serde_json::from_str(&json).unwrap();
    assert_eq!(back.session_id, "session-1");
    assert_eq!(back.results.len(), 2);
    assert_eq!(back.results[0].call_id, "call_0");
    assert_eq!(back.results[1].call_id, "call_1");
}

// [trait] Clone 克隆后一致
#[test]
fn tool_result_set_clone() {
    let set = ToolResultSet {
        session_id: "sid".into(),
        results: vec![ToolResultItem {
            call_id: "c0".into(),
            status: "success".into(),
            result: serde_json::json!(null),
            error: None,
        }],
    };
    let cloned = set.clone();
    assert_eq!(set.session_id, cloned.session_id);
    assert_eq!(set.results.len(), cloned.results.len());
}

// [边界] session_id 为空字符串
#[test]
fn tool_result_set_empty_session_id() {
    let set = ToolResultSet {
        session_id: "".into(),
        results: vec![],
    };
    assert_eq!(set.session_id, "");
}

// [trait] Debug 输出可读
#[test]
fn tool_result_set_debug() {
    let set = ToolResultSet {
        session_id: "sid".into(),
        results: vec![ToolResultItem {
            call_id: "c0".into(),
            status: "success".into(),
            result: serde_json::json!({"x": 1}),
            error: None,
        }],
    };
    let debug = format!("{set:?}");
    assert!(debug.contains("sid"));
    assert!(debug.contains("c0"));
}
```

---

### `crates/arf-mcp/src/tests/config_tests.rs`

```rust
use arf_mcp::config::{RemoteConfig, ScriptRuntime, ToolConfig};
use serde_json::Value;

// ═══════════════════════════════════════════════════════════════
// ScriptRuntime — 8 tests
// ═══════════════════════════════════════════════════════════════

// [覆盖] 三种变体均可构造
#[test]
fn script_runtime_all_variants_construct() {
    let _ = ScriptRuntime::Python;
    let _ = ScriptRuntime::Bash;
    let _ = ScriptRuntime::Rust;
}

// [trait] Clone + PartialEq
#[test]
fn script_runtime_clone_and_eq() {
    let a = ScriptRuntime::Python;
    let b = a.clone();
    assert_eq!(a, b);
    assert_ne!(a, ScriptRuntime::Bash);
    assert_ne!(ScriptRuntime::Bash, ScriptRuntime::Rust);
}

// [序列化] Python → "python" → Python
#[test]
fn script_runtime_serialization_python() {
    let json = serde_json::to_string(&ScriptRuntime::Python).unwrap();
    assert_eq!(json, r#""python""#);
    let back: ScriptRuntime = serde_json::from_str(&json).unwrap();
    assert_eq!(back, ScriptRuntime::Python);
}

// [序列化] Bash → "bash" → Bash
#[test]
fn script_runtime_serialization_bash() {
    let json = serde_json::to_string(&ScriptRuntime::Bash).unwrap();
    assert_eq!(json, r#""bash""#);
    let back: ScriptRuntime = serde_json::from_str(&json).unwrap();
    assert_eq!(back, ScriptRuntime::Bash);
}

// [序列化] Rust → "rust" → Rust
#[test]
fn script_runtime_serialization_rust() {
    let json = serde_json::to_string(&ScriptRuntime::Rust).unwrap();
    assert_eq!(json, r#""rust""#);
    let back: ScriptRuntime = serde_json::from_str(&json).unwrap();
    assert_eq!(back, ScriptRuntime::Rust);
}

// [兼容] 从 TOML 小写字符串反序列化（模拟 tool.toml 读取路径）
#[test]
fn script_runtime_deserialize_toml_style() {
    // TOML 解析后得到的是 serde_json::Value 或直接解析
    let back: ScriptRuntime = serde_json::from_str(r#""python""#).unwrap();
    assert_eq!(back, ScriptRuntime::Python);
}

// [边界] 非法 runtime 字符串反序列化应报错
#[test]
fn script_runtime_deserialize_invalid() {
    let result: Result<ScriptRuntime, _> = serde_json::from_str(r#""javascript""#);
    assert!(result.is_err());
}

// [trait] Debug 输出变体名
#[test]
fn script_runtime_debug() {
    let debug = format!("{:?}", ScriptRuntime::Python);
    assert!(debug.contains("Python"));
    let debug = format!("{:?}", ScriptRuntime::Bash);
    assert!(debug.contains("Bash"));
}

// ═══════════════════════════════════════════════════════════════
// ToolConfig — 8 tests
// ═══════════════════════════════════════════════════════════════

// [构造] 所有字段正确赋值
#[test]
fn tool_config_all_fields() {
    let config = ToolConfig {
        name: "cleanup_logs".into(),
        description: "Delete log files older than N days".into(),
        runtime: ScriptRuntime::Bash,
        entrypoint: "main.sh".into(),
        timeout_ms: Some(30000),
        params_schema: serde_json::json!({
            "type": "object",
            "properties": {
                "days": {"type": "integer", "default": 30}
            }
        }),
    };
    assert_eq!(config.name, "cleanup_logs");
    assert_eq!(config.runtime, ScriptRuntime::Bash);
    assert_eq!(config.entrypoint, "main.sh");
    assert_eq!(config.timeout_ms, Some(30000));
}

// [边界] timeout_ms = None（未设置超时）
#[test]
fn tool_config_timeout_none() {
    let config = ToolConfig {
        name: "fast_tool".into(),
        description: "Quick operation".into(),
        runtime: ScriptRuntime::Python,
        entrypoint: "main.py".into(),
        timeout_ms: None,
        params_schema: serde_json::Value::Null,
    };
    assert_eq!(config.timeout_ms, None);
}

// [边界] params_schema 为 Null
#[test]
fn tool_config_null_params_schema() {
    let config = ToolConfig {
        name: "no_params".into(),
        description: "A tool without parameters".into(),
        runtime: ScriptRuntime::Bash,
        entrypoint: "run.sh".into(),
        timeout_ms: None,
        params_schema: serde_json::Value::Null,
    };
    assert_eq!(config.params_schema, serde_json::Value::Null);
}

// [序列化] serde 往返
#[test]
fn tool_config_serialization_roundtrip() {
    let config = ToolConfig {
        name: "read_file".into(),
        description: "Read file contents".into(),
        runtime: ScriptRuntime::Python,
        entrypoint: "main.py".into(),
        timeout_ms: Some(10000),
        params_schema: serde_json::json!({"type": "object", "properties": {"path": {"type": "string"}}}),
    };
    let json = serde_json::to_string(&config).unwrap();
    let back: ToolConfig = serde_json::from_str(&json).unwrap();
    assert_eq!(back.name, "read_file");
    assert_eq!(back.runtime, ScriptRuntime::Python);
    assert_eq!(back.entrypoint, "main.py");
    assert_eq!(back.timeout_ms, Some(10000));
    assert_eq!(back.params_schema["type"], "object");
}

// [序列化] runtime 字段序列化为小写
#[test]
fn tool_config_runtime_serialized_as_lowercase() {
    let config = ToolConfig {
        name: "t".into(),
        description: "d".into(),
        runtime: ScriptRuntime::Python,
        entrypoint: "e".into(),
        timeout_ms: None,
        params_schema: serde_json::Value::Null,
    };
    let json = serde_json::to_string(&config).unwrap();
    assert!(json.contains(r#""python""#));
}

// [兼容] 从 TOML 格式 JSON 反序列化（模拟 tool.toml 解析）
#[test]
fn tool_config_deserialize_toml_style_minimal() {
    // Simulates a minimal tool.toml parsed via toml crate → JSON
    let json = r#"{
        "name": "hello",
        "description": "Say hello",
        "runtime": "python",
        "entrypoint": "hello.py"
    }"#;
    let config: ToolConfig = serde_json::from_str(json).unwrap();
    assert_eq!(config.name, "hello");
    assert_eq!(config.runtime, ScriptRuntime::Python);
    assert_eq!(config.entrypoint, "hello.py");
    assert_eq!(config.timeout_ms, None);
    assert_eq!(config.params_schema, serde_json::Value::Null);
}

// [trait] Clone 克隆后一致
#[test]
fn tool_config_clone() {
    let config = ToolConfig {
        name: "x".into(),
        description: "y".into(),
        runtime: ScriptRuntime::Rust,
        entrypoint: "main.rs".into(),
        timeout_ms: Some(5000),
        params_schema: serde_json::json!({"type": "object"}),
    };
    let cloned = config.clone();
    assert_eq!(config.name, cloned.name);
    assert_eq!(config.runtime, cloned.runtime);
    assert_eq!(config.timeout_ms, cloned.timeout_ms);
    assert_eq!(config.params_schema, cloned.params_schema);
}

// [边界] 空 description 和 name：不 panic
#[test]
fn tool_config_empty_strings() {
    let config = ToolConfig {
        name: "".into(),
        description: "".into(),
        runtime: ScriptRuntime::Bash,
        entrypoint: "".into(),
        timeout_ms: None,
        params_schema: serde_json::Value::Null,
    };
    assert_eq!(config.name, "");
    assert_eq!(config.description, "");
    assert_eq!(config.entrypoint, "");
}

// ═══════════════════════════════════════════════════════════════
// RemoteConfig — 5 tests
// ═══════════════════════════════════════════════════════════════

// [构造] 所有字段正确赋值
#[test]
fn remote_config_all_fields() {
    let config = RemoteConfig {
        transport: "streamable-http".into(),
        url: "https://mcp.codetidy.dev".into(),
    };
    assert_eq!(config.transport, "streamable-http");
    assert_eq!(config.url, "https://mcp.codetidy.dev");
}

// [序列化] serde 往返
#[test]
fn remote_config_serialization_roundtrip() {
    let config = RemoteConfig {
        transport: "streamable-http".into(),
        url: "https://mcp.example.com".into(),
    };
    let json = serde_json::to_string(&config).unwrap();
    let back: RemoteConfig = serde_json::from_str(&json).unwrap();
    assert_eq!(back.transport, "streamable-http");
    assert_eq!(back.url, "https://mcp.example.com");
}

// [trait] Clone 克隆后一致
#[test]
fn remote_config_clone() {
    let config = RemoteConfig {
        transport: "streamable-http".into(),
        url: "https://example.com".into(),
    };
    let cloned = config.clone();
    assert_eq!(config.transport, cloned.transport);
    assert_eq!(config.url, cloned.url);
}

// [边界] 空 URL：不 panic
#[test]
fn remote_config_empty_url() {
    let config = RemoteConfig {
        transport: "streamable-http".into(),
        url: "".into(),
    };
    assert_eq!(config.url, "");
}

// [trait] Debug 输出包含字段值
#[test]
fn remote_config_debug() {
    let config = RemoteConfig {
        transport: "streamable-http".into(),
        url: "https://mcp.example.com".into(),
    };
    let debug = format!("{config:?}");
    assert!(debug.contains("streamable-http"));
    assert!(debug.contains("mcp.example.com"));
}
```

---

### `crates/arf-mcp/src/tests/mod.rs`

```rust
mod config_tests;
mod tool_tests;
mod types_tests;
```

---

## lib.rs 更新

在 `lib.rs` 末尾添加测试模块声明：

```rust
#[cfg(test)]
mod tests;
```

完整 lib.rs：

```rust
//! ARF MCP — Model Context Protocol node.
//!
//! Each MCP instance is one namespace = one node on the Bus.
//! LocalMcpNode discovers tools/skills from the filesystem;
//! RemoteMcpNode proxies to an external MCP server via HTTP.

pub mod config;
pub mod tool;
pub mod types;

#[cfg(test)]
mod tests;
```

---

## workspace 注册

根 `Cargo.toml` 的 `members` 已包含 `crates/arf-mcp`（预创建目录时注册），无需修改。

---

## 验证命令

```bash
# 编译检查
. "$HOME/.cargo/env" && cargo check -p arf-mcp

# 运行 arf-mcp 测试
. "$HOME/.cargo/env" && cargo test -p arf-mcp

# Workspace 全量测试
. "$HOME/.cargo/env" && cargo test --workspace
```

---

## 测试覆盖摘要

| 文件 | 测试数 | 覆盖角度 |
|------|--------|---------|
| `tool_tests.rs` | 8 | `[构造][方法][边界][类型][覆盖]` |
| `types_tests.rs` | 40 | `[构造][边界][序列化][trait][兼容]` |
| `config_tests.rs` | 21 | `[覆盖][序列化][构造][边界][兼容][trait]` |
| **合计** | **69** | |
