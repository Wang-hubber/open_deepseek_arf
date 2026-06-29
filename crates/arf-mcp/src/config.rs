use serde::{Deserialize, Serialize};

// ── ScriptRuntime ─────────────────────────────────────────────────────

/// Supported script runtimes.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum ScriptRuntime {
    Python,
    Bash,
    Rust,
}

// ── ToolConfig ─────────────────────────────────────────────────────────

/// Parsed tool.toml — script tool metadata.
///
/// Each directory under `{root}/tools/` with a valid `tool.toml` is
/// registered as a `ScriptTool`. The `runtime` field selects the executor.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolConfig {
    /// Unique tool name (kebab-case).
    pub name: String,
    /// Human-readable description for LLM function calling.
    pub description: String,
    /// Script runtime: `"python"`, `"bash"`, or `"rust"`.
    pub runtime: ScriptRuntime,
    /// Entry point script filename relative to the tool directory.
    pub entrypoint: String,
    /// Per-call timeout in milliseconds. `None` = no timeout.
    #[serde(default)]
    pub timeout_ms: Option<u64>,
    /// JSON Schema for the tool's parameters.
    #[serde(default)]
    pub params_schema: serde_json::Value,
}

impl ToolConfig {
    /// Parse a `tool.toml` file content into a `ToolConfig`.
    ///
    /// The TOML format uses lowercase runtime names ("python", "bash", "rust")
    /// matching `ScriptRuntime`'s `#[serde(rename_all = "lowercase")]`.
    pub fn from_toml_str(content: &str) -> Result<Self, String> {
        toml::from_str(content).map_err(|e| format!("invalid tool.toml: {e}"))
    }
}

// ── RemoteConfig ───────────────────────────────────────────────────────

/// Streamable HTTP transport configuration for a remote MCP server.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RemoteConfig {
    /// Transport protocol: `"streamable-http"`.
    pub transport: String,
    /// Base URL of the remote MCP server.
    pub url: String,
}
