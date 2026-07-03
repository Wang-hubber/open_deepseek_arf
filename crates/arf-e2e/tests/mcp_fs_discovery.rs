//! mcp_fs_discovery.rs — Phase 9 task 9.5.1
//!
//! McpNode + FsDiscovery（filesystem 扫描本地 tool/skill）端到端探查。
//!
//! 4 test cases：
//! 1. fs_discovery_scans_tool_toml — tmpdir 写 2 个 tool.toml，FsDiscovery::scan 列出 2 个 tool
//! 2. mcp_node_local_connects_to_bus — tmpdir + McpNode::local + connect(bus) 成功
//! 3. discovery_backend_trait_methods — FsDiscovery 实现 DiscoveryBackend 多方法端到端
//! 4. tool_execute_via_script_tool — FsDiscovery::scan + resolve_tool + execute 端到端

mod common;

use std::fs;
use std::io::Write;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use arf_mcp::McpNode;
use arf_mcp::discovery::{DiscoveryBackend, FsDiscovery};
use arf_bus::Bus;

// ═══════════════════════════════════════════════════════════════════════
// Helpers
// ═══════════════════════════════════════════════════════════════════════

fn setup_root(tools: &[(&str, &str, &str)], skills: &[(&str, &str)]) -> PathBuf {
    let id = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let root = std::env::temp_dir().join(format!("arf_fs_disc_e2e_{id}"));
    let _ = fs::remove_dir_all(&root);
    fs::create_dir_all(&root).unwrap();

    for (name, toml, script) in tools {
        let tool_dir = root.join("tools").join(name);
        fs::create_dir_all(&tool_dir).unwrap();
        let mut f = fs::File::create(tool_dir.join("tool.toml")).unwrap();
        f.write_all(toml.as_bytes()).unwrap();
        fs::write(tool_dir.join("main.py"), script).unwrap();
    }

    if !skills.is_empty() {
        for (name, content) in skills {
            let skill_dir = root.join("skills").join(name);
            fs::create_dir_all(&skill_dir).unwrap();
            let mut f = fs::File::create(skill_dir.join("SKILL.md")).unwrap();
            f.write_all(content.as_bytes()).unwrap();
        }
    }

    root
}

fn echo_toml(name: &str) -> String {
    format!(
        r#"name = "{name}"
description = "Echo tool"
runtime = "python"
entrypoint = "main.py"
"#
    )
}

fn echo_script() -> &'static str {
    "import sys, json\nparams = json.loads(sys.stdin.read())\nprint(json.dumps(params))\n"
}

fn minimal_skill(name: &str, desc: &str) -> String {
    format!("---\nname: {name}\ndescription: {desc}\n---\n\n# {name}\n\nBody.\n")
}

// ═══════════════════════════════════════════════════════════════════════
// Test 1: fs_discovery_scans_tool_toml
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn fs_discovery_scans_tool_toml() {
    let root = setup_root(
        &[
            ("echo", &echo_toml("echo"), echo_script()),
            ("upper", &echo_toml("upper"), echo_script()),
        ],
        &[],
    );
    let dm = FsDiscovery::scan(root.clone()).unwrap();
    let tools = dm.list_tools();
    println!("[test1] FsDiscovery 扫到 {} 个 tool", tools.len());
    for ti in tools {
        println!("[test1]   - {}: {}", ti.name, ti.description);
    }
    assert_eq!(tools.len(), 2, "应扫到 2 个 tool");
    let names: Vec<&str> = tools.iter().map(|t| t.name.as_str()).collect();
    assert!(names.contains(&"echo"));
    assert!(names.contains(&"upper"));
    let _ = fs::remove_dir_all(&root);
    println!("[test1] FsDiscovery::scan 扫 tool.toml 端到端 OK ✓");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 2: mcp_node_local_connects_to_bus
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn mcp_node_local_connects_to_bus() {
    let root = setup_root(
        &[("echo", &echo_toml("echo"), echo_script())],
        &[],
    );
    let node = McpNode::local("test-mcp", root.clone()).expect("McpNode::local");
    println!("[test2] McpNode::local 创建成功：namespace=test-mcp, root={}", root.display());

    let bus = Arc::new(Bus::new(
        Duration::from_secs(30),
        Duration::from_secs(60),
        1024,
    ));
    node.connect(&bus).await.expect("McpNode::connect");
    println!("[test2] McpNode::connect(bus) 成功");

    // 给 bus 一点时间注册 listener
    tokio::time::sleep(Duration::from_millis(100)).await;
    let _sub = bus.subscribe();
    println!("[test2] bus.subscribe() OK ✓");

    let _ = fs::remove_dir_all(&root);
    println!("[test2] McpNode + bus connect 端到端 OK ✓");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 3: discovery_backend_trait_methods
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn discovery_backend_trait_methods() {
    let root = setup_root(
        &[("echo", &echo_toml("echo"), echo_script())],
        &[("greet", &minimal_skill("greet", "Greet user"))],
    );
    let dm = FsDiscovery::scan(root.clone()).unwrap();

    // list_tools
    let tools = dm.list_tools();
    assert_eq!(tools.len(), 1, "list_tools 应 1 个");
    println!("[test3] list_tools() = {} tools ✓", tools.len());

    // tool_map
    let tool_map = dm.tool_map();
    assert!(tool_map.contains_key("echo"), "tool_map 应含 echo");
    println!("[test3] tool_map() 含 echo ✓");

    // resolve_tool
    let resolved = dm.resolve_tool("echo");
    assert!(resolved.is_some(), "resolve_tool('echo') 应 Some");
    println!("[test3] resolve_tool('echo') = Some ✓");
    let missing = dm.resolve_tool("nonexistent");
    assert!(missing.is_none(), "resolve_tool('nonexistent') 应 None");
    println!("[test3] resolve_tool('nonexistent') = None ✓");

    // list_skills
    let skills = dm.list_skills();
    assert_eq!(skills.len(), 1, "list_skills 应 1 个");
    println!("[test3] list_skills() = {} skills ✓", skills.len());

    // resolve_skill
    let skill = dm.resolve_skill("greet");
    assert!(skill.is_some(), "resolve_skill('greet') 应 Some");
    println!("[test3] resolve_skill('greet') = Some ✓");

    // load_skill_body
    let body = dm.load_skill_body("greet");
    assert!(body.is_some(), "load_skill_body 应 Some");
    println!("[test3] load_skill_body('greet') = Some ✓");

    let _ = fs::remove_dir_all(&root);
    println!("[test3] DiscoveryBackend 多方法端到端 OK ✓");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 4: tool_execute_via_script_tool
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn tool_execute_via_script_tool() {
    let root = setup_root(
        &[("echo", &echo_toml("echo"), echo_script())],
        &[],
    );
    let dm = FsDiscovery::scan(root.clone()).unwrap();
    let echo_tool = dm.resolve_tool("echo").expect("resolve_tool('echo')");
    let result = echo_tool
        .execute(serde_json::json!({"hello": "world"}))
        .await
        .expect("execute");
    println!("[test4] echo tool execute result: {:?}", result);
    let parsed: serde_json::Value = serde_json::from_value(result).expect("parse");
    assert_eq!(parsed["hello"], "world", "echo 应回传输入参数");
    let _ = fs::remove_dir_all(&root);
    println!("[test4] ScriptTool execute 端到端 OK ✓");
}