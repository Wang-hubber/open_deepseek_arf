//! skill_tool_progressive.rs — Phase 9 task 9.6.3
//!
//! skill body → tool 端到端探查。
//!
//! 4 test cases：
//! 1. skill_tool_execute_via_run_skill_tool — SkillIndex::run_tool 调起 skill 内部 main.py 执行
//! 2. skill_tool_auto_infer_without_tool_toml — 缺 tool.toml 时 infer_tool_defaults 自动推断
//! 3. skill_tool_scoped_name — config.name = "{skill_name}/{tool_name}" 命名空间隔离
//! 4. run_skill_script_protocol_end_to_end — bus send run_skill_script → 收 skill_script_result

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

fn setup_skill_with_tool(
    skill_name: &str,
    tool_name: &str,
    with_toml: bool,
    toml_content: Option<&str>,
    script_content: &str,
) -> PathBuf {
    let id = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let root = std::env::temp_dir().join(format!("arf_skill_tool_{id}"));
    let _ = fs::remove_dir_all(&root);
    fs::create_dir_all(&root).unwrap();

    let tool_dir = root.join("skills").join(skill_name).join("tools").join(tool_name);
    fs::create_dir_all(&tool_dir).unwrap();
    if with_toml {
        let toml = toml_content.expect("with_toml=true → toml_content required");
        fs::write(tool_dir.join("tool.toml"), toml).unwrap();
    }
    fs::write(tool_dir.join("main.py"), script_content).unwrap();

    // SKILL.md
    let skill_dir = root.join("skills").join(skill_name);
    fs::create_dir_all(&skill_dir).unwrap();
    let mut f = fs::File::create(skill_dir.join("SKILL.md")).unwrap();
    write!(
        f,
        "---\nname: {skill_name}\ndescription: Skill with tool {tool_name}\n---\n\n# {skill_name}\n"
    )
    .unwrap();

    root
}

async fn register_requester(bus: &Bus, label: &str) -> anyhow::Result<(NodeId, arf_bus::NodeHandle)> {
    let requester_id = NodeId::new(format!("test/skill_tool/{label}"));
    let info = NodeInfo {
        node_id: requester_id.clone(),
        node_type: "test-requester".into(),
        capabilities: json!({}),
        online_since: 0,
    };
    let filter = MessageFilter {
        types: Some(vec!["skill_script_result".into()]),
        to_match: ToMatch::All,
    };
    let handle = bus.connect(info, filter).await?;
    Ok((requester_id, handle))
}

async fn send_run_skill_script(
    bus: &Bus,
    from: &NodeId,
    mcp_id: &NodeId,
    skill_name: &str,
    tool_name: &str,
    params: Value,
    handle: &mut arf_bus::NodeHandle,
    timeout: Duration,
) -> anyhow::Result<Message> {
    let payload = json!({
        "skill_name": skill_name,
        "tool_name": tool_name,
        "call_id": Uuid::new_v4().to_string(),
        "session_id": Uuid::new_v4().to_string(),
        "params": params,
    });
    bus.send(Message::new(
        "run_skill_script",
        from.clone(),
        vec![mcp_id.clone()],
        payload,
    ))
    .await
    .map_err(|e| anyhow::anyhow!("send run_skill_script: {e}"))?;

    let deadline = std::time::Instant::now() + timeout;
    loop {
        let m = tokio::time::timeout(Duration::from_millis(200), handle.recv()).await;
        match m {
            Ok(Ok(msg)) => {
                if msg.msg_type == "skill_script_result" {
                    return Ok(msg);
                }
            }
            Ok(Err(_)) => return Err(anyhow::anyhow!("recv error")),
            Err(_) => {
                if std::time::Instant::now() >= deadline {
                    return Err(anyhow::anyhow!("timeout waiting for skill_script_result"));
                }
            }
        }
    }
}

// ═══════════════════════════════════════════════════════════════════════
// Test 1: skill_tool_execute_via_run_skill_tool
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn skill_tool_execute_via_run_skill_tool() {
    let script = "import sys, json\nparams = json.loads(sys.stdin.read())\nprint(json.dumps({'echoed': params.get('msg', ''), 'doubled': params.get('n', 0) * 2}))\n";
    let root = setup_skill_with_tool(
        "echo-skill",
        "echo",
        true,
        Some(
            r#"name = "echo"
description = "Echo back params"
runtime = "python"
entrypoint = "main.py"
"#,
        ),
        script,
    );
    let dm = FsDiscovery::scan(root.clone()).unwrap();

    // SkillIndex::run_tool 调起 main.py，expect 实际执行
    let result = dm
        .run_skill_tool("echo-skill", "echo", json!({"msg": "hello", "n": 21}))
        .await
        .expect("run_skill_tool");
    println!("[test1] run_skill_tool result = {}", result);
    assert_eq!(result["echoed"], "hello");
    assert_eq!(result["doubled"], 42);

    let _ = fs::remove_dir_all(&root);
    println!("[test1] SkillIndex::run_tool 调起 main.py 端到端 OK ✓");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 2: skill_tool_auto_infer_without_tool_toml
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn skill_tool_auto_infer_without_tool_toml() {
    let script = "import sys, json\nprint(json.dumps({'inferred': True, 'input': json.loads(sys.stdin.read())}))\n";
    let root = setup_skill_with_tool("bare-skill", "inferred", false, None, script);
    let dm = FsDiscovery::scan(root.clone()).unwrap();

    // 没有 tool.toml → load_tool_config 返 None（**不**自动推断，infer 仅在 run_tool 路径）
    let tool_config = dm.load_tool_config("bare-skill", "inferred");
    println!("[test2] load_tool_config（无 tool.toml） = {:?}", tool_config);
    assert!(tool_config.is_none(), "load_tool_config 在缺 tool.toml 时返 None");

    // 但 run_skill_tool 会调 infer_tool_defaults 自动推断 runtime=python + entrypoint=main.py
    // 实际执行验证推断 OK
    let result = dm
        .run_skill_tool("bare-skill", "inferred", json!({"x": 7}))
        .await
        .expect("run_skill_tool（推断应 work）");
    println!("[test2] run result = {}", result);
    assert_eq!(result["inferred"], true);
    assert_eq!(result["input"]["x"], 7);

    let _ = fs::remove_dir_all(&root);
    println!("[test2] infer_tool_defaults 自动推断（仅 run_tool 路径）+ 实际执行 端到端 OK ✓");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 3: skill_tool_scoped_name
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn skill_tool_scoped_name() {
    let root = setup_skill_with_tool(
        "scoped",
        "compute",
        true,
        Some(
            r#"name = "compute"
description = "Compute something"
runtime = "python"
entrypoint = "main.py"
"#,
        ),
        "import sys, json\nprint(json.dumps({'ok': True}))\n",
    );
    let dm = FsDiscovery::scan(root.clone()).unwrap();

    // config.name 应被改写为 "{skill_name}/{tool_name}"，**不**是 "compute"
    let cfg = dm
        .load_tool_config("scoped", "compute")
        .expect("load_tool_config");
    // load_tool_config 返回 **未改写**的 config（来自 tool.toml）
    // run_tool 内部才会改写 config.name 为 "{skill_name}/{tool_name}"
    println!("[test3] raw tool_config.name = {}", cfg.name);
    assert_eq!(cfg.name, "compute", "load_tool_config 返原始 tool.toml.name");

    // 但 skill_script_result 的 name 字段应是 scoped
    let node = McpNode::local("scoped-mcp", root.clone()).expect("McpNode::local");
    let bus = Arc::new(Bus::new(
        Duration::from_millis(500),
        Duration::from_secs(2),
        32,
    ));
    node.connect(&bus).await.expect("connect");
    tokio::time::sleep(Duration::from_millis(50)).await;
    let (requester, mut handle) = register_requester(&bus, "test3").await.expect("register");

    let resp = send_run_skill_script(
        &bus,
        &requester,
        &NodeId::new("mcp/scoped-mcp"),
        "scoped",
        "compute",
        json!({}),
        &mut handle,
        Duration::from_secs(3),
    )
    .await
    .expect("send_run_skill_script");
    let name = resp.payload.get("name").and_then(|v| v.as_str()).expect("name");
    println!("[test3] skill_script_result.name = {}", name);
    assert_eq!(name, "scoped/compute", "scoped name 应是 {{skill}}/{{tool}}");

    let _ = fs::remove_dir_all(&root);
    println!("[test3] skill tool scoped 命名空间隔离 端到端 OK ✓");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 4: run_skill_script_protocol_end_to_end
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn run_skill_script_protocol_end_to_end() {
    let script = "import sys, json\np = json.loads(sys.stdin.read())\nprint(json.dumps({'result': p.get('a', 0) + p.get('b', 0)}))\n";
    let root = setup_skill_with_tool(
        "math",
        "add",
        true,
        Some(
            r#"name = "add"
description = "Add two numbers"
runtime = "python"
entrypoint = "main.py"
"#,
        ),
        script,
    );
    let node = McpNode::local("math-mcp", root.clone()).expect("McpNode::local");
    let bus = Arc::new(Bus::new(
        Duration::from_millis(500),
        Duration::from_secs(2),
        32,
    ));
    node.connect(&bus).await.expect("connect");
    tokio::time::sleep(Duration::from_millis(50)).await;
    let (requester, mut handle) = register_requester(&bus, "test4").await.expect("register");

    let resp = send_run_skill_script(
        &bus,
        &requester,
        &NodeId::new("mcp/math-mcp"),
        "math",
        "add",
        json!({"a": 17, "b": 25}),
        &mut handle,
        Duration::from_secs(3),
    )
    .await
    .expect("send_run_skill_script");
    println!("[test4] response payload = {}", resp.payload);
    assert_eq!(resp.msg_type, "skill_script_result");
    assert_eq!(resp.payload["status"], "success");
    assert_eq!(resp.payload["result"]["result"], 42);
    assert_eq!(resp.payload["name"], "math/add");
    assert!(resp.payload["error"].is_null());

    let _ = fs::remove_dir_all(&root);
    println!("[test4] run_skill_script 协议端到端 OK ✓");
}
