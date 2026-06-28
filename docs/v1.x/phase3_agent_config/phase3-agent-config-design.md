# Phase 3 — AgentConfig 设计

> 父文档：`docs/v1.x/2026-06-26-arfv1-roadmap.md`
> 依赖：Phase 1 (Bus) + Phase 2 (State) — 已完成
> 状态：✅ 已完成（任务 3.1–3.6）

## 定位

**AgentConfig 是声明式配置骨架，纯数据结构。** Agent 的概念就是一个 struct——AgentConfig 声明"我需要什么"，Engine（Phase 4）负责"怎么做"。

```
AgentConfig (配置骨架) ──被读取──→ Engine (转移引擎) ──驱动──→ State (状态快照)
    WHAT                            HOW                        WHERE
```

AgentConfig 不感知 Bus，不持有 NodeId，所有字段使用逻辑名。它不执行任何操作——只是数据。

## 依赖关系

AgentConfig 是纯数据，放在 `arf-agent` crate。它不需要 `arf-core` 的任何类型（不引用 NodeId、Message 等），只依赖 `serde` + `serde_json`。

```
serde + serde_json
        ↑
   arf-agent (AgentConfig + ModelSpec + ToolSpec + ResourceSpec)
        ↑
   arf-engine (Phase 4 — 读取 AgentConfig，执行 discovery + ReAct)
```

Phase 3 同时修正当前 `arf-agent → arf-engine` 的反向依赖：

```toml
# crates/arf-agent/Cargo.toml — Phase 3 修改
[dependencies]
# 移除 arf-engine，AgentConfig 是纯数据
serde = { version = "1", features = ["derive"] }
serde_json = "1"
```

## 数据结构

### AgentConfig

顶层 struct，Agent 的完整声明式配置。

```rust
/// Declarative agent configuration — pure data, no behavior.
///
/// AgentConfig declares WHAT an agent needs. Engine (Phase 4) reads it
/// and figures out HOW to resolve each logical resource to concrete
/// NodeIds on the Bus.
///
/// AgentConfig knows nothing about the Bus, NodeIds, or whether
/// resources are online. It only speaks logical names.
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

所有字段 `#[serde(default)]`：空 AgentConfig 可合法存在（不含模型、不含工具的极简 agent）。YAML/JSON 反序列化时缺失字段不报错。

### ModelSpec — 模型声明

```rust
/// A model that this agent may use.
///
/// Engine resolves each ModelSpec to a model node on the Bus
/// (matching by `provider` + `model_name` against node capabilities),
/// then selects the first one that's online.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ModelSpec {
    /// Provider identifier: "deepseek", "openai", "anthropic".
    pub provider: String,
    /// Model name: "deepseek-flash", "gpt-4o", "claude-sonnet-4-6".
    pub model_name: String,
    /// Whether thinking/reasoning is enabled.
    #[serde(default)]
    pub thinking_enabled: bool,
    /// Sampling temperature (0.0–2.0). Provider default if unset.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub temperature: Option<f32>,
    /// Hard limit on output tokens. Provider default if unset.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub max_output_tokens: Option<u32>,
    /// Provider-specific extra parameters (e.g., top_p, frequency_penalty).
    /// Passed through to the model API as-is.
    #[serde(default, skip_serializing_if = "serde_json::Value::is_null")]
    pub extra: serde_json::Value,
}
```

### ToolSpec — 工具声明

```rust
/// A tool this agent may call, with permission constraints.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ToolSpec {
    /// Tool name as it appears on the Bus (e.g., "read_file", "web_search").
    pub name: String,
    /// Permission level for this tool.
    pub permission: ToolPermission,
    /// Optional parameter filter/constraints.
    /// E.g., {"paths": ["/workspace/*"]} to restrict file tool access.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub parameter_filter: Option<serde_json::Value>,
    /// Natural-language description for the model's function calling.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub description: Option<String>,
    /// JSON Schema for the tool's parameters (for function calling).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub parameters: Option<serde_json::Value>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub enum ToolPermission {
    /// Tool runs without asking user.
    Allow,
    /// Tool must ask user before running.
    Ask,
    /// Tool is blocked entirely. Engine rejects calls.
    Deny,
}
```

### ResourceSpec — 逻辑资源需求（1:N）

```rust
/// Declares a logical resource dependency.
///
/// Agent says "I need a filesystem". Engine discovers N matching nodes
/// on the Bus and registers all of them. At runtime, Engine selects
/// the first online node.
///
/// 1:N mapping is inherent: one logical need → multiple concrete nodes.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ResourceSpec {
    /// Agent-given alias for this resource (e.g., "primary_fs", "code_reviewer").
    pub name: String,
    /// Expected `node_type` on the Bus when Engine does discovery.
    /// Common values: "mcp", "agent/subagent", "agent/teammate".
    pub node_type: String,
    /// Optional capabilities matcher. Engine filters discovery results
    /// by matching node_info.capabilities against this value.
    /// E.g., {"resources": ["tool/read", "tool/write"]} to find
    /// only MCP nodes that provide file I/O tools.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub capabilities: Option<serde_json::Value>,
}
```

## 构造方式

AgentConfig 支持三种构造路径：

```rust
// 1. 代码构造
let config = AgentConfig {
    system_prompt: "You are a helpful assistant.".into(),
    models: vec![ModelSpec {
        provider: "deepseek".into(),
        model_name: "deepseek-flash".into(),
        thinking_enabled: false,
        temperature: None,
        max_output_tokens: None,
        extra: serde_json::Value::Null,
    }],
    ..AgentConfig::default()
};

// 2. YAML/JSON 反序列化
let config: AgentConfig = serde_yaml::from_str(yaml_str)?;

// 3. Default + builder（如果需要，在 task spec 中细化）
let config = AgentConfig::default()
    .with_model(ModelSpec { ... })
    .with_tool(ToolSpec { ... });
```

`AgentConfig::default()` 返回全字段为空的合法配置。应用代码按需填充。

## 与后续 Phase 的关系

| Phase | 如何使用 AgentConfig |
|-------|---------------------|
| Phase 4 Engine | 读取 AgentConfig，调用 `bus.graph()` 获取在线节点表，按 `node_type` + `capabilities` 过滤匹配，将 ResourceSpec 解析为 `ResolvedManifest`（逻辑名 → `Vec<NodeId>`），订阅后续 `node_online`/`node_offline` 事件动态更新 |
| Phase 5 ModelAdapter | 根据 `ModelSpec.provider` + `model_name` 选择适配器，使用 `temperature`/`max_output_tokens`/`extra`/`thinking_enabled` 构造 API 请求 |
| Phase 6 MCP | MCP 节点上线时广播 `node_online{type=mcp, capabilities}`，Engine 用 `ResourceSpec.capabilities` 做匹配过滤 |

## 任务拆解

| # | 任务 | 内容 | 产出 |
|---|------|------|------|
| 3.1 | `ModelSpec` + `ToolSpec` + `ToolPermission` ✅ | 纯数据 struct + serde + 25 tests | `crates/arf-agent/src/model.rs`, `crates/arf-agent/src/tool.rs` |
| 3.2 | `ResourceSpec` ✅ | 纯数据 struct + 1:N 语义 + 9 tests | `crates/arf-agent/src/resource.rs` |
| 3.3 | `AgentConfig` ✅ | 聚合 struct + Default + serde + 9 tests | `crates/arf-agent/src/config.rs` |
| 3.4 | `arf-agent` 依赖修正 ✅ | 移除 `arf-engine` 依赖，添加 `serde` | `crates/arf-agent/Cargo.toml` |
| 3.5 | `lib.rs` 公开接口 ✅ | 重新导出核心类型 + module docstring | `crates/arf-agent/src/lib.rs` |
| 3.6 | 序列化兼容测试 ✅ | JSON 往返 + 缺字段反序列化 + extra 嵌套，43 tests | `#[cfg(test)]` |

## 交付标准

- [x] `cargo test --workspace` 全部通过（230 tests）
- [x] `arf-agent` 不再依赖 `arf-engine`，仅依赖 `serde` + `serde_json`
- [x] `AgentConfig` / `ModelSpec` / `ToolSpec` / `ResourceSpec` serde 往返一致
- [x] 空 `AgentConfig::default()` 合法，所有字段为空 Vec/String
- [x] JSON 缺字段反序列化不报错（`#[serde(default)]`）
- [x] `ResourceSpec` 的 1:N 语义在文档和测试中体现（一个 spec → 多个 nodes 的预留）
