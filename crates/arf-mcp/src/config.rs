use std::collections::HashMap;
use std::path::PathBuf;

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
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolConfig {
    pub name: String,
    pub description: String,
    pub runtime: ScriptRuntime,
    pub entrypoint: String,
    #[serde(default)]
    pub timeout_ms: Option<u64>,
    #[serde(default)]
    pub params_schema: serde_json::Value,
}

impl ToolConfig {
    pub fn from_toml_str(content: &str) -> Result<Self, String> {
        toml::from_str(content).map_err(|e| format!("invalid tool.toml: {e}"))
    }
}

// ── RemoteConfig ───────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RemoteConfig {
    pub transport: String,
    pub url: String,
    #[serde(default)]
    pub timeout_secs: Option<u64>,
    #[serde(default)]
    pub headers: HashMap<String, String>,
    #[serde(default)]
    pub tls_ca_cert: Option<PathBuf>,
    #[serde(default)]
    pub retry: Option<RetryConfig>,
}

// ── RetryConfig ───────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RetryConfig {
    #[serde(default = "default_max_retries")]
    pub max_retries: u32,
    #[serde(default = "default_initial_backoff_ms")]
    pub initial_backoff_ms: u64,
    #[serde(default = "default_max_backoff_ms")]
    pub max_backoff_ms: u64,
}

fn default_max_retries() -> u32 {
    3
}
fn default_initial_backoff_ms() -> u64 {
    1000
}
fn default_max_backoff_ms() -> u64 {
    30000
}
