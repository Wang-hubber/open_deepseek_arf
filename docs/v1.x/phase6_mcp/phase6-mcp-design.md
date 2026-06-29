# Phase 6 — MCP 设计

> 父文档：`docs/v1.x/2026-06-26-arfv1-roadmap.md`
> 依赖：Phase 1 (Bus) — 已完成
> 状态：📝 设计中

## 定位

MCP (Model Context Protocol) 是 Bus 上的**资源管理节点**，负责工具/技能的注册、发现与执行。MCP 不感知 Agent——只认 Bus 上的消息格式。

```
Engine ──tool_call_set──→ Bus ──tool_call_set──→ MCP Node
                                                    │ 构建 DAG
                                                    │ 拓扑排序
                                                    │ 并发执行
                                                    │ 级联取消
                                                    │
Engine ←──tool_result_set── Bus ←──tool_result_set──┘

Skill 发现流程：
Engine ──use_skill──→ Bus ──use_skill──→ MCP Node
                                            │ SkillIndex.load_body()
Engine ←──skill_loaded── Bus ←──skill_loaded──┘
```

**核心原则：可插拔。** MCP 自身可替换——换一个 MCP 实现，只要消息格式不变，整个系统无感。

## 节点类型

| 类型 | 标识 | 本阶段 |
|------|------|--------|
| `LocalMcpNode` | `node_type: "mcp"`, 静态注册工具/技能 | ✅ |
| `RemoteMcpNode` | `node_type: "mcp"`, 外部 MCP 协议（stdio/SSE） | 🔲 未来 |

**注册与刷新**：静态注册——MCP Node 构造时传入工具和技能列表，启动后广播。无独立 refresh 方法——要刷新资源列表就重启节点。

## 依赖关系

```
arf-core: NodeId, Message, NodeInfo, ModelMessage
    ↑
arf-bus: Bus, NodeHandle, MessageFilter
    ↑
arf-mcp ─── depends on: arf-core + arf-bus + tokio + serde + serde_json
```

不依赖 `arf-state`、不依赖 `arf-agent`、不依赖 `arf-engine`。

---

## 数据结构

### ToolError

```rust
/// Error returned by a tool's execute().
///
/// The executor catches this and packages it into the ToolResultItem.
/// Tool authors don't need to follow any error convention — just return Err.
#[derive(Debug, Clone)]
pub struct ToolError {
    pub message: String,
}

impl std::fmt::Display for ToolError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.message)
    }
}

impl std::error::Error for ToolError {}

impl From<&str> for ToolError {
    fn from(s: &str) -> Self {
        Self { message: s.to_string() }
    }
}

impl From<String> for ToolError {
    fn from(s: String) -> Self {
        Self { message: s }
    }
}
```

### Tool trait

```rust
/// A tool that MCP can execute.
///
/// Implementations are Send + Sync so they can be shared across tokio tasks.
/// Tool authors only need to implement `execute()` — error handling, panic
/// catching, and result packaging are centralized in the executor.
/// Sandboxing and approval are handled by separate Nodes on the Bus — not here.
pub trait Tool: Send + Sync {
    /// Unique tool name: "read_file", "write_file", "search_content".
    fn name(&self) -> &str;

    /// Human-readable description for LLM function calling.
    fn description(&self) -> &str;

    /// JSON Schema for the tool's parameters.
    fn parameters_schema(&self) -> serde_json::Value;

    /// Execute the tool with the given parameters.
    ///
    /// Returns Ok(result) on success, Err(ToolError) on failure.
    /// The executor also wraps this in catch_unwind for panic safety —
    /// tool authors can use plain `unwrap()` / `expect()` without breaking MCP.
    async fn execute(
        &self,
        params: serde_json::Value,
    ) -> Result<serde_json::Value, ToolError>;

    /// Cancel an in-progress execution.
    ///
    /// Called by the executor when:
    /// - A dependency fails (cascade cancel)
    /// - Engine sends a cancellation for this tool_call_set
    ///
    /// Default implementation is a no-op. Tools with long-running or
    /// resource-holding operations should override this to release
    /// resources and set a cancellation flag.
    async fn cancel(&self) {
        // no-op by default
    }
}
```

### ToolCallItem — 单个工具调用

```rust
/// A single tool invocation within a tool_call_set.
///
/// Optional `blocked_by` declares intra-set dependencies:
/// this call must wait for the listed call IDs to complete before executing.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolCallItem {
    /// Unique ID within this tool_call_set (e.g., "call_0", "call_1").
    pub id: String,
    /// Tool name to invoke.
    pub tool: String,
    /// Parameters for the tool call.
    pub params: serde_json::Value,
    /// IDs of calls within the same set that must complete before this one.
    /// Empty = no dependencies, can execute immediately.
    #[serde(default)]
    pub blocked_by: Vec<String>,
}
```

### ToolCallSet — Engine → MCP

```rust
/// A set of 1-N tool calls dispatched together.
///
/// Engine assembles this after receiving a model_response with tool_calls.
/// MCP builds a DAG, topologically sorts, and executes:
/// - Calls without blocked_by: concurrent execution
/// - Calls with blocked_by: serialized by dependency order
/// - Any call fails → cascade cancel all downstream calls
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolCallSet {
    /// The session this tool call set belongs to.
    pub session_id: String,
    /// 1-N tool calls in this set.
    pub calls: Vec<ToolCallItem>,
    /// Per-call timeout in milliseconds. None = no timeout (Engine's choice).
    /// MCP does not impose a default or hidden deadline.
    /// When a call exceeds this timeout, the executor cancels it and
    /// cascades to its dependents.
    #[serde(default)]
    pub timeout_ms: Option<u64>,
}
```

### ToolResultItem — 单个工具结果

```rust
/// Result of a single tool call.
///
/// All error packaging is centralized in the executor — tool authors
/// never construct this struct directly. The executor:
/// 1. Calls tool.execute() inside catch_unwind
/// 2. On Ok(val) → status: "success", result: val
/// 3. On Err(e) → status: "error", error: e.message
/// 4. On panic → status: "error", error: "panic: {message}"
/// 5. On cancel (cascade or timeout) → calls tool.cancel(), status: "cancelled"
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolResultItem {
    /// Matches ToolCallItem.id.
    pub call_id: String,
    /// "success" or "error" or "cancelled".
    pub status: String,
    /// The tool's return value. Null on error/cancelled.
    pub result: serde_json::Value,
    /// Error message populated by executor on error/cancelled.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}
```

### ToolResultSet — MCP → Engine

```rust
/// Aggregated results for a tool_call_set.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolResultSet {
    /// The session this result set belongs to.
    pub session_id: String,
    /// Results, one per call in the original ToolCallSet.
    pub results: Vec<ToolResultItem>,
}
```

---

## Skill 数据模型

沿用 v0.x 格式，每个 skill 是一个目录：

```
skills/{name}/
  skill.yaml   → {name, description, tools_sequence, keywords}
  skill.md     → 领域知识 body (Markdown)
```

### SkillEntry

```rust
/// A skill registered by MCP — metadata only.
///
/// The full body is loaded on demand via use_skill → skill_loaded.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SkillEntry {
    /// Unique skill name.
    pub name: String,
    /// Human-readable description.
    pub description: String,
    /// Trigger keywords for LLM to decide when to use this skill.
    #[serde(default)]
    pub keywords: Vec<String>,
    /// Tool names associated with this skill. MCP that hosts the skill
    /// must also host these tools.
    #[serde(default)]
    pub tools_sequence: Vec<String>,
    /// Path to skill directory (for body loading).
    #[serde(skip)]
    pub source_dir: String,
}
```

### SkillIndex

> SkillIndex 是 MCP 内部组件，不对外暴露。Engine 通过 Bus 消息（`use_skill` → `skill_loaded`）获取 skill body，不直接扫描文件系统。

```rust
/// Scan, index, and retrieve lazy-loaded skills.
///
/// Scans `<root>/skills/` for subdirectories containing skill.yaml + skill.md.
/// MCP-internal — Engine never sees this struct.
pub struct SkillIndex {
    root: PathBuf,
    index: HashMap<String, SkillEntry>,
}
```

方法：
- `scan()` — (重新)构建索引
- `resolve(name) -> Option<&SkillEntry>` — 按名查找
- `load_body(name) -> Option<String>` — 读取 skill.md 内容
- `list_index() -> Vec<&SkillEntry>` — 列出全部

---

## Bus 消息协议

### node_online — MCP 注册

```json
{
  "msg_type": "node_online",
  "from": "mcp/filesystem",
  "to": [],
  "payload": {
    "node_type": "mcp",
    "node_id": "mcp/filesystem",
    "capabilities": {
      "resources": {
        "tools": [
          {"name": "read_file", "description": "Read file contents"},
          {"name": "write_file", "description": "Write content to a file"},
          {"name": "search_content", "description": "Search files for a pattern"}
        ],
        "skills": [
          {
            "name": "react-component",
            "description": "Build React components with TypeScript",
            "keywords": ["react", "component", "tsx"],
            "tools_sequence": ["read_file", "write_file", "search_content"]
          }
        ]
      }
    },
    "online_since": 1719500000000
  }
}
```

### tool_call_set — Engine → MCP

定向消息（`to: ["mcp/filesystem"]`）：

```json
{
  "msg_type": "tool_call_set",
  "from": "engine/session-1",
  "to": ["mcp/filesystem"],
  "payload": {
    "session_id": "session-1",
    "calls": [
      {
        "id": "call_0",
        "tool": "read_file",
        "params": {"path": "/workspace/src/main.rs"}
      },
      {
        "id": "call_1",
        "tool": "search_content",
        "params": {"pattern": "fn main", "path": "/workspace/src"}
      },
      {
        "id": "call_2",
        "tool": "write_file",
        "params": {"path": "/workspace/out.txt", "content": "..."},
        "blocked_by": ["call_0", "call_1"]
      }
    ]
  }
}
```

### tool_result_set — MCP → Engine

定向消息（返回给 Engine）：

```json
{
  "msg_type": "tool_result_set",
  "from": "mcp/filesystem",
  "to": ["engine/session-1"],
  "payload": {
    "session_id": "session-1",
    "results": [
      {
        "call_id": "call_0",
        "status": "success",
        "result": "fn main() {\n    println!(\"Hello\");\n}\n"
      },
      {
        "call_id": "call_1",
        "status": "success",
        "result": {"matches": [{"line": 1, "content": "fn main() {"}]}
      },
      {
        "call_id": "call_2",
        "status": "success",
        "result": {"ok": true, "path": "/workspace/out.txt", "bytes": 42}
      }
    ]
  }
}
```

### 级联取消示例

当 `call_1` 失败时，`call_2`（blocked_by call_0, call_1）被取消：

```json
{
  "msg_type": "tool_result_set",
  "payload": {
    "session_id": "session-1",
    "results": [
      {"call_id": "call_0", "status": "success", "result": "..."},
      {
        "call_id": "call_1",
        "status": "error",
        "result": null,
        "error": "pattern syntax error: unmatched ("
      },
      {
        "call_id": "call_2",
        "status": "cancelled",
        "result": null,
        "error": "cancelled: dependency call_1 failed"
      }
    ]
  }
}
```

### use_skill — Engine → MCP

```json
{
  "msg_type": "use_skill",
  "from": "engine/session-1",
  "to": ["mcp/filesystem"],
  "payload": {
    "name": "react-component"
  }
}
```

### skill_loaded — MCP → Engine

```json
{
  "msg_type": "skill_loaded",
  "from": "mcp/filesystem",
  "to": ["engine/session-1"],
  "payload": {
    "name": "react-component",
    "description": "Build React components with TypeScript",
    "tools_sequence": ["read_file", "write_file", "search_content"],
    "body": "# React Component Skill\n\n## When to use\n..."
  }
}
```

---

## 执行模型

### DAG 构建与执行

```
ToolCallSet.calls
  → 构建邻接表（blocked_by → edges）
    → 检测环（DFS，有环则全部返回 error）
      → 拓扑排序（Kahn 算法）
        → 按层级分批：
            Layer 0: [call_0, call_1]   ← 无依赖，concurrent
            Layer 1: [call_2]           ← 依赖 0,1 都完成
            Layer 2: [call_3]           ← 依赖 2 完成
          → 每层内部并发执行（tokio::spawn）
            → 任一步失败 → 级联取消 downstream
              → 组装 ToolResultSet 返回
```

### 集中式错误处理

每个 tool call 执行时，executor 统一包裹三层保护。Tool 作者不需要遵守任何错误约定——抛异常、返回 Err、或 unwrap panic 都会被捕获并封装为标准的 tool_result message。

```
spawn per call:
  ┌─ tokio::spawn(async {
  │    // Layer 1: catch_unwind — panic safety
  │    let result = catch_unwind(AssertUnwindSafe(|| async {
  │        // Layer 2: Result<_, ToolError> — type-safe errors
  │        tool.execute(params).await
  │    })).await;
  │
  │    // Layer 3: timeout (if ToolCallSet.timeout_ms is set)
  │    // tokio::time::timeout wraps the entire execution
  │
  │    match result {
  │        Ok(Ok(val))            → ToolResultItem { status: "success", result: val }
  │        Ok(Err(tool_err))      → ToolResultItem { status: "error", error: tool_err.message }
  │        Err(panic_payload)     → ToolResultItem { status: "error", error: "panic: {msg}" }
  │    }
  │  })
```

### 级联取消策略

- 当 call X 返回 `status: "error"`，沿 blocking 链标记所有直接/间接依赖者为 `status: "cancelled"`
- 对每个 cancelled call，调用 `tool.cancel()` 通知工具释放资源，然后跳过执行
- 最终 ToolResultSet 包含所有 call 的结果（含 cancelled）

### 超时

- `ToolCallSet.timeout_ms` 由 Engine 设置，MCP 不做默认值、不设兜底
- 超时只由 Engine 的业务需求驱动——Engine 不传就是无超时
- 超时触发时：executor 调用 `tool.cancel()` → 级联取消 dependents → 标记 `status: "cancelled"`

### 取消机制

`Tool::cancel()` 是优雅退出的钩子：
- 默认实现为空（no-op），简单工具不需要关心
- 持有文件句柄、网络连接等资源的工具应覆盖 `cancel()` 设置标志位
- `cancel()` 被调用后，正在运行的 `execute()` 应尽快返回
- 取消不等待——executor 调用 `cancel()` 后 abort tokio task

### 并发模型

- 每个 tool call 在独立的 `tokio::spawn` 任务中执行
- 层内并发数无硬性限制（由 tokio runtime 调度）
- 所有 tool 实现必须是 `Send + Sync`
- 执行期间 executor 持有每个 task 的 `JoinHandle`，用于取消

---

## 首批内置工具

### read_file

| 字段 | 值 |
|------|-----|
| name | `read_file` |
| params | `{"path": "/workspace/src/main.rs"}` |
| result | 文件内容字符串 |
| error | `{"ok": false, "error": "file not found: ..."}` |

### write_file

| 字段 | 值 |
|------|-----|
| name | `write_file` |
| params | `{"path": "/workspace/out.txt", "content": "..."}` |
| result | `{"ok": true, "path": "...", "bytes": 42}` |
| error | `{"ok": false, "error": "permission denied: ..."}` |

### search_content

| 字段 | 值 |
|------|-----|
| name | `search_content` |
| params | `{"pattern": "fn main", "path": "/workspace/src"}` |
| result | `{"matches": [{"line": 1, "content": "...", "file": "..."}]}` |
| error | `{"ok": false, "error": "invalid regex: ..."}` |

> bash 工具暂不纳入本阶段——先验证工具链的三条核心链路（读/写/搜）完整跑通。

---

## 目录结构

```
crates/arf-mcp/
├── Cargo.toml
└── src/
    ├── lib.rs              # pub mod, re-exports
    ├── tool.rs             # Tool trait
    ├── types.rs            # ToolCallItem, ToolCallSet, ToolResultItem, ToolResultSet
    ├── skill.rs            # SkillEntry, SkillIndex
    ├── executor.rs         # DAG builder, cycle detection, topological sort, parallel exec
    ├── node.rs             # LocalMcpNode: Bus lifecycle + listen loop
    ├── tools/
    │   ├── mod.rs          # Built-in tool registry
    │   ├── read_file.rs    # ReadFileTool
    │   ├── write_file.rs   # WriteFileTool
    │   └── search_content.rs # SearchContentTool
    └── tests/
        ├── tool_tests.rs       # Tool trait + individual tools
        ├── skill_tests.rs      # SkillIndex scan/resolve/load
        ├── executor_tests.rs   # DAG build, cycle detect, topo sort, cascade cancel
        ├── node_tests.rs       # LocalMcpNode lifecycle + Bus integration
        └── integration_tests.rs # E2E: node online → tool_call_set → result_set
```

---

## 任务拆解

| # | 任务 | 内容 | 产出 |
|---|------|------|------|
| 6.1 | 脚手架 + 类型定义 | `Cargo.toml`、`types.rs`、`tool.rs`、`lib.rs` | `crates/arf-mcp/` |
| 6.2 | Tool trait + 三个内置工具 | `ReadFileTool`、`WriteFileTool`、`SearchContentTool` | `tool.rs`, `tools/*.rs` |
| 6.3 | SkillIndex | 扫描 skill 目录、resolve、load_body | `skill.rs` |
| 6.4 | DAG 执行器 | 邻接表、环检测、拓扑排序、分层并发、`catch_unwind` + `Result` 集中错误处理、超时、`cancel()` 级联取消 | `executor.rs` |
| 6.5 | LocalMcpNode | Bus 节点生命周期 + listen loop（`tool_call_set` / `use_skill` 消息处理） | `node.rs` |
| 6.6 | Workspace 注册 | 根 `Cargo.toml` 添加 `arf-mcp` | `Cargo.toml` |
| 6.7 | 集成测试 | LocalMcpNode + Bus + mock Engine 消息收发 | `tests/` |

## 交付标准

- [ ] `cargo test --workspace` 全部通过
- [ ] `arf-mcp` 仅依赖 `arf-core` + `arf-bus` + `tokio` + `serde_json`
- [ ] 三个内置工具可完成实际文件读写和内容搜索
- [ ] `LocalMcpNode` 正确广播 `node_online`（含 tools + skills 元数据）
- [ ] `LocalMcpNode` 正确响应 `tool_call_set` → 执行 → `tool_result_set`
- [ ] `LocalMcpNode` 正确响应 `use_skill` → `skill_loaded`
- [ ] DAG 执行器：无依赖并发、有依赖拓扑排序、失败级联取消、`catch_unwind` panic 安全
- [ ] 超时机制：Engine 传入 `timeout_ms` → executor 按 `tokio::time::timeout` 终止
- [ ] `Tool::cancel()` 被调用后才 abort task，保证优雅退出
- [ ] Tool trait 可 mock（用于 Engine 单测）
- [ ] SkillIndex 正确扫描 skill 目录并加载 body，不对外暴露

---

## 与其他 Phase 的关系

| Phase | 如何使用 MCP |
|-------|-------------|
| Phase 5 Engine | 监听 `node_online` 发现 MCP 节点 → AgentConfig 匹配工具/技能 → 发 `tool_call_set` → 收 `tool_result_set` → 注入消息流 |
| Phase 7 集成 | E2E：Engine + ModelAdapter + MCP 完整 ReAct 循环 |
| 未来 RemoteMCP | 同消息格式，只是工具执行转发到外部进程 |
