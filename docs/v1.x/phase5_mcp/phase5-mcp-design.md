# Phase 5 — MCP 设计

> 父文档：`docs/v1.x/2026-06-26-arfv1-roadmap.md`
> 依赖：Phase 1 (Bus) — 已完成
> 状态：📝 设计中

## 定位

**一个 MCP 实例 = 一个 namespace = Bus 上一个节点。** Engine 只和 `mcp/{namespace}` 通讯，声明请求类型；MCP 内部管理 discovery/runtime 子模块，按 `msg_type` 分发。

```
Bus
 │
 ├── mcp/filesystem   (namespace="filesystem")
 │       │
 │       ├── [内部] discovery → 扫描 tools/skills
 │       └── [内部] runtime   → 执行 tool_call_set
 │
 ├── mcp/network      (namespace="network")
 │       │
 │       ├── [内部] discovery
 │       └── [内部] runtime
 │
 └── mcp/sandbox      (namespace="sandbox")
         ├── [内部] discovery
         └── [内部] runtime (sandboxed)
```

Engine 视角——**一个 MCP 节点，一个入口**：

```
Engine
  │
  │ 看到 Bus 上有两个 MCP 节点：
  │   mcp/filesystem  → node_online{tools: [{read_file, ...}, {write_file, ...}], skills: [...]}
  │   mcp/network     → node_online{tools: [{read_file, ...}, {fetch_url, ...}], skills: [...]}
  │
  │ 需要读文件 → tool_call_set 发给 mcp/filesystem（声明：我要执行工具）
  │ 需要发请求 → tool_call_set 发给 mcp/network
  │ 需要 skill   → use_skill 发给 mcp/filesystem（声明：我要加载资源）
  │
  │ 两个 read_file 分属不同 namespace，不冲突
```

**MCP 内部消息分发**：

```
Engine 发消息给 mcp/{namespace}
  │
  ▼
McpNode (Bus NodeHandle)
  │
  │ 按 msg_type 分发：
  │
  ├── msg_type = tool_call_set        → [internal] runtime 处理
  ├── msg_type = use_skill            → [internal] discovery 处理
  └── msg_type = load_skill_resource  → [internal] discovery 处理
```

## 节点结构

MCP 对外是**单一 Bus 节点**，内部组合 discovery 和 runtime 两个模块：

```rust
/// An MCP node = one Bus node = one namespace.
///
/// Engine talks ONLY to this node. Discovery and runtime are internal
/// modules — Engine never sees them.
pub struct McpNode {
    /// Namespace identifier (e.g. "filesystem", "network").
    pub namespace: String,
    /// Bus-facing NodeId: `mcp/{namespace}`.
    pub node_id: NodeId,
    /// Internal: resource scanning, indexing, L2/L3 queries.
    discovery: DiscoveryModule,
    /// Internal: DAG execution, tool lifecycle, future sandbox entry.
    runtime: RuntimeModule,
}
```

| | 对 Engine 可见 | NodeId |
|---|---|---|
| `McpNode` | ✅ 唯一入口 | `mcp/{namespace}` |
| `DiscoveryModule` | ❌ 内部 | — |
| `RuntimeModule` | ❌ 内部 | — |

**namespace 约定**：仅含小写字母、数字和连字符（如 `filesystem`、`code-review`）。MCP 构造时内部去重——同一 namespace 内 tool/skill name 冲突直接 panic（开发期错误）。跨 namespace 同名无影响。

**Engine 路由逻辑**（极简）：
1. 监听 `node_online` → 看到 `mcp/filesystem`、`mcp/network` 等节点
2. 所有与 MCP 的通讯都发给 `mcp/{namespace}` 这一个 NodeId
3. MCP 内部按 `msg_type` 分发给 discovery 或 runtime 模块

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
/// Bidirectional dependency lock (same pattern as task State):
///   blocked_by — who blocks me (I depend on them)
///   blocking  — who I block (they depend on me)
///
/// Engine sets the dependencies; executor derives the reverse edges
/// during DAG construction for efficient bidirectional traversal.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolCallItem {
    /// Unique ID within this tool_call_set (e.g., "call_0", "call_1").
    pub id: String,
    /// Tool name to invoke.
    pub tool: String,
    /// Parameters for the tool call.
    pub params: serde_json::Value,
    /// IDs of calls that must complete before this one.
    /// Empty = no dependencies, can execute immediately.
    #[serde(default)]
    pub blocked_by: Vec<String>,
    /// IDs of calls that depend on this one.
    /// Empty = nothing waits for this call.
    #[serde(default)]
    pub blocking: Vec<String>,
}
```

### ToolCallSet — Engine → MCP

```rust
/// A set of 1-N tool calls dispatched together.
///
/// Engine assembles this after receiving a model_response with tool_calls.
/// MCP builds a DAG from the bidirectional lock (blocked_by / blocking),
/// topologically sorts, and executes:
/// - Calls without dependencies: concurrent execution
/// - Calls with blocked_by: serialized by dependency order
/// - Any call fails → cascade cancel along blocking (forward)
/// - Any call cancelled by upstream → cascade cancel along blocking (forward)
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

**Skill 是纯数据——"给 AI 的工作手册"。** 不包含可执行逻辑，不自带 Tool trait。LLM 读取 body 后自己决定调什么工具、用什么资源。MCP 只负责存储、索引和按需出货。

### 目录结构

每个 skill 是标准化的文件夹，`SKILL.md` 为唯一入口：

```
skills/{name}/           # name 为 kebab-case（如 react-component）
├── SKILL.md             # (必选) YAML frontmatter + Markdown body
├── scripts/             # (可选) 可执行脚本
├── references/          # (可选) 参考文档
└── assets/              # (可选) 静态资源（模板、图片等）
```

### SKILL.md 格式

```markdown
---
name: react-component
description: >
  Use when asked to build React components with TypeScript.
  Keywords: react, component, tsx, frontend.
compatibility: node>=18
---

# React Component Skill

## Prerequisites
- Ensure tsconfig.json has strict mode enabled.

## Main Flow
1. Read existing component files to understand project conventions.
2. Run `scripts/generate-component.py` to scaffold the component.
...
```

YAML frontmatter 定义：
- **`name`** (必填)：唯一标识，kebab-case，仅含小写字母、数字和连字符
- **`description`** (必填)：含触发短语、使用时机和关键词，LLM 据此判断是否加载
- **`compatibility`** (可选)：运行环境或依赖声明

### 渐进式披露（L1 → L2 → L3）

| 层级 | 触发时机 | 内容 | 暴露方式 |
|------|---------|------|---------|
| L1 元数据 | Agent 启动 | `{name, description}` | `node_online` 广播 |
| L2 说明文档 | LLM 决定使用此 Skill | body (SKILL.md 全文) + `resources` (文件清单) | `use_skill` → `skill_loaded` |
| L3 资源文件 | LLM 需要具体脚本/模板 | 单个文件内容 | `load_skill_resource` → `skill_resource_loaded` |

### SkillEntry

> 纯数据 struct，无 trait，无 execute。

```rust
/// A skill registered by MCP — L1 metadata only.
///
/// The full body and resources are loaded on demand via
/// use_skill (L2) and load_skill_resource (L3).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SkillEntry {
    /// Unique skill name (kebab-case). Parsed from SKILL.md frontmatter.
    pub name: String,
    /// Human-readable description with trigger phrases and keywords.
    /// LLM uses this to decide whether to load the skill.
    pub description: String,
    /// Optional compatibility constraint (e.g. "node>=18").
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub compatibility: Option<String>,
    /// Path to skill directory (for body/resource loading).
    /// Not serialized — MCP-internal.
    #[serde(skip)]
    pub source_dir: String,
}

/// File manifest for a skill's resource directories.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SkillResources {
    /// Files under scripts/ (e.g. ["generate-component.py"]).
    #[serde(default)]
    pub scripts: Vec<String>,
    /// Files under references/ (e.g. ["api-guide.md"]).
    #[serde(default)]
    pub references: Vec<String>,
    /// Files under assets/ (e.g. ["template.tsx"]).
    #[serde(default)]
    pub assets: Vec<String>,
}
```

### SkillIndex

> MCP 内部组件，不对外暴露。Engine 通过 Bus 消息获取 skill 内容，不直接扫描文件系统。

```rust
/// Scan, index, and retrieve lazy-loaded skills.
///
/// Scans `<root>/skills/*/SKILL.md`, parses YAML frontmatter for L1 metadata.
/// Body and resources loaded on demand. MCP-internal — Engine never sees this.
pub struct SkillIndex {
    root: PathBuf,
    entries: HashMap<String, SkillEntry>,
}
```

方法：
- `scan()` — 扫描 `skills/*/SKILL.md`，解析 YAML frontmatter 构建 L1 索引；同时遍历 `scripts/`、`references/`、`assets/` 构建 `SkillResources`
- `resolve(name) -> Option<&SkillEntry>` — 按名查找
- `load_body(name) -> Option<String>` — 读取 `SKILL.md` 全文（L2）
- `load_resources(name) -> SkillResources` — 列出三级目录文件清单（L2 附带）
- `load_resource_file(name, resource_path) -> Option<String>` — 读取具体资源文件内容（L3）
- `list_index() -> Vec<&SkillEntry>` — 列出全部 L1 元数据

### MCP 自检

`scan()` 时交叉校验：
- `SKILL.md` 不存在 → 跳过该目录（非 skill）
- `name` 不符合 kebab-case → warning 日志，仍注册
- body 中引用的 `scripts/`、`references/`、`assets/` 下的文件缺失 → warning 日志，不阻断
- 不强制资源完整性——Skill 可以只有 `SKILL.md`，无子目录

---

## Bus 消息协议

### node_online — MCP 节点注册

MCP 节点上线时广播一个 `node_online`，携带本 namespace 的全部能力——tools（含描述，供 LLM）+ skills（L1 元数据）。Engine 据此构建 system prompt 并路由后续请求。

```json
{
  "msg_type": "node_online",
  "from": "mcp/filesystem",
  "to": [],
  "payload": {
    "node_type": "mcp",
    "node_id": "mcp/filesystem",
    "namespace": "filesystem",
    "capabilities": {
      "tools": [
        {"name": "read_file", "description": "Read file contents"},
        {"name": "write_file", "description": "Write content to a file"},
        {"name": "search_content", "description": "Search files for a pattern"}
      ],
      "skills": [
        {
          "name": "react-component",
          "description": "Use when asked to build React components with TypeScript. Keywords: react, component, tsx, frontend."
        }
      ]
    },
    "online_since": 1719500000000
  }
}
```

> **Engine 发现逻辑（极简）**：
> 1. 收到 `node_online` → 看到 `mcp/filesystem` 及其全部能力
> 2. 工具描述注入 system prompt（`filesystem/read_file`）
> 3. 所有后续请求都发给 `mcp/filesystem`：`tool_call_set`、`use_skill`、`load_skill_resource`
> 4. MCP 内部按 `msg_type` 分发给 discovery 或 runtime 模块——Engine 不需要知道细节

### tool_call_set — Engine → MCP

Engine 按 tool 的 namespace 前缀路由到对应 MCP 节点，MCP 内部分发给 runtime 模块执行。

```json
{
  "msg_type": "tool_call_set",
  "from": "engine/session-1",
  "to": ["mcp/filesystem"],
  "payload": {
    "namespace": "filesystem",
    "session_id": "session-1",
    "calls": [
      {
        "id": "call_0",
        "tool": "read_file",
        "params": {"path": "/workspace/src/main.rs"},
        "blocking": ["call_2"]
      },
      {
        "id": "call_1",
        "tool": "search_content",
        "params": {"pattern": "fn main", "path": "/workspace/src"},
        "blocking": ["call_2"]
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

当 `call_1` 失败时，executor 沿 `blocking` 正向遍历：call_1.blocking → call_2 → cancelled。同时沿 `blocked_by` 回查可确认 call_2 的所有上游状态。

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

### 逆向取消示例

当 `call_0` 被 Engine 直接取消（非执行失败，如用户中断），沿 `blocking` 正向级联：

```
Engine 发 cancel 消息 → executor 标记 call_0 = cancelled
  → call_0.blocking = ["call_2"]
    → call_2 沿 blocked_by 回查：call_1 仍在跑，但 call_0 已取消
      → call_2 仍有一组上游未全部成功 → call_2 = cancelled
        → call_2.blocking = ["call_3"] → call_3 = cancelled
```

### use_skill — Engine → MCP (L2)

```json
{
  "msg_type": "use_skill",
  "from": "engine/session-1",
  "to": ["mcp/filesystem"],
  "payload": {
    "namespace": "filesystem",
    "name": "react-component"
  }
}
```

### skill_loaded — MCP → Engine (L2)

返回 `SKILL.md` 全文 + 资源文件清单。Engine 将 body 注入 LLM 上下文，`resources` 供 LLM 按需请求 L3。

```json
{
  "msg_type": "skill_loaded",
  "from": "mcp/filesystem",
  "to": ["engine/session-1"],
  "payload": {
    "namespace": "filesystem",
    "name": "react-component",
    "description": "Use when asked to build React components with TypeScript...",
    "body": "---\nname: react-component\ndescription: >\n  Use when asked to build React components...\n---\n\n# React Component Skill\n\n## Prerequisites\n...\n## Main Flow\n1. Run `scripts/generate-component.py` ...\n",
    "resources": {
      "scripts": ["generate-component.py"],
      "references": ["design-system.md"],
      "assets": ["component-template.tsx"]
    }
  }
}
```

### load_skill_resource — Engine → MCP (L3)

```json
{
  "msg_type": "load_skill_resource",
  "from": "engine/session-1",
  "to": ["mcp/filesystem"],
  "payload": {
    "namespace": "filesystem",
    "skill_name": "react-component",
    "resource_path": "scripts/generate-component.py"
  }
}
```

### skill_resource_loaded — MCP → Engine (L3)

```json
{
  "msg_type": "skill_resource_loaded",
  "from": "mcp/filesystem",
  "to": ["engine/session-1"],
  "payload": {
    "namespace": "filesystem",
    "skill_name": "react-component",
    "resource_path": "scripts/generate-component.py",
    "content": "#!/usr/bin/env python3\n\nimport sys\n..."
  }
}
```

MCP 安全约束：`resource_path` 必须在 `{skill_dir}/scripts/`、`references/` 或 `assets/` 下，路径穿越（`../`）和绝对路径拒绝返回 error。

---

## 执行模型

### DAG 构建与执行

```
ToolCallSet.calls
  → 构建双向链：
       blocked_by → edges（正向：谁阻塞我 — 入度）
       blocking   → reverse edges（逆向：我阻塞谁 — 出度）
       Engine 同时提供 blocked_by + blocking，executor 交叉验证：
         A.blocking 包含 B ⇔ B.blocked_by 包含 A
         不一致 → 全部返回 error（数据错误，不静默修复）
    → 检测环（DFS，有环则全部返回 error）
      → 拓扑排序（Kahn 算法，基于 blocked_by 入度）
        → 按层级分批：
            Layer 0: [call_0, call_1]   ← 入度 = 0，concurrent
            Layer 1: [call_2]           ← 依赖 0,1 都完成
            Layer 2: [call_3]           ← 依赖 2 完成
          → 每层内部并发执行（tokio::spawn）
            → 任一步失败 → 沿 blocking 正向级联取消
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

双向锁支持正向和逆向遍历，与 Phase 2 Task 状态的双向锁模式一致：

```
              blocked_by (I depend on them)
       call_0 ──────────────────→ call_2 ──→ call_3
       call_1 ──────────────────→ call_2
              blocking (they depend on me)
       call_0 ←────────────────── call_2 ←── call_3
       call_1 ←────────────────── call_2
```

**正向取消（沿 blocking）**：call X 执行失败或超时 → 沿 `blocking` 链标记所有直接/间接下游为 `cancelled`
**逆向查找（沿 blocked_by）**：需要知道"call Y 被谁阻塞"时，沿 `blocked_by` 回查上游

- 当 call X 返回 `status: "error"` 或超时 → 沿 `blocking` 正向遍历，标记所有下游为 `cancelled`
- 对每个 cancelled call，调用 `tool.cancel()` 通知工具释放资源，跳过执行
- 如果 call X 的所有上游（`blocked_by`）中任一失败或取消，call X 也被级联取消
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
    ├── node.rs             # McpNode: Bus lifecycle + msg_type dispatch → internal modules
    ├── discovery.rs        # DiscoveryModule: tools/skills scanning, indexing, L2/L3 handling
    ├── runtime.rs          # RuntimeModule: tool_call_set → executor dispatch (🔲 future sandbox)
    ├── tools/
    │   ├── mod.rs          # Built-in tool registry
    │   ├── read_file.rs    # ReadFileTool
    │   ├── write_file.rs   # WriteFileTool
    │   └── search_content.rs # SearchContentTool
    └── tests/
        ├── tool_tests.rs       # Tool trait + individual tools
        ├── skill_tests.rs      # SkillIndex scan/resolve/load
        ├── executor_tests.rs   # DAG build, cycle detect, topo sort, cascade cancel
        ├── node_tests.rs          # McpNode: msg_type dispatch + internal routing
        ├── discovery_tests.rs    # DiscoveryModule: scan + L2/L3
        ├── runtime_tests.rs      # RuntimeModule: tool execution + sandbox boundary
        └── integration_tests.rs # E2E: node online → tool_call_set → result_set
```

---

## 任务拆解

| # | 任务 | 内容 | 产出 |
|---|------|------|------|
| 5.1 | 脚手架 + 类型定义 | `Cargo.toml`、`types.rs`、`tool.rs`、`lib.rs` | `crates/arf-mcp/` |
| 5.2 | Tool trait + 三个内置工具 | `ReadFileTool`、`WriteFileTool`、`SearchContentTool` | `tool.rs`, `tools/*.rs` |
| 5.3 | SkillIndex | 扫描 `skills/*/SKILL.md` YAML frontmatter → L1 索引、`load_body` (L2)、`load_resource_file` (L3)、自检 | `skill.rs` |
| 5.4 | DAG 执行器 | 邻接表、环检测、拓扑排序、分层并发、`catch_unwind` + `Result` 集中错误处理、超时、`cancel()` 级联取消 | `executor.rs` |
| 5.5 | McpNode | Bus 生命周期 + 内部 msg_type 分发（tool_call_set → runtime, use_skill/load_skill_resource → discovery） | `node.rs` |
| 5.6 | DiscoveryModule | tools/skills 扫描索引、`node_online` 广播、L2/L3 查询处理 | `discovery.rs` |
| 5.7 | RuntimeModule | `tool_call_set` 接收 → executor 调度 → `tool_result_set` 返回（🔲 future sandbox） | `runtime.rs` |
| 5.8 | Workspace 注册 | 根 `Cargo.toml` 添加 `arf-mcp` | `Cargo.toml` |
| 5.9 | 集成测试 | McpNode + 多 namespace 隔离 + Bus + mock Engine | `tests/` |

## 交付标准

- [ ] `cargo test --workspace` 全部通过
- [ ] `arf-mcp` 仅依赖 `arf-core` + `arf-bus` + `tokio` + `serde_json`
- [ ] 三个内置工具可完成实际文件读写和内容搜索
- [ ] `McpNode` 正确广播一条 `node_online`（含全部 tools 描述 + skills L1 + namespace）
- [ ] `McpNode` 内部按 `msg_type` 正确分发：`tool_call_set` → runtime，`use_skill`/`load_skill_resource` → discovery
- [ ] `McpNode` 正确响应 `tool_call_set` → executor 调度 → `tool_result_set`
- [ ] `McpNode` 正确响应 `use_skill` → `skill_loaded` (L2: body + resources)
- [ ] `McpNode` 正确响应 `load_skill_resource` → `skill_resource_loaded` (L3: 单文件)
- [ ] 内部去重：同一 namespace 内 tool/skill name 冲突 → panic
- [ ] 跨 namespace 重名工具不冲突（`filesystem/read_file` ≠ `network/read_file`），Engine 只需按 NodeId 路由
- [ ] RuntimeModule 是未来 sandbox 的清晰接入点（execute 逻辑集中在此）
- [ ] L3 资源读取有路径安全校验（拒绝 `../` 和绝对路径）
- [ ] DAG 执行器：无依赖并发、有依赖拓扑排序、失败级联取消、`catch_unwind` panic 安全
- [ ] 超时机制：Engine 传入 `timeout_ms` → executor 按 `tokio::time::timeout` 终止
- [ ] `Tool::cancel()` 被调用后才 abort task，保证优雅退出
- [ ] Tool trait 可 mock（用于 Engine 单测）
- [ ] SkillIndex 正确扫描 `SKILL.md` YAML frontmatter → L1 索引，不对外暴露
- [ ] MCP 自检：kebab-case 校验、资源文件存在性检验（warning 不阻断）
- [ ] Discovery 和 Runtime 可独立启动、独立替换（不同 Bus NodeHandle）

---

## 与其他 Phase 的关系

| Phase | 如何使用 MCP |
|-------|-------------|
| Phase 6 Engine | 监听 `node_online` → AgentConfig 匹配 → 所有 MCP 请求发 `mcp/{namespace}` → MCP 内部按 msg_type 分发 |
| Phase 7 集成 | E2E：Engine + ModelAdapter + MCP 完整 ReAct 循环 |
| 未来 RemoteMCP | 同消息格式，只是工具执行转发到外部进程 |
