use std::fs;
use std::io::Write;
use std::path::PathBuf;
use std::sync::atomic::Ordering;

use crate::config::ScriptRuntime;
use crate::skill::SkillIndex;

/// Create a temp skills directory structure for testing.
fn setup_skills_dir(skills: &[(&str, &str)]) -> (PathBuf, Cleanup) {
    let id = super::TEST_COUNTER.fetch_add(1, Ordering::Relaxed);
    let root = std::env::temp_dir().join(format!("arf_mcp_skill_test_{id}"));
    let _ = fs::remove_dir_all(&root);
    fs::create_dir_all(&root).unwrap();

    let skills_dir = root.join("skills");
    fs::create_dir_all(&skills_dir).unwrap();

    for (name, frontmatter_and_body) in skills {
        let skill_dir = skills_dir.join(name);
        fs::create_dir_all(&skill_dir).unwrap();
        fs::create_dir_all(skill_dir.join("scripts")).ok();
        fs::create_dir_all(skill_dir.join("references")).ok();
        fs::create_dir_all(skill_dir.join("assets")).ok();
        let mut file = fs::File::create(skill_dir.join("SKILL.md")).unwrap();
        file.write_all(frontmatter_and_body.as_bytes()).unwrap();
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

fn minimal_skill(name: &str, description: &str) -> String {
    format!(
        "---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n\nSome body content.\n"
    )
}

// ═══════════════════════════════════════════════════════════════
// SkillIndex::scan — 9 tests
// ═══════════════════════════════════════════════════════════════

#[test]
fn scan_empty_directory() {
    let (root, _cleanup) = setup_skills_dir(&[]);
    let index = SkillIndex::scan(root);
    assert_eq!(index.list_index().len(), 0);
}

#[test]
fn scan_single_valid_skill() {
    let (root, _cleanup) =
        setup_skills_dir(&[("my-skill", &minimal_skill("my-skill", "Does things"))]);
    let index = SkillIndex::scan(root);
    let entries = index.list_index();
    assert_eq!(entries.len(), 1);
    assert_eq!(entries[0].name, "my-skill");
    assert_eq!(entries[0].description, "Does things");
    assert!(entries[0].compatibility.is_none());
}

#[test]
fn scan_multiple_skills() {
    let (root, _cleanup) = setup_skills_dir(&[
        ("skill-a", &minimal_skill("skill-a", "First")),
        ("skill-b", &minimal_skill("skill-b", "Second")),
        ("skill-c", &minimal_skill("skill-c", "Third")),
    ]);
    let index = SkillIndex::scan(root);
    assert_eq!(index.list_index().len(), 3);
}

#[test]
fn scan_skill_with_compatibility() {
    let content =
        "---\nname: node-tool\ndescription: Node tool\ncompatibility: node>=18\n---\n\n# Body\n";
    let (root, _cleanup) = setup_skills_dir(&[("node-tool", &content.to_string())]);
    let index = SkillIndex::scan(root);
    let entry = index.resolve("node-tool").unwrap();
    assert_eq!(entry.compatibility.as_deref(), Some("node>=18"));
}

#[test]
fn scan_skip_directory_without_skill_md() {
    let (root, _cleanup) =
        setup_skills_dir(&[("real-skill", &minimal_skill("real-skill", "A real skill"))]);
    let empty_dir = root.join("skills").join("not-a-skill");
    fs::create_dir_all(&empty_dir).unwrap();
    let index = SkillIndex::scan(root);
    assert_eq!(index.list_index().len(), 1);
}

#[test]
fn scan_no_skills_directory() {
    let id = super::TEST_COUNTER.fetch_add(1, Ordering::Relaxed);
    let root = std::env::temp_dir().join(format!("arf_mcp_no_skills_{id}"));
    let _ = fs::remove_dir_all(&root);
    fs::create_dir_all(&root).unwrap();
    let index = SkillIndex::scan(root.clone());
    assert_eq!(index.list_index().len(), 0);
    let _ = fs::remove_dir_all(&root);
}

#[test]
fn scan_missing_name_field() {
    let content = "---\ndescription: No name field\n---\n\n# Body\n";
    let (root, _cleanup) = setup_skills_dir(&[("bad-skill", &content.to_string())]);
    let index = SkillIndex::scan(root);
    assert_eq!(index.list_index().len(), 0);
}

#[test]
fn scan_invalid_yaml_frontmatter() {
    let content = "---\nthis is : not : valid yaml : :\n---\n\n# Body\n";
    let (root, _cleanup) = setup_skills_dir(&[("bad-yaml", &content.to_string())]);
    let index = SkillIndex::scan(root);
    assert_eq!(index.list_index().len(), 0);
}

#[test]
fn scan_body_contains_dashes() {
    let content =
        "---\nname: resilient\ndescription: Handles body dashes\n---\n\n# Title\n\nBody with --- in it.\n";
    let (root, _cleanup) = setup_skills_dir(&[("resilient", &content.to_string())]);
    let index = SkillIndex::scan(root);
    let entry = index.resolve("resilient").unwrap();
    assert_eq!(entry.name, "resilient");
}

// ═══════════════════════════════════════════════════════════════
// ScriptMeta + load_script_meta — 4 tests
// ═══════════════════════════════════════════════════════════════

#[test]
fn load_script_meta_with_toml() {
    let skill_md = minimal_skill("s", "S");
    let (root, _cleanup) = setup_skills_dir(&[("s", &skill_md)]);
    let script_dir = root.join("skills").join("s").join("scripts");
    fs::create_dir_all(&script_dir).unwrap();
    fs::write(script_dir.join("gen.py"), "print('hi')").unwrap();
    fs::write(
        script_dir.join("gen.toml"),
        r#"description = "Generate code"
runtime = "python"
timeout_ms = 10000

[params_schema]
type = "object"
required = ["name"]

[params_schema.properties.name]
type = "string"
"#,
    )
    .unwrap();

    let index = SkillIndex::scan(root);
    let meta = index.load_script_meta("s", "scripts/gen.py").unwrap();
    assert_eq!(meta.description, "Generate code");
    assert_eq!(meta.runtime, ScriptRuntime::Python);
    assert_eq!(meta.timeout_ms, Some(10000));
    assert_eq!(meta.params_schema["type"], "object");
}

#[test]
fn load_script_meta_without_toml() {
    let (root, _cleanup) = setup_skills_dir(&[("s", &minimal_skill("s", "S"))]);
    let script_dir = root.join("skills").join("s").join("scripts");
    fs::create_dir_all(&script_dir).unwrap();
    fs::write(script_dir.join("gen.py"), "print('hi')").unwrap();

    let index = SkillIndex::scan(root);
    assert!(index.load_script_meta("s", "scripts/gen.py").is_none());
}

#[test]
fn load_script_meta_skill_not_found() {
    let (root, _cleanup) = setup_skills_dir(&[]);
    let index = SkillIndex::scan(root);
    assert!(index
        .load_script_meta("ghost", "scripts/x.py")
        .is_none());
}

#[test]
fn load_script_meta_invalid_toml() {
    let (root, _cleanup) = setup_skills_dir(&[("s", &minimal_skill("s", "S"))]);
    let script_dir = root.join("skills").join("s").join("scripts");
    fs::create_dir_all(&script_dir).unwrap();
    fs::write(script_dir.join("gen.py"), "print('hi')").unwrap();
    fs::write(script_dir.join("gen.toml"), "not valid toml {{{").unwrap();

    let index = SkillIndex::scan(root);
    assert!(index.load_script_meta("s", "scripts/gen.py").is_none());
}

// ═══════════════════════════════════════════════════════════════
// resolve + list_index — 3 tests
// ═══════════════════════════════════════════════════════════════

#[test]
fn resolve_existing() {
    let (root, _cleanup) = setup_skills_dir(&[("test", &minimal_skill("test", "A test"))]);
    let index = SkillIndex::scan(root);
    assert!(index.resolve("test").is_some());
}

#[test]
fn resolve_nonexistent() {
    let (root, _cleanup) = setup_skills_dir(&[("test", &minimal_skill("test", "A test"))]);
    let index = SkillIndex::scan(root);
    assert!(index.resolve("ghost").is_none());
}

#[test]
fn list_index_returns_all() {
    let (root, _cleanup) = setup_skills_dir(&[
        ("a", &minimal_skill("a", "A")),
        ("b", &minimal_skill("b", "B")),
    ]);
    let index = SkillIndex::scan(root);
    let mut names: Vec<&str> = index.list_index().iter().map(|e| e.name.as_str()).collect();
    names.sort();
    assert_eq!(names, vec!["a", "b"]);
}

// ═══════════════════════════════════════════════════════════════
// L2: load_body + load_resources — 4 tests
// ═══════════════════════════════════════════════════════════════

#[test]
fn load_body_returns_full_content() {
    let content =
        "---\nname: full\ndescription: Full body test\n---\n\n# Title\n\nContent here.\n";
    let (root, _cleanup) = setup_skills_dir(&[("full", &content.to_string())]);
    let index = SkillIndex::scan(root);
    let body = index.load_body("full").unwrap();
    assert!(body.contains("---"));
    assert!(body.contains("# Title"));
    assert!(body.contains("Content here."));
}

#[test]
fn load_body_nonexistent() {
    let (root, _cleanup) = setup_skills_dir(&[]);
    let index = SkillIndex::scan(root);
    assert!(index.load_body("ghost").is_none());
}

#[test]
fn load_resources_lists_files() {
    let (root, _cleanup) = setup_skills_dir(&[("rich", &minimal_skill("rich", "Rich skill"))]);
    let skill_dir = root.join("skills").join("rich");
    fs::write(skill_dir.join("scripts").join("gen.py"), "print('hi')").unwrap();
    fs::write(skill_dir.join("references").join("api.md"), "# API").unwrap();
    fs::write(skill_dir.join("assets").join("template.tsx"), "// template").unwrap();

    let index = SkillIndex::scan(root);
    let resources = index.load_resources("rich").unwrap();
    assert_eq!(resources.scripts, vec!["gen.py"]);
    assert_eq!(resources.references, vec!["api.md"]);
    assert_eq!(resources.assets, vec!["template.tsx"]);
}

#[test]
fn load_resources_nonexistent() {
    let (root, _cleanup) = setup_skills_dir(&[]);
    let index = SkillIndex::scan(root);
    assert!(index.load_resources("ghost").is_none());
}

// ═══════════════════════════════════════════════════════════════
// L3: load_resource_file — 7 tests
// ═══════════════════════════════════════════════════════════════

#[test]
fn load_resource_file_success() {
    let (root, _cleanup) = setup_skills_dir(&[("s", &minimal_skill("s", "S"))]);
    let script_path = root.join("skills").join("s").join("scripts").join("run.py");
    fs::create_dir_all(script_path.parent().unwrap()).unwrap();
    fs::write(&script_path, "print('hello')").unwrap();

    let index = SkillIndex::scan(root);
    let res = index.load_resource_file("s", "scripts/run.py").unwrap();
    assert_eq!(res.content, "print('hello')");
    assert!(res.description.is_none());
    assert!(res.params_schema.is_none());
}

#[test]
fn load_resource_file_with_script_meta() {
    let (root, _cleanup) = setup_skills_dir(&[("s", &minimal_skill("s", "S"))]);
    let script_dir = root.join("skills").join("s").join("scripts");
    fs::create_dir_all(&script_dir).unwrap();
    fs::write(script_dir.join("gen.py"), "print(1)").unwrap();
    fs::write(
        script_dir.join("gen.toml"),
        r#"description = "Generate"
runtime = "python"
[params_schema]
type = "object"
"#,
    )
    .unwrap();

    let index = SkillIndex::scan(root);
    let res = index.load_resource_file("s", "scripts/gen.py").unwrap();
    assert_eq!(res.content, "print(1)");
    assert_eq!(res.description.unwrap(), "Generate");
    assert_eq!(res.params_schema.unwrap()["type"], "object");
}

#[test]
fn load_resource_file_reference_no_metadata() {
    let (root, _cleanup) = setup_skills_dir(&[("s", &minimal_skill("s", "S"))]);
    let ref_dir = root.join("skills").join("s").join("references");
    fs::create_dir_all(&ref_dir).unwrap();
    fs::write(ref_dir.join("api.md"), "# API").unwrap();

    let index = SkillIndex::scan(root);
    let res = index.load_resource_file("s", "references/api.md").unwrap();
    assert_eq!(res.content, "# API");
    assert!(res.description.is_none());
}

#[test]
fn load_resource_file_rejects_parent_traversal() {
    let (root, _cleanup) = setup_skills_dir(&[("s", &minimal_skill("s", "S"))]);
    let index = SkillIndex::scan(root);
    let result = index.load_resource_file("s", "scripts/../../etc/passwd");
    assert!(result.is_err());
    assert!(result.unwrap_err().contains("path traversal"));
}

#[test]
fn load_resource_file_rejects_absolute_path() {
    let (root, _cleanup) = setup_skills_dir(&[("s", &minimal_skill("s", "S"))]);
    let index = SkillIndex::scan(root);
    let result = index.load_resource_file("s", "/etc/passwd");
    assert!(result.is_err());
    assert!(result.unwrap_err().contains("absolute path"));
}

#[test]
fn load_resource_file_rejects_invalid_prefix() {
    let (root, _cleanup) = setup_skills_dir(&[("s", &minimal_skill("s", "S"))]);
    let index = SkillIndex::scan(root);
    let result = index.load_resource_file("s", "secret.key");
    assert!(result.is_err());
}

#[test]
fn load_resource_file_not_found() {
    let (root, _cleanup) = setup_skills_dir(&[("s", &minimal_skill("s", "S"))]);
    let index = SkillIndex::scan(root);
    let result = index.load_resource_file("s", "scripts/ghost.py");
    assert!(result.is_err());
}

// ═══════════════════════════════════════════════════════════════
// run_script — 2 tests
// ═══════════════════════════════════════════════════════════════

#[tokio::test]
async fn run_script_python_echo() {
    let (root, _cleanup) = setup_skills_dir(&[("s", &minimal_skill("s", "S"))]);
    let script_dir = root.join("skills").join("s").join("scripts");
    fs::create_dir_all(&script_dir).unwrap();
    fs::write(
        script_dir.join("echo.py"),
        "import sys, json\nparams = json.loads(sys.stdin.read())\nprint(json.dumps(params))\n",
    )
    .unwrap();

    let index = SkillIndex::scan(root);
    let result = index
        .run_script("s", "scripts/echo.py", serde_json::json!({"x": 1}))
        .await
        .unwrap();
    assert_eq!(result["x"], 1);
}

#[tokio::test]
async fn run_script_without_toml_defaults() {
    let (root, _cleanup) = setup_skills_dir(&[("s", &minimal_skill("s", "S"))]);
    let script_dir = root.join("skills").join("s").join("scripts");
    fs::create_dir_all(&script_dir).unwrap();
    fs::write(
        script_dir.join("echo.py"),
        "import sys, json\nprint(json.dumps({'ok': True}))\n",
    )
    .unwrap();

    let index = SkillIndex::scan(root);
    let result = index
        .run_script("s", "scripts/echo.py", serde_json::json!({}))
        .await
        .unwrap();
    assert_eq!(result["ok"], true);
}
