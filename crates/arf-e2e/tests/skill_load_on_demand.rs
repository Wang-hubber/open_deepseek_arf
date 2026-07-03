//! skill_load_on_demand.rs — Phase 9 task 9.6.2
//!
//! `use_skill` 协议端到端探查。
//!
//! 4 test cases：
//! 1. use_skill_protocol_round_trip — bus 端 send use_skill → McpNode dispatch → skill_loaded 响应
//! 2. use_skill_includes_resources_manifest — response 含 resources 清单（tools / references / assets）
//! 3. use_skill_unknown_returns_error — 不存在 skill 名 → skill_error 响应（非 panic）
//! 4. use_skill_does_not_load_body_at_scan — scan/list 阶段不读 body；use_skill 触发后才读

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

fn setup_skill_with_resources(name: &str) -> PathBuf {
    let id = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let root = std::env::temp_dir().join(format!("arf_use_skill_{id}"));
    let _ = fs::remove_dir_all(&root);
    fs::create_dir_all(&root).unwrap();

    // tools/gen/main.py (skill 内部 tool)
    let gen_dir = root.join("skills").join(name).join("tools").join("gen");
    fs::create_dir_all(&gen_dir).unwrap();
    fs::write(
        gen_dir.join("main.py"),
        "import sys, json\nprint(json.dumps({'generated': 'ok'}))\n",
    )
    .unwrap();

    // references/api.md
    let refs = root.join("skills").join(name).join("references");
    fs::create_dir_all(&refs).unwrap();
    fs::write(refs.join("api.md"), "# API Reference\n\nEndpoints...").unwrap();

    // assets/template.txt
    let assets = root.join("skills").join(name).join("assets");
    fs::create_dir_all(&assets).unwrap();
    fs::write(assets.join("template.txt"), "TEMPLATE").unwrap();

    // SKILL.md
    let skill_dir = root.join("skills").join(name);
    let mut f = fs::File::create(skill_dir.join("SKILL.md")).unwrap();
    write!(
        f,
        "---\nname: {name}\ndescription: Skill with tools and references\n---\n\n# {name}\n\nBody of the skill. Use the gen tool to generate.\n"
    )
    .unwrap();

    root
}

fn setup_skill_minimal(name: &str, desc: &str, body: &str) -> PathBuf {
    let id = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let root = std::env::temp_dir().join(format!("arf_use_skill_min_{id}"));
    let _ = fs::remove_dir_all(&root);
    fs::create_dir_all(&root).unwrap();
    let skill_dir = root.join("skills").join(name);
    fs::create_dir_all(&skill_dir).unwrap();
    let mut f = fs::File::create(skill_dir.join("SKILL.md")).unwrap();
    write!(f, "---\nname: {name}\ndescription: {desc}\n---\n\n{body}").unwrap();
    root
}

/// Register a requester node that listens for a given response type.
async fn register_requester(
    bus: &Bus,
    label: &str,
    response_types: Vec<&str>,
) -> anyhow::Result<(NodeId, arf_bus::NodeHandle)> {
    let requester_id = NodeId::new(format!("test/use_skill/{label}"));
    let info = NodeInfo {
        node_id: requester_id.clone(),
        node_type: "test-requester".into(),
        capabilities: json!({}),
        online_since: 0,
    };
    let filter = MessageFilter {
        types: Some(response_types.into_iter().map(String::from).collect()),
        to_match: ToMatch::All,
    };
    let handle = bus.connect(info, filter).await?;
    Ok((requester_id, handle))
}

/// Send use_skill and await skill_loaded or skill_error response.
async fn send_use_skill_and_await(
    bus: &Bus,
    from: &NodeId,
    mcp_id: &NodeId,
    skill_name: &str,
    handle: &mut arf_bus::NodeHandle,
    timeout: Duration,
) -> anyhow::Result<Message> {
    let payload = json!({ "name": skill_name });
    let receipt = bus
        .send(Message::new(
            "use_skill",
            from.clone(),
            vec![mcp_id.clone()],
            payload,
        ))
        .await
        .map_err(|e| anyhow::anyhow!("send use_skill: {e}"))?;
    println!(
        "[use_skill] sent: msg_id={}, online={}, matching={}",
        receipt.message_id, receipt.online_nodes, receipt.matching_nodes
    );

    let deadline = std::time::Instant::now() + timeout;
    loop {
        let m = tokio::time::timeout(Duration::from_millis(200), handle.recv()).await;
        match m {
            Ok(Ok(msg)) => {
                if msg.msg_type == "skill_loaded" || msg.msg_type == "skill_error" {
                    return Ok(msg);
                }
                // ignore other messages
            }
            Ok(Err(_)) => return Err(anyhow::anyhow!("recv error")),
            Err(_) => {
                if std::time::Instant::now() >= deadline {
                    return Err(anyhow::anyhow!(
                        "timeout waiting for skill response ({}ms)",
                        timeout.as_millis()
                    ));
                }
            }
        }
    }
}

// ═══════════════════════════════════════════════════════════════════════
// Test 1: use_skill_protocol_round_trip
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn use_skill_protocol_round_trip() -> anyhow::Result<()> {
    let root = setup_skill_minimal("greet", "Greet user", "# Greet body\n\nHello!");
    let node = McpNode::local("skill-mcp", root.clone())?;
    let bus = Arc::new(Bus::new(
        Duration::from_millis(500),
        Duration::from_secs(2),
        32,
    ));
    node.connect(&bus).await?;
    tokio::time::sleep(Duration::from_millis(50)).await;

    let (requester, mut handle) =
        register_requester(&bus, "test1", vec!["skill_loaded", "skill_error"]).await?;

    let resp = send_use_skill_and_await(
        &bus,
        &requester,
        &NodeId::new("mcp/skill-mcp"),
        "greet",
        &mut handle,
        Duration::from_secs(3),
    )
    .await?;

    println!("[test1] response msg_type = {}", resp.msg_type);
    assert_eq!(resp.msg_type, "skill_loaded", "应回 skill_loaded");

    let p = &resp.payload;
    println!(
        "[test1] payload keys = {:?}",
        p.as_object().map(|o| o.keys().collect::<Vec<_>>())
    );
    assert_eq!(p.get("namespace").and_then(|v| v.as_str()), Some("skill-mcp"));
    assert_eq!(p.get("name").and_then(|v| v.as_str()), Some("greet"));
    assert_eq!(
        p.get("description").and_then(|v| v.as_str()),
        Some("Greet user")
    );

    let body = p
        .get("body")
        .and_then(|v| v.as_str())
        .expect("body 应存在");
    assert!(body.contains("Hello!"), "body 应含 SKILL.md 全文");
    println!("[test1] body 长度 = {} bytes", body.len());

    // resources 应是 {tools, references, assets} object
    let resources = p.get("resources").expect("resources 应存在");
    assert!(resources.get("tools").is_some());
    assert!(resources.get("references").is_some());
    assert!(resources.get("assets").is_some());
    println!("[test1] resources keys = {:?}", resources.as_object().map(|o| o.keys().collect::<Vec<_>>()));

    let _ = fs::remove_dir_all(&root);
    println!("[test1] use_skill → skill_loaded 端到端 OK ✓");
    Ok(())
}

// ═══════════════════════════════════════════════════════════════════════
// Test 2: use_skill_includes_resources_manifest
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn use_skill_includes_resources_manifest() -> anyhow::Result<()> {
    let root = setup_skill_with_resources("gen-skill");
    let node = McpNode::local("res-mcp", root.clone())?;
    let bus = Arc::new(Bus::new(
        Duration::from_millis(500),
        Duration::from_secs(2),
        32,
    ));
    node.connect(&bus).await?;
    tokio::time::sleep(Duration::from_millis(50)).await;

    let (requester, mut handle) =
        register_requester(&bus, "test2", vec!["skill_loaded", "skill_error"]).await?;

    let resp = send_use_skill_and_await(
        &bus,
        &requester,
        &NodeId::new("mcp/res-mcp"),
        "gen-skill",
        &mut handle,
        Duration::from_secs(3),
    )
    .await?;
    assert_eq!(resp.msg_type, "skill_loaded");

    let resources = &resp.payload["resources"];
    let tools: Vec<String> = resources["tools"]
        .as_array()
        .map(|a| {
            a.iter()
                .filter_map(|v| v.as_str().map(String::from))
                .collect()
        })
        .unwrap_or_default();
    let refs: Vec<String> = resources["references"]
        .as_array()
        .map(|a| {
            a.iter()
                .filter_map(|v| v.as_str().map(String::from))
                .collect()
        })
        .unwrap_or_default();
    let assets: Vec<String> = resources["assets"]
        .as_array()
        .map(|a| {
            a.iter()
                .filter_map(|v| v.as_str().map(String::from))
                .collect()
        })
        .unwrap_or_default();

    println!("[test2] resources.tools = {:?}", tools);
    println!("[test2] resources.references = {:?}", refs);
    println!("[test2] resources.assets = {:?}", assets);
    assert!(tools.contains(&"gen".to_string()), "tools 应含 gen");
    assert!(refs.contains(&"api.md".to_string()), "references 应含 api.md");
    assert!(
        assets.contains(&"template.txt".to_string()),
        "assets 应含 template.txt"
    );

    let _ = fs::remove_dir_all(&root);
    println!("[test2] resources manifest 端到端 OK ✓");
    Ok(())
}

// ═══════════════════════════════════════════════════════════════════════
// Test 3: use_skill_unknown_returns_error
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn use_skill_unknown_returns_error() -> anyhow::Result<()> {
    let root = setup_skill_minimal("known", "Known skill", "body");
    let node = McpNode::local("err-mcp", root.clone())?;
    let bus = Arc::new(Bus::new(
        Duration::from_millis(500),
        Duration::from_secs(2),
        32,
    ));
    node.connect(&bus).await?;
    tokio::time::sleep(Duration::from_millis(50)).await;

    let (requester, mut handle) =
        register_requester(&bus, "test3", vec!["skill_loaded", "skill_error"]).await?;

    let resp = send_use_skill_and_await(
        &bus,
        &requester,
        &NodeId::new("mcp/err-mcp"),
        "nonexistent",
        &mut handle,
        Duration::from_secs(3),
    )
    .await?;
    println!("[test3] response msg_type = {}", resp.msg_type);
    assert_eq!(resp.msg_type, "skill_error", "不存在 skill 应回 skill_error");

    let p = &resp.payload;
    let err = p
        .get("error")
        .and_then(|v| v.as_str())
        .expect("error 字段应存在");
    assert!(err.contains("nonexistent"), "error 应含 skill 名");
    println!("[test3] error message = {}", err);

    let _ = fs::remove_dir_all(&root);
    println!("[test3] 未知 skill → skill_error（不 panic）端到端 OK ✓");
    Ok(())
}

// ═══════════════════════════════════════════════════════════════════════
// Test 4: use_skill_does_not_load_body_at_scan
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn use_skill_does_not_load_body_at_scan() -> anyhow::Result<()> {
    // 在 FsDiscovery::scan 阶段 list_skills() 不读 body；
    // use_skill 触发后才读 body
    let root = setup_skill_minimal("lazy", "Lazy body", "THIS IS THE BODY CONTENT THAT SHOULD NOT LOAD EARLY");
    let dm = FsDiscovery::scan(root.clone())?;

    // 阶段 1: list_skills() — body 不应加载
    let skills = dm.list_skills();
    assert_eq!(skills.len(), 1);
    let entry = skills[0];
    println!("[test4] list 阶段: skill '{}' 在 entry（无 body 字段）", entry.name);

    // 阶段 2: 显式 load_skill_body — body 加载
    let body = dm.load_skill_body("lazy");
    assert!(body.is_some());
    let body_str = body.unwrap();
    assert!(body_str.contains("THIS IS THE BODY CONTENT"));
    println!("[test4] load_skill_body 阶段: body 长度 = {} bytes", body_str.len());

    // 阶段 3: use_skill 协议端到端
    let node = McpNode::local("lazy-mcp", root.clone())?;
    let bus = Arc::new(Bus::new(
        Duration::from_millis(500),
        Duration::from_secs(2),
        32,
    ));
    node.connect(&bus).await?;
    tokio::time::sleep(Duration::from_millis(50)).await;
    let (requester, mut handle) =
        register_requester(&bus, "test4", vec!["skill_loaded", "skill_error"]).await?;
    let resp = send_use_skill_and_await(
        &bus,
        &requester,
        &NodeId::new("mcp/lazy-mcp"),
        "lazy",
        &mut handle,
        Duration::from_secs(3),
    )
    .await?;
    assert_eq!(resp.msg_type, "skill_loaded");
    let resp_body = resp.payload["body"].as_str().expect("body");
    assert!(resp_body.contains("THIS IS THE BODY CONTENT"));
    println!("[test4] use_skill 协议: body 长度 = {} bytes", resp_body.len());

    let _ = fs::remove_dir_all(&root);
    println!("[test4] progressive L1 → L2 端到端 OK ✓");
    Ok(())
}
