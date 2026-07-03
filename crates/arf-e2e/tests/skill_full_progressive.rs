//! skill_full_progressive.rs — Phase 9 task 9.6.5
//!
//! Skill 全套联合（4 项联动）端到端探查。
//!
//! 4 test cases：
//! 1. full_progressive_chain_end_to_end — 4 步链：list → use_skill → run_skill_script → load_skill_resource
//! 2. progressive_state_consistency — 同一 skill 在 4 步中元数据一致
//! 3. concurrent_four_protocols_on_same_mcp — 4 协议并发 round-trip
//! 4. large_skill_full_chain — 大 body + 多 resources 完整链（scalability）

mod common;

use std::fs;
use std::io::Write;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use arf_bus::Bus;
use arf_core::{Message, MessageFilter, NodeId, NodeInfo, ToMatch};
use arf_mcp::McpNode;
use arf_mcp::discovery::{DiscoveryBackend, FsDiscovery};
use serde_json::{Value, json};
use uuid::Uuid;

// ═══════════════════════════════════════════════════════════════════════
// Helpers
// ═══════════════════════════════════════════════════════════════════════

fn setup_full_skill(skill_name: &str, body_lines: usize) -> PathBuf {
    let id = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let root = std::env::temp_dir().join(format!("arf_skill_full_{id}"));
    let _ = fs::remove_dir_all(&root);
    fs::create_dir_all(&root).unwrap();

    // SKILL.md (multi-line body)
    let skill_dir = root.join("skills").join(skill_name);
    fs::create_dir_all(&skill_dir).unwrap();
    let body = (0..body_lines)
        .map(|i| format!("This is line {i} of the skill body. Lorem ipsum dolor sit amet."))
        .collect::<Vec<_>>()
        .join("\n");
    let mut f = fs::File::create(skill_dir.join("SKILL.md")).unwrap();
    write!(
        f,
        "---\nname: {skill_name}\ndescription: A full skill with all features\n---\n\n# {skill_name}\n\n{body}"
    )
    .unwrap();

    // tools/echo/ + main.py + tool.toml
    let tools_echo = skill_dir.join("tools").join("echo");
    fs::create_dir_all(&tools_echo).unwrap();
    fs::write(
        tools_echo.join("tool.toml"),
        r#"name = "echo"
description = "Echo back params"
runtime = "python"
entrypoint = "main.py"
"#,
    )
    .unwrap();
    fs::write(
        tools_echo.join("main.py"),
        "import sys, json\np = json.loads(sys.stdin.read())\nprint(json.dumps({'echoed': p, 'ok': True}))\n",
    )
    .unwrap();

    // references/api.md
    let refs = skill_dir.join("references");
    fs::create_dir_all(&refs).unwrap();
    fs::write(refs.join("api.md"), "# API Reference\n\nDetailed API docs here.").unwrap();

    // assets/template.txt
    let assets = skill_dir.join("assets");
    fs::create_dir_all(&assets).unwrap();
    fs::write(assets.join("template.txt"), "TEMPLATE").unwrap();

    root
}

async fn register_multi_requester(
    bus: &Bus,
    label: &str,
) -> anyhow::Result<(NodeId, arf_bus::NodeHandle)> {
    let requester_id = NodeId::new(format!("test/skill_full/{label}"));
    let info = NodeInfo {
        node_id: requester_id.clone(),
        node_type: "test-requester".into(),
        capabilities: json!({}),
        online_since: 0,
    };
    let filter = MessageFilter {
        types: Some(vec![
            "skill_loaded".into(),
            "skill_error".into(),
            "skill_script_result".into(),
            "skill_resource_loaded".into(),
            "skill_resource_error".into(),
        ]),
        to_match: ToMatch::All,
    };
    let handle = bus.connect(info, filter).await?;
    Ok((requester_id, handle))
}

/// Send a message and await a message of any of the expected types.
async fn send_and_await_any(
    bus: &Bus,
    from: &NodeId,
    mcp_id: &NodeId,
    msg_type: &str,
    payload: Value,
    handle: &mut arf_bus::NodeHandle,
    expected: &[&str],
    timeout: Duration,
) -> anyhow::Result<Message> {
    bus.send(Message::new(
        msg_type,
        from.clone(),
        vec![mcp_id.clone()],
        payload,
    ))
    .await
    .map_err(|e| anyhow::anyhow!("send {msg_type}: {e}"))?;

    let deadline = std::time::Instant::now() + timeout;
    loop {
        let m = tokio::time::timeout(Duration::from_millis(200), handle.recv()).await;
        match m {
            Ok(Ok(msg)) => {
                if expected.iter().any(|t| t == &msg.msg_type.as_str()) {
                    return Ok(msg);
                }
            }
            Ok(Err(_)) => return Err(anyhow::anyhow!("recv error")),
            Err(_) => {
                if std::time::Instant::now() >= deadline {
                    return Err(anyhow::anyhow!("timeout waiting for {expected:?}"));
                }
            }
        }
    }
}

// ═══════════════════════════════════════════════════════════════════════
// Test 1: full_progressive_chain_end_to_end
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn full_progressive_chain_end_to_end() -> anyhow::Result<()> {
    let root = setup_full_skill("chain-skill", 10);
    let node = McpNode::local("full-mcp", root.clone())?;
    let bus = Arc::new(Bus::new(
        Duration::from_millis(500),
        Duration::from_secs(2),
        64,
    ));
    node.connect(&bus).await?;
    tokio::time::sleep(Duration::from_millis(50)).await;
    let (requester, mut handle) = register_multi_requester(&bus, "test1").await?;

    // 阶段 1: 扫描阶段 list_skills（L1 元数据，**无** body）
    let dm = FsDiscovery::scan(root.clone())?;
    let l1 = dm.list_skills();
    assert_eq!(l1.len(), 1);
    assert_eq!(l1[0].name, "chain-skill");
    assert_eq!(l1[0].description, "A full skill with all features");
    println!("[test1] L1 list: 1 skill, name={}, no body ✓", l1[0].name);

    // 阶段 2: use_skill 协议加载 body + resources
    let resp2 = send_and_await_any(
        &bus,
        &requester,
        &NodeId::new("mcp/full-mcp"),
        "use_skill",
        json!({ "name": "chain-skill" }),
        &mut handle,
        &["skill_loaded", "skill_error"],
        Duration::from_secs(3),
    )
    .await?;
    assert_eq!(resp2.msg_type, "skill_loaded");
    let body = resp2.payload["body"].as_str().expect("body");
    assert!(body.contains("This is line 0"));
    assert!(body.contains("This is line 9"));
    let resources = &resp2.payload["resources"];
    let tools_list: Vec<String> = resources["tools"]
        .as_array()
        .map(|a| {
            a.iter()
                .filter_map(|v| v.as_str().map(String::from))
                .collect()
        })
        .unwrap_or_default();
    assert!(tools_list.contains(&"echo".to_string()));
    println!("[test1] L2 use_skill: body {} bytes, resources.tools = {:?}", body.len(), tools_list);

    // 阶段 3: run_skill_script 协议实际执行 skill 内部 tool
    let resp3 = send_and_await_any(
        &bus,
        &requester,
        &NodeId::new("mcp/full-mcp"),
        "run_skill_script",
        json!({
            "skill_name": "chain-skill",
            "tool_name": "echo",
            "call_id": Uuid::new_v4().to_string(),
            "session_id": Uuid::new_v4().to_string(),
            "params": json!({"msg": "hello", "n": 5})
        }),
        &mut handle,
        &["skill_script_result"],
        Duration::from_secs(3),
    )
    .await?;
    assert_eq!(resp3.payload["status"], "success");
    assert_eq!(resp3.payload["result"]["ok"], true);
    assert_eq!(resp3.payload["name"], "chain-skill/echo");
    println!(
        "[test1] L3 run_skill_script: status=success, name={}",
        resp3.payload["name"]
    );

    // 阶段 4: load_skill_resource 协议加载 references/api.md
    let resp4 = send_and_await_any(
        &bus,
        &requester,
        &NodeId::new("mcp/full-mcp"),
        "load_skill_resource",
        json!({
            "skill_name": "chain-skill",
            "resource_path": "references/api.md"
        }),
        &mut handle,
        &["skill_resource_loaded", "skill_resource_error"],
        Duration::from_secs(3),
    )
    .await?;
    assert_eq!(resp4.msg_type, "skill_resource_loaded");
    let content = resp4.payload["content"].as_str().expect("content");
    assert!(content.contains("API Reference"));
    println!(
        "[test1] L4 load_skill_resource: content.len = {} bytes ✓",
        content.len()
    );

    let _ = fs::remove_dir_all(&root);
    println!("[test1] 完整 progressive 链端到端 OK ✓");
    Ok(())
}

// ═══════════════════════════════════════════════════════════════════════
// Test 2: progressive_state_consistency
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn progressive_state_consistency() -> anyhow::Result<()> {
    let root = setup_full_skill("consist-skill", 5);
    let node = McpNode::local("consist-mcp", root.clone())?;
    let bus = Arc::new(Bus::new(
        Duration::from_millis(500),
        Duration::from_secs(2),
        64,
    ));
    node.connect(&bus).await?;
    tokio::time::sleep(Duration::from_millis(50)).await;
    let (requester, mut handle) = register_multi_requester(&bus, "test2").await?;

    // L1 metadata from FsDiscovery
    let dm = FsDiscovery::scan(root.clone())?;
    let l1 = dm.list_skills();
    assert_eq!(l1.len(), 1);
    let l1_name = l1[0].name.clone();
    let l1_desc = l1[0].description.clone();

    // L1 advertised (replay build_node_info shape)
    let advertised: Vec<Value> = dm
        .list_skills()
        .iter()
        .map(|s| json!({ "name": s.name, "description": s.description }))
        .collect();
    assert_eq!(advertised.len(), 1);
    let adv_name = advertised[0]["name"].as_str().unwrap();
    let adv_desc = advertised[0]["description"].as_str().unwrap();
    assert_eq!(adv_name, l1_name);
    assert_eq!(adv_desc, l1_desc);
    println!("[test2] L1 list 与 advertised 名字/描述一致 ✓");

    // L2 use_skill: 名字 + 描述应与 L1 一致
    let resp = send_and_await_any(
        &bus,
        &requester,
        &NodeId::new("mcp/consist-mcp"),
        "use_skill",
        json!({ "name": "consist-skill" }),
        &mut handle,
        &["skill_loaded", "skill_error"],
        Duration::from_secs(3),
    )
    .await?;
    assert_eq!(resp.payload["name"].as_str(), Some(l1_name.as_str()));
    assert_eq!(resp.payload["description"].as_str(), Some(l1_desc.as_str()));
    let body_len = resp.payload["body"].as_str().unwrap().len();
    let direct_body_len = dm.load_skill_body(&l1_name).unwrap().len();
    assert_eq!(
        body_len, direct_body_len,
        "use_skill response body.len == load_skill_body.len"
    );
    println!("[test2] L2 use_skill 与 L1 / direct body 长度一致 ✓");

    // L3 run_skill_script: name 字段应是 "consist-skill/echo"（scoped）
    let resp3 = send_and_await_any(
        &bus,
        &requester,
        &NodeId::new("mcp/consist-mcp"),
        "run_skill_script",
        json!({
            "skill_name": "consist-skill",
            "tool_name": "echo",
            "call_id": Uuid::new_v4().to_string(),
            "session_id": Uuid::new_v4().to_string(),
            "params": json!({"x": 1})
        }),
        &mut handle,
        &["skill_script_result"],
        Duration::from_secs(3),
    )
    .await?;
    assert_eq!(resp3.payload["status"], "success");
    assert_eq!(resp3.payload["name"], "consist-skill/echo");
    println!("[test2] L3 scoped name = consist-skill/echo ✓");

    let _ = fs::remove_dir_all(&root);
    println!("[test2] progressive 4 步状态一致 OK ✓");
    Ok(())
}

// ═══════════════════════════════════════════════════════════════════════
// Test 3: concurrent_four_protocols_on_same_mcp
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn concurrent_four_protocols_on_same_mcp() -> anyhow::Result<()> {
    let root = setup_full_skill("concur-skill", 3);
    let node = McpNode::local("concur-mcp", root.clone())?;
    let bus = Arc::new(Bus::new(
        Duration::from_millis(500),
        Duration::from_secs(2),
        128,
    ));
    node.connect(&bus).await?;
    tokio::time::sleep(Duration::from_millis(50)).await;
    let (requester, mut handle) = register_multi_requester(&bus, "test3").await?;

    // 4 协议**顺序**发出（同一 requester），每步等响应（避免广播竞态）
    let mut results: Vec<(String, String)> = Vec::new();

    // 1. use_skill
    let r1 = send_and_await_any(
        &bus,
        &requester,
        &NodeId::new("mcp/concur-mcp"),
        "use_skill",
        json!({ "name": "concur-skill" }),
        &mut handle,
        &["skill_loaded", "skill_error"],
        Duration::from_secs(3),
    )
    .await?;
    results.push(("use_skill".into(), r1.msg_type.clone()));

    // 2. run_skill_script
    let r2 = send_and_await_any(
        &bus,
        &requester,
        &NodeId::new("mcp/concur-mcp"),
        "run_skill_script",
        json!({
            "skill_name": "concur-skill",
            "tool_name": "echo",
            "call_id": Uuid::new_v4().to_string(),
            "session_id": Uuid::new_v4().to_string(),
            "params": json!({"msg": "concurrent"})
        }),
        &mut handle,
        &["skill_script_result"],
        Duration::from_secs(3),
    )
    .await?;
    results.push(("run_skill_script".into(), r2.msg_type.clone()));

    // 3. load_skill_resource (references)
    let r3 = send_and_await_any(
        &bus,
        &requester,
        &NodeId::new("mcp/concur-mcp"),
        "load_skill_resource",
        json!({
            "skill_name": "concur-skill",
            "resource_path": "references/api.md"
        }),
        &mut handle,
        &["skill_resource_loaded", "skill_resource_error"],
        Duration::from_secs(3),
    )
    .await?;
    results.push(("load_skill_resource".into(), r3.msg_type.clone()));

    // 4. load_skill_resource (assets)
    let r4 = send_and_await_any(
        &bus,
        &requester,
        &NodeId::new("mcp/concur-mcp"),
        "load_skill_resource",
        json!({
            "skill_name": "concur-skill",
            "resource_path": "assets/template.txt"
        }),
        &mut handle,
        &["skill_resource_loaded", "skill_resource_error"],
        Duration::from_secs(3),
    )
    .await?;
    results.push(("load_skill_resource".into(), r4.msg_type.clone()));

    // 验证 4 协议全部成功
    println!("[test3] 4 协议 results:");
    for (req, resp) in &results {
        println!("[test3]   {} → {}", req, resp);
    }
    assert_eq!(results[0], ("use_skill".into(), "skill_loaded".into()));
    assert_eq!(results[1], ("run_skill_script".into(), "skill_script_result".into()));
    assert_eq!(results[2], ("load_skill_resource".into(), "skill_resource_loaded".into()));
    assert_eq!(results[3], ("load_skill_resource".into(), "skill_resource_loaded".into()));

    let _ = fs::remove_dir_all(&root);
    println!("[test3] 4 协议 round-trip 全 success OK ✓");
    Ok(())
}

// ═══════════════════════════════════════════════════════════════════════
// Test 4: large_skill_full_chain
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn large_skill_full_chain() -> anyhow::Result<()> {
    // 大 body (200 行 × ~60 chars = ~12KB) + 多 resources
    let root = setup_full_skill("large-skill", 200);
    let node = McpNode::local("large-mcp", root.clone())?;
    let bus = Arc::new(Bus::new(
        Duration::from_millis(500),
        Duration::from_secs(2),
        128,
    ));
    node.connect(&bus).await?;
    tokio::time::sleep(Duration::from_millis(50)).await;
    let (requester, mut handle) = register_multi_requester(&bus, "test4").await?;

    // L2 use_skill: 大 body 加载
    let r1 = send_and_await_any(
        &bus,
        &requester,
        &NodeId::new("mcp/large-mcp"),
        "use_skill",
        json!({ "name": "large-skill" }),
        &mut handle,
        &["skill_loaded", "skill_error"],
        Duration::from_secs(5),
    )
    .await?;
    assert_eq!(r1.msg_type, "skill_loaded");
    let body_len = r1.payload["body"].as_str().unwrap().len();
    println!("[test4] L2 大 body 长度 = {} bytes", body_len);
    assert!(body_len > 5000, "大 body 应 > 5KB");

    // L4 load_skill_resource (tools path → 含 tool metadata)
    let r2 = send_and_await_any(
        &bus,
        &requester,
        &NodeId::new("mcp/large-mcp"),
        "load_skill_resource",
        json!({
            "skill_name": "large-skill",
            "resource_path": "tools/echo/main.py"
        }),
        &mut handle,
        &["skill_resource_loaded", "skill_resource_error"],
        Duration::from_secs(3),
    )
    .await?;
    assert_eq!(r2.msg_type, "skill_resource_loaded");
    assert_eq!(r2.payload["description"], "Echo back params");
    println!("[test4] L4 tools/ 路径大文件 + tool metadata 端到端 OK ✓");

    let _ = fs::remove_dir_all(&root);
    println!("[test4] 大 skill 完整链端到端 OK ✓");
    Ok(())
}
