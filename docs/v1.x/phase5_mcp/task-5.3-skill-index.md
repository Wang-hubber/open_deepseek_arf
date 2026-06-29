# 任务 5.3：SkillIndex

> Phase 5 — MCP 第三项任务
> 父文档：`docs/v1.x/phase5_mcp/phase5-mcp-design.md`
> 依赖：Task 5.1 (类型定义)

## 设计思路

**Skill 是纯数据——"给 AI 的工作手册"。** 不包含可执行逻辑，不自带 Tool trait。LLM 读取 body 后自己决定调什么工具、用什么资源。MCP 只负责存储、索引和按需出货。

`SkillIndex` 扫描 `{root}/skills/*/SKILL.md`，解析 YAML frontmatter 构建 L1 索引，按需加载 L2（全文）和 L3（单个资源文件）。自检校验 kebab-case 命名和资源文件存在性——warning 不阻断。

三层渐进式披露：

| 层级 | 触发 | 内容 | 方法 |
|------|------|------|------|
| L1 | Agent 启动 | `{name, description}` | `scan()` → `list_index()` |
| L2 | LLM 决定使用 | body 全文 + 资源文件清单 | `load_body()` + `load_resources()` |
| L3 | LLM 需要具体文件 | 单个资源文件内容 | `load_resource_file()` |

| 文件 | 操作 | 内容 |
|------|------|------|
| `Cargo.toml` | 更新 | 添加 `serde_yaml` |
| `skill.rs` | 新建 | `SkillEntry`、`SkillResources`、`SkillIndex` + 实现 |
| `lib.rs` | 更新 | `pub mod skill;` |

---

## 代码实现

### `crates/arf-mcp/Cargo.toml` 更新

```toml
[dependencies]
# ... existing ...
serde_yaml = "0.9"
```

### `crates/arf-mcp/src/skill.rs` — 新建

```rust
use std::collections::HashMap;
use std::fs;
use std::path::PathBuf;

use serde::Deserialize;

// ── SkillFrontmatter (internal, not pub) ────────────────────────────

/// Parsed YAML frontmatter from SKILL.md.
#[derive(Debug, Deserialize)]
struct SkillFrontmatter {
    name: String,
    description: String,
    #[serde(default)]
    compatibility: Option<String>,
}

// ── SkillEntry ──────────────────────────────────────────────────────

/// A skill registered by MCP — L1 metadata only.
///
/// The full body and resources are loaded on demand via
/// `load_body()` (L2) and `load_resource_file()` (L3).
#[derive(Debug, Clone)]
pub struct SkillEntry {
    /// Unique skill name (kebab-case). Parsed from SKILL.md frontmatter.
    pub name: String,
    /// Human-readable description with trigger phrases and keywords.
    pub description: String,
    /// Optional compatibility constraint (e.g. "node>=18").
    pub compatibility: Option<String>,
    /// Path to skill directory (for body/resource loading). Internal.
    pub(crate) source_dir: PathBuf,
}

// ── SkillResources ──────────────────────────────────────────────────

/// File manifest for a skill's resource directories.
#[derive(Debug, Clone)]
pub struct SkillResources {
    /// Files under scripts/ (e.g. ["generate-component.py"]).
    pub scripts: Vec<String>,
    /// Files under references/ (e.g. ["api-guide.md"]).
    pub references: Vec<String>,
    /// Files under assets/ (e.g. ["template.tsx"]).
    pub assets: Vec<String>,
}

// ── SkillIndex ──────────────────────────────────────────────────────

/// Scan, index, and retrieve lazy-loaded skills.
///
/// Scans `<root>/skills/*/SKILL.md`, parses YAML frontmatter for L1 metadata.
/// Body and resources loaded on demand. MCP-internal — Engine never sees this.
pub struct SkillIndex {
    root: PathBuf,
    entries: HashMap<String, SkillEntry>,
}

impl SkillIndex {
    /// Scan `<root>/skills/*/SKILL.md` and build the L1 index.
    ///
    /// Returns a SkillIndex even if no skills are found (empty index).
    /// Validation warnings (kebab-case, missing files) are logged to stderr
    /// but never block — a skill with warnings is still registered.
    pub fn scan(root: PathBuf) -> Self {
        let mut entries = HashMap::new();

        let skills_dir = root.join("skills");
        if !skills_dir.is_dir() {
            return Self { root, entries };
        }

        if let Ok(dir_iter) = fs::read_dir(&skills_dir) {
            for entry in dir_iter.flatten() {
                let skill_dir = entry.path();
                if !skill_dir.is_dir() {
                    continue;
                }

                let skill_md = skill_dir.join("SKILL.md");
                let content = match fs::read_to_string(&skill_md) {
                    Ok(c) => c,
                    Err(_) => continue, // no SKILL.md → not a skill
                };

                // Parse YAML frontmatter between --- delimiters
                let fm = match parse_frontmatter(&content) {
                    Some(fm) => fm,
                    None => {
                        eprintln!(
                            "WARNING [SkillIndex]: no valid frontmatter in {}",
                            skill_md.display()
                        );
                        continue;
                    }
                };

                // Kebab-case check (warning only)
                if !is_kebab_case(&fm.name) {
                    eprintln!(
                        "WARNING [SkillIndex]: name '{}' is not kebab-case (in {})",
                        fm.name,
                        skill_md.display()
                    );
                }

                entries.insert(
                    fm.name.clone(),
                    SkillEntry {
                        name: fm.name,
                        description: fm.description,
                        compatibility: fm.compatibility,
                        source_dir: skill_dir,
                    },
                );
            }
        }

        Self { root, entries }
    }

    /// Look up a skill by name.
    pub fn resolve(&self, name: &str) -> Option<&SkillEntry> {
        self.entries.get(name)
    }

    /// List all L1 metadata entries (for node_online broadcast).
    pub fn list_index(&self) -> Vec<&SkillEntry> {
        self.entries.values().collect()
    }

    /// Load the full SKILL.md body (L2).
    pub fn load_body(&self, name: &str) -> Option<String> {
        let entry = self.entries.get(name)?;
        let path = entry.source_dir.join("SKILL.md");
        fs::read_to_string(&path).ok()
    }

    /// List resource files in scripts/, references/, assets/ (L2 attached).
    pub fn load_resources(&self, name: &str) -> Option<SkillResources> {
        let entry = self.entries.get(name)?;
        Some(SkillResources {
            scripts: list_files(&entry.source_dir.join("scripts")),
            references: list_files(&entry.source_dir.join("references")),
            assets: list_files(&entry.source_dir.join("assets")),
        })
    }

    /// Load a single resource file (L3).
    ///
    /// Security: rejects `../` and absolute paths. Only files within
    /// scripts/, references/, or assets/ are accessible.
    pub fn load_resource_file(&self, name: &str, resource_path: &str) -> Result<String, String> {
        // Path safety checks
        if resource_path.contains("..") {
            return Err("path traversal rejected: '..' not allowed".into());
        }
        if resource_path.starts_with('/') {
            return Err("absolute path rejected".into());
        }

        // Must be under one of the three allowed subdirectories
        let valid_prefixes = ["scripts/", "references/", "assets/"];
        let allowed = valid_prefixes.iter().any(|prefix| resource_path.starts_with(prefix));
        if !allowed {
            return Err(format!(
                "resource path must start with scripts/, references/, or assets/. Got: {resource_path}"
            ));
        }

        let entry = self.entries.get(name).ok_or("skill not found")?;
        let full_path = entry.source_dir.join(resource_path);

        // Canonicalize and verify we're still under the skill dir
        let canonical = full_path.canonicalize().map_err(|e| format!("file not found: {e}"))?;
        if !canonical.starts_with(&entry.source_dir) {
            return Err("path traversal rejected".into());
        }

        fs::read_to_string(&canonical).map_err(|e| format!("read error: {e}"))
    }
}

// ── Helpers ─────────────────────────────────────────────────────────

/// Extract YAML frontmatter from markdown content.
/// Returns the parsed SkillFrontmatter if delimiters and required fields are present.
fn parse_frontmatter(content: &str) -> Option<SkillFrontmatter> {
    let mut parts = content.splitn(3, "---");
    parts.next()?; // skip empty before first ---
    let yaml_str = parts.next()?; // YAML between first and second ---
    serde_yaml::from_str::<SkillFrontmatter>(yaml_str).ok()
}

/// Check if a string is valid kebab-case.
fn is_kebab_case(s: &str) -> bool {
    !s.is_empty()
        && s.chars()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-')
        && !s.starts_with('-')
        && !s.ends_with('-')
}

/// List all file names (not full paths) in a directory.
fn list_files(dir: &std::path::Path) -> Vec<String> {
    let mut files: Vec<String> = Vec::new();
    if let Ok(iter) = fs::read_dir(dir) {
        for entry in iter.flatten() {
            if entry.file_type().map(|t| t.is_file()).unwrap_or(false) {
                if let Some(name) = entry.file_name().to_str() {
                    files.push(name.to_string());
                }
            }
        }
    }
    files.sort();
    files
}
```

逐行解释：
- `SkillFrontmatter` — `pub(crate)` 非公开，YAML 反序列化的中间结构。`compatibility` 带 `#[serde(default)]`——未声明时为 None
- `parse_frontmatter()` — 按 `---` 分割 Markdown，取中间段用 `serde_yaml::from_str` 反序列化。`splitn(3, "---")` 处理 body 中可能出现的 `---`
- `is_kebab_case()` — 仅含小写字母、数字、连字符，不以连字符开头或结尾
- `list_files()` — 读取目录，仅保留文件名。不存在的目录 → 空 Vec，不报错
- `load_resource_file()` 安全策略：
  1. 拒绝 `..` 和绝对路径
  2. 要求 resource_path 以 `scripts/`、`references/` 或 `assets/` 开头
  3. `canonicalize()` 解析符号链接后再次检查是否在 skill 目录内——防止通过符号链接绕过

### `crates/arf-mcp/src/lib.rs` 更新

```rust
pub mod config;
pub mod script;
pub mod skill;
pub mod tool;
pub mod types;

#[cfg(test)]
mod tests;
```

---

## 测试

### 测试结构

```
crates/arf-mcp/src/tests/
├── mod.rs
├── skill_tests.rs       # 新建
├── ...
```

### `crates/arf-mcp/src/tests/mod.rs` 更新

```rust
mod config_tests;
mod script_tests;
mod skill_tests;
mod tool_tests;
mod types_tests;
```

### `crates/arf-mcp/src/tests/skill_tests.rs` — 新建

```rust
use std::fs;
use std::io::Write;
use std::path::PathBuf;

use crate::skill::SkillIndex;

/// Create a temp skills directory structure for testing.
/// Returns the root path and a cleanup guard.
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
        // Create subdirs
        fs::create_dir_all(skill_dir.join("scripts")).ok();
        fs::create_dir_all(skill_dir.join("references")).ok();
        fs::create_dir_all(skill_dir.join("assets")).ok();
        // Write SKILL.md
        let mut file = fs::File::create(skill_dir.join("SKILL.md")).unwrap();
        file.write_all(frontmatter_and_body.as_bytes()).unwrap();
    }

    (root, Cleanup(root))
}

struct Cleanup(PathBuf);
impl Drop for Cleanup {
    fn drop(&mut self) {
        if self.0.exists() {
            let _ = fs::remove_dir_all(&self.0);
        }
    }
}

/// A minimal valid SKILL.md.
fn minimal_skill(name: &str, description: &str) -> String {
    format!(
        "---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n\nSome body content.\n"
    )
}

// ═══════════════════════════════════════════════════════════════
// SkillIndex::scan — 9 tests
// ═══════════════════════════════════════════════════════════════

// [构造] 空目录 → 0 entries
#[test]
fn scan_empty_directory() {
    let (root, _cleanup) = setup_skills_dir(&[]);
    let index = SkillIndex::scan(root);
    assert_eq!(index.list_index().len(), 0);
}

// [构造] 单个有效 skill → 1 entry，字段正确
#[test]
fn scan_single_valid_skill() {
    let (root, _cleanup) = setup_skills_dir(&[("my-skill", &minimal_skill("my-skill", "Does things"))]);
    let index = SkillIndex::scan(root);
    let entries = index.list_index();
    assert_eq!(entries.len(), 1);
    assert_eq!(entries[0].name, "my-skill");
    assert_eq!(entries[0].description, "Does things");
    assert!(entries[0].compatibility.is_none());
}

// [构造] 多个 skill → 全部索引
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

// [构造] 带 compatibility 的 skill
#[test]
fn scan_skill_with_compatibility() {
    let content = "---\nname: node-tool\ndescription: Node tool\ncompatibility: node>=18\n---\n\n# Body\n";
    let (root, _cleanup) = setup_skills_dir(&[("node-tool", &content.to_string())]);
    let index = SkillIndex::scan(root);
    let entry = index.resolve("node-tool").unwrap();
    assert_eq!(entry.compatibility.as_deref(), Some("node>=18"));
}

// [边界] 目录下无 SKILL.md → 跳过
#[test]
fn scan_skip_directory_without_skill_md() {
    let (root, _cleanup) = setup_skills_dir(&[("real-skill", &minimal_skill("real-skill", "A real skill"))]);
    // Create a subdir under skills/ without SKILL.md
    let empty_dir = root.join("skills").join("not-a-skill");
    fs::create_dir_all(&empty_dir).unwrap();
    let index = SkillIndex::scan(root);
    assert_eq!(index.list_index().len(), 1);
}

// [边界] 无 skills 目录 → 空索引，不 panic
#[test]
fn scan_no_skills_directory() {
    let root = std::env::temp_dir().join(format!("arf_mcp_no_skills_{}", std::process::id()));
    let _ = fs::remove_dir_all(&root);
    fs::create_dir_all(&root).unwrap();
    let index = SkillIndex::scan(root);
    assert_eq!(index.list_index().len(), 0);
    let _ = fs::remove_dir_all(&root);
}

// [边界] 缺少 name 字段的 frontmatter → 跳过（serde_yaml 报错）
#[test]
fn scan_missing_name_field() {
    let content = "---\ndescription: No name field\n---\n\n# Body\n";
    let (root, _cleanup) = setup_skills_dir(&[("bad-skill", &content.to_string())]);
    let index = SkillIndex::scan(root);
    assert_eq!(index.list_index().len(), 0); // skipped
}

// [边界] 非法的 YAML frontmatter → 跳过
#[test]
fn scan_invalid_yaml_frontmatter() {
    let content = "---\nthis is : not : valid yaml : :\n---\n\n# Body\n";
    let (root, _cleanup) = setup_skills_dir(&[("bad-yaml", &content.to_string())]);
    let index = SkillIndex::scan(root);
    assert_eq!(index.list_index().len(), 0);
}

// [边界] body 中包含 --- 不影响 frontmatter 解析
#[test]
fn scan_body_contains_dashes() {
    let content = "---\nname: resilient\ndescription: Handles body dashes\n---\n\n# Title\n\nBody with --- in it.\n";
    let (root, _cleanup) = setup_skills_dir(&[("resilient", &content.to_string())]);
    let index = SkillIndex::scan(root);
    let entry = index.resolve("resilient").unwrap();
    assert_eq!(entry.name, "resilient");
}

// ═══════════════════════════════════════════════════════════════
// resolve + list_index — 3 tests
// ═══════════════════════════════════════════════════════════════

// [方法] resolve 存在 → Some
#[test]
fn resolve_existing() {
    let (root, _cleanup) = setup_skills_dir(&[("test", &minimal_skill("test", "A test"))]);
    let index = SkillIndex::scan(root);
    assert!(index.resolve("test").is_some());
}

// [方法] resolve 不存在 → None
#[test]
fn resolve_nonexistent() {
    let (root, _cleanup) = setup_skills_dir(&[("test", &minimal_skill("test", "A test"))]);
    let index = SkillIndex::scan(root);
    assert!(index.resolve("ghost").is_none());
}

// [方法] list_index 返回所有 entries
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

// [方法] load_body 返回 SKILL.md 全文
#[test]
fn load_body_returns_full_content() {
    let content = "---\nname: full\ndescription: Full body test\n---\n\n# Title\n\nContent here.\n";
    let (root, _cleanup) = setup_skills_dir(&[("full", &content.to_string())]);
    let index = SkillIndex::scan(root);
    let body = index.load_body("full").unwrap();
    assert!(body.contains("---"));
    assert!(body.contains("# Title"));
    assert!(body.contains("Content here."));
}

// [方法] load_body 不存在 → None
#[test]
fn load_body_nonexistent() {
    let (root, _cleanup) = setup_skills_dir(&[]);
    let index = SkillIndex::scan(root);
    assert!(index.load_body("ghost").is_none());
}

// [方法] load_resources 列出子目录文件
#[test]
fn load_resources_lists_files() {
    let (root, _cleanup) = setup_skills_dir(&[("rich", &minimal_skill("rich", "Rich skill"))]);
    // Create resource files
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

// [方法] load_resources 不存在 → None
#[test]
fn load_resources_nonexistent() {
    let (root, _cleanup) = setup_skills_dir(&[]);
    let index = SkillIndex::scan(root);
    assert!(index.load_resources("ghost").is_none());
}

// ═══════════════════════════════════════════════════════════════
// L3: load_resource_file — 5 tests
// ═══════════════════════════════════════════════════════════════

// [方法] 读取存在的资源文件
#[test]
fn load_resource_file_success() {
    let (root, _cleanup) = setup_skills_dir(&[("s", &minimal_skill("s", "S"))]);
    let script_path = root.join("skills").join("s").join("scripts").join("run.py");
    fs::create_dir_all(script_path.parent().unwrap()).unwrap();
    fs::write(&script_path, "print('hello')").unwrap();

    let index = SkillIndex::scan(root);
    let content = index.load_resource_file("s", "scripts/run.py").unwrap();
    assert_eq!(content, "print('hello')");
}

// [边界] path traversal (..) 被拒绝
#[test]
fn load_resource_file_rejects_parent_traversal() {
    let (root, _cleanup) = setup_skills_dir(&[("s", &minimal_skill("s", "S"))]);
    let index = SkillIndex::scan(root);
    let result = index.load_resource_file("s", "scripts/../../etc/passwd");
    assert!(result.is_err());
    assert!(result.unwrap_err().contains("path traversal"));
}

// [边界] 绝对路径被拒绝
#[test]
fn load_resource_file_rejects_absolute_path() {
    let (root, _cleanup) = setup_skills_dir(&[("s", &minimal_skill("s", "S"))]);
    let index = SkillIndex::scan(root);
    let result = index.load_resource_file("s", "/etc/passwd");
    assert!(result.is_err());
    assert!(result.unwrap_err().contains("absolute path"));
}

// [边界] 不在 scripts/references/assets 下的路径被拒绝
#[test]
fn load_resource_file_rejects_invalid_prefix() {
    let (root, _cleanup) = setup_skills_dir(&[("s", &minimal_skill("s", "S"))]);
    let index = SkillIndex::scan(root);
    let result = index.load_resource_file("s", "secret.key");
    assert!(result.is_err());
}

// [边界] 不存在的文件 → error
#[test]
fn load_resource_file_not_found() {
    let (root, _cleanup) = setup_skills_dir(&[("s", &minimal_skill("s", "S"))]);
    let index = SkillIndex::scan(root);
    let result = index.load_resource_file("s", "scripts/ghost.py");
    assert!(result.is_err());
}
```

测试辅助说明：
- `setup_skills_dir()` 接收 `&[(&str, &str)]` ——每个元组为 (目录名, SKILL.md 内容)，自动创建 `skills/{name}/` 及其子目录
- 使用 `AtomicU64` counter（与 script_tests 共享）确保并行测试不冲突
- Cleanup guard 自动清理

---

## 需要跨文件共享的测试工具

`AtomicU64 TEST_COUNTER` 在 `script_tests.rs` 中定义，`skill_tests.rs` 也需要它。提取到 `tests/mod.rs`：

```rust
// tests/mod.rs
use std::sync::atomic::AtomicU64;

pub(crate) static TEST_COUNTER: AtomicU64 = AtomicU64::new(0);

mod config_tests;
mod script_tests;
mod skill_tests;
mod tool_tests;
mod types_tests;
```

`script_tests.rs` 改为从 `mod.rs` 引用：

```rust
// 移除原有的 static TEST_COUNTER 声明
// 改用 super::TEST_COUNTER
```

---

## 验证命令

```bash
. "$HOME/.cargo/env" && cargo check -p arf-mcp
. "$HOME/.cargo/env" && cargo test -p arf-mcp
. "$HOME/.cargo/env" && cargo test --workspace
```

---

## 测试覆盖摘要

| 文件 | 新增测试 | 覆盖角度 |
|------|---------|---------|
| `skill_tests.rs` | 21 | `[构造][边界][方法]` — scan(9)、resolve/list_index(3)、L2 load(4)、L3 load(5) |
| **合计** | **21** | 累计 arf-mcp: 97 + 21 = **118 tests** |
