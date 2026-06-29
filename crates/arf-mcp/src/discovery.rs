use std::collections::HashMap;
use std::fs;
use std::path::PathBuf;
use std::sync::Arc;

use serde_json::Value;

use crate::config::ToolConfig;
use crate::error::McpError;
use crate::script::ScriptTool;
use crate::skill::{LoadedResource, SkillEntry, SkillIndex, SkillResources};
use crate::tool::Tool;

#[derive(Debug, Clone)]
pub struct ToolInfo {
    pub name: String,
    pub description: String,
    pub parameters_schema: Value,
}

/// Scans the filesystem and builds the tool registry + skill index.
///
/// Pure scan — no Bus. Called by LocalMcpNode during construction.
pub struct DiscoveryModule {
    tools: HashMap<String, Arc<dyn Tool>>,
    tool_info: Vec<ToolInfo>,
    skill_index: SkillIndex,
}

impl std::fmt::Debug for DiscoveryModule {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("DiscoveryModule")
            .field("tool_count", &self.tools.len())
            .field("tool_info", &self.tool_info)
            .field("skill_index", &self.skill_index)
            .finish()
    }
}

impl DiscoveryModule {
    /// Scan `{root}/tools/*/tool.toml` and `{root}/skills/*/SKILL.md`.
    ///
    /// Returns McpError::Discovery only on fatal errors (root doesn't exist).
    /// Empty results (no tools, no skills) is valid.
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
                            eprintln!(
                                "WARNING [DiscoveryModule]: invalid tool.toml in {}: {e}",
                                tool_dir.display()
                            );
                            continue;
                        }
                    };

                    let expected_name =
                        tool_dir.file_name().and_then(|n| n.to_str()).unwrap_or("");
                    if config.name != expected_name {
                        eprintln!(
                            "WARNING [DiscoveryModule]: tool name '{}' doesn't match directory '{}' (in {})",
                            config.name,
                            expected_name,
                            tool_dir.display()
                        );
                    }

                    let name = config.name.clone();
                    let description = config.description.clone();
                    let schema = config.params_schema.clone();

                    let script_tool =
                        Arc::new(ScriptTool::new(config, tool_dir)) as Arc<dyn Tool>;

                    tool_info.push(ToolInfo {
                        name: name.clone(),
                        description,
                        parameters_schema: schema,
                    });
                    tools.insert(name, script_tool);
                }
            }
        }

        let skill_index = SkillIndex::scan(root);

        Ok(Self {
            tools,
            tool_info,
            skill_index,
        })
    }

    // ── Tool access ─────────────────────────────────────────────

    pub fn resolve_tool(&self, name: &str) -> Option<Arc<dyn Tool>> {
        self.tools.get(name).cloned()
    }

    pub fn list_tools(&self) -> &[ToolInfo] {
        &self.tool_info
    }

    pub fn tool_map(&self) -> &HashMap<String, Arc<dyn Tool>> {
        &self.tools
    }

    // ── Skill access (delegates to SkillIndex) ──────────────────

    pub fn resolve_skill(&self, name: &str) -> Option<&SkillEntry> {
        self.skill_index.resolve(name)
    }

    pub fn list_skills(&self) -> Vec<&SkillEntry> {
        self.skill_index.list_index()
    }

    pub fn load_skill_body(&self, name: &str) -> Option<String> {
        self.skill_index.load_body(name)
    }

    pub fn load_skill_resources(&self, name: &str) -> Option<SkillResources> {
        self.skill_index.load_resources(name)
    }

    pub fn load_resource_file(
        &self,
        skill_name: &str,
        resource_path: &str,
    ) -> Result<LoadedResource, String> {
        self.skill_index
            .load_resource_file(skill_name, resource_path)
    }

    pub fn load_tool_config(&self, skill_name: &str, tool_name: &str) -> Option<ToolConfig> {
        self.skill_index.load_tool_config(skill_name, tool_name)
    }

    pub async fn run_skill_tool(
        &self,
        skill_name: &str,
        tool_name: &str,
        params: Value,
    ) -> Result<Value, String> {
        self.skill_index
            .run_tool(skill_name, tool_name, params)
            .await
    }
}
