use std::collections::HashMap;

use crate::config::{RemoteConfig, RetryConfig, ScriptRuntime, ToolConfig};

// ═══════════════════════════════════════════════════════════════
// ScriptRuntime — 8 tests
// ═══════════════════════════════════════════════════════════════

// [覆盖] 三种变体均可构造
#[test]
fn script_runtime_all_variants_construct() {
    let _ = ScriptRuntime::Python;
    let _ = ScriptRuntime::Bash;
    let _ = ScriptRuntime::Rust;
}

// [trait] Clone + PartialEq
#[test]
fn script_runtime_clone_and_eq() {
    let a = ScriptRuntime::Python;
    let b = a.clone();
    assert_eq!(a, b);
    assert_ne!(a, ScriptRuntime::Bash);
    assert_ne!(ScriptRuntime::Bash, ScriptRuntime::Rust);
}

// [序列化] Python → "python" → Python
#[test]
fn script_runtime_serialization_python() {
    let json = serde_json::to_string(&ScriptRuntime::Python).unwrap();
    assert_eq!(json, r#""python""#);
    let back: ScriptRuntime = serde_json::from_str(&json).unwrap();
    assert_eq!(back, ScriptRuntime::Python);
}

// [序列化] Bash → "bash" → Bash
#[test]
fn script_runtime_serialization_bash() {
    let json = serde_json::to_string(&ScriptRuntime::Bash).unwrap();
    assert_eq!(json, r#""bash""#);
    let back: ScriptRuntime = serde_json::from_str(&json).unwrap();
    assert_eq!(back, ScriptRuntime::Bash);
}

// [序列化] Rust → "rust" → Rust
#[test]
fn script_runtime_serialization_rust() {
    let json = serde_json::to_string(&ScriptRuntime::Rust).unwrap();
    assert_eq!(json, r#""rust""#);
    let back: ScriptRuntime = serde_json::from_str(&json).unwrap();
    assert_eq!(back, ScriptRuntime::Rust);
}

// [兼容] 从 TOML 小写字符串反序列化（模拟 tool.toml 读取路径）
#[test]
fn script_runtime_deserialize_toml_style() {
    // TOML 解析后得到的是 serde_json::Value 或直接解析
    let back: ScriptRuntime = serde_json::from_str(r#""python""#).unwrap();
    assert_eq!(back, ScriptRuntime::Python);
}

// [边界] 非法 runtime 字符串反序列化应报错
#[test]
fn script_runtime_deserialize_invalid() {
    let result: Result<ScriptRuntime, _> = serde_json::from_str(r#""javascript""#);
    assert!(result.is_err());
}

// [trait] Debug 输出变体名
#[test]
fn script_runtime_debug() {
    let debug = format!("{:?}", ScriptRuntime::Python);
    assert!(debug.contains("Python"));
    let debug = format!("{:?}", ScriptRuntime::Bash);
    assert!(debug.contains("Bash"));
}

// ═══════════════════════════════════════════════════════════════
// ToolConfig — 8 tests
// ═══════════════════════════════════════════════════════════════

// [构造] 所有字段正确赋值
#[test]
fn tool_config_all_fields() {
    let config = ToolConfig {
        name: "cleanup_logs".into(),
        description: "Delete log files older than N days".into(),
        runtime: ScriptRuntime::Bash,
        entrypoint: "main.sh".into(),
        timeout_ms: Some(30000),
        params_schema: serde_json::json!({
            "type": "object",
            "properties": {
                "days": {"type": "integer", "default": 30}
            }
        }),
    };
    assert_eq!(config.name, "cleanup_logs");
    assert_eq!(config.runtime, ScriptRuntime::Bash);
    assert_eq!(config.entrypoint, "main.sh");
    assert_eq!(config.timeout_ms, Some(30000));
}

// [边界] timeout_ms = None（未设置超时）
#[test]
fn tool_config_timeout_none() {
    let config = ToolConfig {
        name: "fast_tool".into(),
        description: "Quick operation".into(),
        runtime: ScriptRuntime::Python,
        entrypoint: "main.py".into(),
        timeout_ms: None,
        params_schema: serde_json::Value::Null,
    };
    assert_eq!(config.timeout_ms, None);
}

// [边界] params_schema 为 Null
#[test]
fn tool_config_null_params_schema() {
    let config = ToolConfig {
        name: "no_params".into(),
        description: "A tool without parameters".into(),
        runtime: ScriptRuntime::Bash,
        entrypoint: "run.sh".into(),
        timeout_ms: None,
        params_schema: serde_json::Value::Null,
    };
    assert_eq!(config.params_schema, serde_json::Value::Null);
}

// [序列化] serde 往返
#[test]
fn tool_config_serialization_roundtrip() {
    let config = ToolConfig {
        name: "read_file".into(),
        description: "Read file contents".into(),
        runtime: ScriptRuntime::Python,
        entrypoint: "main.py".into(),
        timeout_ms: Some(10000),
        params_schema: serde_json::json!({"type": "object", "properties": {"path": {"type": "string"}}}),
    };
    let json = serde_json::to_string(&config).unwrap();
    let back: ToolConfig = serde_json::from_str(&json).unwrap();
    assert_eq!(back.name, "read_file");
    assert_eq!(back.runtime, ScriptRuntime::Python);
    assert_eq!(back.entrypoint, "main.py");
    assert_eq!(back.timeout_ms, Some(10000));
    assert_eq!(back.params_schema["type"], "object");
}

// [序列化] runtime 字段序列化为小写
#[test]
fn tool_config_runtime_serialized_as_lowercase() {
    let config = ToolConfig {
        name: "t".into(),
        description: "d".into(),
        runtime: ScriptRuntime::Python,
        entrypoint: "e".into(),
        timeout_ms: None,
        params_schema: serde_json::Value::Null,
    };
    let json = serde_json::to_string(&config).unwrap();
    assert!(json.contains(r#""python""#));
}

// [兼容] 从 TOML 格式 JSON 反序列化（模拟 tool.toml 解析）
#[test]
fn tool_config_deserialize_toml_style_minimal() {
    // Simulates a minimal tool.toml parsed via toml crate → JSON
    let json = r#"{
        "name": "hello",
        "description": "Say hello",
        "runtime": "python",
        "entrypoint": "hello.py"
    }"#;
    let config: ToolConfig = serde_json::from_str(json).unwrap();
    assert_eq!(config.name, "hello");
    assert_eq!(config.runtime, ScriptRuntime::Python);
    assert_eq!(config.entrypoint, "hello.py");
    assert_eq!(config.timeout_ms, None);
    assert_eq!(config.params_schema, serde_json::Value::Null);
}

// [trait] Clone 克隆后一致
#[test]
fn tool_config_clone() {
    let config = ToolConfig {
        name: "x".into(),
        description: "y".into(),
        runtime: ScriptRuntime::Rust,
        entrypoint: "main.rs".into(),
        timeout_ms: Some(5000),
        params_schema: serde_json::json!({"type": "object"}),
    };
    let cloned = config.clone();
    assert_eq!(config.name, cloned.name);
    assert_eq!(config.runtime, cloned.runtime);
    assert_eq!(config.timeout_ms, cloned.timeout_ms);
    assert_eq!(config.params_schema, cloned.params_schema);
}

// [边界] 空 description 和 name：不 panic
#[test]
fn tool_config_empty_strings() {
    let config = ToolConfig {
        name: "".into(),
        description: "".into(),
        runtime: ScriptRuntime::Bash,
        entrypoint: "".into(),
        timeout_ms: None,
        params_schema: serde_json::Value::Null,
    };
    assert_eq!(config.name, "");
    assert_eq!(config.description, "");
    assert_eq!(config.entrypoint, "");
}

// ═══════════════════════════════════════════════════════════════
// RemoteConfig — 5 tests
// ═══════════════════════════════════════════════════════════════

// [构造] 所有字段正确赋值
#[test]
fn remote_config_all_fields() {
    let config = RemoteConfig {
        transport: "streamable-http".into(),
        url: "https://mcp.codetidy.dev".into(),
        timeout_secs: None,
        headers: HashMap::new(),
        tls_ca_cert: None,
        retry: None,
    };
    assert_eq!(config.transport, "streamable-http");
    assert_eq!(config.url, "https://mcp.codetidy.dev");
}

// [序列化] serde 往返
#[test]
fn remote_config_serialization_roundtrip() {
    let config = RemoteConfig {
        transport: "streamable-http".into(),
        url: "https://mcp.example.com".into(),
        timeout_secs: None, headers: HashMap::new(), tls_ca_cert: None, retry: None,
    };
    let json = serde_json::to_string(&config).unwrap();
    let back: RemoteConfig = serde_json::from_str(&json).unwrap();
    assert_eq!(back.transport, "streamable-http");
    assert_eq!(back.url, "https://mcp.example.com");
}

// [trait] Clone 克隆后一致
#[test]
fn remote_config_clone() {
    let config = RemoteConfig {
        transport: "streamable-http".into(),
        url: "https://example.com".into(),
        timeout_secs: None, headers: HashMap::new(), tls_ca_cert: None, retry: None,
    };
    let cloned = config.clone();
    assert_eq!(config.transport, cloned.transport);
    assert_eq!(config.url, cloned.url);
}

// [边界] 空 URL：不 panic
#[test]
fn remote_config_empty_url() {
    let config = RemoteConfig {
        transport: "streamable-http".into(),
        url: "".into(),
        timeout_secs: None, headers: HashMap::new(), tls_ca_cert: None, retry: None,
    };
    assert_eq!(config.url, "");
}

// [trait] Debug 输出包含字段值
#[test]
fn remote_config_debug() {
    let config = RemoteConfig {
        transport: "streamable-http".into(),
        url: "https://mcp.example.com".into(),
        timeout_secs: None, headers: HashMap::new(), tls_ca_cert: None, retry: None,
    };
    let debug = format!("{config:?}");
    assert!(debug.contains("streamable-http"));
    assert!(debug.contains("mcp.example.com"));
}

// ═══════════════════════════════════════════════════════════════
// ToolConfig::from_toml_str — 8 tests
// ═══════════════════════════════════════════════════════════════

// [构造] 完整 tool.toml 解析成功
#[test]
fn tool_config_from_toml_full() {
    let toml_content = r#"
name = "cleanup_logs"
description = "Delete log files older than N days"
runtime = "bash"
entrypoint = "main.sh"
timeout_ms = 30000

[params_schema]
type = "object"
"#;
    let config = ToolConfig::from_toml_str(toml_content).unwrap();
    assert_eq!(config.name, "cleanup_logs");
    assert_eq!(
        config.description,
        "Delete log files older than N days"
    );
    assert_eq!(config.runtime, ScriptRuntime::Bash);
    assert_eq!(config.entrypoint, "main.sh");
    assert_eq!(config.timeout_ms, Some(30000));
    assert_eq!(config.params_schema["type"], "object");
}

// [构造] 最小 tool.toml（仅必填字段）
#[test]
fn tool_config_from_toml_minimal() {
    let toml_content = r#"
name = "hello"
description = "Say hello"
runtime = "python"
entrypoint = "hello.py"
"#;
    let config = ToolConfig::from_toml_str(toml_content).unwrap();
    assert_eq!(config.name, "hello");
    assert_eq!(config.runtime, ScriptRuntime::Python);
    assert_eq!(config.timeout_ms, None);
    assert_eq!(config.params_schema, serde_json::Value::Null);
}

// [构造] Python runtime 解析
#[test]
fn tool_config_from_toml_runtime_python() {
    let toml_content = r#"
name = "t"
description = "d"
runtime = "python"
entrypoint = "e.py"
"#;
    let config = ToolConfig::from_toml_str(toml_content).unwrap();
    assert_eq!(config.runtime, ScriptRuntime::Python);
}

// [构造] Bash runtime 解析
#[test]
fn tool_config_from_toml_runtime_bash() {
    let toml_content = r#"
name = "t"
description = "d"
runtime = "bash"
entrypoint = "e.sh"
"#;
    let config = ToolConfig::from_toml_str(toml_content).unwrap();
    assert_eq!(config.runtime, ScriptRuntime::Bash);
}

// [构造] Rust runtime 解析
#[test]
fn tool_config_from_toml_runtime_rust() {
    let toml_content = r#"
name = "t"
description = "d"
runtime = "rust"
entrypoint = "main.rs"
"#;
    let config = ToolConfig::from_toml_str(toml_content).unwrap();
    assert_eq!(config.runtime, ScriptRuntime::Rust);
}

// [边界] 无效 runtime 字符串 → 报错
#[test]
fn tool_config_from_toml_invalid_runtime() {
    let result = ToolConfig::from_toml_str(
        r#"name="t" description="d" runtime="javascript" entrypoint="x""#,
    );
    assert!(result.is_err());
}

// [边界] 缺少必填字段 name → 报错
#[test]
fn tool_config_from_toml_missing_name() {
    let result =
        ToolConfig::from_toml_str(r#"description="d" runtime="python" entrypoint="x""#);
    assert!(result.is_err());
}

// [边界] params_schema 为嵌套 TOML table
#[test]
fn tool_config_from_toml_nested_params_schema() {
    let toml_content = r#"
name = "search"
description = "Full-text search"
runtime = "python"
entrypoint = "search.py"

[params_schema]
type = "object"

[params_schema.properties.query]
type = "string"
description = "Search query"

[params_schema.properties.max_results]
type = "integer"
default = 10
"#;
    let config = ToolConfig::from_toml_str(toml_content).unwrap();
    let schema = &config.params_schema;
    assert_eq!(schema["type"], "object");
    assert_eq!(schema["properties"]["query"]["type"], "string");
    assert_eq!(schema["properties"]["max_results"]["type"], "integer");
    assert_eq!(schema["properties"]["max_results"]["default"], 10);
}

// ═══════════════════════════════════════════════════════════════
// RetryConfig — 3 tests
// ═══════════════════════════════════════════════════════════════

#[test]
fn retry_config_defaults() {
    let config: RetryConfig = serde_json::from_str("{}").unwrap();
    assert_eq!(config.max_retries, 3);
    assert_eq!(config.initial_backoff_ms, 1000);
    assert_eq!(config.max_backoff_ms, 30000);
}

#[test]
fn retry_config_custom() {
    let json = r#"{"max_retries":5,"initial_backoff_ms":500,"max_backoff_ms":10000}"#;
    let config: RetryConfig = serde_json::from_str(json).unwrap();
    assert_eq!(config.max_retries, 5);
    assert_eq!(config.initial_backoff_ms, 500);
    assert_eq!(config.max_backoff_ms, 10000);
}

#[test]
fn retry_config_serialization_roundtrip() {
    let config = RetryConfig {
        max_retries: 3,
        initial_backoff_ms: 1000,
        max_backoff_ms: 30000,
    };
    let json = serde_json::to_string(&config).unwrap();
    let back: RetryConfig = serde_json::from_str(&json).unwrap();
    assert_eq!(back.max_retries, 3);
}

// ═══════════════════════════════════════════════════════════════
// RemoteConfig 扩展字段 — 2 tests
// ═══════════════════════════════════════════════════════════════

#[test]
fn remote_config_with_retry_and_headers() {
    let json = r#"{
        "transport":"streamable-http",
        "url":"https://mcp.example.com",
        "headers":{"Authorization":"Bearer sk-xxx"},
        "retry":{"max_retries":5}
    }"#;
    let config: RemoteConfig = serde_json::from_str(json).unwrap();
    assert_eq!(config.transport, "streamable-http");
    assert_eq!(config.headers.get("Authorization").unwrap(), "Bearer sk-xxx");
    assert!(config.retry.is_some());
    assert_eq!(config.retry.unwrap().max_retries, 5);
    assert!(config.timeout_secs.is_none());
}

#[test]
fn remote_config_minimal_no_extensions() {
    let json = r#"{"transport":"streamable-http","url":"https://mcp.example.com"}"#;
    let config: RemoteConfig = serde_json::from_str(json).unwrap();
    assert!(config.timeout_secs.is_none());
    assert!(config.headers.is_empty());
    assert!(config.tls_ca_cert.is_none());
    assert!(config.retry.is_none());
}
