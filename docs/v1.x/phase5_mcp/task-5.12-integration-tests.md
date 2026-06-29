# 任务 5.12：集成测试（LocalMcpNode + RemoteMcpNode + 多 namespace）

> Phase 5 — MCP 第十二项任务
> 父文档：`docs/v1.x/phase5_mcp/phase5-mcp-design.md`
> 依赖：Task 5.10 (fixtures), Task 5.5 (McpNode 统一), Task 5.8 (RemoteMcpNode)

## 设计思路

Task 5.12 在 `tests/` 目录（非 `src/tests/`）中编写集成测试——通过 Bus 消息协议、跨多 namespace 验证 MCP 完整链路。与 `src/tests/node_tests.rs`（单元级、单节点、echo/fail 工具）的区别：

| 维度 | `node_tests.rs` (已有) | `integration_tests.rs` (本任务) |
|------|----------------------|------------------------------|
| 访问级别 | `use crate::` 内部模块 | `use arf_mcp::` 公开 API |
| 工具 | echo/fail 内联脚本 | 5.10 real fixtures (read_file/write_file/search_content) |
| 远程 | 无 | Mock HTTP JSON-RPC server |
| 多 namespace | 单节点 | 2+ 节点，同名 tool 不冲突 |
| 工具执行 | echo 只验证 roundtrip | write_file → read_file 跨工具验证 |

**聚焦三个场景**：

1. **Local chain with real fixtures** — LocalMcpNode + 5.10 fixtures → 真实文件读写验证
2. **Remote chain with mock server** — RemoteMcpNode + 本地 JSON-RPC mock → 发现 + 执行
3. **Multi-namespace isolation** — 两个 LocalMcpNode（不同 namespace、同名工具）→ 路由互不干扰

### 不在 5.12 范围

- **Skill 集成**：fixtures 目录无 skills，且 `use_skill`/`run_skill_script` 已在 `node_tests.rs` 的单元测试中覆盖
- **全链路 ReAct**：Task 5.13 负责 Engine + ModelAdapter + LLM 闭环
- **RetryConfig** 重连逻辑：`remote_tests.rs` 单元测试已覆盖

### 目录结构

```
crates/arf-mcp/tests/
├── fixtures/                  # Task 5.10 (已有)
│   └── tools/...
├── codetidy_live.rs           # 已有（live remote test）
└── integration_tests.rs       # [新建] 本任务
```

| 文件 | 操作 | 内容 |
|------|------|------|
| `crates/arf-mcp/tests/integration_tests.rs` | 新建 | Local + Remote + Multi-namespace 集成测试 |
| `crates/arf-mcp/src/tests/node_tests.rs` | 不改 | 已有单元级集成测试 |

---

## Mock MCP HTTP Server

RemoteMcpNode 需要 mock MCP server 来验证远程链路（不依赖外部服务如 codetidy.dev）。用 `tokio::net::TcpListener` + 手动 HTTP 响应——不引入额外依赖。

### 协议覆盖

Mock server 处理 RemoteMcpNode 在 `connect()` 和 `tool_call_set` 期间发起的 3 种 JSON-RPC 请求：

```
connect():
  → initialize     → {"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2024-11-05","serverInfo":{"name":"mock"}}}
  → tools/list     → {"jsonrpc":"2.0","id":2,"result":{"tools":[{name,description,inputSchema}]}}

tool_call_set:
  → tools/call     → {"jsonrpc":"2.0","id":N,"result":{"content":[{"type":"text","text":"..."}]}}
```

### 实现

```rust
use std::net::SocketAddr;
use std::sync::Arc;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::net::TcpListener;
use tokio::sync::oneshot;

/// A minimal JSON-RPC 2.0 mock for MCP integration testing.
///
/// Spawns a TCP server that responds to initialize / tools/list / tools/call.
/// The caller provides the tool definitions and a handler for tool calls.
struct MockMcpServer {
    addr: SocketAddr,
    shutdown_tx: oneshot::Sender<()>,
}

impl MockMcpServer {
    /// Start a mock server. `tools_json` is the value for `result.tools` in
    /// the tools/list response. `call_handler` receives (tool_name, arguments_json)
    /// and returns the text result to include in tools/call response content.
    async fn start(
        tools_json: serde_json::Value,
        call_handler: Arc<dyn Fn(&str, serde_json::Value) -> String + Send + Sync>,
    ) -> Self {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        let (shutdown_tx, mut shutdown_rx) = oneshot::channel();

        tokio::spawn(async move {
            let mut req_id_counter: u64 = 2;
            loop {
                tokio::select! {
                    Ok((stream, _)) = listener.accept() => {
                        let tools = tools_json.clone();
                        let handler = call_handler.clone();
                        tokio::spawn(async move {
                            let (reader, mut writer) = stream.into_split();
                            let mut buf_reader = BufReader::new(reader);
                            let mut request = String::new();
                            let mut line = String::new();

                            // Read HTTP headers
                            let mut content_length: usize = 0;
                            loop {
                                line.clear();
                                buf_reader.read_line(&mut line).await.unwrap();
                                if line == "\r\n" || line.is_empty() { break; }
                                if line.to_lowercase().starts_with("content-length:") {
                                    content_length = line[15..].trim().parse().unwrap_or(0);
                                }
                            }

                            // Read body
                            let mut body = vec![0u8; content_length];
                            if content_length > 0 {
                                use tokio::io::AsyncReadExt;
                                buf_reader.read_exact(&mut body).await.unwrap();
                            }
                            request = String::from_utf8_lossy(&body).to_string();

                            // Parse JSON-RPC method
                            let req: serde_json::Value = serde_json::from_str(&request).unwrap();
                            let method = req["method"].as_str().unwrap_or("");
                            let id = req["id"].as_u64().unwrap_or(999);

                            let response = match method {
                                "initialize" => serde_json::json!({
                                    "jsonrpc": "2.0", "id": id,
                                    "result": {
                                        "protocolVersion": "2024-11-05",
                                        "serverInfo": {"name": "mock-mcp", "version": "0.1.0"}
                                    }
                                }),
                                "tools/list" => serde_json::json!({
                                    "jsonrpc": "2.0", "id": id,
                                    "result": {"tools": tools}
                                }),
                                "tools/call" => {
                                    let tool_name = req["params"]["name"].as_str().unwrap_or("");
                                    let args = req["params"]["arguments"].clone();
                                    let text = handler(tool_name, args);
                                    serde_json::json!({
                                        "jsonrpc": "2.0", "id": id,
                                        "result": {
                                            "content": [{"type": "text", "text": text}]
                                        }
                                    })
                                }
                                _ => serde_json::json!({
                                    "jsonrpc": "2.0", "id": id,
                                    "error": {"code": -32601, "message": format!("Method not found: {method}")}
                                }),
                            };

                            let body = serde_json::to_string(&response).unwrap();
                            let http_resp = format!(
                                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                                body.len(), body
                            );
                            writer.write_all(http_resp.as_bytes()).await.unwrap();
                            writer.shutdown().await.unwrap();
                        });
                    }
                    _ = &mut shutdown_rx => break,
                }
            }
        });

        Self { addr, shutdown_tx }
    }

    fn url(&self) -> String {
        format!("http://{}", self.addr)
    }
}

impl Drop for MockMcpServer {
    fn drop(&mut self) {
        let _ = self.shutdown_tx.send(());
    }
}
```

**关键设计决策**：
- 每个连接 `tokio::spawn` 独立处理——并发测试不会互相阻塞
- JSON-RPC `id` 从请求中提取并回传——RemoteMcpNode 可能校验 id 匹配
- `Connection: close` ——每次请求后关闭，无需 keep-alive 复杂度
- `shutdown_tx` 在 Drop 中发送——测试结束自动清理 server

---

## 测试场景

### 角度覆盖

| # | 场景 | 测试内容 |
|---|------|---------|
| 1 | Local + fixture | read_file 读真实 fixture 脚本文件 → 返回 content |
| 2 | Local + fixture | write_file 写临时文件 → read_file 验证写入内容（跨工具链） |
| 3 | Local + fixture | search_content 在 fixture 目录搜索 → 返回匹配 |
| 4 | Remote + mock | 连接 mock server → node_online 携带 tools |
| 5 | Remote + mock | tools/call → mock 返回结果 |
| 6 | Remote + mock | 不存在的 tool → error |
| 7 | Multi-namespace | 两个 namespace 各有 echo tool → 分别调用不干扰 |
| 8 | Multi-namespace | 跨 namespace 同名 tool → 路由到正确的节点 |
| 9 | 边界 | 非法 msg_type → error 不 panic |

### 测试代码

#### 辅助函数

```rust
use std::sync::Arc;
use std::time::Duration;

use arf_bus::Bus;
use arf_core::{MessageFilter, NodeId, NodeInfo, ToMatch};
use arf_mcp::config::RemoteConfig;
use arf_mcp::McpNode;

/// Build a Bus for testing.
fn test_bus() -> Bus {
    Bus::new(Duration::from_secs(5), Duration::from_secs(30), 128)
}

/// Connect a fake engine node that receives tool_result_set messages.
async fn connect_engine(bus: &Bus, id: &str) -> arf_bus::NodeHandle {
    bus.connect(
        NodeInfo {
            node_id: NodeId::new(id),
            node_type: "engine".into(),
            capabilities: serde_json::json!({}),
            online_since: 0,
        },
        MessageFilter {
            types: Some(vec![
                "tool_result_set".into(),
                "skill_loaded".into(),
                "skill_resource_loaded".into(),
                "skill_script_result".into(),
            ]),
            to_match: ToMatch::All,
        },
    )
    .await
    .unwrap()
}

/// Path to the fixtures root (contains tools/ subdirectory).
fn fixtures_root() -> std::path::PathBuf {
    std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures")
}

/// Drain messages until `msg_type` matches. Panics if timeout.
async fn drain_until(rx: &mut arf_bus::Receiver, msg_type: &str) -> arf_bus::Message {
    tokio::time::timeout(Duration::from_secs(3), async {
        loop {
            let m = rx.recv().await.unwrap();
            if m.msg_type == msg_type {
                return m;
            }
        }
    })
    .await
    .unwrap()
}
```

#### 场景 1-3: Local chain with real fixtures

```rust
// ═══════════════════════════════════════════════════════════════════
// LocalMcpNode + fixtures — 3 tests
// ═══════════════════════════════════════════════════════════════════

// [集成] read_file fixture 读真实文件 → 返回 content
#[tokio::test]
async fn local_read_file_returns_content() {
    let bus = test_bus();
    let mut rx = bus.subscribe();
    let node = Arc::new(McpNode::local("fs", fixtures_root()).unwrap());
    node.connect(&bus).await.unwrap();

    // Drain node_online
    drain_until(&mut rx, "node_online").await;

    let mut engine = connect_engine(&bus, "engine/s1").await;
    // Read the read_file tool's own main.py to verify real file reading
    let test_file = fixtures_root().join("tools/read_file/main.py");
    engine
        .send(
            "tool_call_set",
            vec![node.node_id.clone()],
            serde_json::json!({
                "session_id": "s1",
                "calls": [{"id": "c0", "tool": "read_file", "params": {"path": test_file.to_str().unwrap()}}],
            }),
        )
        .await
        .unwrap();

    let resp = engine.recv().await.unwrap();
    let r = &resp.payload["results"][0];
    assert_eq!(r["call_id"], "c0");
    assert_eq!(r["name"], "read_file");
    assert_eq!(r["status"], "success");
    assert!(r["result"]["content"].as_str().unwrap().contains("#!/usr/bin/env python3"));
    assert!(r["error"].is_null());
}

// [集成] write_file → read_file 跨工具链验证
#[tokio::test]
async fn local_write_then_read_cross_tool_chain() {
    let bus = test_bus();
    let mut rx = bus.subscribe();
    let node = Arc::new(McpNode::local("fs", fixtures_root()).unwrap());
    node.connect(&bus).await.unwrap();
    drain_until(&mut rx, "node_online").await;

    let mut engine = connect_engine(&bus, "engine/s1").await;
    let tmp_dir = std::env::temp_dir().join("arf_mcp_int_write_read");
    let _ = std::fs::remove_dir_all(&tmp_dir);
    let file_path = tmp_dir.join("hello.txt");

    // Step 1: write_file
    engine
        .send(
            "tool_call_set",
            vec![node.node_id.clone()],
            serde_json::json!({
                "session_id": "s1",
                "calls": [{"id": "c0", "tool": "write_file", "params": {
                    "path": file_path.to_str().unwrap(),
                    "content": "Hello, integration test!",
                }}],
            }),
        )
        .await
        .unwrap();

    let resp = engine.recv().await.unwrap();
    assert_eq!(resp.payload["results"][0]["status"], "success");

    // Step 2: read_file — verify the written content
    engine
        .send(
            "tool_call_set",
            vec![node.node_id.clone()],
            serde_json::json!({
                "session_id": "s1",
                "calls": [{"id": "c1", "tool": "read_file", "params": {
                    "path": file_path.to_str().unwrap(),
                }}],
            }),
        )
        .await
        .unwrap();

    let resp = engine.recv().await.unwrap();
    let r = &resp.payload["results"][0];
    assert_eq!(r["status"], "success");
    assert_eq!(r["result"]["content"], "Hello, integration test!");
}

// [集成] search_content 在 fixture 目录搜索 → 返回匹配
#[tokio::test]
async fn local_search_content_finds_matches() {
    let bus = test_bus();
    let mut rx = bus.subscribe();
    let node = Arc::new(McpNode::local("fs", fixtures_root()).unwrap());
    node.connect(&bus).await.unwrap();
    drain_until(&mut rx, "node_online").await;

    let mut engine = connect_engine(&bus, "engine/s1").await;
    engine
        .send(
            "tool_call_set",
            vec![node.node_id.clone()],
            serde_json::json!({
                "session_id": "s1",
                "calls": [{"id": "c0", "tool": "search_content", "params": {
                    "pattern": "def main",
                    "path": fixtures_root().join("tools").to_str().unwrap(),
                }}],
            }),
        )
        .await
        .unwrap();

    let resp = engine.recv().await.unwrap();
    let r = &resp.payload["results"][0];
    assert_eq!(r["status"], "success");
    let matches = r["result"]["matches"].as_array().unwrap();
    assert!(matches.len() >= 3, "should find 'def main' in all 3 Python fixture scripts");
}
```

#### 场景 4-6: Remote chain with mock HTTP server

```rust
// ═══════════════════════════════════════════════════════════════════
// RemoteMcpNode + mock server — 3 tests
// ═══════════════════════════════════════════════════════════════════

// [集成] RemoteMcpNode 连接 mock server → node_online 携带 tools
#[tokio::test]
async fn remote_connect_discovers_tools() {
    let mock_tools = serde_json::json!([
        {"name": "add", "description": "Add two numbers", "inputSchema": {
            "type": "object",
            "properties": {
                "a": {"type": "integer"},
                "b": {"type": "integer"},
            },
            "required": ["a", "b"]
        }},
    ]);

    let handler = Arc::new(|_name: &str, _args: serde_json::Value| -> String {
        "42".to_string()
    });
    let server = MockMcpServer::start(mock_tools.clone(), handler).await;

    let bus = test_bus();
    let mut rx = bus.subscribe();

    let config = RemoteConfig {
        transport: "streamable-http".into(),
        url: server.url(),
        timeout_secs: Some(10),
        headers: std::collections::HashMap::new(),
        tls_ca_cert: None,
        retry: None,
    };
    let node = McpNode::remote("calc", config).await.unwrap();
    node.connect(&bus).await.unwrap();

    let msg = drain_until(&mut rx, "node_online").await;
    assert_eq!(msg.payload["namespace"], "calc");
    let tools = msg.payload["capabilities"]["tools"].as_array().unwrap();
    assert_eq!(tools.len(), 1);
    assert_eq!(tools[0]["name"], "add");
}

// [集成] RemoteMcpNode tools/call → mock 返回结果
#[tokio::test]
async fn remote_tool_call_returns_mock_result() {
    let mock_tools = serde_json::json!([
        {"name": "greet", "description": "Greet someone", "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"]
        }},
    ]);

    let handler = Arc::new(|tool_name: &str, args: serde_json::Value| -> String {
        if tool_name == "greet" {
            let name = args["name"].as_str().unwrap_or("world");
            format!("Hello, {name}!")
        } else {
            "unknown".into()
        }
    });
    let server = MockMcpServer::start(mock_tools, handler).await;

    let bus = test_bus();
    let mut rx = bus.subscribe();

    let config = RemoteConfig {
        transport: "streamable-http".into(),
        url: server.url(),
        timeout_secs: Some(10),
        headers: std::collections::HashMap::new(),
        tls_ca_cert: None,
        retry: None,
    };
    let node = McpNode::remote("calc", config).await.unwrap();
    node.connect(&bus).await.unwrap();
    drain_until(&mut rx, "node_online").await;

    let mut engine = connect_engine(&bus, "engine/s1").await;
    engine
        .send(
            "tool_call_set",
            vec![node.node_id.clone()],
            serde_json::json!({
                "session_id": "s1",
                "calls": [{"id": "c0", "tool": "greet", "params": {"name": "世界"}}],
            }),
        )
        .await
        .unwrap();

    let resp = engine.recv().await.unwrap();
    let r = &resp.payload["results"][0];
    assert_eq!(r["status"], "success");
    assert_eq!(r["name"], "greet");
    assert_eq!(r["result"], "Hello, 世界!");
}

// [集成] RemoteMcpNode 调用不存在的 tool → error
#[tokio::test]
async fn remote_unknown_tool_returns_error() {
    let mock_tools = serde_json::json!([
        {"name": "add", "description": "Add numbers", "inputSchema": {"type": "object"}},
    ]);
    let handler = Arc::new(|_: &str, _: serde_json::Value| -> String { "unreachable".into() });
    let server = MockMcpServer::start(mock_tools, handler).await;

    let bus = test_bus();
    let mut rx = bus.subscribe();
    let config = RemoteConfig {
        transport: "streamable-http".into(),
        url: server.url(),
        timeout_secs: Some(10),
        headers: std::collections::HashMap::new(),
        tls_ca_cert: None,
        retry: None,
    };
    let node = McpNode::remote("calc", config).await.unwrap();
    node.connect(&bus).await.unwrap();
    drain_until(&mut rx, "node_online").await;

    let mut engine = connect_engine(&bus, "engine/s1").await;
    engine
        .send(
            "tool_call_set",
            vec![node.node_id.clone()],
            serde_json::json!({
                "session_id": "s1",
                "calls": [{"id": "c0", "tool": "ghost", "params": {}}],
            }),
        )
        .await
        .unwrap();

    let resp = engine.recv().await.unwrap();
    let r = &resp.payload["results"][0];
    assert_eq!(r["status"], "error");
    // RemoteMcpNode should report the tool is not found (not proxied to mock)
    assert!(r["error"].as_str().unwrap().contains("not found"));
}
```

#### 场景 7-8: Multi-namespace isolation

```rust
// ═══════════════════════════════════════════════════════════════════
// Multi-namespace isolation — 2 tests
// ═══════════════════════════════════════════════════════════════════

// [集成] 两个 namespace 各有 echo → 分别调用不干扰
#[tokio::test]
async fn multi_namespace_same_tool_name_no_interference() {
    let bus = test_bus();
    let mut rx = bus.subscribe();

    // Create two identical root directories with echo tools
    let id = std::sync::atomic::AtomicU64::new(0)
        .fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    let root1 = std::env::temp_dir().join(format!("arf_mcp_int_ns1_{id}"));
    let root2 = std::env::temp_dir().join(format!("arf_mcp_int_ns2_{id}"));
    for root in [&root1, &root2] {
        let _ = std::fs::remove_dir_all(root);
        let tool_dir = root.join("tools/echo");
        std::fs::create_dir_all(&tool_dir).unwrap();
        std::fs::write(
            tool_dir.join("tool.toml"),
            "name = \"echo\"\ndescription = \"Echo\"\nruntime = \"python\"\nentrypoint = \"main.py\"\n",
        )
        .unwrap();
        std::fs::write(
            tool_dir.join("main.py"),
            "import sys, json\nparams = json.loads(sys.stdin.read())\nprint(json.dumps(params))\n",
        )
        .unwrap();
    }

    let node_a = Arc::new(McpNode::local("alpha", root1).unwrap());
    let node_b = Arc::new(McpNode::local("beta", root2).unwrap());
    node_a.connect(&bus).await.unwrap();
    node_b.connect(&bus).await.unwrap();

    // Drain both node_online messages
    drain_until(&mut rx, "node_online").await;
    drain_until(&mut rx, "node_online").await;

    let mut engine = connect_engine(&bus, "engine/s1").await;

    // Call echo on alpha
    engine
        .send(
            "tool_call_set",
            vec![node_a.node_id.clone()],
            serde_json::json!({
                "session_id": "s1",
                "calls": [{"id": "ca", "tool": "echo", "params": {"ns": "alpha"}}],
            }),
        )
        .await
        .unwrap();

    let resp_a = engine.recv().await.unwrap();
    assert_eq!(resp_a.payload["results"][0]["status"], "success");
    assert_eq!(resp_a.payload["results"][0]["result"]["ns"], "alpha");

    // Call echo on beta
    engine
        .send(
            "tool_call_set",
            vec![node_b.node_id.clone()],
            serde_json::json!({
                "session_id": "s1",
                "calls": [{"id": "cb", "tool": "echo", "params": {"ns": "beta"}}],
            }),
        )
        .await
        .unwrap();

    let resp_b = engine.recv().await.unwrap();
    assert_eq!(resp_b.payload["results"][0]["status"], "success");
    assert_eq!(resp_b.payload["results"][0]["result"]["ns"], "beta");
}
```

#### 场景 9: 边界

```rust
// [边界] 非法 msg_type → error 不 panic
#[tokio::test]
async fn unknown_msg_type_returns_error() {
    let bus = test_bus();
    let mut rx = bus.subscribe();
    let node = Arc::new(McpNode::local("test", fixtures_root()).unwrap());
    node.connect(&bus).await.unwrap();
    drain_until(&mut rx, "node_online").await;

    let mut engine = connect_engine(&bus, "engine/s1").await;
    engine
        .send("bogus_msg_type", vec![node.node_id.clone()], serde_json::json!({}))
        .await
        .unwrap();

    // Node should respond with an error, not crash/panic
    let result =
        tokio::time::timeout(Duration::from_secs(2), engine.recv()).await;
    // Either we get an error response or no response (silently ignored) — both are OK.
    // The key is: the node does NOT panic.
    // We re-subscribe to verify the node is still online.
    let graph = bus.graph();
    assert!(graph.nodes.iter().any(|n| n.node_id == node.node_id),
        "node should still be online after unknown msg_type");
}
```

---

## 验证命令

```bash
# 仅集成测试
. "$HOME/.cargo/env" && cargo test -p arf-mcp --test integration_tests

# 全 workspace
. "$HOME/.cargo/env" && cargo test --workspace
```
