# Phase 3 — Engine 设计

> 父文档：`docs/v1.x/2026-06-26-arfv1-roadmap.md` §Phase 3
> 依赖：Phase 1 (Bus) + Phase 2 (State) — 已完成
> 状态：📝 设计中

## 核心模型

**Agent = 声明式配置骨架**。Agent 声明"我需要什么"——哪些模型、哪些工具、哪些 subagent、哪些 teammate。Agent 不知道 Bus 的存在，不知道 MCP 节点的具体位置，不知道 model node 是否在线。

**State = 运行时状态快照**。Engine 每轮 ReAct 循环后写入 messages 和 tasks。State 不包含业务方法——Engine 负责所有状态转换。

**Engine = 状态机转移引擎**。Engine 拿 Agent 的声明式配置，连上 Bus 后执行资源发现（discovery），将逻辑名解析为 NodeId，构建运行时上下文，然后驱动 ReAct 循环。

```
Agent (配置骨架) ──定义──→ Engine (转移引擎) ──驱动──→ State (状态快照)
```

三者分离：Agent 说 WHAT，Engine 做 HOW，State 存 WHERE。

---

## 依赖关系（修改）

当前 `arf-agent → arf-engine` 反了。修正后：

```
arf-core: Agent trait + AgentConfig + ResourceSpec + core types
    ↑
    ├── arf-state (State, Task) ─── 纯数据结构
    ├── arf-agent ───────────────── 实现 Agent trait
    └── arf-engine ──────────────── depends on: Agent trait (core) + State + Bus
                                     owns: ReAct loop + resource discovery
```

`arf-agent` 不再依赖 `arf-engine`。Engine 依赖 Agent trait（在 core 定义），不依赖具体 Agent 实现。

Cargo.toml 变更：
- `arf-agent/Cargo.toml`：移除 `arf-engine` 依赖，改为 `arf-state`
- `arf-engine/Cargo.toml`：保持不变（`arf-core` + `arf-bus` + `arf-state`）

---

## arf-core 新增：Agent trait + 配置类型

### Agent trait

Engine 通过此 trait 获取 Agent 的能力。定义在 `arf-core`，实现在 `arf-agent`（Phase 4）。

```rust
/// Agent capability provider — trait defined in arf-core,
/// implemented in arf-agent (Phase 4).
pub trait Agent {
    /// System prompt injected at the start of each model call.
    fn system_prompt(&self) -> &str;

    /// Available tools with their permission constraints.
    fn tools(&self) -> &[ToolSpec];

    /// Call the model with conversation history and tool definitions.
    /// Returns either a text response or tool call requests.
    async fn model_call(
        &self,
        messages: &[ModelMessage],
        tools: &[ToolSpec],
    ) -> Result<ModelResponse, ModelError>;

    /// Execute a tool by name with the given arguments.
    /// Engine calls this after receiving tool_calls from model_call.
    async fn execute_tool(
        &self,
        name: &str,
        arguments: serde_json::Value,
    ) -> Result<ToolResult, ToolError>;
}
```

### AgentConfig — 声明式资源需求

Agent 创建时填充，**只对 Engine 暴露**。Agent 不感知 Bus，所有字段使用逻辑名。

```rust
/// Declarative agent configuration — WHAT this agent needs.
/// Only Engine reads this. Agent fills it at creation time.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AgentConfig {
    /// System prompt injected at the start of every model call.
    pub system_prompt: String,

    /// Models in priority order. Engine picks the first one whose
    /// model node is online on the Bus.
    pub models: Vec<ModelSpec>,

    /// Tools this agent may use, each with permission constraints.
    pub tools: Vec<ToolSpec>,

    /// File system paths this agent is allowed to access.
    pub allowed_paths: Vec<String>,

    /// Subagents this agent can delegate tasks to.
    pub subagents: Vec<ResourceSpec>,

    /// Teammates this agent can coordinate with.
    pub teammates: Vec<ResourceSpec>,
}
```

### ModelSpec — 模型声明

```rust
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ModelSpec {
    /// Provider identifier: "deepseek", "openai", "anthropic".
    pub provider: String,
    /// Model name: "deepseek-chat", "gpt-4", etc.
    pub model_name: String,
    /// Whether thinking/reasoning is enabled.
    pub thinking_enabled: bool,
    /// Sampling temperature (0.0–2.0).
    pub temperature: Option<f32>,
    /// Hard limit on output tokens.
    pub max_output_tokens: Option<u32>,
    /// Provider-specific extra parameters.
    pub extra: serde_json::Value,
}
```

### ToolSpec — 工具声明

```rust
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolSpec {
    /// Tool name as registered on the Bus by an MCP node.
    pub name: String,
    /// Permission level for this tool.
    pub permission: ToolPermission,
    /// Optional parameter filter/constraints (e.g., whitelist args).
    pub parameter_filter: Option<serde_json::Value>,
    /// Tool description for the model's function calling.
    pub description: Option<String>,
    /// JSON Schema for the tool's parameters.
    pub parameters: Option<serde_json::Value>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub enum ToolPermission {
    /// Tool runs without asking.
    Allow,
    /// Tool must ask user before running.
    Ask,
    /// Tool is blocked entirely.
    Deny,
}
```

### ResourceSpec — 逻辑资源需求（1:N 映射）

Agent 声明"我需要一个 filesystem"，Engine 在 Bus 上可能发现 3 个 `mcp/filesystem` 节点。全部注册，运行时选第一个在线的。

```rust
/// Declares a logical resource dependency.
/// Engine resolves 1 ResourceSpec → N NodeIds at init time.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ResourceSpec {
    /// Agent-given alias for this resource (e.g., "primary_fs").
    pub name: String,
    /// Expected node_type on the Bus: "mcp", "agent/subagent", "agent/teammate".
    pub node_type: String,
    /// Optional capabilities matcher.
    /// E.g., {"resources": ["tool/read", "tool/write"]} to filter MCP nodes.
    pub capabilities: Option<serde_json::Value>,
}
```

### 运行时类型

`ModelResponse` 和 `ToolResult` 定义在 `arf-core`，Engine 和 Agent trait 共用：

```rust
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ModelResponse {
    /// Message to append to State. Role is "assistant".
    pub message: ModelMessage,
    /// Optional parallel tool calls from the model.
    pub tool_calls: Option<Vec<ToolCallRequest>>,
    /// Token usage if provider reports it.
    pub usage: Option<TokenUsage>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolCallRequest {
    pub id: String,
    pub name: String,
    pub arguments: serde_json::Value,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TokenUsage {
    pub input_tokens: u32,
    pub output_tokens: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolResult {
    pub content: String,
    pub is_error: bool,
}
```

### ModelError / ToolError

```rust
#[derive(Debug)]
pub enum ModelError {
    /// No model node matching the spec is online.
    NoModelAvailable(String),
    /// The model API returned an error.
    ApiError(String),
    /// Timeout waiting for model response.
    Timeout,
}

#[derive(Debug)]
pub enum ToolError {
    /// Tool not found in the resolved manifest.
    ToolNotFound(String),
    /// Tool execution failed.
    ExecutionError(String),
    /// Permission denied for this tool.
    PermissionDenied(String),
}
```

---

## arf-engine 新增：资源发现 + ReAct 循环

### ResolvedManifest — 运行时解析结果

Engine init 后构建，将 AgentConfig 的逻辑名映射为 Bus 上的具体 NodeId。

```rust
// arf-engine
pub struct ResolvedManifest {
    /// Currently selected model (first online match in AgentConfig.models).
    pub active_model: ResolvedModel,
    /// Resolved tools: one ResourceSpec → N NodeIds.
    pub tools: Vec<ResolvedResource>,
    /// Resolved subagents.
    pub subagents: Vec<ResolvedResource>,
    /// Resolved teammates.
    pub teammates: Vec<ResolvedResource>,
}

pub struct ResolvedModel {
    pub spec: ModelSpec,
    pub node_id: NodeId,
}

/// A logical resource resolved to N concrete nodes.
/// Engine selects the first online node at runtime.
pub struct ResolvedResource {
    pub spec: ResourceSpec,
    pub nodes: Vec<NodeId>,
}
```

### Engine init 流程

```
Engine::init(bus, agent_config) → ResolvedManifest
          │
          ├─ 1. 连接 Bus，注册 Engine 节点
          ├─ 2. 发送 discovery 广播（带 ResourceSpec 条件）
          ├─ 3. 收集响应 + 扫描已有 node_online
          ├─ 4. 按 AgentConfig 逐一匹配：
          │      ├─ models → 找第一个在线的 model node
          │      ├─ tools → 收集所有匹配的 mcp node
          │      ├─ subagents → 收集所有匹配的 subagent node
          │      └─ teammates → 收集所有匹配的 teammate node
          └─ 5. 构建 ResolvedManifest，返回
```

### ReAct 循环

```rust
// arf-engine
pub struct Engine {
    bus: Bus,
    manifest: ResolvedManifest,
    state: State,
}

impl Engine {
    /// Initialize: connect to Bus, discover resources, build manifest.
    pub async fn init(bus: Bus, config: AgentConfig) -> Result<Self, EngineError>;

    /// Run one round of the ReAct loop for the given user input.
    /// Returns the final text response.
    pub async fn chat(&mut self, agent: &dyn Agent, input: &str) -> Result<String, EngineError>;

    /// Current session state (for persistence, snapshot, etc.).
    pub fn state(&self) -> &State;
}
```

`chat()` 内部循环：

```
1. state.messages.append(user_message)
2. loop:
   a. agent.model_call(state.messages, agent.tools()) → response
   b. state.messages.append(response.message)
   c. if response.tool_calls:
        for tc in response.tool_calls:
            result = agent.execute_tool(tc.name, tc.arguments)
            state.messages.append(tool_result_message)
        continue  // back to model_call
   d. else:
        break  // text response → done
3. return final_text
```

Resource selection during tool execution:

```rust
impl ResolvedResource {
    /// Find the first online node for this resource.
    /// Called each time Engine needs to route a tool call.
    fn first_online(&self, bus: &Bus) -> Option<&NodeId> {
        self.nodes.iter().find(|nid| bus.is_online(nid))
    }
}
```

---

## 任务拆解

| # | 任务 | 内容 | 产出 |
|---|------|------|------|
| 3.1 | `arf-core` 新增类型 | `Agent` trait, `AgentConfig`, `ModelSpec`, `ToolSpec`, `ResourceSpec`, `ToolPermission`, `ModelResponse`, `ToolCallRequest`, `TokenUsage`, `ToolResult`, `ModelError`, `ToolError` | `crates/arf-core/src/lib.rs` |
| 3.2 | `arf-engine` 脚手架 | `Engine` struct, `ResolvedManifest`, `ResolvedModel`, `ResolvedResource`, `Engine::init()`, `Engine::chat()` | `crates/arf-engine/src/` |
| 3.3 | Engine init 实现 | Bus 连接 + discovery 广播 + 资源解析 + manifest 构建 | `crates/arf-engine/src/init.rs` |
| 3.4 | ReAct 循环实现 | `chat()` 主循环：model_call → parse → tool_exec → loop | `crates/arf-engine/src/react.rs` |
| 3.5 | 依赖关系修正 | `arf-agent/Cargo.toml` 移除 `arf-engine` → 改为 `arf-state` | `crates/arf-agent/Cargo.toml` |
| 3.6 | 单元测试 | Agent trait mock + Engine init + ReAct loop + resource selection | `#[cfg(test)]` |
| 3.7 | 集成测试 | Engine + Bus + State 完整链路 | `crates/arf-engine/tests/` |

---

## 设计决策记录

### Agent trait 为何包含 `model_call` 和 `execute_tool`？

Agent 是配置骨架，但 Engine 仍然需要调用模型和执行工具。完整流程中，`model_call` 通过 Bus 发消息给 ModelAdapter node，`execute_tool` 通过 Bus 发消息给 MCP node。Agent trait 封装这些操作，使 Engine 不关心消息路由细节。

Phase 3 Engine 单元测试可用 mock Agent trait，不依赖真实 Bus/ModelAdapter/MCP。

### async-trait

Rust 原生 async fn in trait 尚不稳定。Phase 3 需要 `#[async_trait]` macro（`async-trait` crate）或使用 `-> Pin<Box<dyn Future<...>>>` 返回类型。在 task spec 中再确定具体方案。

### Engine 不暴露 AgentConfig 的构造

`AgentConfig` 由 Agent 创建（Phase 4），Engine 只读取。构造逻辑不属于 Engine 的职责范围。

## 交付标准

- [ ] `cargo test --workspace` 全部通过
- [ ] `arf-agent` 不再依赖 `arf-engine`
- [ ] `Engine::init()` 能完成 discovery + 资源解析
- [ ] `Engine::chat()` 完成完整 ReAct 循环（含 tool call 往返）
- [ ] 1:N 资源映射：多个同类型 node 在线时选第一个可用的
- [ ] Agent trait 可 mock（用于 Engine 单测）
