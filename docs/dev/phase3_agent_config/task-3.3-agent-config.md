# 任务 3.3 + 3.5：`AgentConfig` 聚合 struct + `lib.rs` 公开接口

> Phase 3 — AgentConfig 第三项任务
> 父文档：`docs/v1.x/phase3_agent_config/phase3-agent-config-design.md`
> 依赖：3.1（ModelSpec + ToolSpec + ToolPermission）+ 3.2（ResourceSpec），已完成

## 设计思路

`AgentConfig` 聚合 3.1 和 3.2 的全部类型，是 Agent 声明式配置的顶层 struct。所有字段 `#[serde(default)]`：空 `AgentConfig` 合法存在（不含模型的极简 agent）。

| 字段 | 类型 | 用途 |
|------|------|------|
| `system_prompt` | `String` | 每轮模型调用前注入的系统提示词 |
| `models` | `Vec<ModelSpec>` | 模型列表，按优先级排列 |
| `tools` | `Vec<ToolSpec>` | 可用工具及权限 |
| `allowed_paths` | `Vec<String>` | 沙箱路径白名单 |
| `subagents` | `Vec<ResourceSpec>` | 可委托的 subagent |
| `teammates` | `Vec<ResourceSpec>` | 可协作的 teammate |

## 代码实现

### `crates/arf-agent/src/config.rs`（新文件）

```rust
//! AgentConfig — top-level declarative agent configuration.

use serde::{Deserialize, Serialize};

use crate::{ModelSpec, ResourceSpec, ToolSpec};

/// Declarative agent configuration — pure data, no behavior.
///
/// AgentConfig declares WHAT an agent needs. Engine (Phase 4) reads it
/// and figures out HOW to resolve each logical resource to concrete
/// NodeIds on the Bus.
///
/// AgentConfig knows nothing about the Bus, NodeIds, or whether
/// resources are online. It only speaks logical names.
///
/// All fields use `#[serde(default)]`: an empty AgentConfig is valid.
/// Missing fields in YAML/JSON deserialization do not error.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AgentConfig {
    /// System prompt injected at the start of every model call.
    #[serde(default)]
    pub system_prompt: String,

    /// Models in priority order. Engine picks the first one whose
    /// model node is online on the Bus.
    #[serde(default)]
    pub models: Vec<ModelSpec>,

    /// Tools this agent may use, each with permission constraints.
    #[serde(default)]
    pub tools: Vec<ToolSpec>,

    /// File system paths this agent is allowed to access.
    /// Sandbox enforces these boundaries.
    #[serde(default)]
    pub allowed_paths: Vec<String>,

    /// Subagents this agent can delegate tasks to.
    /// One ResourceSpec may resolve to N NodeIds on the Bus.
    #[serde(default)]
    pub subagents: Vec<ResourceSpec>,

    /// Teammates this agent can coordinate with.
    /// One ResourceSpec may resolve to N NodeIds on the Bus.
    #[serde(default)]
    pub teammates: Vec<ResourceSpec>,
}
```

逐行：
- `use crate::{ModelSpec, ResourceSpec, ToolSpec}` — 从同 crate 导入 3.1/3.2 定义的类型
- 六个字段全部 `#[serde(default)]` — JSON 缺字段不报错，取类型默认值（`String::default()` = `""`，`Vec::default()` = `[]`）
- `Debug + Clone + PartialEq + Serialize + Deserialize` — 标准 derive 集合

```rust
impl AgentConfig {
    /// Create a new AgentConfig with all fields empty.
    pub fn new() -> Self {
        Self {
            system_prompt: String::new(),
            models: Vec::new(),
            tools: Vec::new(),
            allowed_paths: Vec::new(),
            subagents: Vec::new(),
            teammates: Vec::new(),
        }
    }
}

impl Default for AgentConfig {
    fn default() -> Self {
        Self::new()
    }
}
```

逐行：
- `new()` — 显式构造器，等价于 `Default::default()`
- `impl Default` — 标准 Rust trait，方便框架中 `AgentConfig::default()` 和 `#[derive(Default)]` 场景。手写而非 derive 以保证 `new()` 和 `default()` 使用同一实现

---

### `crates/arf-agent/src/lib.rs` — 追加 config 模块

```rust
//! ARF AgentConfig — declarative resource configuration skeleton.
//!
//! AgentConfig is a pure data structure that declares WHAT an agent needs:
//! models, tools, subagents, teammates, allowed paths. It uses only logical
//! names and knows nothing about the Bus, NodeIds, or resource availability.
//! Engine (Phase 4) reads AgentConfig and resolves resources at runtime.

mod config;
mod model;
mod resource;
mod tool;

pub use config::AgentConfig;
pub use model::ModelSpec;
pub use resource::ResourceSpec;
pub use tool::{ToolPermission, ToolSpec};
```

---

## 测试

### AgentConfig — 9 tests

所有测试在 `crates/arf-agent/src/config.rs` 的 `#[cfg(test)] mod tests` 块内。

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use crate::{ModelSpec, ResourceSpec, ToolPermission, ToolSpec};
    use serde_json;

    // ═══════════════════════════════════════════════════════════════
    // AgentConfig — 9 tests
    // ═══════════════════════════════════════════════════════════════

    // [构造] new() 创建全空字段的配置
    #[test]
    fn agent_config_new_is_empty() {
        let config = AgentConfig::new();
        assert_eq!(config.system_prompt, "");
        assert!(config.models.is_empty());
        assert!(config.tools.is_empty());
        assert!(config.allowed_paths.is_empty());
        assert!(config.subagents.is_empty());
        assert!(config.teammates.is_empty());
    }

    // [trait] Default：AgentConfig::default() 等于 AgentConfig::new()
    #[test]
    fn agent_config_default_equals_new() {
        assert_eq!(AgentConfig::default(), AgentConfig::new());
    }

    // [构造] 含所有字段的完整配置正确存储
    #[test]
    fn agent_config_with_all_fields() {
        let model = ModelSpec {
            provider: "deepseek".into(),
            model_name: "deepseek-flash".into(),
            thinking_enabled: true,
            temperature: Some(0.7),
            max_output_tokens: Some(4096),
            extra: serde_json::Value::Null,
        };
        let tool = ToolSpec {
            name: "read_file".into(),
            permission: ToolPermission::Allow,
            parameter_filter: None,
            description: None,
            parameters: None,
        };
        let sub = ResourceSpec {
            name: "code_reviewer".into(),
            node_type: "agent/subagent".into(),
            capabilities: None,
        };
        let config = AgentConfig {
            system_prompt: "You are helpful.".into(),
            models: vec![model.clone()],
            tools: vec![tool.clone()],
            allowed_paths: vec!["/workspace".into()],
            subagents: vec![sub.clone()],
            teammates: vec![],
        };
        assert_eq!(config.system_prompt, "You are helpful.");
        assert_eq!(config.models.len(), 1);
        assert_eq!(config.models[0], model);
        assert_eq!(config.tools.len(), 1);
        assert_eq!(config.tools[0], tool);
        assert_eq!(config.allowed_paths.len(), 1);
        assert_eq!(config.subagents.len(), 1);
        assert!(config.teammates.is_empty());
    }

    // [trait] Clone：克隆后相等
    #[test]
    fn agent_config_clone() {
        let config = AgentConfig {
            system_prompt: "test".into(),
            ..AgentConfig::default()
        };
        assert_eq!(config, config.clone());
    }

    // [trait] PartialEq：相同字段相等
    #[test]
    fn agent_config_equality() {
        let a = AgentConfig {
            system_prompt: "prompt".into(),
            ..AgentConfig::default()
        };
        let b = AgentConfig {
            system_prompt: "prompt".into(),
            ..AgentConfig::default()
        };
        let c = AgentConfig {
            system_prompt: "different".into(),
            ..AgentConfig::default()
        };
        assert_eq!(a, b);
        assert_ne!(a, c);
    }

    // [序列化] 空 Config serde 往返：所有字段为空
    #[test]
    fn agent_config_empty_serialization_roundtrip() {
        let config = AgentConfig::new();
        let json = serde_json::to_string(&config).unwrap();
        let back: AgentConfig = serde_json::from_str(&json).unwrap();
        assert_eq!(config, back);
        assert!(json.contains("system_prompt"));
        assert!(json.contains("models"));
        assert!(json.contains("tools"));
    }

    // [序列化] 含数据的完整往返
    #[test]
    fn agent_config_full_serialization_roundtrip() {
        let config = AgentConfig {
            system_prompt: "You are an assistant.".into(),
            models: vec![ModelSpec {
                provider: "deepseek".into(),
                model_name: "deepseek-flash".into(),
                thinking_enabled: true,
                temperature: Some(0.5),
                max_output_tokens: Some(8192),
                extra: serde_json::json!({"top_p": 0.95}),
            }],
            tools: vec![ToolSpec {
                name: "search".into(),
                permission: ToolPermission::Ask,
                parameter_filter: None,
                description: Some("Search the web".into()),
                parameters: None,
            }],
            allowed_paths: vec!["/workspace".into(), "/tmp".into()],
            subagents: vec![ResourceSpec {
                name: "reviewer".into(),
                node_type: "agent/subagent".into(),
                capabilities: Some(serde_json::json!({"skills": ["code_review"]})),
            }],
            teammates: vec![],
        };
        let json = serde_json::to_string(&config).unwrap();
        let back: AgentConfig = serde_json::from_str(&json).unwrap();
        assert_eq!(config, back);
        assert_eq!(back.models.len(), 1);
        assert_eq!(back.tools.len(), 1);
        assert_eq!(back.subagents.len(), 1);
    }

    // [兼容] 空 JSON 对象反序列化：全部取默认值
    #[test]
    fn agent_config_deserialize_empty_json() {
        let json = "{}";
        let config: AgentConfig = serde_json::from_str(json).unwrap();
        assert_eq!(config, AgentConfig::new());
    }

    // [兼容] 部分字段缺失不报错，缺失字段取默认值
    #[test]
    fn agent_config_deserialize_partial_json() {
        let json = r#"{"system_prompt": "hello", "allowed_paths": ["/x"]}"#;
        let config: AgentConfig = serde_json::from_str(json).unwrap();
        assert_eq!(config.system_prompt, "hello");
        assert_eq!(config.allowed_paths, vec!["/x"]);
        assert!(config.models.is_empty());
        assert!(config.tools.is_empty());
        assert!(config.subagents.is_empty());
        assert!(config.teammates.is_empty());
    }
}
```

---

## 测试汇总

| 类型 | 测试数 | 覆盖角度 |
|------|--------|---------|
| AgentConfig | 9 | 构造(×2：new+全字段)、Default、Clone、PartialEq、序列化(×2：空+全)、兼容(×2：空JSON+缺字段) |
| **累计** (3.1 + 3.2 + 3.3) | **43** | |

---

## 与现有代码的关系

- 新增 `crates/arf-agent/src/config.rs`
- 修改 `crates/arf-agent/src/lib.rs`（加 `mod config;` + `pub use AgentConfig`）
- 不修改其他文件

---

## 交付标准

- `cargo test --workspace` 全部通过（221 + 9 = 230 tests）
- `cargo fmt --check` + `cargo clippy` 无警告
- `AgentConfig` serde 往返一致，缺字段兼容
- `arf-agent` 仅依赖 `serde` + `serde_json`，不依赖任何 ARF crate
