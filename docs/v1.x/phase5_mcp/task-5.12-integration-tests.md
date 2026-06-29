# 任务 5.12：集成测试（LocalMcpNode + RemoteMcpNode + MiniEngine + ModelAdapter）

> Phase 5 — MCP 第十二项任务
> 父文档：`docs/v1.x/phase5_mcp/phase5-mcp-design.md`
> 依赖：Task 5.10 (fixtures), Task 5.5 (McpNode 统一), Task 5.8 (RemoteMcpNode), Task 5.1a (ModelAdapter convert)

## 设计思路

构建一个 `MiniEngine`——最小可行的 Engine 骨架，通过 Bus 发现 MCP 节点、路由 tool_call_set、接收 tool_result_set、调用 `tool_result_to_model_message()` 转换结果。配合 5.10 fixtures（本地链路）和 CodeTidy（远程链路）验证 MCP 框架的完整集成。

### 与已有测试的关系

| 测试层 | 文件 | 做什么 | 局限 |
|--------|------|--------|------|
| 单元 | `src/tests/node_tests.rs` | 单节点 echo/fail，DAG，级联取消 | `use crate::` 内部，非公开 API |
| live | `tests/codetidy_live.rs` | 直接用 Bus + engine handle 调 CodeTidy | 手动构造消息，无 Engine 抽象 |
| **集成** | `tests/integration_tests.rs` | MiniEngine + fixtures + CodeTidy + ModelAdapter | **本任务** |

### MiniEngine 是什么

```
                    ┌─────────────────────────────────┐
                    │           MiniEngine             │
                    │                                 │
  node_online ──────┤→ 注册 namespace → tools         │
                    │                                 │
  call_tool(ns,     │  按 namespace 路由              │
    tool, params) ──┤  → tool_call_set → mcp/{ns}     │
                    │                                 │
  tool_result_set ──┤→ tool_result_to_model_message() │
                    │  → ModelMessage                 │
                    └─────────────────────────────────┘
```

MiniEngine 不是完整的 ReAct 引擎（Task 5.13 才引入真实 LLM），但它验证了 Engine 最核心的集成能力——**发现 → 路由 → 执行 → 转换**——在真实 Bus + 真实 MCP 节点上的完整链路。

### Remote 选择：CodeTidy

使用 CodeTidy（`https://mcp.codetidy.dev`，免费、无需认证、62 个开发者工具）作为远程 MCP 测试目标。已在 `codetidy_live.rs` 中验证过基础连接和 12 个测试。本任务扩展更多工具类型和边界条件。

### 目录结构

```
crates/arf-mcp/tests/
├── fixtures/                  # Task 5.10 (已有)
├── codetidy_live.rs           # 已有
└── integration_tests.rs       # [新建]
```

| 文件 | 操作 | 内容 |
|------|------|------|
| `crates/arf-mcp/tests/integration_tests.rs` | 新建 | MiniEngine + Local fixtures + CodeTidy + ModelAdapter 集成 |
| `crates/arf-mcp/Cargo.toml` | 更新 | 添加 `arf-model-adapter` dev-dependency |

---

## MiniEngine 实现

```rust
use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration;

use arf_bus::Bus;
use arf_core::{MessageFilter, ModelMessage, NodeId, NodeInfo, ToMatch};
use arf_mcp::types::ToolResultItem;
use arf_model_adapter::convert::tool_result_to_model_message;

/// Tool metadata from node_online for routing.
#[derive(Debug, Clone)]
struct McpToolInfo {
    name: String,
    description: String,
}

/// Registered MCP node information.
#[derive(Debug, Clone)]
struct McpNodeEntry {
    node_id: NodeId,
    namespace: String,
    tools: Vec<McpToolInfo>,
}

/// A minimal Engine for integration testing.
///
/// Connects to the Bus as an engine node, discovers MCP nodes via
/// `node_online`, routes `tool_call_set` by namespace, and converts
/// `tool_result_set` to `ModelMessage` via ModelAdapter.
struct MiniEngine {
    bus: Bus,
    handle: arf_bus::NodeHandle,
    session_id: String,
    mcp_nodes: HashMap<String, McpNodeEntry>, // namespace → entry
}

impl MiniEngine {
    /// Connect to the Bus as an engine node and start listening for MCP nodes.
    async fn new(bus: Bus, session_id: &str) -> Self {
        let handle = bus
            .connect(
                NodeInfo {
                    node_id: NodeId::new(format!("engine/{session_id}")),
                    node_type: "engine".into(),
                    capabilities: serde_json::json!({}),
                    online_since: 0,
                },
                MessageFilter {
                    types: Some(vec![
                        "tool_result_set".into(),
                        "skill_error".into(),
                        "skill_loaded".into(),
                        "skill_resource_loaded".into(),
                        "skill_script_result".into(),
                    ]),
                    to_match: ToMatch::All,
                },
            )
            .await
            .unwrap();

        MiniEngine { bus, handle, session_id: session_id.into(), mcp_nodes: HashMap::new() }
    }

    /// Register an MCP node. Called after `node_online` is received.
    fn register_mcp(&mut self, namespace: &str, node_id: NodeId, tools: Vec<McpToolInfo>) {
        self.mcp_nodes.insert(
            namespace.into(),
            McpNodeEntry { node_id, namespace: namespace.into(), tools },
        );
    }

    /// Get all known MCP node namespaces.
    fn namespaces(&self) -> Vec<&str> {
        self.mcp_nodes.keys().map(|s| s.as_str()).collect()
    }

    /// Get the list of tool names for a namespace.
    fn tool_names(&self, namespace: &str) -> Option<Vec<String>> {
        self.mcp_nodes.get(namespace).map(|e| {
            e.tools.iter().map(|t| t.name.clone()).collect()
        })
    }

    /// Send a tool_call_set to the MCP node for the given namespace,
    /// wait for the tool_result_set, and convert each result to ModelMessage.
    async fn call_tool(
        &mut self,
        namespace: &str,
        tool_name: &str,
        params: serde_json::Value,
    ) -> Result<Vec<ModelMessage>, String> {
        let entry = self.mcp_nodes.get(namespace)
            .ok_or_else(|| format!("unknown namespace: {namespace}"))?;

        let call_id = "c0";
        self.handle
            .send(
                "tool_call_set",
                vec![entry.node_id.clone()],
                serde_json::json!({
                    "session_id": self.session_id,
                    "calls": [{"id": call_id, "tool": tool_name, "params": params}],
                }),
            )
            .await
            .map_err(|e| format!("send error: {e}"))?;

        let resp = self.handle.recv().await
            .map_err(|e| format!("recv error: {e}"))?;

        if resp.msg_type != "tool_result_set" {
            return Err(format!("unexpected msg_type: {}", resp.msg_type));
        }

        let results: Vec<ToolResultItem> = serde_json::from_value(
            resp.payload["results"].clone(),
        )
        .map_err(|e| format!("parse error: {e}"))?;

        let messages: Vec<ModelMessage> = results
            .iter()
            .map(|item| tool_result_to_model_message(item))
            .collect();

        Ok(messages)
    }

    /// Send tool_call_set with multiple calls (for DAG/concurrent tests).
    async fn call_tools_batch(
        &mut self,
        namespace: &str,
        calls: serde_json::Value,
    ) -> Result<Vec<ToolResultItem>, String> {
        let entry = self.mcp_nodes.get(namespace)
            .ok_or_else(|| format!("unknown namespace: {namespace}"))?;

        self.handle
            .send(
                "tool_call_set",
                vec![entry.node_id.clone()],
                serde_json::json!({
                    "session_id": self.session_id,
                    "calls": calls,
                }),
            )
            .await
            .map_err(|e| format!("send error: {e}"))?;

        let resp = self.handle.recv().await
            .map_err(|e| format!("recv error: {e}"))?;

        serde_json::from_value(resp.payload["results"].clone())
            .map_err(|e| format!("parse error: {e}"))
    }
}
```

**关键设计**：
- `call_tool()` 返回 `Vec<ModelMessage>`——每次调用自动走 `tool_result_to_model_message()` 转换
- `call_tools_batch()` 返回原始 `Vec<ToolResultItem>`——供边界测试直接检查 result/error 字段
- `register_mcp()` 由外部调用——Engine 从 Bus 订阅中提取 `node_online` payload 后注册

---

## 辅助函数

```rust
/// Build a Bus for testing.
fn test_bus() -> Bus {
    Bus::new(Duration::from_secs(10), Duration::from_secs(30), 128)
}

/// Path to the fixtures root (contains tools/ subdirectory).
fn fixtures_root() -> std::path::PathBuf {
    std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures")
}

/// CodeTidy remote MCP config.
fn codetidy_config() -> arf_mcp::config::RemoteConfig {
    arf_mcp::config::RemoteConfig {
        transport: "streamable-http".into(),
        url: "https://mcp.codetidy.dev".into(),
        timeout_secs: Some(30),
        headers: HashMap::new(),
        tls_ca_cert: None,
        retry: None,
    }
}

/// Subscribe to Bus, drain all messages until `node_online` for a given
/// namespace. Namespace is NOT a field in the payload — it is embedded in
/// `node_id` as `mcp/{namespace}`. Match by `node_id` prefix.
async fn wait_for_node_online(
    rx: &mut tokio::sync::broadcast::Receiver<arf_core::Message>,
    namespace: &str,
) -> (NodeId, Vec<McpToolInfo>) {
    let expected_prefix = format!("mcp/{namespace}");
    loop {
        let m = rx.recv().await.unwrap();
        let payload_node_id = m.payload["node_id"].as_str().unwrap_or("");
        if m.msg_type == "node_online" && payload_node_id == expected_prefix {
            let node_id = NodeId::new(payload_node_id);
            let tools: Vec<McpToolInfo> = m.payload["capabilities"]["tools"]
                .as_array()
                .unwrap()
                .iter()
                .map(|t| McpToolInfo {
                    name: t["name"].as_str().unwrap().into(),
                    description: t["description"].as_str().unwrap_or("").into(),
                })
                .collect();
            return (node_id, tools);
        }
    }
}
```

---

## 测试场景

### 角度覆盖

| # | 链路 | 测试内容 |
|---|------|---------|
| 1 | Local + MiniEngine | read_file 读真实文件 → ModelMessage |
| 2 | Local + MiniEngine | write_file → read_file 跨工具验证 |
| 3 | Local + MiniEngine | search_content 搜索返回匹配 |
| 4 | Local | **边界**: read_file 不存在的文件 → error status |
| 5 | Local | **边界**: write_file 自动创建父目录 |
| 6 | Local | **边界**: search_content 无匹配 → 空数组 |
| 7 | Local | **边界**: 写入含 Unicode/emoji/换行的内容 → 读回一致 |
| 8 | Remote + MiniEngine | 连接 CodeTidy → MiniEngine 注册 → call_tool |
| 9 | Remote + MiniEngine | base64 编码/解码往返 → ModelMessage |
| 10 | Remote + MiniEngine | hash 不同算法交叉验证（SHA256/MD5/SHA1） |
| 11 | Remote + MiniEngine | URL 编码/解码往返 |
| 12 | Remote + MiniEngine | JWT 解码已知 token → 结构化字段验证 |
| 13 | Remote | **边界**: 不存在的 tool → error status |
| 14 | Remote | **边界**: 空 params 工具（uuid）正确返回 |
| 15 | Remote | **边界**: 缺失必填参数 → 工具报错 |
| 16 | Remote | **边界**: 超大输入（base64 编码 ~10KB 文本） |
| 17 | Multi-ns + Engine | Local + Remote 共存，MiniEngine 按 namespace 路由 |
| 18 | Multi-ns + Engine | 两个 Local namespace 同名 tool → 路由不混淆 |
| 19 | ModelAdapter | tool_result_to_model_message 三种 status → 正确转换 |

### 测试代码

#### 场景 1-3: Local + MiniEngine + fixtures

```rust
// ═══════════════════════════════════════════════════════════════════
// MiniEngine + Local fixtures — 3 tests
// ═══════════════════════════════════════════════════════════════════

// [集成] MiniEngine: read_file fixture → ModelMessage
#[tokio::test]
async fn engine_local_read_file_to_model_message() {
    let bus = test_bus();
    let mut rx = bus.subscribe();

    let node = Arc::new(McpNode::local("fs", fixtures_root()).unwrap());
    node.connect(&bus).await.unwrap();
    let (node_id, tools) = wait_for_node_online(&mut rx, "fs").await;

    let mut engine = MiniEngine::new(bus, "s1").await;
    engine.register_mcp("fs", node_id, tools);

    let test_file = fixtures_root().join("tools/read_file/main.py");
    let messages = engine
        .call_tool("fs", "read_file", serde_json::json!({
            "path": test_file.to_str().unwrap(),
        }))
        .await
        .unwrap();

    assert_eq!(messages.len(), 1);
    let msg = &messages[0];
    assert_eq!(msg.role, "tool");
    assert!(msg.content.contains("#!/usr/bin/env python3"));
    assert_eq!(msg.tool_call_id.as_deref(), Some("c0"));
    assert_eq!(msg.name.as_deref(), Some("read_file"));
}

// [集成] MiniEngine: write_file → read_file 跨工具链
#[tokio::test]
async fn engine_local_write_then_read() {
    let bus = test_bus();
    let mut rx = bus.subscribe();

    let node = Arc::new(McpNode::local("fs", fixtures_root()).unwrap());
    node.connect(&bus).await.unwrap();
    let (node_id, tools) = wait_for_node_online(&mut rx, "fs").await;

    let mut engine = MiniEngine::new(bus, "s1").await;
    engine.register_mcp("fs", node_id, tools);

    let tmp_dir = std::env::temp_dir().join("arf_int_engine_wr");
    let _ = std::fs::remove_dir_all(&tmp_dir);
    std::fs::create_dir_all(&tmp_dir).unwrap();
    let file_path = tmp_dir.join("hello.txt");

    // Step 1: write
    let messages = engine
        .call_tool("fs", "write_file", serde_json::json!({
            "path": file_path.to_str().unwrap(),
            "content": "Hello from MiniEngine!",
        }))
        .await
        .unwrap();
    assert!(messages[0].content.contains("ok"));

    // Step 2: read back
    let messages = engine
        .call_tool("fs", "read_file", serde_json::json!({
            "path": file_path.to_str().unwrap(),
        }))
        .await
        .unwrap();
    assert!(messages[0].content.contains("Hello from MiniEngine!"));
}

// [集成] MiniEngine: search_content 搜索
#[tokio::test]
async fn engine_local_search_content() {
    let bus = test_bus();
    let mut rx = bus.subscribe();

    let node = Arc::new(McpNode::local("fs", fixtures_root()).unwrap());
    node.connect(&bus).await.unwrap();
    let (node_id, tools) = wait_for_node_online(&mut rx, "fs").await;

    let mut engine = MiniEngine::new(bus, "s1").await;
    engine.register_mcp("fs", node_id, tools);

    let messages = engine
        .call_tool("fs", "search_content", serde_json::json!({
            "pattern": "def main",
            "path": fixtures_root().join("tools").to_str().unwrap(),
        }))
        .await
        .unwrap();

    assert!(messages[0].content.contains("def main"));
}
```

#### 场景 4-7: Local 边界条件

```rust
// ═══════════════════════════════════════════════════════════════════
// Local 边界条件 — 4 tests
// ═══════════════════════════════════════════════════════════════════

// [边界] read_file 不存在的文件 → error status via ModelMessage
#[tokio::test]
async fn local_read_nonexistent_file_returns_error_model_message() {
    let bus = test_bus();
    let mut rx = bus.subscribe();

    let node = Arc::new(McpNode::local("fs", fixtures_root()).unwrap());
    node.connect(&bus).await.unwrap();
    let (node_id, tools) = wait_for_node_online(&mut rx, "fs").await;

    let mut engine = MiniEngine::new(bus, "s1").await;
    engine.register_mcp("fs", node_id, tools);

    let messages = engine
        .call_tool("fs", "read_file", serde_json::json!({
            "path": "/nonexistent/file.xyz",
        }))
        .await
        .unwrap();

    assert_eq!(messages[0].role, "tool");
    // Error status → ModelMessage content is {"error": "..."}
    assert!(messages[0].content.contains("error"));
    assert!(messages[0].content.contains("file not found"));
}

// [边界] write_file 自动创建父目录
#[tokio::test]
async fn local_write_creates_parent_dirs() {
    let bus = test_bus();
    let mut rx = bus.subscribe();

    let node = Arc::new(McpNode::local("fs", fixtures_root()).unwrap());
    node.connect(&bus).await.unwrap();
    let (node_id, tools) = wait_for_node_online(&mut rx, "fs").await;

    let mut engine = MiniEngine::new(bus, "s1").await;
    engine.register_mcp("fs", node_id, tools);

    let tmp = std::env::temp_dir().join("arf_int_deep_dirs");
    let _ = std::fs::remove_dir_all(&tmp);
    let deep_path = tmp.join("a/b/c/d/output.txt");

    let messages = engine
        .call_tool("fs", "write_file", serde_json::json!({
            "path": deep_path.to_str().unwrap(),
            "content": "deeply nested",
        }))
        .await
        .unwrap();

    assert!(messages[0].content.contains("ok"));
    assert!(deep_path.exists());
    assert_eq!(std::fs::read_to_string(&deep_path).unwrap(), "deeply nested");
}

// [边界] search_content 无匹配 → 空 matches
#[tokio::test]
async fn local_search_no_matches_returns_empty() {
    let bus = test_bus();
    let mut rx = bus.subscribe();

    let node = Arc::new(McpNode::local("fs", fixtures_root()).unwrap());
    node.connect(&bus).await.unwrap();
    let (node_id, tools) = wait_for_node_online(&mut rx, "fs").await;

    let mut engine = MiniEngine::new(bus, "s1").await;
    engine.register_mcp("fs", node_id, tools);

    let messages = engine
        .call_tool("fs", "search_content", serde_json::json!({
            "pattern": "ZZZZZ_NO_MATCH_ZZZZZ",
            "path": fixtures_root().join("tools").to_str().unwrap(),
        }))
        .await
        .unwrap();

    assert!(messages[0].content.contains("\"matches\": []"));
}

// [边界] Unicode + emoji + 换行 content → write → read 一致
#[tokio::test]
async fn local_unicode_emoji_newline_roundtrip() {
    let bus = test_bus();
    let mut rx = bus.subscribe();

    let node = Arc::new(McpNode::local("fs", fixtures_root()).unwrap());
    node.connect(&bus).await.unwrap();
    let (node_id, tools) = wait_for_node_online(&mut rx, "fs").await;

    let mut engine = MiniEngine::new(bus, "s1").await;
    engine.register_mcp("fs", node_id, tools);

    let tmp_dir = std::env::temp_dir().join("arf_int_unicode");
    let _ = std::fs::remove_dir_all(&tmp_dir);
    std::fs::create_dir_all(&tmp_dir).unwrap();
    let file_path = tmp_dir.join("unicode.txt");

    let content = "你好世界 🌍\nLine 2: \"quoted\"\nLine 3: \\backslash\\\nLine 4: \t tab";
    engine
        .call_tool("fs", "write_file", serde_json::json!({
            "path": file_path.to_str().unwrap(),
            "content": content,
        }))
        .await
        .unwrap();

    let messages = engine
        .call_tool("fs", "read_file", serde_json::json!({
            "path": file_path.to_str().unwrap(),
        }))
        .await
        .unwrap();

    assert!(messages[0].content.contains("你好世界"));
    assert!(messages[0].content.contains("\\\"quoted\\\""));
    assert!(messages[0].content.contains("\\\\backslash\\\\"));
}
```

#### 场景 8-12: Remote + CodeTidy + MiniEngine

```rust
// ═══════════════════════════════════════════════════════════════════
// MiniEngine + CodeTidy Remote — 5 tests
// ═══════════════════════════════════════════════════════════════════

// [集成] CodeTidy 连接 → MiniEngine 注册 → 发起 call_tool
#[tokio::test]
async fn engine_codetidy_connect_and_call() {
    let bus = test_bus();
    let mut rx = bus.subscribe();

    let node = McpNode::remote("codetidy", codetidy_config()).await.unwrap();
    node.connect(&bus).await.unwrap();
    let (node_id, tools) = wait_for_node_online(&mut rx, "codetidy").await;

    let mut engine = MiniEngine::new(bus, "s1").await;
    engine.register_mcp("codetidy", node_id, tools);

    let messages = engine
        .call_tool("codetidy", "codetidy_uppercase", serde_json::json!({
            "input": "hello world",
        }))
        .await
        .unwrap();

    assert_eq!(messages[0].role, "tool");
    assert!(messages[0].content.contains("HELLO WORLD"));
    assert_eq!(messages[0].name.as_deref(), Some("codetidy_uppercase"));
}

// [集成] base64 编码→解码往返
#[tokio::test]
async fn engine_codetidy_base64_roundtrip() {
    let bus = test_bus();
    let mut rx = bus.subscribe();

    let node = McpNode::remote("codetidy", codetidy_config()).await.unwrap();
    node.connect(&bus).await.unwrap();
    let (node_id, tools) = wait_for_node_online(&mut rx, "codetidy").await;

    let mut engine = MiniEngine::new(bus, "s1").await;
    engine.register_mcp("codetidy", node_id, tools);

    // Encode
    let messages = engine
        .call_tool("codetidy", "codetidy_base64_encode", serde_json::json!({
            "input": "Roundtrip test 往返测试!",
        }))
        .await
        .unwrap();
    let encoded: serde_json::Value = serde_json::from_str(&messages[0].content).unwrap();
    let encoded_str = encoded.as_str().unwrap();

    // Decode
    let messages = engine
        .call_tool("codetidy", "codetidy_base64_decode", serde_json::json!({
            "input": encoded_str,
        }))
        .await
        .unwrap();

    assert!(messages[0].content.contains("Roundtrip test"));
    assert!(messages[0].content.contains("往返测试"));
}

// [集成] hash 不同算法交叉验证 — 同一输入 SHA256/MD5 结果不同
#[tokio::test]
async fn engine_codetidy_hash_cross_validate() {
    let bus = test_bus();
    let mut rx = bus.subscribe();

    let node = McpNode::remote("codetidy", codetidy_config()).await.unwrap();
    node.connect(&bus).await.unwrap();
    let (node_id, tools) = wait_for_node_online(&mut rx, "codetidy").await;

    let mut engine = MiniEngine::new(bus, "s1").await;
    engine.register_mcp("codetidy", node_id, tools);

    // SHA256("test")
    let messages = engine
        .call_tool("codetidy", "codetidy_hash_generate", serde_json::json!({
            "input": "test",
            "algorithm": "sha256",
        }))
        .await
        .unwrap();
    let sha256: serde_json::Value = serde_json::from_str(&messages[0].content).unwrap();
    assert!(sha256.as_str().unwrap().to_lowercase().starts_with("9f86d081"));

    // MD5("test") — should be different
    let messages = engine
        .call_tool("codetidy", "codetidy_hash_generate", serde_json::json!({
            "input": "test",
            "algorithm": "md5",
        }))
        .await
        .unwrap();
    let md5: serde_json::Value = serde_json::from_str(&messages[0].content).unwrap();

    // Same input, different algorithm → different hash
    assert_ne!(sha256.as_str().unwrap(), md5.as_str().unwrap());
}

// [集成] URL 编码→解码往返
#[tokio::test]
async fn engine_codetidy_url_encode_decode_roundtrip() {
    let bus = test_bus();
    let mut rx = bus.subscribe();

    let node = McpNode::remote("codetidy", codetidy_config()).await.unwrap();
    node.connect(&bus).await.unwrap();
    let (node_id, tools) = wait_for_node_online(&mut rx, "codetidy").await;

    let mut engine = MiniEngine::new(bus, "s1").await;
    engine.register_mcp("codetidy", node_id, tools);

    let original = "hello world & more?key=value#fragment";
    let messages = engine
        .call_tool("codetidy", "codetidy_url_encode", serde_json::json!({
            "input": original,
        }))
        .await
        .unwrap();
    let encoded: serde_json::Value = serde_json::from_str(&messages[0].content).unwrap();
    let encoded_str = encoded.as_str().unwrap();
    assert!(encoded_str.contains("%20")); // space encoded

    let messages = engine
        .call_tool("codetidy", "codetidy_url_decode", serde_json::json!({
            "input": encoded_str,
        }))
        .await
        .unwrap();

    assert!(messages[0].content.contains("hello world & more"));
}

// [集成] JWT 解码已知 token → 结构化字段
#[tokio::test]
async fn engine_codetidy_jwt_decode_known_token() {
    let bus = test_bus();
    let mut rx = bus.subscribe();

    let node = McpNode::remote("codetidy", codetidy_config()).await.unwrap();
    node.connect(&bus).await.unwrap();
    let (node_id, tools) = wait_for_node_online(&mut rx, "codetidy").await;

    let mut engine = MiniEngine::new(bus, "s1").await;
    engine.register_mcp("codetidy", node_id, tools);

    // A well-known JWT with payload: {"sub":"1234567890","name":"John Doe","iat":1516239022}
    let jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c";
    let messages = engine
        .call_tool("codetidy", "codetidy_jwt_decode", serde_json::json!({
            "token": jwt,
        }))
        .await
        .unwrap();

    // JWT decode should return structured payload
    assert!(messages[0].content.contains("John Doe"));
    assert!(messages[0].content.contains("1234567890"));
}
```

#### 场景 13-16: Remote 边界条件

```rust
// ═══════════════════════════════════════════════════════════════════
// Remote 边界条件 — 4 tests
// ═══════════════════════════════════════════════════════════════════

// [边界] 不存在的 tool → error status → ModelMessage 含 error
#[tokio::test]
async fn remote_nonexistent_tool_returns_model_error() {
    let bus = test_bus();
    let mut rx = bus.subscribe();

    let node = McpNode::remote("codetidy", codetidy_config()).await.unwrap();
    node.connect(&bus).await.unwrap();
    let (node_id, tools) = wait_for_node_online(&mut rx, "codetidy").await;

    let mut engine = MiniEngine::new(bus, "s1").await;
    engine.register_mcp("codetidy", node_id, tools);

    let messages = engine
        .call_tool("codetidy", "this_tool_does_not_exist_xyz", serde_json::json!({}))
        .await
        .unwrap();

    assert!(messages[0].content.contains("error"));
    assert!(messages[0].content.contains("not found"));
}

// [边界] 空 params 工具（uuid_generate）正确返回
#[tokio::test]
async fn remote_empty_params_tool_works() {
    let bus = test_bus();
    let mut rx = bus.subscribe();

    let node = McpNode::remote("codetidy", codetidy_config()).await.unwrap();
    node.connect(&bus).await.unwrap();
    let (node_id, tools) = wait_for_node_online(&mut rx, "codetidy").await;

    let mut engine = MiniEngine::new(bus, "s1").await;
    engine.register_mcp("codetidy", node_id, tools);

    let messages = engine
        .call_tool("codetidy", "codetidy_uuid_generate", serde_json::json!({}))
        .await
        .unwrap();

    let uuid_str = &messages[0].content;
    assert!(uuid_str.len() >= 32, "expected UUID, got: {uuid_str}");
    // UUID v4: xxxxxxxx-xxxx-4xxx-xxxx-xxxxxxxxxxxx
    assert!(uuid_str.contains('-'), "UUID should contain dashes");
}

// [边界] 缺失必填参数 → 工具报错
#[tokio::test]
async fn remote_missing_required_param_errors() {
    let bus = test_bus();
    let mut rx = bus.subscribe();

    let node = McpNode::remote("codetidy", codetidy_config()).await.unwrap();
    node.connect(&bus).await.unwrap();
    let (node_id, tools) = wait_for_node_online(&mut rx, "codetidy").await;

    let mut engine = MiniEngine::new(bus, "s1").await;
    engine.register_mcp("codetidy", node_id, tools);

    // hash_generate requires "input" and "algorithm" — omit both
    let messages = engine
        .call_tool("codetidy", "codetidy_hash_generate", serde_json::json!({}))
        .await
        .unwrap();

    // hash without input should fail or return error
    // The tool itself may return an error or the MCP server rejects it
    assert!(
        messages[0].content.contains("error")
            || messages[0].content.contains("missing")
            || messages[0].content.contains("required"),
        "expected error, got: {}",
        messages[0].content
    );
}

// [边界] 超大输入（~10KB base64 编码）
#[tokio::test]
async fn remote_large_input_base64() {
    let bus = test_bus();
    let mut rx = bus.subscribe();

    let node = McpNode::remote("codetidy", codetidy_config()).await.unwrap();
    node.connect(&bus).await.unwrap();
    let (node_id, tools) = wait_for_node_online(&mut rx, "codetidy").await;

    let mut engine = MiniEngine::new(bus, "s1").await;
    engine.register_mcp("codetidy", node_id, tools);

    // Generate ~10KB of text
    let large_text = "The quick brown fox jumps over the lazy dog. ".repeat(200);
    assert!(large_text.len() > 8000); // ~9000 chars

    let messages = engine
        .call_tool("codetidy", "codetidy_base64_encode", serde_json::json!({
            "input": large_text,
        }))
        .await
        .unwrap();

    // Should succeed with a long base64 string
    let encoded: serde_json::Value = serde_json::from_str(&messages[0].content).unwrap();
    assert!(encoded.as_str().unwrap().len() > large_text.len());

    // Verify roundtrip: decode the encoded result
    let messages = engine
        .call_tool("codetidy", "codetidy_base64_decode", serde_json::json!({
            "input": encoded.as_str().unwrap(),
        }))
        .await
        .unwrap();

    assert!(messages[0].content.contains("The quick brown fox"));
    assert!(messages[0].content.contains("lazy dog"));
}
```

#### 场景 17-18: Multi-namespace + MiniEngine 路由

```rust
// ═══════════════════════════════════════════════════════════════════
// Multi-namespace + MiniEngine 路由 — 2 tests
// ═══════════════════════════════════════════════════════════════════

// [集成] Local + Remote 共存，MiniEngine 按 namespace 路由
#[tokio::test]
async fn engine_routes_local_and_remote_by_namespace() {
    let bus = test_bus();
    let mut rx = bus.subscribe();

    // Local MCP
    let local_node = Arc::new(McpNode::local("files", fixtures_root()).unwrap());
    local_node.connect(&bus).await.unwrap();
    let (local_nid, local_tools) = wait_for_node_online(&mut rx, "files").await;

    // Remote MCP (CodeTidy)
    let remote_node = McpNode::remote("ct", codetidy_config()).await.unwrap();
    remote_node.connect(&bus).await.unwrap();
    let (remote_nid, remote_tools) = wait_for_node_online(&mut rx, "ct").await;

    let mut engine = MiniEngine::new(bus, "s1").await;
    engine.register_mcp("files", local_nid, local_tools);
    engine.register_mcp("ct", remote_nid, remote_tools);

    // Call local tool
    let test_file = fixtures_root().join("tools/read_file/main.py");
    let local_resp = engine
        .call_tool("files", "read_file", serde_json::json!({
            "path": test_file.to_str().unwrap(),
        }))
        .await
        .unwrap();
    assert!(local_resp[0].content.contains("#!/usr/bin/env python3"));

    // Call remote tool — different namespace, different result shape
    let remote_resp = engine
        .call_tool("ct", "codetidy_uppercase", serde_json::json!({
            "input": "hello",
        }))
        .await
        .unwrap();
    assert!(remote_resp[0].content.contains("HELLO"));

    // Both should be accessible via engine.namespaces()
    let nss = engine.namespaces();
    assert!(nss.contains(&"files"));
    assert!(nss.contains(&"ct"));
}

// [集成] 两个 Local namespace 同名 tool → 路由不混淆
#[tokio::test]
async fn engine_same_tool_name_different_namespace() {
    let bus = test_bus();
    let mut rx = bus.subscribe();

    // Create two identical roots with echo tools, different namespaces
    let id = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let root_a = std::env::temp_dir().join(format!("arf_int_ns_a_{id}"));
    let root_b = std::env::temp_dir().join(format!("arf_int_ns_b_{id}"));
    for (root, ns_tag) in [(&root_a, "alpha"), (&root_b, "beta")] {
        let _ = std::fs::remove_dir_all(root);
        let tool_dir = root.join("tools/echo");
        std::fs::create_dir_all(&tool_dir).unwrap();
        std::fs::write(
            tool_dir.join("tool.toml"),
            format!("name = \"echo\"\ndescription = \"Echo in {ns_tag}\"\nruntime = \"python\"\nentrypoint = \"main.py\"\n"),
        )
        .unwrap();
        std::fs::write(
            tool_dir.join("main.py"),
            format!("import sys, json\nparams = json.loads(sys.stdin.read())\nparams[\"ns\"] = \"{ns_tag}\"\nprint(json.dumps(params))\n"),
        )
        .unwrap();
    }

    let node_a = Arc::new(McpNode::local("alpha", root_a).unwrap());
    let node_b = Arc::new(McpNode::local("beta", root_b).unwrap());
    node_a.connect(&bus).await.unwrap();
    node_b.connect(&bus).await.unwrap();

    let (nid_a, tools_a) = wait_for_node_online(&mut rx, "alpha").await;
    let (nid_b, tools_b) = wait_for_node_online(&mut rx, "beta").await;

    let mut engine = MiniEngine::new(bus, "s1").await;
    engine.register_mcp("alpha", nid_a, tools_a);
    engine.register_mcp("beta", nid_b, tools_b);

    // Call echo on alpha
    let msg_a = engine
        .call_tool("alpha", "echo", serde_json::json!({"msg": "a"}))
        .await
        .unwrap();
    assert!(msg_a[0].content.contains("\"alpha\""));

    // Call echo on beta — should return "beta", not "alpha"
    let msg_b = engine
        .call_tool("beta", "echo", serde_json::json!({"msg": "b"}))
        .await
        .unwrap();
    assert!(msg_b[0].content.contains("\"beta\""));
}
```

#### 场景 19: ModelAdapter 转换

```rust
// ═══════════════════════════════════════════════════════════════════
// ModelAdapter 转换验证 — 1 test
// ═══════════════════════════════════════════════════════════════════

// [类型] tool_result_to_model_message 三种 status → 正确转换
#[test]
fn tool_result_to_model_message_all_statuses() {
    use arf_mcp::types::ToolResultItem;
    use arf_model_adapter::convert::tool_result_to_model_message;

    // success
    let success = ToolResultItem {
        call_id: "c0".into(),
        name: "echo".into(),
        status: "success".into(),
        result: serde_json::json!({"ok": true, "data": 42}),
        error: None,
    };
    let msg = tool_result_to_model_message(&success);
    assert_eq!(msg.role, "tool");
    assert_eq!(msg.tool_call_id.as_deref(), Some("c0"));
    assert_eq!(msg.name.as_deref(), Some("echo"));
    assert!(msg.content.contains("42"));

    // error
    let error = ToolResultItem {
        call_id: "c1".into(),
        name: "fail".into(),
        status: "error".into(),
        result: serde_json::Value::Null,
        error: Some("something went wrong".into()),
    };
    let msg = tool_result_to_model_message(&error);
    assert!(msg.content.contains("error"));
    assert!(msg.content.contains("something went wrong"));

    // cancelled
    let cancelled = ToolResultItem {
        call_id: "c2".into(),
        name: "cancelled_tool".into(),
        status: "cancelled".into(),
        result: serde_json::Value::Null,
        error: Some("cancelled: dependency c1 failed".into()),
    };
    let msg = tool_result_to_model_message(&cancelled);
    assert!(msg.content.contains("error"));
    assert!(msg.content.contains("cancelled"));
    assert!(msg.content.contains("c1"));
}
```

---

## 依赖更新

`crates/arf-mcp/Cargo.toml` 的 dev-dependencies 需添加 `arf-model-adapter`：

```toml
[dev-dependencies]
tokio = { version = "1", features = ["rt", "macros"] }
tempfile = "3"
arf-model-adapter = { path = "../arf-model-adapter" }
```

---

## 验证命令

```bash
# 仅集成测试（需要网络 — CodeTidy）
. "$HOME/.cargo/env" && cargo test -p arf-mcp --test integration_tests

# 跳过远程测试（仅本地）
. "$HOME/.cargo/env" && cargo test -p arf-mcp --test integration_tests -- local

# 全 workspace
. "$HOME/.cargo/env" && cargo test --workspace
```

---

## 实施记录

### 1. `convert` 模块私有 → 外部测试无法 import

**编译错误**：
```
error[E0603]: module `convert` is private
  --> crates/arf-mcp/tests/integration_tests.rs:18:24
   |
18 | use arf_model_adapter::convert::tool_result_to_model_message;
   |                        ^^^^^^^ private module
```

**发现**：`arf-model-adapter/src/lib.rs` 中 `mod convert;` 而非 `pub mod convert;`。集成测试在 `tests/` 下属于外部 crate，只能访问公开 API。`tool_result_to_model_message()` 作为 MCP→ModelAdapter 边界函数，应是公开 API 的一部分。

**修复**：`mod convert;` → `pub mod convert;`。不增加 `pub use` 重导出——调用方应通过 `arf_model_adapter::convert::tool_result_to_model_message` 全路径访问，保持 API 的显式性。

### 2. `node_online` payload 无 `namespace` 字段 → `wait_for_node_online` 永久挂起

**测试挂起**：所有 local 测试在 `wait_for_node_online()` 处超时（15 秒无输出）。

**调试**：添加 `eprintln!` 发现 `connect()` 成功返回但 `rx.recv()` 永远收不到匹配的 `node_online`。

**根因**：设计文档假设 `node_online.payload` 有顶层 `namespace` 字段：
```rust
// 文档中错误的假设
m.payload["namespace"].as_str() == Some(namespace)
```

实际 `NodeInfo` 结构的序列化不包含 `namespace`：
```rust
pub struct NodeInfo {
    pub node_id: NodeId,    // "mcp/{namespace}" — namespace 仅在此
    pub node_type: String,  // "mcp"
    pub capabilities: Value,
    pub online_since: u64,
}
```

**修复**：改为匹配 `node_id` 前缀（`node_id` 格式为 `mcp/{namespace}`）：
```rust
// 错误 — NodeInfo 无 namespace 字段
m.payload["namespace"].as_str() == Some(namespace)

// 正确 — 从 node_id 提取 namespace
let expected = format!("mcp/{namespace}");
let id = m.payload["node_id"].as_str().unwrap_or("");
m.msg_type == "node_online" && id == expected
```

### 3. CodeTidy 输出含推广 footer → base64 往返失败

**测试失败**：`remote_engine_codetidy_base64_roundtrip`、`remote_large_input_base64` 断言 `contains("Roundtrip test")` 失败。

**调试输出**：
```
"Um91bmR0cmlwIHRlc3Qg5b6A6L+U5rWL6K+VIQ==\n\n---\nPowered by CodeTidy..."
```

CodeTidy 在每个工具输出后追加：
```
\n\n---
Powered by CodeTidy — free developer tools at https://codetidy.dev
Full interactive version: https://codetidy.dev/{tool}
```

base64 编码工具输出含 footer 文本，导致解码时 base64 字符串包含无效字符。base64 往返测试失败。

**修复**：提取第一行作为纯工具输出：
```rust
let raw_output = encoded.as_str().unwrap();
let encoded_str = raw_output.lines().next().unwrap();
```

影响 3 个测试：base64 往返、大输入 base64、URL 编码（URL 版 footer 含 URL 字符但不影响 `%20` 匹配，故未失败）。JWT 测试需额外修复（见 #4）。

### 4. `codetidy_jwt_decode` 参数名为 `input` 而非 `token`

**测试失败**：
```
MCP error -32602: Input validation error: Invalid arguments for tool codetidy_jwt_decode:
  { "path": ["input"], "message": "Required" }
```

**根因**：CodeTidy 所有工具的输入参数统一使用 `"input"` 名称（`codetidy_uppercase`、`codetidy_base64_encode` 等均是 `{"input": "..."}`）。设计文档中 JWT 测试用了 `"token"`，不符合 CodeTidy 的参数约定。

**修复**：`{"token": jwt}` → `{"input": jwt}`。

### 5. `Receiver` 类型未从 `arf_bus` 公开

**编译错误**：
```
error[E0425]: cannot find type `Receiver` in crate `arf_bus`
```

**发现**：`bus.subscribe()` 返回 `tokio::sync::broadcast::Receiver<Message>`，但 `Receiver` 别名未在 `arf_bus` 中公开。`arf_bus` 只 `pub use connection::NodeHandle;`。

**修复**：使用完整类型路径 `tokio::sync::broadcast::Receiver<arf_core::Message>`。

---

**最终结果**：`cargo test --workspace` — 19 个集成测试全部通过，全 workspace 零失败。
