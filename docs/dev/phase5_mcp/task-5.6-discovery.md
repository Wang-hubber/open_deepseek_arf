# 任务 5.6：DiscoveryBackend + FsDiscovery（原 DiscoveryModule）

> **订正**：此文档中的类型名已被 [McpNode 统一重构](./mcp-node-unified-design.md) 更新。当前实现以代码为准。
> Phase 5 — MCP 第六项任务
> 父文档：`docs/v1.x/phase5_mcp/phase5-mcp-design.md`
> 依赖：Task 5.2 (ScriptTool), Task 5.3 (SkillIndex)

## 设计思路

`DiscoveryModule` 扫描 `{root}/` 下的 `tools/` 和 `skills/` 两个目录，产出工具注册表 + SkillIndex。纯扫描，不涉 Bus。

```
DiscoveryModule::scan(root)
  ├── tools/*/tool.toml → ToolConfig → ScriptTool → Arc<dyn Tool>
  └── skills/*/SKILL.md → SkillIndex
```

同时引入 `McpError` 类型——被 LocalMcpNode/RemoteMcpNode 共用。

| 文件 | 操作 | 内容 |
|------|------|------|
| `error.rs` | 新建 | `McpError` 枚举 |
| `discovery.rs` | 新建 | `DiscoveryModule` + `ToolInfo` |
| `lib.rs` | 更新 | `pub mod error; pub mod discovery;` |

---

## 代码实现

### `crates/arf-mcp/src/error.rs` — 新建

```rust
/// Unified error type for MCP node creation and connection.
#[derive(Debug, Clone)]
pub enum McpError {
    /// Resource discovery failed (scan error, no valid tools/skills).
    Discovery { reason: String },
    /// Remote MCP server unreachable (RemoteMcpNode only).
    RemoteUnreachable { url: String, reason: String },
    /// MCP handshake rejected (RemoteMcpNode only).
    RemoteRejected { url: String, code: i32, message: String },
    /// Bus connection failed.
    BusConnect { reason: String },
}

impl std::fmt::Display for McpError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Discovery { reason } => write!(f, "discovery error: {reason}"),
            Self::RemoteUnreachable { url, reason } => {
                write!(f, "remote MCP server unreachable ({url}): {reason}")
            }
            Self::RemoteRejected { url, code, message } => {
                write!(f, "remote MCP server rejected handshake ({url}): [{code}] {message}")
            }
            Self::BusConnect { reason } => write!(f, "bus connection failed: {reason}"),
        }
    }
}

impl std::error::Error for McpError {}
```

### `crates/arf-mcp/src/discovery.rs` — 新建

```rust
use std::collections::HashMap;
use std::fs;
use std::path::PathBuf;
use std::sync::Arc;

use serde_json::Value;

use crate::config::{ScriptRuntime, ToolConfig};
use crate::error::McpError;
use crate::script::ScriptTool;
use crate::skill::{LoadedResource, SkillEntry, SkillIndex, SkillResources};
use crate::tool::Tool;

/// Lightweight tool metadata for node_online broadcast.
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
    /// Tool name → tool instance (for execution).
    tools: HashMap<String, Arc<dyn Tool>>,
    /// Tool metadata (for node_online broadcast).
    tool_info: Vec<ToolInfo>,
    /// Skill index (for L1/L2/L3 and script execution).
    skill_index: SkillIndex,
}

impl DiscoveryModule {
    /// Scan `{root}/tools/*/tool.toml` and `{root}/skills/*/SKILL.md`.
    ///
    /// Returns McpError::Discovery only on fatal errors (root doesn't exist
    /// and is unreadable). Empty results (no tools, no skills) is valid.
    pub fn scan(root: PathBuf) -> Result<Self, McpError> {
        // Verify root exists
        if !root.is_dir() {
            return Err(McpError::Discovery {
                reason: format!("root directory does not exist: {}", root.display()),
            });
        }

        let mut tools: HashMap<String, Arc<dyn Tool>> = HashMap::new();
        let mut tool_info: Vec<ToolInfo> = Vec::new();

        // ── Scan tools/ ─────────────────────────────────────────
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
                        Err(_) => continue, // no tool.toml → not a tool
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

                    // Validate that name matches directory (optional consistency check)
                    let expected_name = tool_dir
                        .file_name()
                        .and_then(|n| n.to_str())
                        .unwrap_or("");
                    if config.name != expected_name {
                        eprintln!(
                            "WARNING [DiscoveryModule]: tool name '{}' doesn't match directory '{}' (in {})",
                            config.name, expected_name, tool_dir.display()
                        );
                    }

                    let name = config.name.clone();
                    let description = config.description.clone();
                    let schema = config.params_schema.clone();

                    let script_tool = Arc::new(ScriptTool::new(config, tool_dir))
                        as Arc<dyn Tool>;

                    tool_info.push(ToolInfo {
                        name: name.clone(),
                        description,
                        parameters_schema: schema,
                    });
                    tools.insert(name, script_tool);
                }
            }
        }

        // ── Scan skills/ ────────────────────────────────────────
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
        self.skill_index.load_resource_file(skill_name, resource_path)
    }

    pub fn load_tool_config(
        &self,
        skill_name: &str,
        tool_name: &str,
    ) -> Option<ToolConfig> {
        self.skill_index.load_tool_config(skill_name, tool_name)
    }

    pub async fn run_skill_tool(
        &self,
        skill_name: &str,
        tool_name: &str,
        params: Value,
    ) -> Result<Value, String> {
        self.skill_index.run_tool(skill_name, tool_name, params).await
    }
}
```

### `crates/arf-mcp/src/lib.rs` 更新

```rust
pub mod config;
pub mod discovery;
pub mod error;
pub mod executor;
pub mod script;
pub mod skill;
pub mod tool;
pub mod types;
```

---

## 测试

### `crates/arf-mcp/src/tests/discovery_tests.rs` — 新建

```rust
use std::fs;
use std::io::Write;

use crate::discovery::DiscoveryModule;

fn setup_discovery_root(tools: &[(&str, &str, &str)], skills: &[(&str, &str)]) -> (PathBuf, Cleanup) {
    let id = super::TEST_COUNTER.fetch_add(1, Ordering::Relaxed);
    let root = std::env::temp_dir().join(format!("arf_mcp_disc_test_{id}"));
    let _ = fs::remove_dir_all(&root);
    fs::create_dir_all(&root).unwrap();

    // Setup tools
    for (name, toml, script) in tools {
        let tool_dir = root.join("tools").join(name);
        fs::create_dir_all(&tool_dir).unwrap();
        let mut f = fs::File::create(tool_dir.join("tool.toml")).unwrap();
        f.write_all(toml.as_bytes()).unwrap();
        fs::write(tool_dir.join("main.py"), script).unwrap();
    }

    // Setup skills
    if !skills.is_empty() {
        for (name, content) in skills {
            let skill_dir = root.join("skills").join(name);
            fs::create_dir_all(&skill_dir).unwrap();
            let mut f = fs::File::create(skill_dir.join("SKILL.md")).unwrap();
            f.write_all(content.as_bytes()).unwrap();
        }
    }

    (root, Cleanup(root))
}

// ... (same Cleanup struct as other tests)
```

测试：
- scan 空目录 → 0 tools, 0 skills
- scan 一个 tool → 1 tool, resolve 成功
- scan 多个 tools → 全部注册
- scan skill → resolve_skill 成功
- scan tools + skills 共存
- invalid tool.toml → 跳过, 不 panic
- 无 tool.toml 的目录 → 跳过
- root 不存在 → McpError::Discovery

| 文件 | 新增测试 | 覆盖角度 |
|------|---------|---------|
| `discovery_tests.rs` | 8 | `[构造][方法][边界]` |
| **合计** | **8** | 累计 arf-mcp: 153 + 8 = **161 tests** |
