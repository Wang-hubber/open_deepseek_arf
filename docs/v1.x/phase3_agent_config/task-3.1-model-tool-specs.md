# 任务 3.1：`ModelSpec` + `ToolSpec` + `ToolPermission`

> Phase 3 — AgentConfig 第一项任务
> 父文档：`docs/v1.x/phase3_agent_config/phase3-agent-config-design.md`
> 依赖：Phase 0 脚手架（已完成），`arf-agent` Cargo.toml（已修正）

## 设计思路

`arf-agent` 是纯数据结构 crate，Phase 3 定义 Agent 的声明式配置类型。任务 3.1 先实现三个叶子类型——它们不依赖 `arf-core` 的任何类型，只依赖 `serde`。

| 类型 | 用途 | 关键字段 |
|------|------|---------|
| `ModelSpec` | 声明一个模型 | provider, model_name, thinking_enabled, temperature, max_output_tokens, extra |
| `ToolSpec` | 声明一个工具及权限 | name, permission, parameter_filter, description, parameters |
| `ToolPermission` | 工具三级权限 | Allow / Ask / Deny |

这三个类型完全自包含，不引用 `NodeId`、`TaskId` 等 ARF 类型。Agent 声明"我要用 deepseek-chat，温度 0.7"，不关心这个模型在 Bus 上的 NodeId 是什么——那是 Engine（Phase 4）的事。

## 代码实现

### 文件结构

```
crates/arf-agent/src/
├── lib.rs        # 模块声明 + 重新导出
├── model.rs      # ModelSpec
└── tool.rs       # ToolSpec + ToolPermission
```

---

### `crates/arf-agent/src/model.rs`

```rust
//! Agent model declaration — provider, model name, inference parameters.

use serde::{Deserialize, Serialize};

/// A model that this agent may use, in priority order.
///
/// `ModelSpec` is a pure data declaration. It uses logical names
/// (`provider` + `model_name`) and does not reference any Bus NodeId.
/// Engine (Phase 4) resolves each spec to a concrete model node at runtime.
///
/// Agent declares multiple `ModelSpec`s in priority order. Engine picks
/// the first one whose provider + model_name matches an online model node.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ModelSpec {
    /// Provider identifier: `"deepseek"`, `"openai"`, `"anthropic"`.
    pub provider: String,

    /// Model name: `"deepseek-chat"`, `"gpt-4o"`, `"claude-sonnet-4-6"`.
    pub model_name: String,

    /// Whether thinking/reasoning is enabled for this model.
    /// Provider default if model doesn't support thinking.
    #[serde(default)]
    pub thinking_enabled: bool,

    /// Sampling temperature (0.0–2.0). Provider default if unset.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub temperature: Option<f32>,

    /// Hard limit on output tokens. Provider default if unset.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub max_output_tokens: Option<u32>,

    /// Provider-specific extra parameters (e.g., `top_p`, `frequency_penalty`).
    /// Passed through to the model API as-is. ModelAdapter reads this.
    #[serde(default, skip_serializing_if = "serde_json::Value::is_null")]
    pub extra: serde_json::Value,
}
```

逐行：
- `provider` / `model_name` — 逻辑标识，Engine 用来匹配 Bus 上的 model node
- `thinking_enabled: bool` — `#[serde(default)]`：旧配置缺此字段反序列化为 `false`
- `temperature: Option<f32>` — `skip_serializing_if`：JSON 缺省时不输出，由 Provider 使用自身默认值
- `max_output_tokens: Option<u32>` — 同上，不输出即不限制
- `extra: serde_json::Value` — 供应商专属参数黑洞，ModelAdapter 全权读写。`default` + `skip_serializing_if null`：缺省为 `Null`，为 `Null` 时不写入 JSON

---

### `crates/arf-agent/src/tool.rs`

```rust
//! Agent tool declaration — tool name, permission, parameter constraints.

use serde::{Deserialize, Serialize};

/// Permission level for an agent tool.
///
/// Controls whether the tool runs automatically, requires user approval,
/// or is blocked entirely. Engine enforces this at runtime.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub enum ToolPermission {
    /// Tool runs without asking the user.
    Allow,
    /// Tool must ask the user before running.
    Ask,
    /// Tool is blocked — Engine rejects any call.
    Deny,
}

/// A tool this agent may call, with permission constraints.
///
/// `ToolSpec` declares a tool by its logical name (matching the tool name
/// registered by an MCP node on the Bus). It does not reference any Bus
/// NodeId — Engine resolves the tool name to concrete MCP nodes at runtime.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ToolSpec {
    /// Tool name as registered on the Bus (e.g., `"read_file"`, `"web_search"`).
    pub name: String,

    /// Permission level for this tool.
    pub permission: ToolPermission,

    /// Optional parameter filter/constraints.
    /// E.g., `{"paths": ["/workspace/*"]}` to restrict file tool access.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub parameter_filter: Option<serde_json::Value>,

    /// Natural-language description for the model's function calling.
    /// If `None`, the tool's Bus-registered description is used as fallback.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub description: Option<String>,

    /// JSON Schema for the tool's parameters (for function calling).
    /// If `None`, the tool's Bus-registered schema is used as fallback.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub parameters: Option<serde_json::Value>,
}
```

逐行：
- `ToolPermission` — 三个变体：`Allow`（自动执行）、`Ask`（需用户批准）、`Deny`（禁止）。Engine 在 `before_tools` 检查点执行判定
- `parameter_filter` — 对工具参数的约束白名单。如限制文件工具只能访问 `/workspace/*`。`None` 表示不过滤
- `description` / `parameters` — 这两个字段可覆盖 MCP 节点注册时声明的工具描述和 schema。`None` 表示用 MCP 节点的默认值
- 所有可选字段 `skip_serializing_if = "Option::is_none"`：不设置就不输出到 JSON

---

### `crates/arf-agent/src/lib.rs`

```rust
//! ARF AgentConfig — declarative resource configuration skeleton.
//!
//! AgentConfig is a pure data structure that declares WHAT an agent needs:
//! models, tools, subagents, teammates, allowed paths. It uses only logical
//! names and knows nothing about the Bus, NodeIds, or resource availability.
//! Engine (Phase 4) reads AgentConfig and resolves resources at runtime.

mod model;
mod tool;

pub use model::ModelSpec;
pub use tool::{ToolPermission, ToolSpec};
```

---

## 测试

所有测试在各自源文件的 `#[cfg(test)] mod tests` 块内。

### ModelSpec — 11 tests

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use serde_json;

    // ═══════════════════════════════════════════════════════════════
    // ModelSpec — 11 tests
    // ═══════════════════════════════════════════════════════════════

    // [构造] 所有字段显式赋值可读，值正确
    #[test]
    fn model_spec_all_fields() {
        let spec = ModelSpec {
            provider: "deepseek".into(),
            model_name: "deepseek-chat".into(),
            thinking_enabled: true,
            temperature: Some(0.7),
            max_output_tokens: Some(4096),
            extra: serde_json::json!({"top_p": 0.9}),
        };
        assert_eq!(spec.provider, "deepseek");
        assert_eq!(spec.model_name, "deepseek-chat");
        assert!(spec.thinking_enabled);
        assert_eq!(spec.temperature, Some(0.7));
        assert_eq!(spec.max_output_tokens, Some(4096));
        assert_eq!(spec.extra["top_p"], 0.9);
    }

    // [边界] thinking_enabled 默认 false（旧配置缺字段兼容）
    #[test]
    fn model_spec_thinking_disabled_by_default() {
        let json = r#"{"provider":"openai","model_name":"gpt-4"}"#;
        let spec: ModelSpec = serde_json::from_str(json).unwrap();
        assert!(!spec.thinking_enabled);
    }

    // [边界] temperature 为 None 时不序列化到 JSON
    #[test]
    fn model_spec_temperature_none_skipped() {
        let spec = ModelSpec {
            provider: "openai".into(),
            model_name: "gpt-4o".into(),
            thinking_enabled: false,
            temperature: None,
            max_output_tokens: Some(1024),
            extra: serde_json::Value::Null,
        };
        let json = serde_json::to_string(&spec).unwrap();
        assert!(!json.contains("temperature"));
    }

    // [边界] max_output_tokens 为 None 时不序列化到 JSON
    #[test]
    fn model_spec_max_tokens_none_skipped() {
        let spec = ModelSpec {
            provider: "deepseek".into(),
            model_name: "deepseek-chat".into(),
            thinking_enabled: false,
            temperature: Some(0.3),
            max_output_tokens: None,
            extra: serde_json::Value::Null,
        };
        let json = serde_json::to_string(&spec).unwrap();
        assert!(!json.contains("max_output_tokens"));
    }

    // [边界] extra 为 Null 时不序列化到 JSON
    #[test]
    fn model_spec_extra_null_skipped() {
        let spec = ModelSpec {
            provider: "deepseek".into(),
            model_name: "deepseek-chat".into(),
            thinking_enabled: false,
            temperature: None,
            max_output_tokens: None,
            extra: serde_json::Value::Null,
        };
        let json = serde_json::to_string(&spec).unwrap();
        assert!(!json.contains("extra"));
    }

    // [边界] 最小合法 JSON：仅 provider + model_name，其余取默认值
    #[test]
    fn model_spec_minimal_json() {
        let json = r#"{"provider":"anthropic","model_name":"claude-sonnet-4-6"}"#;
        let spec: ModelSpec = serde_json::from_str(json).unwrap();
        assert_eq!(spec.provider, "anthropic");
        assert_eq!(spec.model_name, "claude-sonnet-4-6");
        assert!(!spec.thinking_enabled);
        assert_eq!(spec.temperature, None);
        assert_eq!(spec.max_output_tokens, None);
        assert_eq!(spec.extra, serde_json::Value::Null);
    }

    // [trait] Clone：克隆后与原值相等
    #[test]
    fn model_spec_clone() {
        let spec = ModelSpec {
            provider: "deepseek".into(),
            model_name: "deepseek-chat".into(),
            thinking_enabled: true,
            temperature: Some(0.5),
            max_output_tokens: Some(2048),
            extra: serde_json::json!({"key": "value"}),
        };
        assert_eq!(spec, spec.clone());
    }

    // [trait] PartialEq：相同字段相等，不同 provider 不等
    #[test]
    fn model_spec_equality() {
        let a = ModelSpec {
            provider: "x".into(),
            model_name: "m".into(),
            thinking_enabled: false,
            temperature: None,
            max_output_tokens: None,
            extra: serde_json::Value::Null,
        };
        let b = ModelSpec {
            provider: "x".into(),
            model_name: "m".into(),
            thinking_enabled: false,
            temperature: None,
            max_output_tokens: None,
            extra: serde_json::Value::Null,
        };
        let c = ModelSpec {
            provider: "y".into(),
            ..a.clone()
        };
        assert_eq!(a, b);
        assert_ne!(a, c);
    }

    // [序列化] 全字段 serde 往返：所有字段逐项一致
    #[test]
    fn model_spec_serialization_roundtrip_full() {
        let spec = ModelSpec {
            provider: "deepseek".into(),
            model_name: "deepseek-chat".into(),
            thinking_enabled: true,
            temperature: Some(0.7),
            max_output_tokens: Some(8192),
            extra: serde_json::json!({"reasoning_effort": "high"}),
        };
        let json = serde_json::to_string(&spec).unwrap();
        let back: ModelSpec = serde_json::from_str(&json).unwrap();
        assert_eq!(spec, back);
    }

    // [序列化] 最简 spec 往返：仅 provider + model_name
    #[test]
    fn model_spec_serialization_roundtrip_minimal() {
        let spec = ModelSpec {
            provider: "openai".into(),
            model_name: "gpt-4o".into(),
            thinking_enabled: false,
            temperature: None,
            max_output_tokens: None,
            extra: serde_json::Value::Null,
        };
        let json = serde_json::to_string(&spec).unwrap();
        let back: ModelSpec = serde_json::from_str(&json).unwrap();
        assert_eq!(spec, back);
    }

    // [兼容] 未知字段反序列化不报错（前向兼容）
    #[test]
    fn model_spec_unknown_fields_ignored() {
        let json = r#"{"provider":"x","model_name":"y","future_field":123}"#;
        let spec: ModelSpec = serde_json::from_str(json).unwrap();
        assert_eq!(spec.provider, "x");
        assert_eq!(spec.model_name, "y");
    }
}
```

### ToolSpec + ToolPermission — 14 tests

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use serde_json;

    // ═══════════════════════════════════════════════════════════════
    // ToolPermission — 6 tests
    // ═══════════════════════════════════════════════════════════════

    // [覆盖] 三种变体均可构造
    #[test]
    fn tool_permission_all_variants_construct() {
        let _ = ToolPermission::Allow;
        let _ = ToolPermission::Ask;
        let _ = ToolPermission::Deny;
    }

    // [trait] PartialEq：相同变体相等，不同不等
    #[test]
    fn tool_permission_equality() {
        assert_eq!(ToolPermission::Allow, ToolPermission::Allow);
        assert_eq!(ToolPermission::Ask, ToolPermission::Ask);
        assert_ne!(ToolPermission::Allow, ToolPermission::Deny);
        assert_ne!(ToolPermission::Ask, ToolPermission::Deny);
    }

    // [trait] Clone：克隆后与原值相等
    #[test]
    fn tool_permission_clone() {
        assert_eq!(ToolPermission::Allow, ToolPermission::Allow.clone());
        assert_eq!(ToolPermission::Ask, ToolPermission::Ask.clone());
        assert_eq!(ToolPermission::Deny, ToolPermission::Deny.clone());
    }

    // [序列化] Allow — JSON 字符串 "Allow" 往返
    #[test]
    fn tool_permission_serialization_allow() {
        let json = serde_json::to_string(&ToolPermission::Allow).unwrap();
        assert_eq!(json, r#""Allow""#);
        let back: ToolPermission = serde_json::from_str(&json).unwrap();
        assert_eq!(back, ToolPermission::Allow);
    }

    // [序列化] Ask / Deny 变体往返
    #[test]
    fn tool_permission_serialization_ask_deny() {
        for perm in [ToolPermission::Ask, ToolPermission::Deny] {
            let json = serde_json::to_string(&perm).unwrap();
            let back: ToolPermission = serde_json::from_str(&json).unwrap();
            assert_eq!(perm, back);
        }
    }

    // [兼容] 未知变体反序列化报错（拒绝未知权限）
    #[test]
    fn tool_permission_unknown_variant_error() {
        let result: Result<ToolPermission, _> = serde_json::from_str(r#""Unknown""#);
        assert!(result.is_err());
    }

    // ═══════════════════════════════════════════════════════════════
    // ToolSpec — 8 tests
    // ═══════════════════════════════════════════════════════════════

    // [构造] 所有字段显式赋值可读
    #[test]
    fn tool_spec_all_fields() {
        let spec = ToolSpec {
            name: "read_file".into(),
            permission: ToolPermission::Ask,
            parameter_filter: Some(serde_json::json!({"paths": ["/workspace/*"]})),
            description: Some("Read a file from the workspace".into()),
            parameters: Some(serde_json::json!({"type": "object", "properties": {}})),
        };
        assert_eq!(spec.name, "read_file");
        assert_eq!(spec.permission, ToolPermission::Ask);
        assert!(spec.parameter_filter.is_some());
        assert!(spec.description.is_some());
        assert!(spec.parameters.is_some());
    }

    // [构造] 仅有 name + permission 的最简 ToolSpec
    #[test]
    fn tool_spec_minimal() {
        let spec = ToolSpec {
            name: "search".into(),
            permission: ToolPermission::Allow,
            parameter_filter: None,
            description: None,
            parameters: None,
        };
        assert_eq!(spec.name, "search");
        assert_eq!(spec.permission, ToolPermission::Allow);
    }

    // [边界] 可选字段为 None 时不序列化到 JSON
    #[test]
    fn tool_spec_optionals_skipped() {
        let spec = ToolSpec {
            name: "run".into(),
            permission: ToolPermission::Deny,
            parameter_filter: None,
            description: None,
            parameters: None,
        };
        let json = serde_json::to_string(&spec).unwrap();
        assert!(!json.contains("parameter_filter"));
        assert!(!json.contains("description"));
        assert!(!json.contains("parameters"));
    }

    // [权限] Allow 变体：工具自动执行
    #[test]
    fn tool_spec_permission_allow() {
        let spec = ToolSpec {
            name: "auto_tool".into(),
            permission: ToolPermission::Allow,
            parameter_filter: None,
            description: None,
            parameters: None,
        };
        assert_eq!(spec.permission, ToolPermission::Allow);
    }

    // [权限] Deny 变体：工具被禁止
    #[test]
    fn tool_spec_permission_deny() {
        let spec = ToolSpec {
            name: "blocked_tool".into(),
            permission: ToolPermission::Deny,
            parameter_filter: None,
            description: None,
            parameters: None,
        };
        assert_eq!(spec.permission, ToolPermission::Deny);
    }

    // [trait] Clone：克隆后相等
    #[test]
    fn tool_spec_clone() {
        let spec = ToolSpec {
            name: "t".into(),
            permission: ToolPermission::Ask,
            parameter_filter: Some(serde_json::json!({"x": 1})),
            description: Some("desc".into()),
            parameters: None,
        };
        assert_eq!(spec, spec.clone());
    }

    // [序列化] 全字段 serde 往返
    #[test]
    fn tool_spec_serialization_roundtrip_full() {
        let spec = ToolSpec {
            name: "web_search".into(),
            permission: ToolPermission::Ask,
            parameter_filter: Some(serde_json::json!({"domains": ["wikipedia.org"]})),
            description: Some("Search the web".into()),
            parameters: Some(serde_json::json!({"type": "object"})),
        };
        let json = serde_json::to_string(&spec).unwrap();
        let back: ToolSpec = serde_json::from_str(&json).unwrap();
        assert_eq!(spec, back);
    }

    // [序列化] 最简 spec 往返：仅 name + permission
    #[test]
    fn tool_spec_serialization_roundtrip_minimal() {
        let spec = ToolSpec {
            name: "minimal".into(),
            permission: ToolPermission::Allow,
            parameter_filter: None,
            description: None,
            parameters: None,
        };
        let json = serde_json::to_string(&spec).unwrap();
        let back: ToolSpec = serde_json::from_str(&json).unwrap();
        assert_eq!(spec, back);
    }
}
```

---

## 测试汇总

| 类型 | 测试数 | 覆盖角度 |
|------|--------|---------|
| ModelSpec | 11 | 构造(×1)、边界(×3：thinking默认/false、temp/max_tokens/extra 跳过)、边界(×1：最小JSON)、Clone、PartialEq、序列化(×2：全字段+最简)、兼容(×1) |
| ToolPermission | 6 | 覆盖(×1)、Eq、Clone、序列化(×2：Allow+Ask/Deny)、兼容(×1) |
| ToolSpec | 8 | 构造(×2：全字段+最简)、边界(×1：可选字段跳过)、权限(×2)、Clone、序列化(×2：全字段+最简) |
| **合计** | **25** | |

---

## 与现有代码的关系

- `crates/arf-agent/Cargo.toml` — 已有 `serde` + `serde_json`（任务 3.4 已修正），无需修改
- `crates/arf-agent/src/lib.rs` — 替换占位内容，添加 `mod model; mod tool;` 和 `pub use`
- 新增 `crates/arf-agent/src/model.rs`
- 新增 `crates/arf-agent/src/tool.rs`
- 不修改 `arf-core` 或其他 crate

---

## 交付标准

- `cargo test --workspace` 全部通过（含已有 187 + 新增 25 = 212 tests）
- `cargo fmt --check` + `cargo clippy` 无警告
- `ModelSpec` / `ToolSpec` / `ToolPermission` serde 往返一致
- 旧配置兼容：缺字段反序列化不报错，默认值正确
- `arf-agent` 仅依赖 `serde` + `serde_json`，不依赖任何 ARF crate
