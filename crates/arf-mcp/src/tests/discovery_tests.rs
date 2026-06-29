use crate::discovery::DiscoveryBackend;
use std::fs;
use std::io::Write;
use std::path::PathBuf;
use std::sync::atomic::Ordering;

use crate::discovery::FsDiscovery;

fn setup_root(
    tools: &[(&str, &str, &str)],
    skills: &[(&str, &str)],
) -> (PathBuf, Cleanup) {
    let id = super::TEST_COUNTER.fetch_add(1, Ordering::Relaxed);
    let root = std::env::temp_dir().join(format!("arf_mcp_disc_test_{id}"));
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

    (root.clone(), Cleanup(root))
}

struct Cleanup(PathBuf);
impl Drop for Cleanup {
    fn drop(&mut self) {
        if self.0.exists() {
            let _ = fs::remove_dir_all(&self.0);
        }
    }
}

fn echo_toml(name: &str, runtime: &str) -> String {
    format!(
        r#"name = "{name}"
description = "Echo tool"
runtime = "{runtime}"
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

// ═══════════════════════════════════════════════════════════════
// FsDiscovery::scan — 8 tests
// ═══════════════════════════════════════════════════════════════

#[test]
fn scan_empty_root() {
    let (root, _cleanup) = setup_root(&[], &[]);
    let dm = FsDiscovery::scan(root).unwrap();
    assert_eq!(dm.list_tools().len(), 0);
    assert_eq!(dm.list_skills().len(), 0);
}

#[test]
fn scan_single_tool() {
    let (root, _cleanup) = setup_root(&[("echo", &echo_toml("echo", "python"), echo_script())], &[]);
    let dm = FsDiscovery::scan(root).unwrap();
    assert_eq!(dm.list_tools().len(), 1);
    assert_eq!(dm.list_tools()[0].name, "echo");
    assert_eq!(dm.list_tools()[0].description, "Echo tool");
    assert!(dm.resolve_tool("echo").is_some());
}

#[test]
fn scan_multiple_tools() {
    let (root, _cleanup) = setup_root(
        &[
            ("t1", &echo_toml("t1", "python"), echo_script()),
            ("t2", &echo_toml("t2", "bash"), "#!/bin/bash\ncat\n"),
        ],
        &[],
    );
    let dm = FsDiscovery::scan(root).unwrap();
    assert_eq!(dm.list_tools().len(), 2);
    assert!(dm.resolve_tool("t1").is_some());
    assert!(dm.resolve_tool("t2").is_some());
}

#[test]
fn scan_single_skill() {
    let (root, _cleanup) = setup_root(&[], &[("react", &minimal_skill("react", "React skill"))]);
    let dm = FsDiscovery::scan(root).unwrap();
    assert_eq!(dm.list_skills().len(), 1);
    assert_eq!(dm.list_skills()[0].name, "react");
    assert!(dm.resolve_skill("react").is_some());
}

#[test]
fn scan_tools_and_skills_coexist() {
    let (root, _cleanup) = setup_root(
        &[("echo", &echo_toml("echo", "python"), echo_script())],
        &[("react", &minimal_skill("react", "React skill"))],
    );
    let dm = FsDiscovery::scan(root).unwrap();
    assert_eq!(dm.list_tools().len(), 1);
    assert_eq!(dm.list_skills().len(), 1);
}

#[test]
fn scan_invalid_tool_toml_skipped() {
    let (root, _cleanup) = setup_root(
        &[
            ("good", &echo_toml("good", "python"), echo_script()),
            ("bad", "not valid toml {{{", echo_script()),
        ],
        &[],
    );
    let dm = FsDiscovery::scan(root).unwrap();
    assert_eq!(dm.list_tools().len(), 1); // only "good"
    assert!(dm.resolve_tool("good").is_some());
}

#[test]
fn scan_directory_without_toml_skipped() {
    let (root, _cleanup) = setup_root(
        &[("good", &echo_toml("good", "python"), echo_script())],
        &[],
    );
    // Create a directory under tools/ without tool.toml
    let not_a_tool = root.join("tools").join("not-a-tool");
    fs::create_dir_all(&not_a_tool).unwrap();
    fs::write(not_a_tool.join("main.py"), echo_script()).unwrap();

    let dm = FsDiscovery::scan(root).unwrap();
    assert_eq!(dm.list_tools().len(), 1); // "not-a-tool" skipped
}

#[test]
fn scan_root_not_exists_returns_error() {
    let root = PathBuf::from("/tmp/arf_mcp_nonexistent_root_xyz");
    let _ = fs::remove_dir_all(&root);
    let result = FsDiscovery::scan(root);
    assert!(result.is_err());
    let err = result.unwrap_err();
    assert!(matches!(err, crate::error::McpError::Discovery { .. }));
}

// [覆盖] FsDiscovery scan fixtures/tools/ → 9 tools (3 tools × 3 runtimes)
#[test]
fn scan_fixtures_discovers_all_nine_tools() {
    let fixture_root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures");
    let dm = FsDiscovery::scan(fixture_root).unwrap();

    let tools = dm.list_tools();
    assert_eq!(tools.len(), 9, "fixtures should have exactly 9 tools (3 × 3 runtimes)");

    let names: Vec<&str> = tools.iter().map(|t| t.name.as_str()).collect();
    assert_eq!(names.iter().filter(|n| **n == "read_file").count(), 3);
    assert_eq!(names.iter().filter(|n| **n == "write_file").count(), 3);
    assert_eq!(names.iter().filter(|n| **n == "search_content").count(), 3);
}
