//! mcp_http_discovery.rs — Phase 9 task 9.5.2
//!
//! McpNode + HttpDiscovery（JSON-RPC initialize + tools/list）端到端探查。
//!
//! 4 test cases：
//! 1. http_discovery_initialize_and_tools_list — mock HTTP server + HttpDiscovery::connect
//! 2. mcp_node_remote_connects_to_bus — McpNode::remote + connect(bus)
//! 3. http_proxy_tool_executes_via_tools_call — HttpProxyTool 转发 tools/call
//! 4. http_discovery_handles_404 — 错误路径（server 返回非 JSON）
//!
//! Mock HTTP server：tokio::net::TcpListener 写最简 HTTP/1.1 responder
//! （POST application/json → 返回 JSON-RPC 响应），无需 axum/hyper。

mod common;

use std::sync::Arc;
use std::time::Duration;

use arf_mcp::McpNode;
use arf_mcp::config::RemoteConfig;
use arf_mcp::discovery::DiscoveryBackend;
use arf_mcp::remote::HttpDiscovery;
use arf_bus::Bus;
use serde_json::Value;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;

// ═══════════════════════════════════════════════════════════════════════
// Mock HTTP server — 接收 POST application/json，返回 JSON-RPC 响应
// ═══════════════════════════════════════════════════════════════════════

/// Mock JSON-RPC server — handle initialize + tools/list + tools/call。
async fn spawn_mock_mcp_server(
    tools_response: Value,
    tools_call_response: Value,
) -> u16 {
    let listener = TcpListener::bind("127.0.0.1:0").await.expect("bind");
    let port = listener.local_addr().unwrap().port();
    tokio::spawn(async move {
        loop {
            let (mut sock, _) = match listener.accept().await {
                Ok(p) => p,
                Err(_) => break,
            };
            let tools_response = tools_response.clone();
            let tools_call_response = tools_call_response.clone();
            tokio::spawn(async move {
                let mut buf = vec![0u8; 8192];
                let n = match sock.read(&mut buf).await { Ok(n) => n, Err(_) => return };
                let req = String::from_utf8_lossy(&buf[..n]);
                // 解析 method（找 "method" 字段）
                let method = extract_method(&req);
                let body = match method.as_deref() {
                    Some("initialize") => serde_json::to_string(&serde_json::json!({
                        "jsonrpc":"2.0","id":1,"result":{"protocolVersion":"1.0","serverInfo":{"name":"mock-mcp","version":"1.0"},"capabilities":{}}
                    })).unwrap(),
                    Some("tools/list") => serde_json::to_string(&serde_json::json!({
                        "jsonrpc":"2.0","id":1,"result": tools_response
                    })).unwrap(),
                    Some("tools/call") => serde_json::to_string(&serde_json::json!({
                        "jsonrpc":"2.0","id":1,"result": tools_call_response
                    })).unwrap(),
                    _ => serde_json::to_string(&serde_json::json!({
                        "jsonrpc":"2.0","id":1,"error":{"code":-32601,"message":"method not found"}
                    })).unwrap(),
                };
                let resp = format!(
                    "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                    body.len(), body
                );
                let _ = sock.write_all(resp.as_bytes()).await;
                let _ = sock.shutdown().await;
            });
        }
    });
    port
}

/// Spawn a mock server that returns 404 for everything (error path).
async fn spawn_mock_404_server() -> u16 {
    let listener = TcpListener::bind("127.0.0.1:0").await.expect("bind");
    let port = listener.local_addr().unwrap().port();
    tokio::spawn(async move {
        loop {
            let (mut sock, _) = match listener.accept().await {
                Ok(p) => p,
                Err(_) => break,
            };
            tokio::spawn(async move {
                let mut buf = vec![0u8; 4096];
                let _ = sock.read(&mut buf).await;
                let body = "not found";
                let resp = format!(
                    "HTTP/1.1 404 Not Found\r\nContent-Type: text/plain\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                    body.len(), body
                );
                let _ = sock.write_all(resp.as_bytes()).await;
                let _ = sock.shutdown().await;
            });
        }
    });
    port
}

fn extract_method(req: &str) -> Option<String> {
    let body_start = req.find("\r\n\r\n")? + 4;
    let body = &req[body_start..];
    let v: Value = serde_json::from_str(body).ok()?;
    v.get("method").and_then(|m| m.as_str()).map(String::from)
}

// ═══════════════════════════════════════════════════════════════════════
// Test 1: http_discovery_initialize_and_tools_list
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn http_discovery_initialize_and_tools_list() {
    // mock server: tools/list 返回 2 个 tool
    let tools_response = serde_json::json!({
        "tools": [
            {"name":"remote_echo","description":"echo on remote","inputSchema":{"type":"object"}},
            {"name":"remote_sum","description":"sum on remote","inputSchema":{"type":"object"}},
        ]
    });
    let port = spawn_mock_mcp_server(tools_response, serde_json::json!({})).await;

    let config = RemoteConfig {
        transport: "http".into(),
        url: format!("http://127.0.0.1:{port}/mcp"),
        timeout_secs: Some(5),
        headers: Default::default(),
        tls_ca_cert: None,
        retry: None,
    };

    let discovery = HttpDiscovery::connect(config).await.expect("HttpDiscovery::connect");
    let tools = discovery.list_tools();
    println!("[test1] HttpDiscovery list_tools() = {} tools", tools.len());
    for ti in tools {
        println!("[test1]   - {}: {}", ti.name, ti.description);
    }
    assert_eq!(tools.len(), 2, "应列出 2 个 tool");
    let names: Vec<&str> = tools.iter().map(|t| t.name.as_str()).collect();
    assert!(names.contains(&"remote_echo"));
    assert!(names.contains(&"remote_sum"));

    // resolve_tool
    let rt = discovery.resolve_tool("remote_echo");
    assert!(rt.is_some(), "resolve_tool 应 Some");
    println!("[test1] resolve_tool('remote_echo') = Some ✓");

    // tool_map
    let tool_map = discovery.tool_map();
    assert_eq!(tool_map.len(), 2);
    println!("[test1] tool_map 含 2 entries ✓");

    println!("[test1] HttpDiscovery::connect (initialize + tools/list) 端到端 OK ✓");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 2: mcp_node_remote_connects_to_bus
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn mcp_node_remote_connects_to_bus() {
    let tools_response = serde_json::json!({
        "tools": [
            {"name":"remote_echo","description":"echo on remote","inputSchema":{"type":"object"}},
        ]
    });
    let port = spawn_mock_mcp_server(tools_response, serde_json::json!({})).await;

    let config = RemoteConfig {
        transport: "http".into(),
        url: format!("http://127.0.0.1:{port}/mcp"),
        timeout_secs: Some(5),
        headers: Default::default(),
        tls_ca_cert: None,
        retry: None,
    };

    let node = McpNode::remote("test-remote-mcp", config).await.expect("McpNode::remote");
    println!("[test2] McpNode::remote 创建成功");

    let bus = Arc::new(Bus::new(
        Duration::from_secs(30),
        Duration::from_secs(60),
        1024,
    ));
    node.connect(&bus).await.expect("McpNode::connect");
    println!("[test2] McpNode::remote + connect(bus) 成功");

    tokio::time::sleep(Duration::from_millis(150)).await;
    let _sub = bus.subscribe();
    println!("[test2] bus.subscribe() OK ✓");

    println!("[test2] McpNode::remote + bus connect 端到端 OK ✓");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 3: http_proxy_tool_executes_via_tools_call
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn http_proxy_tool_executes_via_tools_call() {
    let tools_response = serde_json::json!({
        "tools": [
            {"name":"remote_echo","description":"echo on remote","inputSchema":{"type":"object"}},
        ]
    });
    // tools/call response: JSON-RPC result 包 CallToolResult 形态
    let tools_call_response = serde_json::json!({
        "content": [{"type":"text","text":"hello-from-remote"}]
    });
    let port = spawn_mock_mcp_server(tools_response, tools_call_response).await;

    let config = RemoteConfig {
        transport: "http".into(),
        url: format!("http://127.0.0.1:{port}/mcp"),
        timeout_secs: Some(5),
        headers: Default::default(),
        tls_ca_cert: None,
        retry: None,
    };

    let discovery = HttpDiscovery::connect(config).await.expect("HttpDiscovery::connect");
    let echo_tool = discovery.resolve_tool("remote_echo").expect("resolve_tool");
    let result = echo_tool
        .execute(serde_json::json!({"msg": "hi"}))
        .await
        .expect("execute");
    println!("[test3] HttpProxyTool execute result: {:?}", result);
    let s = result.as_str().expect("result 应为字符串");
    assert_eq!(s, "hello-from-remote", "tools/call 应回传 remote 字符串");

    println!("[test3] HttpProxyTool::execute (tools/call) 端到端 OK ✓");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 4: http_discovery_handles_404
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn http_discovery_handles_404() {
    let port = spawn_mock_404_server().await;
    let config = RemoteConfig {
        transport: "http".into(),
        url: format!("http://127.0.0.1:{port}/mcp"),
        timeout_secs: Some(5),
        headers: Default::default(),
        tls_ca_cert: None,
        retry: None,
    };

    // connect 应该失败（server 返回非 JSON → parse 失败 → error）
    let result = tokio::time::timeout(
        Duration::from_secs(10),
        HttpDiscovery::connect(config),
    )
    .await
    .expect("HttpDiscovery::connect 不应无限等待");

    match result {
        Err(e) => println!("[test4] HttpDiscovery::connect 返回 Err（如预期）: {e}"),
        Ok(d) => {
            // 如果走 default 然后 parse 失败，看 list_tools 数
            let tools = d.list_tools();
            println!("[test4] 意外 Ok：list_tools() = {}", tools.len());
            // 404 的响应不是 JSON → parse 返回 error 字段 → 不应当成 result
            assert_eq!(tools.len(), 0, "404 server 应返回 0 tools 或 Err");
        }
    }
    println!("[test4] HttpDiscovery 错误路径 OK ✓");
}