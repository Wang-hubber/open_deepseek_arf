# ARF AgentConfig — 声明式配置数据模型

> **Phase 3** · 纯数据结构 · `arf-agent` crate
>
> 仅依赖 `serde` + `serde_json`，零 ARF 依赖。Engine (Phase 4) 读取并解析。

---

## 设计哲学

**Agent 不知道 Runtime 的存在。** AgentConfig 是 Agent 的"愿望清单"——它声明"我需要什么"，但不知道这些资源在哪里、是否在线、如何连接。所有字段使用逻辑名，不引用任何 Bus 概念（NodeId、节点类型、连接状态）。

Engine 是"采购员"——它拿这份清单，在 Bus 上找到实际资源，建立连接，然后驱动 ReAct 循环。

```
AgentConfig (WHAT)              Engine (HOW)                State (WHERE)
─────────────                   ──────────                  ──────────
"我需要 deepseek-flash"    →    bus.graph() 查在线节点  →   messages 追加
"我需要 filesystem"        →    node_type 过滤匹配     →   tasks 更新
"我需要 code_reviewer"     →    1 个 spec → N 个 NodeId →
```

**为什么是纯数据？**

- **可序列化** — YAML/JSON 文件即配置，无需编译。一份配置可以给不同的 Engine 实例使用
- **可校验** — 在 Engine 启动前就能检查配置完整性（缺少必需字段、权限冲突等）
- **可传递** — A2A 场景中，父 Agent 可以把子 Agent 的配置通过 Bus 发送出去
- **可测试** — 不依赖 Bus/ModelAdapter/MCP，单元测试直接构造 struct

---

## 概念模型

```
AgentConfig
├── system_prompt: String          # 系统提示词
├── models: Vec<ModelSpec>         # 模型列表，按优先级排列
│   ├── provider + model_name      # 逻辑标识
│   ├── thinking_enabled           # 是否开启思考
│   ├── temperature                # 采样温度（可选）
│   ├── max_output_tokens          # 输出上限（可选）
│   └── extra                      # 供应商专属参数
├── tools: Vec<ToolSpec>           # 工具列表
│   ├── name                      # 工具逻辑名
│   ├── permission                # Allow / Ask / Deny
│   ├── parameter_filter          # 参数约束（可选）
│   ├── description               # 覆盖工具描述（可选）
│   └── parameters                # 覆盖 JSON Schema（可选）
├── allowed_paths: Vec<String>     # 沙箱路径白名单
├── subagents: Vec<ResourceSpec>   # 可委托的 subagent
│   ├── name                      # Agent 起的别名
│   ├── node_type                 # "agent/subagent"
│   └── capabilities              # 能力匹配条件（可选）
└── teammates: Vec<ResourceSpec>   # 可协作的 teammate
    ├── name                      # Agent 起的别名
    ├── node_type                 # "agent/teammate"
    └── capabilities              # 能力匹配条件（可选）
```

---

## API Reference

### AgentConfig

顶层配置 struct。所有字段 `#[serde(default)]`，空对象 `{}` 反序列化为合法配置。

```rust
pub struct AgentConfig {
    pub system_prompt: String,
    pub models: Vec<ModelSpec>,
    pub tools: Vec<ToolSpec>,
    pub allowed_paths: Vec<String>,
    pub subagents: Vec<ResourceSpec>,
    pub teammates: Vec<ResourceSpec>,
}
```

| 方法 | 说明 |
|------|------|
| `AgentConfig::new()` | 创建全空配置 |
| `AgentConfig::default()` | 等价于 `new()` |

**Engine 如何使用：**

| 字段 | Engine 行为 |
|------|-----------|
| `system_prompt` | 每轮 `model_call` 前作为第一条 `system` 消息注入 `State.messages` |
| `models` | 遍历列表，对每个 `ModelSpec` 调用 `bus.graph().nodes` 查找 `node_type == "model"` 且 capabilities 匹配 `provider` + `model_name` 的节点，选第一个在线的 |
| `tools` | 对每个 `ToolSpec`，在 Bus 上查找匹配的 MCP 节点。`permission` 决定 Engine 在 `before_tools` 检查点的行为（Allow 放行 / Ask 等待审批 / Deny 拒绝）。`description` 和 `parameters` 覆盖 MCP 节点注册时的默认值 |
| `allowed_paths` | 注入沙箱，Engine 执行工具前校验路径 |
| `subagents` | 对每个 `ResourceSpec`，查 `bus.graph()` 中 `node_type == "agent/subagent"` 的节点，`capabilities` 做子集匹配。结果 1:N 存入 `ResolvedManifest` |
| `teammates` | 同 `subagents`，匹配 `node_type == "agent/teammate"` |

---

### ModelSpec

```rust
pub struct ModelSpec {
    pub provider: String,              // "deepseek", "openai", "anthropic"
    pub model_name: String,            // "deepseek-flash", "gpt-4o"
    pub thinking_enabled: bool,        // 默认 false
    pub temperature: Option<f32>,      // 不设 = 用 provider 默认值
    pub max_output_tokens: Option<u32>,// 不设 = 不限制
    pub extra: serde_json::Value,      // 供应商专属参数
}
```

**Engine 如何使用：**

1. 从 `models[0]` 开始，在 `bus.graph()` 中查找 `node_type == "model"` 的节点
2. 匹配条件：节点的 `capabilities.provider` == `ModelSpec.provider`，且 `capabilities.models` 列表包含 `model_name`
3. 找到第一个在线节点 → 选为 `active_model`，记录其 NodeId → 后续 `model_call` 都发给这个节点
4. 如果当前 model node 掉线 → Engine 收到 `node_offline` 事件 → 重新走列表找下一个在线的
5. `thinking_enabled` / `temperature` / `max_output_tokens` / `extra` → 透传给 ModelAdapter，由它拼入 API 请求

**`extra` 字段的设计：** 供应商专属参数黑洞。State 只存不读，ModelAdapter 全权管理。比每个供应商加一个 `Option<T>` 更可扩展。

```json
// DeepSeek 示例
{"reasoning_effort": "high"}

// OpenAI 示例
{"response_format": {"type": "json_object"}}
```

---

### ToolSpec

```rust
pub struct ToolSpec {
    pub name: String,                         // 工具逻辑名
    pub permission: ToolPermission,           // Allow / Ask / Deny
    pub parameter_filter: Option<Value>,      // 参数约束
    pub description: Option<String>,          // 覆盖工具描述
    pub parameters: Option<serde_json::Value>,// 覆盖 JSON Schema
}
```

**Engine 如何使用：**

1. 在 ResolvedManifest 中查找 `name` 匹配的已解析工具，路由 `tool_call` 到对应 NodeId
2. `before_tools` 检查点根据 `permission` 做判定：

| 权限 | 行为 |
|------|------|
| `Allow` | 工具直接执行，Engine 不拦截 |
| `Ask` | Engine 暂停，发出 `approval_required` 事件，等待用户决议 |
| `Deny` | Engine 拒绝执行，注入 `PermissionDenied` 错误结果 |

3. `parameter_filter` — 执行前校验工具参数。如 `{"paths": ["/workspace/*"]}` 限制文件工具只能访问 `/workspace` 下文件。`None` 表示不过滤
4. `description` / `parameters` — 若 `Some`，覆盖 MCP 节点注册时的工具描述和 JSON Schema。`None` 使用 MCP 节点的默认值。用于 Agent 级别定制工具呈现给模型的方式

**`description` 覆盖的典型场景：** MCP 节点注册 `read_file` 工具时声明了通用描述。Agent A 只想让模型知道"读取工作区文件"，Agent B 想让模型知道"读取系统日志"。同一个 MCP 工具，不同 Agent 可以给模型不同的描述。

---

### ToolPermission

```rust
pub enum ToolPermission {
    Allow,  // 自动执行
    Ask,    // 需用户批准
    Deny,   // 禁止执行
}
```

Engine 在 `before_tools` checkpoint 检查此权限。`Ask` 会触发 Park/Resume 机制等待用户输入。

---

### ResourceSpec

```rust
pub struct ResourceSpec {
    pub name: String,                      // Agent 起的别名
    pub node_type: String,                 // "mcp" | "agent/subagent" | "agent/teammate"
    pub capabilities: Option<serde_json::Value>, // 能力匹配条件
}
```

**1:N 映射模型：** Agent 声明"我需要 filesystem"（一个 ResourceSpec），Engine 在 Bus 上可能发现 3 个 `mcp/filesystem` 节点同时在线。全部注册到 `ResolvedResource.nodes: Vec<NodeId>`，运行时选第一个在线的。一个掉线自动切换下一个。

**Engine 如何使用：**

1. `Engine::init()` 时，调用 `bus.graph()` 获取当前在线节点快照
2. 对每个 `ResourceSpec`，过滤 `node_info.node_type == spec.node_type`
3. 若 `spec.capabilities` 为 `Some`，做子集匹配——节点的 capabilities 需包含 spec 声明的所有 key/value
4. 所有匹配节点的 `NodeId` 收集到 `ResolvedResource.nodes`
5. 订阅 `node_online` / `node_offline` 事件，动态增删 `nodes` 列表
6. 运行时调用 `first_online()` 选第一个在线的节点

**`capabilities` 匹配示例：**

```
# spec.capabilities:
{"resources": ["tool/read", "tool/write"]}

# node_info.capabilities (匹配 ✓):
{"resources": ["tool/read", "tool/write", "tool/search"], "version": "2.0"}

# node_info.capabilities (不匹配 ✗):
{"resources": ["tool/search"]}
```

`None` 表示不过滤——匹配所有同 `node_type` 的节点。

---

## 常见模式

### 1. YAML 配置文件

```yaml
system_prompt: "You are a helpful assistant."
models:
  - provider: deepseek
    model_name: deepseek-flash
    thinking_enabled: true
    temperature: 0.7
    max_output_tokens: 8192
  - provider: openai
    model_name: gpt-4o
    # thinking_enabled 默认 false

tools:
  - name: read_file
    permission: Allow
    parameter_filter:
      paths: ["/workspace/*"]
  - name: web_search
    permission: Ask
    description: "Search the internet for information"
  - name: run_command
    permission: Deny

allowed_paths:
  - /workspace
  - /tmp

subagents:
  - name: code_reviewer
    node_type: agent/subagent
    capabilities:
      skills: ["code_review", "static_analysis"]

teammates: []
```

```rust
let config: AgentConfig = serde_yaml::from_str(yaml_str)?;
```

### 2. 代码构造（最小配置）

```rust
let config = AgentConfig {
    system_prompt: "You are a calculator.".into(),
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
```

### 3. 空 AgentConfig（无模型、无工具）

```rust
let config = AgentConfig::default();
// system_prompt = "", models = [], tools = [], ...
// 合法但不可用 — Engine::init() 会因为没有模型而返回错误
```

### 4. JSON 部分覆盖

只传需要改的字段，其余保持默认：

```json
{"system_prompt": "hello", "allowed_paths": ["/x"]}
```

反序列化后 `models`、`tools`、`subagents`、`teammates` 均为空。

---

## 与 Phase 4 Engine 的交互

```
Engine::init(bus, agent_config) → Engine
    │
    ├─ 1. 连接 Bus，注册 engine node
    │
    ├─ 2. 资源发现（查 bus.graph()）
    │      ├─ models → 找 model node，选第一个在线的
    │      ├─ tools  → 找 MCP node，匹配工具名
    │      ├─ subagents → 找 subagent node，capabilities 子集匹配
    │      └─ teammates → 找 teammate node，capabilities 子集匹配
    │      结果：1 ResourceSpec → Vec<NodeId>
    │
    ├─ 3. 构建 ResolvedManifest（运行时上下文）
    │      ├─ active_model: ResolvedModel
    │      ├─ tools: Vec<ResolvedResource>
    │      ├─ subagents: Vec<ResolvedResource>
    │      └─ teammates: Vec<ResolvedResource>
    │
    ├─ 4. 订阅 node_online / node_offline 动态更新 manifest
    │
    └─ 5. 开始 ReAct 循环
           system_prompt → 注入 State.messages[0]
           model_call   → 发给 active_model.node_id
           tool_call    → 路由到 tools[n].first_online()
           权限检查     → before_tools checkpoint
```

---

## 错误场景

| 场景 | Engine 行为 |
|------|-----------|
| `models` 为空 | `init()` 返回 `EngineError::NoModelConfigured` |
| 所有 model node 离线 | `init()` 返回 `EngineError::NoModelAvailable` |
| 配置的 tool 无对应 MCP 节点 | 运行时 `execute_tool()` 返回 `ToolError::ToolNotFound` |
| `permission: Deny` 的工具被模型调用 | Engine 拒绝，注入 `ToolError::PermissionDenied` |
| `permission: Ask` 的工具等待超时 | Engine park 超时 → 注入失败结果 → 继续循环 |
| ResourceSpec 匹配不到任何节点 | `ResolvedResource.nodes` 为空，首次 `first_online()` 返回 None → Engine 记录 warning，跳过该资源 |
| JSON 反序列化缺字段 | `#[serde(default)]` 保证不报错，缺失字段取类型默认值 |

---

## 测试覆盖

| 类型 | 测试数 | 关键覆盖 |
|------|--------|---------|
| ModelSpec | 11 | 构造、边界（defaults/skip）、Clone、PartialEq、序列化往返、前向兼容 |
| ToolSpec | 8 | 构造、权限变体、可选字段跳过、Clone、序列化 |
| ToolPermission | 6 | 三变体、Eq、Clone、序列化、未知变体拒绝 |
| ResourceSpec | 9 | 构造、capabilities 跳过、最小JSON、Clone、PartialEq、序列化 |
| AgentConfig | 9 | 构造、Default、全字段、Clone、PartialEq、序列化、空JSON兼容、缺字段兼容 |
| **合计** | **43** | |

`cargo test --workspace` — 229 tests, 0 failed.
