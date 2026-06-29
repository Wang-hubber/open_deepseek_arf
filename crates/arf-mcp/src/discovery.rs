use std::collections::HashMap;
use std::fs;
use std::path::PathBuf;
use std::sync::Arc;

use async_trait::async_trait;
use serde_json::Value;

use crate::config::ToolConfig;
use crate::error::McpError;
use crate::script::ScriptTool;
use crate::skill::{LoadedResource, SkillEntry, SkillIndex, SkillResources};
use crate::tool::Tool;

// ── ToolInfo ───────────────────────────────────────────────────────

#[derive(Debug, Clone)]
pub struct ToolInfo {
    pub name: String,
    pub description: String,
    pub parameters_schema: Value,
}

// ── DiscoveryBackend trait ─────────────────────────────────────────

/// Resource discovery backend — bound at McpNode construction.
///
/// All skill methods have default implementations returning None/empty.
/// `FsDiscovery` overrides them to delegate to SkillIndex.
/// `HttpDiscovery` uses the defaults (no skills).
#[async_trait]
pub trait DiscoveryBackend: Send + Sync {
    fn list_tools(&self) -> &[ToolInfo];
    fn tool_map(&self) -> &HashMap<String, Arc<dyn Tool>>;
    fn resolve_tool(&self, name: &str) -> Option<Arc<dyn Tool>>;

    // ── Skill methods (default: no skills) ─────────────────────

    fn resolve_skill(&self, _name: &str) -> Option<&SkillEntry> {
        None
    }
    fn list_skills(&self) -> Vec<&SkillEntry> {
        vec![]
    }
    fn load_skill_body(&self, _name: &str) -> Option<String> {
        None
    }
    fn load_skill_resources(&self, _name: &str) -> Option<SkillResources> {
        None
    }
    fn load_resource_file(&self, _name: &str, _path: &str) -> Result<LoadedResource, String> {
        Err("skills not supported".into())
    }
    fn load_tool_config(&self, _skill: &str, _tool: &str) -> Option<ToolConfig> {
        None
    }
    async fn run_skill_tool(
        &self,
        _skill: &str,
        _tool: &str,
        _params: Value,
    ) -> Result<Value, String> {
        Err("skills not supported".into())
    }
}

// ── FsDiscovery ────────────────────────────────────────────────────

/// Filesystem-based discovery: scans {root}/tools/*/tool.toml + {root}/skills/*/SKILL.md.
pub struct FsDiscovery {
    tools: HashMap<String, Arc<dyn Tool>>,
    tool_info: Vec<ToolInfo>,
    skill_index: SkillIndex,
}

impl std::fmt::Debug for FsDiscovery {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("FsDiscovery")
            .field("tool_count", &self.tools.len())
            .field("tool_info", &self.tool_info)
            .field("skill_index", &self.skill_index)
            .finish()
    }
}

impl FsDiscovery {
    pub fn scan(root: PathBuf) -> Result<Self, McpError> {
        if !root.is_dir() {
            return Err(McpError::Discovery {
                reason: format!("root directory does not exist: {}", root.display()),
            });
        }

        let mut tools: HashMap<String, Arc<dyn Tool>> = HashMap::new();
        let mut tool_info: Vec<ToolInfo> = Vec::new();

        let tools_dir = root.join("tools");
        if tools_dir.is_dir() {
            if let Ok(iter) = fs::read_dir(&tools_dir) {
                for entry in iter.flatten() {
                    let tool_dir = entry.path();
                    if !tool_dir.is_dir() {
                        continue;
                    }
                    let toml_path = tool_dir.join("tool.toml");
                    let toml_content = match fs::read_to_string(&toml_path) {
                        Ok(c) => c,
                        Err(_) => continue,
                    };
                    let config = match ToolConfig::from_toml_str(&toml_content) {
                        Ok(c) => c,
                        Err(e) => {
                            eprintln!("WARNING [FsDiscovery]: invalid tool.toml in {}: {e}", tool_dir.display());
                            continue;
                        }
                    };

                    let name = config.name.clone();
                    let description = config.description.clone();
                    let schema = config.params_schema.clone();
                    let script_tool = Arc::new(ScriptTool::new(config, tool_dir)) as Arc<dyn Tool>;

                    tool_info.push(ToolInfo { name: name.clone(), description, parameters_schema: schema });
                    tools.insert(name, script_tool);
                }
            }
        }

        let skill_index = SkillIndex::scan(root);
        Ok(Self { tools, tool_info, skill_index })
    }
}

#[async_trait]
impl DiscoveryBackend for FsDiscovery {
    fn list_tools(&self) -> &[ToolInfo] {
        &self.tool_info
    }

    fn tool_map(&self) -> &HashMap<String, Arc<dyn Tool>> {
        &self.tools
    }

    fn resolve_tool(&self, name: &str) -> Option<Arc<dyn Tool>> {
        self.tools.get(name).cloned()
    }

    // ── Skill methods — delegate to SkillIndex ─────────────────

    fn resolve_skill(&self, name: &str) -> Option<&SkillEntry> {
        self.skill_index.resolve(name)
    }
    fn list_skills(&self) -> Vec<&SkillEntry> {
        self.skill_index.list_index()
    }
    fn load_skill_body(&self, name: &str) -> Option<String> {
        self.skill_index.load_body(name)
    }
    fn load_skill_resources(&self, name: &str) -> Option<SkillResources> {
        self.skill_index.load_resources(name)
    }
    fn load_resource_file(&self, name: &str, path: &str) -> Result<LoadedResource, String> {
        self.skill_index.load_resource_file(name, path)
    }
    fn load_tool_config(&self, skill: &str, tool: &str) -> Option<ToolConfig> {
        self.skill_index.load_tool_config(skill, tool)
    }
    async fn run_skill_tool(&self, skill: &str, tool: &str, params: Value) -> Result<Value, String> {
        self.skill_index.run_tool(skill, tool, params).await
    }
}
