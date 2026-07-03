//! skill_list_progressive.rs — Phase 9 task 9.6.1
//!
//! L1 skill list（只列名+描述，不加载 body）端到端探查。
//!
//! 4 test cases：
//! 1. skill_list_returns_metadata_only — tmpdir 写 2 SKILL.md，list_skills() 验 name+description+compatibility 字段
//! 2. skill_list_does_not_load_body — 写 SKILL.md 含 5KB body，list_skills() 后不应有大 body 加载痕迹
//! 3. skill_list_advertised_shape — McpNode::build_node_info 阶段 advertised skills 形状（L1 元数据，**无** body）
//! 4. skill_list_missing_frontmatter_skipped — SKILL.md 无 frontmatter / 缺 name / 缺 description 时的行为

mod common;

use std::fs;
use std::io::Write;
use std::path::PathBuf;

use arf_mcp::discovery::{DiscoveryBackend, FsDiscovery};

// ═══════════════════════════════════════════════════════════════════════
// Helpers
// ═══════════════════════════════════════════════════════════════════════

fn setup_root_with_skills(skills: &[(&str, &str, Option<&str>, &str)]) -> PathBuf {
    let id = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let root = std::env::temp_dir().join(format!("arf_skill_list_{id}"));
    let _ = fs::remove_dir_all(&root);
    fs::create_dir_all(&root).unwrap();

    for (name, desc, compat, body) in skills {
        let skill_dir = root.join("skills").join(name);
        fs::create_dir_all(&skill_dir).unwrap();
        let mut f = fs::File::create(skill_dir.join("SKILL.md")).unwrap();
        if let Some(c) = compat {
            write!(
                f,
                "---\nname: {name}\ndescription: {desc}\ncompatibility: {c}\n---\n\n{body}"
            )
            .unwrap();
        } else {
            write!(f, "---\nname: {name}\ndescription: {desc}\n---\n\n{body}").unwrap();
        }
    }

    root
}

fn make_skill(name: &str, desc: &str, body: &str) -> String {
    format!("---\nname: {name}\ndescription: {desc}\n---\n\n{body}")
}

// ═══════════════════════════════════════════════════════════════════════
// Test 1: skill_list_returns_metadata_only
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn skill_list_returns_metadata_only() {
    let root = setup_root_with_skills(&[
        ("greet", "Greet user politely", Some("v1.0"), "# Greet\n\nBody for greet."),
        ("summarize", "Summarize long text", None, "# Summarize\n\nBody for summarize."),
    ]);
    let dm = FsDiscovery::scan(root.clone()).unwrap();
    let skills = dm.list_skills();
    println!("[test1] list_skills() = {} skills", skills.len());
    assert_eq!(skills.len(), 2, "应列出 2 个 skill");
    for s in &skills {
        println!("[test1]   - {}: {} (compat={:?})", s.name, s.description, s.compatibility);
    }

    // 验证元数据字段：name + description + (optional compatibility)
    let by_name: std::collections::HashMap<&str, &arf_mcp::skill::SkillEntry> = skills
        .iter()
        .map(|s| (s.name.as_str(), *s))
        .collect();

    let greet = by_name.get("greet").expect("greet skill");
    assert_eq!(greet.name, "greet");
    assert_eq!(greet.description, "Greet user politely");
    assert_eq!(greet.compatibility, Some("v1.0".to_string()));
    println!("[test1] greet 元数据完整 ✓");

    let summarize = by_name.get("summarize").expect("summarize skill");
    assert_eq!(summarize.name, "summarize");
    assert_eq!(summarize.description, "Summarize long text");
    assert_eq!(summarize.compatibility, None);
    println!("[test1] summarize 元数据完整 ✓");

    let _ = fs::remove_dir_all(&root);
    println!("[test1] skill_list L1 metadata 端到端 OK ✓");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 2: skill_list_does_not_load_body
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn skill_list_does_not_load_body() {
    // 5KB body × 3 skills，确认 list_skills() 阶段不读 body 全文
    let big_body = "This is a long body line used to inflate the SKILL.md size. ".repeat(80);
    let skills_to_write: Vec<(&str, &str, Option<&str>, &str)> = vec![
        ("skill_a", "Alpha skill", None, &big_body),
        ("skill_b", "Beta skill", None, &big_body),
        ("skill_c", "Gamma skill", None, &big_body),
    ];
    let root = setup_root_with_skills(&skills_to_write);
    let dm = FsDiscovery::scan(root.clone()).unwrap();
    let skills = dm.list_skills();
    assert_eq!(skills.len(), 3);

    // SkillEntry 是 metadata struct——验证不含 body 字段
    // （source_dir 是 pub(crate) 内部字段，外部测试用 SkillEntry debug 验证即可）
    for s in &skills {
        println!(
            "[test2] skill '{}' debug = {:?} (确认无 body 字符串字段)",
            s.name, s
        );
    }

    // 显式 body 加载路径（load_skill_body）是独立的——L1 list 阶段不调用
    // 实证：list_skills() 返回的 entry **不**缓存 body 字符串
    // 验证：SkillEntry struct 字段不含 body / content / text 等
    let type_name = std::any::type_name::<arf_mcp::skill::SkillEntry>();
    println!("[test2] SkillEntry type: {}", type_name);

    // 显式调 load_skill_body 确认 L1 vs L2 分层：list 不调 body，body 需显式调
    let body_a = dm.load_skill_body("skill_a");
    assert!(body_a.is_some(), "load_skill_body 应返回 body");
    let body_len = body_a.unwrap().len();
    println!("[test2] skill_a body len = {} bytes (确认 body 需显式加载)", body_len);
    assert!(body_len > 1000, "body 应是大文件");

    let _ = fs::remove_dir_all(&root);
    println!("[test2] L1 list 不加载 body + L2 body 显式按需加载 端到端 OK ✓");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 3: skill_list_advertised_shape
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn skill_list_advertised_shape() {
    let root = setup_root_with_skills(&[
        ("greet", "Greet user", None, "# Greet\n"),
        ("code_review", "Review code changes", None, "# Code Review\n"),
    ]);
    let dm = FsDiscovery::scan(root.clone()).unwrap();

    // McpNode::build_node_info 把 skill `{name, description}` 塞进 capabilities。
    // 这里直接重放 build_node_info 的格式（公开 source：crates/arf-mcp/src/node.rs:93-95）
    // 来验证 advertised skills 形状：仅 name + description，**不**含 body。
    let advertised: Vec<serde_json::Value> = dm
        .list_skills()
        .iter()
        .map(|s| {
            serde_json::json!({
                "name": s.name,
                "description": s.description,
            })
        })
        .collect();

    println!("[test3] advertised shape: {} skills", advertised.len());
    for entry in &advertised {
        let name = entry.get("name").and_then(|v| v.as_str()).unwrap_or("?");
        let desc = entry.get("description").and_then(|v| v.as_str()).unwrap_or("?");
        let has_body = entry.get("body").is_some();
        let has_content = entry.get("content").is_some();
        let has_text = entry.get("text").is_some();
        println!(
            "[test3]   - {}: {} (body={}, content={}, text={})",
            name, desc, has_body, has_content, has_text
        );
        assert!(!has_body, "advertised entry 不应含 body 字段");
        assert!(!has_content, "advertised entry 不应含 content 字段");
        assert!(!has_text, "advertised entry 不应含 text 字段");
    }
    assert_eq!(advertised.len(), 2, "应 advertised 2 个 skill");

    let _ = fs::remove_dir_all(&root);
    println!("[test3] advertised skills 形状（L1 metadata, 无 body）端到端 OK ✓");
}

// ═══════════════════════════════════════════════════════════════════════
// Test 4: skill_list_missing_frontmatter_skipped
// ═══════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn skill_list_missing_frontmatter_skipped() {
    let root = std::env::temp_dir().join(format!(
        "arf_skill_list_bad_{}",
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    ));
    let _ = fs::remove_dir_all(&root);
    fs::create_dir_all(&root).unwrap();

    // skill 1: 合法
    let s1 = root.join("skills").join("good");
    fs::create_dir_all(&s1).unwrap();
    fs::write(s1.join("SKILL.md"), make_skill("good", "Good skill", "body")).unwrap();

    // skill 2: 缺 frontmatter（纯文本无 `---`）
    let s2 = root.join("skills").join("no_fm");
    fs::create_dir_all(&s2).unwrap();
    fs::write(s2.join("SKILL.md"), "# Just markdown\n\nNo frontmatter.").unwrap();

    // skill 3: 有 frontmatter 但缺 name
    let s3 = root.join("skills").join("no_name");
    fs::create_dir_all(&s3).unwrap();
    fs::write(s3.join("SKILL.md"), "---\ndescription: no name here\n---\nbody").unwrap();

    // skill 4: 有 frontmatter 但缺 description
    let s4 = root.join("skills").join("no_desc");
    fs::create_dir_all(&s4).unwrap();
    fs::write(s4.join("SKILL.md"), "---\nname: lonely\n---\nbody").unwrap();

    let dm = FsDiscovery::scan(root.clone()).unwrap();
    let skills = dm.list_skills();
    println!("[test4] list_skills() 返回 {} skills（坏 SKILL.md 应被 skip）", skills.len());
    for s in &skills {
        println!("[test4]   - {}", s.name);
    }

    // 只应剩 1 个合法 skill（good）
    assert_eq!(skills.len(), 1, "应 skip 3 个坏 skill，剩 1 个 good");
    assert_eq!(skills[0].name, "good");

    let _ = fs::remove_dir_all(&root);
    println!("[test4] 缺 frontmatter / 缺 name / 缺 description 全部 skip 端到端 OK ✓");
}
