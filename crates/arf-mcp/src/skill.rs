use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};

use serde::Deserialize;

use crate::config::ToolConfig;
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
    /// Tool names under tools/ (e.g. ["generate-component", "validate"]).
    pub tools: Vec<String>,
    /// Files under references/ (e.g. ["api-guide.md"]).
    pub references: Vec<String>,
    /// Files under assets/ (e.g. ["template.tsx"]).
    pub assets: Vec<String>,
}

// ── LoadedResource ──────────────────────────────────────────────────

/// Result of `load_resource_file()` — content + optional tool metadata.
#[derive(Debug, Clone)]
pub struct LoadedResource {
    pub content: String,
    /// Present for tools/ files with a tool.toml.
    pub description: Option<String>,
    /// Present for tools/ files with a tool.toml.
    pub params_schema: Option<serde_json::Value>,
}

// ── SkillIndex ──────────────────────────────────────────────────────

/// Scan, index, and retrieve lazy-loaded skills.
#[derive(Debug)]
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
            tools: list_dirs(&entry.source_dir.join("tools")),
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

        let (description, params_schema) = if resource_path.starts_with("tools/") {
            // Derive tool name from path: "tools/gen/main.py" → "gen"
            let tool_name = resource_path
                .trim_start_matches("tools/")
                .split('/')
                .next()
                .unwrap_or("");
            if let Some(config) = load_tool_config_from_dir(&entry.source_dir, tool_name) {
                (Some(config.description), Some(config.params_schema))
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

    /// Read `tools/{tool_name}/tool.toml` and return a standard `ToolConfig`.
    pub fn load_tool_config(&self, skill_name: &str, tool_name: &str) -> Option<ToolConfig> {
        let entry = self.entries.get(skill_name)?;
        load_tool_config_from_dir(&entry.source_dir, tool_name)
    }

    /// Execute a skill tool via the ScriptTool subprocess mechanism.
    pub async fn run_tool(
        &self,
        skill_name: &str,
        tool_name: &str,
        params: serde_json::Value,
    ) -> Result<serde_json::Value, String> {
        let entry = self
            .entries
            .get(skill_name)
            .ok_or_else(|| format!("skill not found: {skill_name}"))?;

        let tool_dir = entry.source_dir.join("tools").join(tool_name);
        if !tool_dir.is_dir() {
            return Err(format!("tool not found: {skill_name}/{tool_name}"));
        }

        let mut config = load_tool_config_from_dir(&entry.source_dir, tool_name)
            .or_else(|| infer_tool_defaults(&tool_dir, tool_name))
            .ok_or_else(|| format!("tool not found: {skill_name}/{tool_name}"))?;

        // Override name for skill-scoped identity
        config.name = format!("{skill_name}/{tool_name}");

        let script_tool = ScriptTool::new(config, tool_dir);

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

fn list_dirs(dir: &Path) -> Vec<String> {
    let mut dirs: Vec<String> = Vec::new();
    if let Ok(iter) = fs::read_dir(dir) {
        for entry in iter.flatten() {
            if entry.file_type().map(|t| t.is_dir()).unwrap_or(false) {
                if let Some(name) = entry.file_name().to_str() {
                    dirs.push(name.to_string());
                }
            }
        }
    }
    dirs.sort();
    dirs
}

fn load_tool_config_from_dir(source_dir: &Path, tool_name: &str) -> Option<ToolConfig> {
    let toml_path = source_dir.join("tools").join(tool_name).join("tool.toml");
    let content = fs::read_to_string(&toml_path).ok()?;
    toml::from_str::<ToolConfig>(&content).ok()
}

/// Auto-detect tool config from a directory without tool.toml.
/// Looks for a script file (main.py > main.sh > main) and infers runtime.
fn infer_tool_defaults(tool_dir: &Path, tool_name: &str) -> Option<ToolConfig> {
    let candidates = ["main.py", "main.sh", "main"];
    for candidate in &candidates {
        let path = tool_dir.join(candidate);
        if path.is_file() {
            let runtime = if candidate.ends_with(".py") {
                crate::config::ScriptRuntime::Python
            } else if candidate.ends_with(".sh") {
                crate::config::ScriptRuntime::Bash
            } else {
                crate::config::ScriptRuntime::Bash
            };
            return Some(ToolConfig {
                name: tool_name.into(),
                description: format!("Skill tool: {tool_name}"),
                runtime,
                entrypoint: candidate.to_string(),
                timeout_ms: None,
                params_schema: serde_json::Value::Null,
            });
        }
    }
    None
}

fn resolve_safe_path(source_dir: &Path, resource_path: &str) -> Result<PathBuf, String> {
    if resource_path.contains("..") {
        return Err("path traversal rejected: '..' not allowed".into());
    }
    if resource_path.starts_with('/') {
        return Err("absolute path rejected".into());
    }

    let valid_prefixes = ["tools/", "references/", "assets/"];
    let allowed = valid_prefixes
        .iter()
        .any(|prefix| resource_path.starts_with(prefix));
    if !allowed {
        return Err(format!(
            "resource path must start with tools/, references/, or assets/. Got: {resource_path}"
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
