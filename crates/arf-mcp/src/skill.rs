use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};

use serde::Deserialize;

use crate::config::ScriptRuntime;
use crate::script::ScriptTool;
use crate::tool::Tool;

// ── SkillFrontmatter (internal) ──────────────────────────────────────

#[derive(Debug, Deserialize)]
struct SkillFrontmatter {
    name: String,
    description: String,
    #[serde(default)]
    compatibility: Option<String>,
}

// ── SkillEntry ──────────────────────────────────────────────────────

/// A skill registered by MCP — L1 metadata only.
#[derive(Debug, Clone)]
pub struct SkillEntry {
    pub name: String,
    pub description: String,
    pub compatibility: Option<String>,
    pub(crate) source_dir: PathBuf,
}

// ── SkillResources ──────────────────────────────────────────────────

/// File manifest for a skill's resource directories.
#[derive(Debug, Clone)]
pub struct SkillResources {
    pub scripts: Vec<String>,
    pub references: Vec<String>,
    pub assets: Vec<String>,
}

// ── ScriptMeta ─────────────────────────────────────────────────────

/// Parsed `scripts/{name}.toml` — same fields as `ToolConfig` minus name/entrypoint.
#[derive(Debug, Clone, Deserialize)]
pub struct ScriptMeta {
    pub description: String,
    pub runtime: ScriptRuntime,
    #[serde(default)]
    pub timeout_ms: Option<u64>,
    #[serde(default)]
    pub params_schema: serde_json::Value,
}

// ── LoadedResource ──────────────────────────────────────────────────

/// Result of `load_resource_file()` — content + optional script metadata.
#[derive(Debug, Clone)]
pub struct LoadedResource {
    pub content: String,
    pub description: Option<String>,
    pub params_schema: Option<serde_json::Value>,
}

// ── SkillIndex ──────────────────────────────────────────────────────

/// Scan, index, and retrieve lazy-loaded skills.
pub struct SkillIndex {
    #[allow(dead_code)]
    root: PathBuf,
    entries: HashMap<String, SkillEntry>,
}

impl SkillIndex {
    /// Scan `<root>/skills/*/SKILL.md` and build the L1 index.
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
                    Err(_) => continue,
                };

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

    pub fn resolve(&self, name: &str) -> Option<&SkillEntry> {
        self.entries.get(name)
    }

    pub fn list_index(&self) -> Vec<&SkillEntry> {
        self.entries.values().collect()
    }

    pub fn load_body(&self, name: &str) -> Option<String> {
        let entry = self.entries.get(name)?;
        let path = entry.source_dir.join("SKILL.md");
        fs::read_to_string(&path).ok()
    }

    pub fn load_resources(&self, name: &str) -> Option<SkillResources> {
        let entry = self.entries.get(name)?;
        Some(SkillResources {
            scripts: list_files(&entry.source_dir.join("scripts")),
            references: list_files(&entry.source_dir.join("references")),
            assets: list_files(&entry.source_dir.join("assets")),
        })
    }

    pub fn load_resource_file(
        &self,
        name: &str,
        resource_path: &str,
    ) -> Result<LoadedResource, String> {
        let entry = self.entries.get(name).ok_or("skill not found")?;
        let full_path = resolve_safe_path(&entry.source_dir, resource_path)?;

        let content =
            fs::read_to_string(&full_path).map_err(|e| format!("read error: {e}"))?;

        let (description, params_schema) = if resource_path.starts_with("scripts/") {
            if let Some(meta) = load_script_meta_from_path(resource_path, &entry.source_dir) {
                (Some(meta.description), Some(meta.params_schema))
            } else {
                (None, None)
            }
        } else {
            (None, None)
        };

        Ok(LoadedResource {
            content,
            description,
            params_schema,
        })
    }

    pub fn load_script_meta(&self, skill_name: &str, script_path: &str) -> Option<ScriptMeta> {
        let entry = self.entries.get(skill_name)?;
        load_script_meta_from_path(script_path, &entry.source_dir)
    }

    pub async fn run_script(
        &self,
        skill_name: &str,
        script_path: &str,
        params: serde_json::Value,
    ) -> Result<serde_json::Value, String> {
        let entry = self
            .entries
            .get(skill_name)
            .ok_or_else(|| format!("skill not found: {skill_name}"))?;
        let _full_path = resolve_safe_path(&entry.source_dir, script_path)?;

        let meta = load_script_meta_from_path(script_path, &entry.source_dir).unwrap_or_else(
            || ScriptMeta {
                description: format!("Skill script: {script_path}"),
                runtime: infer_runtime(script_path),
                timeout_ms: None,
                params_schema: serde_json::Value::Null,
            },
        );

        let tool_config = crate::config::ToolConfig {
            name: format!("{skill_name}/{script_path}"),
            description: meta.description,
            runtime: meta.runtime,
            entrypoint: script_path.to_string(),
            timeout_ms: meta.timeout_ms,
            params_schema: meta.params_schema,
        };

        let script_tool = ScriptTool::new(tool_config, entry.source_dir.clone());

        script_tool
            .execute(params)
            .await
            .map_err(|e| e.message)
    }
}

// ── Helpers ─────────────────────────────────────────────────────────

fn parse_frontmatter(content: &str) -> Option<SkillFrontmatter> {
    let mut parts = content.splitn(3, "---");
    parts.next()?;
    let yaml_str = parts.next()?;
    serde_yaml::from_str::<SkillFrontmatter>(yaml_str).ok()
}

fn is_kebab_case(s: &str) -> bool {
    !s.is_empty()
        && s.chars()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-')
        && !s.starts_with('-')
        && !s.ends_with('-')
}

fn list_files(dir: &Path) -> Vec<String> {
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

fn load_script_meta_from_path(script_path: &str, source_dir: &Path) -> Option<ScriptMeta> {
    let toml_name = if let Some(stem) = Path::new(script_path).file_stem().and_then(|s| s.to_str())
    {
        format!("scripts/{stem}.toml")
    } else {
        return None;
    };

    let toml_path = source_dir.join(&toml_name);
    let content = fs::read_to_string(&toml_path).ok()?;
    toml::from_str::<ScriptMeta>(&content).ok()
}

fn resolve_safe_path(source_dir: &Path, resource_path: &str) -> Result<PathBuf, String> {
    if resource_path.contains("..") {
        return Err("path traversal rejected: '..' not allowed".into());
    }
    if resource_path.starts_with('/') {
        return Err("absolute path rejected".into());
    }

    let valid_prefixes = ["scripts/", "references/", "assets/"];
    let allowed = valid_prefixes
        .iter()
        .any(|prefix| resource_path.starts_with(prefix));
    if !allowed {
        return Err(format!(
            "resource path must start with scripts/, references/, or assets/. Got: {resource_path}"
        ));
    }

    let full_path = source_dir.join(resource_path);
    let canonical = full_path
        .canonicalize()
        .map_err(|e| format!("file not found: {e}"))?;
    if !canonical.starts_with(source_dir) {
        return Err("path traversal rejected after canonicalize".into());
    }

    Ok(canonical)
}

fn infer_runtime(script_path: &str) -> ScriptRuntime {
    if script_path.ends_with(".py") {
        ScriptRuntime::Python
    } else if script_path.ends_with(".sh") || script_path.ends_with(".bash") {
        ScriptRuntime::Bash
    } else if script_path.ends_with(".rs") {
        ScriptRuntime::Rust
    } else {
        ScriptRuntime::Bash
    }
}
