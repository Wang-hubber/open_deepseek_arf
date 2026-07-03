//! skill_resource_load.rs — Phase 9 task 9.6.4
//!
//! `load_skill_resource` + `LoadedResource` 端到端探查。
//!
//! 4 test cases：
//! 1. load_resource_file_three_path_prefixes — tools/ / references/ / assets/ 3 类 path 各自加载 OK
//! 2. loaded_resource_attaches_tool_metadata_for_tools_path — tools/ 路径下 LoadedResource 自动附 tool.toml 元数据
//! 3. load_resource_file_rejects_path_traversal — path 含 `..` 或 `/` 开头 → Err
//! 4. load_skill_resource_protocol_end_to_end — bus send load_skill_resource → skill_resource_loaded

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

// ═══════════════════════════════════════════════════════════════════════
// Helpers
// ═══════════════════════════════════════════════════════════════════════

fn setup_skill_with_resources(skill_name: &str) -> PathBuf {
    let id = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let root = std::env::temp_dir().join(format!("arf_skill_res_{id}"));
    let _ = fs::remove_dir_all(&root);
    fs::create_dir_all(&root).unwrap();

    // SKILL.md
    let skill_dir = root.join("skills").join(skill_name);
    fs::create_dir_all(&skill_dir).unwrap();
    let mut f = fs::File::create(skill_dir.join("SKILL.md")).unwrap();
    write!(
        f,
        "---\nname: {skill_name}\ndescription: Skill with multiple resources\n---\n\n# {skill_name}\n"
    )
    .unwrap();

    // references/api.md
    let refs = skill_dir.join("references");
    fs::create_dir_all(&refs).unwrap();
    fs::write(refs.join("api.md"), "# API Reference\n\nEndpoints list...").unwrap();

    // assets/template.txt
    let assets = skill_dir.join("assets");
    fs::create_dir_all(&assets).unwrap();
    fs::write(assets.join("template.txt"), "TEMPLATE CONTENT").unwrap();

    // tools/gen/ + main.py + tool.toml (with params_schema)
    let tools_gen = skill_dir.join("tools").join("gen");
    fs::create_dir_all(&tools_gen).unwrap();
    fs::write(
        tools_gen.join("tool.toml"),
        r#"name = "gen"
description = "Generate output"
runtime = "python"
entrypoint = "main.py"

[params_schema]
type = "object"
required = ["prompt"]

[params_schema.properties.prompt]
type = "string"
"#,
    )
    .unwrap();
    fs::write(
        tools_gen.join("main.py"),
        "import sys, json\nprint(json.dumps({'ok': True}))\n",
    )
    .unwrap();

    root
}

async fn register_requester(bus: &Bus, label: &str) -> anyhow::Result<(NodeId, arf_bus::NodeHandle)> {
    let requester_id = NodeId::new(format!("test/skill_res/{label}"));
    let info = NodeInfo {
        node_id: requester_id.clone(),
        node_type: "test-requester".into(),
        capabilities: json!({}),
        online_since: 0,
    };
    let filter = MessageFilter {
        types: Some(vec![
            "skill_resource_loaded".into(),
            "skill_resource_error".into(),
        ]),
        to_match: ToMatch::All,
    };
    let handle = bus.connect(info, filter).await?;
    Ok((requester_id, handle))
}

async fn send_load_skill_resource(
    bus: &Bus,
    from: &NodeId,
    mcp_id: &NodeId,
    skill_name: &str,
    resource_path: &str,
    handle: &mut arf_bus::NodeHandle,
    timeout: Duration,
) -> anyhow::Result<Message> {
    let payload = json!({
        "skill_name": skill_name,
        "resource_path": resource_path,
    });
    bus.send(Message::new(
        "load_skill_resource",
        from.clone(),
        vec![mcp_id.clone()],
        payload,
    ))
    .await
    .map_err(|e| anyhow::anyhow!("send load_skill_resource: {e}"))?;

    let deadline = std::time::Instant::now() + timeout;
    loop {
        let m = tokio::time::timeout(Duration::from_millis(200), handle.recv()).await;
        match m {
            Ok(Ok(msg)) => {
                if msg.msg_type == "skill_resource_loaded"
                    || msg.msg_type == "skill_resource_error"
                {
                    return Ok(msg);
                }
            }
            Ok(Err(_)) => return Err(anyhow::anyhow!("recv error")),
            Err(_) => {
                if std::time::Instant::now() >= deadline {
                    return Err(anyhow::anyhow!("timeout waiting for resource response"));
                }
            }
        }
    }
}

// ═══════════════════════════════════════════════════════════════════════
// Test 1: load_resource_file_three_path_prefixes
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn load_resource_file_three_path_prefixes() {
    let root = setup_skill_with_resources("res-skill");
    let dm = FsDiscovery::scan(root.clone()).unwrap();

    // 1. references/
    let api = dm
        .load_resource_file("res-skill", "references/api.md")
        .expect("references/api.md 应可加载");
    assert!(api.content.contains("API Reference"));
    assert!(api.description.is_none());
    assert!(api.params_schema.is_none());
    println!(
        "[test1] references/api.md: content.len = {}, desc={:?}",
        api.content.len(),
        api.description
    );

    // 2. assets/
    let tpl = dm
        .load_resource_file("res-skill", "assets/template.txt")
        .expect("assets/template.txt 应可加载");
    assert_eq!(tpl.content, "TEMPLATE CONTENT");
    assert!(tpl.description.is_none());
    assert!(tpl.params_schema.is_none());
    println!(
        "[test1] assets/template.txt: content={:?}, desc={:?}",
        tpl.content, tpl.description
    );

    // 3. tools/gen/main.py — description + params_schema 应**有**（来自 tool.toml）
    let loaded_gen = dm
        .load_resource_file("res-skill", "tools/gen/main.py")
        .expect("tools/gen/main.py 应可加载");
    assert!(loaded_gen.content.contains("ok"));
    assert_eq!(loaded_gen.description, Some("Generate output".to_string()));
    println!(
        "[test1] tools/gen/main.py: content.len = {}, desc={:?}",
        loaded_gen.content.len(),
        loaded_gen.description
    );

    let _ = fs::remove_dir_all(&root);
    println!("[test1] 3 类 path prefix 端到端 OK ✓");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 2: loaded_resource_attaches_tool_metadata_for_tools_path
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn loaded_resource_attaches_tool_metadata_for_tools_path() {
    let root = setup_skill_with_resources("meta-skill");
    let dm = FsDiscovery::scan(root.clone()).unwrap();

    // tools/ 路径下访问**任何**子文件（如 tools/gen/main.py），都应附加 tool.toml 的 description + params_schema
    let loaded = dm
        .load_resource_file("meta-skill", "tools/gen/main.py")
        .expect("load ok");
    println!("[test2] description = {:?}", loaded.description);
    println!("[test2] params_schema = {}", loaded.params_schema.as_ref().unwrap());
    assert!(loaded.description.is_some(), "tools/ 路径下 description 应 Some");
    assert!(
        loaded.params_schema.is_some(),
        "tools/ 路径下 params_schema 应 Some"
    );
    let ps = loaded.params_schema.as_ref().unwrap();
    assert!(ps.is_object(), "tools/ 路径下 params_schema 应是 object");
    assert_eq!(loaded.description.as_deref(), Some("Generate output"));
    println!("[test2] params_schema = {:?}", loaded.params_schema);

    // 对比 references/ 路径下 description 必为 None
    let ref_loaded = dm
        .load_resource_file("meta-skill", "references/api.md")
        .expect("ref ok");
    assert!(
        ref_loaded.description.is_none(),
        "references/ 路径下 description 应 None"
    );

    let _ = fs::remove_dir_all(&root);
    println!("[test2] tools/ 路径自动附加 tool metadata 端到端 OK ✓");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 3: load_resource_file_rejects_path_traversal
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn load_resource_file_rejects_path_traversal() {
    let root = setup_skill_with_resources("sec-skill");
    let dm = FsDiscovery::scan(root.clone()).unwrap();

    // path 含 `..` → 拒绝
    let bad1 = dm.load_resource_file("sec-skill", "references/../../etc/passwd");
    println!("[test3] references/../../etc/passwd = {:?}", bad1.as_ref().err());
    assert!(bad1.is_err(), "path traversal 含 `..` 应被拒绝");

    // path 以 `/` 开头 → 拒绝
    let bad2 = dm.load_resource_file("sec-skill", "/etc/passwd");
    println!("[test3] /etc/passwd = {:?}", bad2.as_ref().err());
    assert!(bad2.is_err(), "绝对路径应被拒绝");

    // path 不以 3 允许 prefix 开头 → 拒绝
    let bad3 = dm.load_resource_file("sec-skill", "config.toml");
    println!("[test3] config.toml = {:?}", bad3.as_ref().err());
    assert!(bad3.is_err(), "非 tools/ / references/ / assets/ 应被拒绝");

    let _ = fs::remove_dir_all(&root);
    println!("[test3] path traversal 全部拒绝 端到端 OK ✓");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 4: load_skill_resource_protocol_end_to_end
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn load_skill_resource_protocol_end_to_end() {
    let root = setup_skill_with_resources("proto-skill");
    let node = McpNode::local("proto-mcp", root.clone()).expect("McpNode::local");
    let bus = Arc::new(Bus::new(
        Duration::from_millis(500),
        Duration::from_secs(2),
        32,
    ));
    node.connect(&bus).await.expect("connect");
    tokio::time::sleep(Duration::from_millis(50)).await;
    let (requester, mut handle) = register_requester(&bus, "test4").await.expect("register");

    // 1. references/ 路径成功
    let resp = send_load_skill_resource(
        &bus,
        &requester,
        &NodeId::new("mcp/proto-mcp"),
        "proto-skill",
        "references/api.md",
        &mut handle,
        Duration::from_secs(3),
    )
    .await
    .expect("send");
    println!("[test4] references/api.md → msg_type = {}", resp.msg_type);
    assert_eq!(resp.msg_type, "skill_resource_loaded");
    assert_eq!(resp.payload["skill_name"], "proto-skill");
    assert_eq!(resp.payload["resource_path"], "references/api.md");
    assert!(resp.payload["content"]
        .as_str()
        .unwrap()
        .contains("API Reference"));
    println!("[test4] references/ 协议 OK ✓");

    // 2. tools/ 路径成功（含 tool metadata）
    let resp2 = send_load_skill_resource(
        &bus,
        &requester,
        &NodeId::new("mcp/proto-mcp"),
        "proto-skill",
        "tools/gen/main.py",
        &mut handle,
        Duration::from_secs(3),
    )
    .await
    .expect("send");
    println!("[test4] tools/gen/main.py → msg_type = {}", resp2.msg_type);
    assert_eq!(resp2.msg_type, "skill_resource_loaded");
    assert_eq!(resp2.payload["description"], "Generate output");
    // params_schema 在 tool.toml 缺省时为 Value::Null；fields 总存在
    assert!(resp2.payload.as_object().unwrap().contains_key("params_schema"));

    // 3. path traversal → skill_resource_error
    let resp3 = send_load_skill_resource(
        &bus,
        &requester,
        &NodeId::new("mcp/proto-mcp"),
        "proto-skill",
        "references/../../etc/passwd",
        &mut handle,
        Duration::from_secs(3),
    )
    .await
    .expect("send");
    println!("[test4] traversal → msg_type = {}", resp3.msg_type);
    assert_eq!(resp3.msg_type, "skill_resource_error");
    let err = resp3.payload["error"].as_str().unwrap();
    assert!(err.contains("..") || err.contains("traversal"));
    println!("[test4] path traversal → error 协议 OK ✓");

    let _ = fs::remove_dir_all(&root);
    println!("[test4] load_skill_resource 协议 round-trip 端到端 OK ✓");
}
