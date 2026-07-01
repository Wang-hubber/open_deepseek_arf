# McpNode 统一架构

> 订正日期：2026-06-29
> 订正原因：LocalMcpNode / RemoteMcpNode 两个 struct 是假区分——真正不同的只有三处，其余全是重复代码。统一为一个 `McpNode`，区分下沉到 backend trait。

## 核心设计

**McpNode 是唯一的 MCP 节点类型。** 本地和远程只是通信方式不同——对 Bus 上其余节点，它们看到一个 MCP，提供一组能力。

```
               Bus
                │
    ┌───────────┴────────────┐
    │       McpNode          │  ← 唯一类型
    │                        │
    │  discovery: DiscoveryBackend   ← trait: 扫描 vs HTTP
    │  runtime:   RuntimeModule      ← trait: 本地 vs HTTP (已有)
    │                        │
    └────────────────────────┘
```

两个后端 trait：

```
DiscoveryBackend              RuntimeModule (已有)
  ├── FsDiscovery               ├── LocalRuntime
  └── HttpDiscovery             └── RemoteRuntime (新)
```

**三种构造，同一类型**：

```rust
// 纯本地 — 文件扫描
McpNode::local("filesystem", root_dir)

// 纯远程 — HTTP 代理 (CodeTidy 等)
McpNode::remote("codetidy", RemoteConfig { url, ... })

// 全功能远程 — 另一个 ARF 节点（HTTP discovery 拿到 tools + skills）
// 同一构造函数，capabilities 由远端决定
McpNode::remote("peer", RemoteConfig { url, ... })
```

**Engine 视角**：`node_online.capabilities` 结构统一，只是填充内容不同。

```json
// 纯本地: FsDiscovery → 扫描 tools/ + skills/
{"tools":[{...}], "skills":[{...}], "runtime":"local"}

// 纯远程: HttpDiscovery → tools/list
{"tools":[{...}], "skills":[], "runtime":"remote"}
// 如果远端是另一个 ARF 节点，skills 非空
{"tools":[{...}], "skills":[{...}], "runtime":"remote"}
```

---

## DiscoveryBackend trait

```rust
/// Resource discovery backend — bound at McpNode construction.
///
/// FsDiscovery: scans {root}/tools/*/tool.toml + {root}/skills/*/SKILL.md
///   → ScriptTool registry + SkillIndex → tools/skills/resource loading
/// HttpDiscovery: HTTP tools/list → RemoteToolDef registry
///   → tools only (skills depend on remote server capabilities)
#[async_trait]
pub trait DiscoveryBackend: Send + Sync {
    /// Tool metadata for node_online broadcast.
    fn list_tools(&self) -> &[ToolInfo];

    /// Tool instance map for execution.
    fn tool_map(&self) -> &HashMap<String, Arc<dyn Tool>>;

    /// Resolve a single tool by name.
    fn resolve_tool(&self, name: &str) -> Option<Arc<dyn Tool>>;

    // ── Skill methods (same signatures as SkillIndex) ──────────

    fn resolve_skill(&self, name: &str) -> Option<&SkillEntry>;
    fn list_skills(&self) -> Vec<&SkillEntry>;
    fn load_skill_body(&self, name: &str) -> Option<String>;
    fn load_skill_resources(&self, name: &str) -> Option<SkillResources>;
    fn load_resource_file(&self, name: &str, path: &str) -> Result<LoadedResource, String>;
    fn load_tool_config(&self, skill: &str, tool: &str) -> Option<ToolConfig>;

    async fn run_skill_tool(&self, skill: &str, tool: &str, params: Value) -> Result<Value, String>;
}

/// Implementations provide defaults for all skill methods → return None/empty.
/// FsDiscovery overrides them to delegate to SkillIndex.
/// HttpDiscovery uses the defaults (no skills).
```

**关键**：所有 skill 方法都有默认实现返回 `None` / 空——`HttpDiscovery` 不需要覆盖任何东西。`FsDiscovery` 覆盖全部方法委托给 `SkillIndex`。

---

## McpNode — 唯一节点

```rust
pub struct McpNode {
    pub namespace: String,
    pub node_id: NodeId,
    discovery: Box<dyn DiscoveryBackend>,
    runtime: Box<dyn RuntimeModule>,
    handle: Mutex<Option<arf_bus::NodeHandle>>,
}

impl McpNode {
    // ── 构造 ─────────────────────────────────────────────────

    /// 本地 MCP — 文件扫描 + LocalRuntime
    pub fn local(namespace: impl Into<String>, root: PathBuf) -> Result<Arc<Self>, McpError> {
        let backend = FsDiscovery::scan(root)?;
        Ok(Arc::new(Self {
            node_id: NodeId::new(&format!("mcp/{namespace}")),
            namespace: namespace.into(),
            discovery: Box::new(backend),
            runtime: Box::new(LocalRuntime),
            handle: Mutex::new(None),
        }))
    }

    /// 远程 MCP — HTTP discovery + RemoteRuntime
    pub async fn remote(namespace: impl Into<String>, config: RemoteConfig) -> Result<Arc<Self>, McpError> {
        let backend = HttpDiscovery::connect(config).await?;
        Ok(Arc::new(Self {
            node_id: NodeId::new(&format!("mcp/{namespace}")),
            namespace: namespace.into(),
            discovery: Box::new(backend),
            runtime: Box::new(RemoteRuntime::new(config)),
            handle: Mutex::new(None),
        }))
    }

    /// 本地 MCP + 自定义 RuntimeModule（如 SandboxRuntime）
    pub fn local_with_runtime(
        namespace: impl Into<String>,
        root: PathBuf,
        runtime: Box<dyn RuntimeModule>,
    ) -> Result<Arc<Self>, McpError> {
        let backend = FsDiscovery::scan(root)?;
        Ok(Arc::new(Self {
            node_id: NodeId::new(&format!("mcp/{namespace}")),
            namespace: namespace.into(),
            discovery: Box::new(backend),
            runtime,
            handle: Mutex::new(None),
        }))
    }

    // ── 生命周期 ─────────────────────────────────────────────

    pub async fn connect(self: &Arc<Self>, bus: &Bus) -> Result<(), McpError> { ... }
    // message_loop + dispatch — 同当前实现，无变化
}
```

**dispatch 无变化**——`tool_call_set` → `runtime.execute(tools)`，skill 消息 → `discovery.load_skill_body()` 等。`HttpDiscovery` 的 skill 方法默认返回 None → dispatch 自然返回 `skill_error`。

---

## 后端实现

### FsDiscovery（原 DiscoveryModule）

```rust
pub struct FsDiscovery {
    tools: HashMap<String, Arc<dyn Tool>>,
    tool_info: Vec<ToolInfo>,
    skill_index: SkillIndex,
}

impl DiscoveryBackend for FsDiscovery {
    // 所有方法直接委托给 self.tools / self.skill_index
}
```

### HttpDiscovery（原 RemoteMcpNode 的 discover 逻辑）

```rust
pub struct HttpDiscovery {
    tool_info: Vec<ToolInfo>,
    known_tools: HashMap<String, Arc<dyn Tool>>,  // wrapped RemoteToolDef
}

impl HttpDiscovery {
    async fn connect(config: RemoteConfig) -> Result<Self, McpError> {
        // HTTP initialize → tools/list → build tool_info + known_tools
        // 将每个 RemoteToolDef 包装为 HttpProxyTool（实现 Tool trait）
    }
}
```

**HttpProxyTool**：把一个远端 tool 包装成 `Tool` trait。`execute()` = HTTP `tools/call` 请求，返回值提取 text content。

```rust
struct HttpProxyTool {
    name: String,
    description: String,
    schema: Value,
    config: RemoteConfig, // clone of http client config
}

impl Tool for HttpProxyTool {
    fn execute(&self, params: Value) -> Result<Value, ToolError> {
        // HTTP POST tools/call → parse SSE/JSON → extract text
    }
}
```

### RemoteRuntime

```rust
struct RemoteRuntime {
    // 空 struct — execute() 的默认实现委托 executor::execute()
    // run_single() 的默认实现调用 tool.execute()
    // HttpProxyTool::execute() 已包含 HTTP 逻辑
}

impl RuntimeModule for RemoteRuntime {
    fn capabilities(&self) -> Value {
        serde_json::json!({"runtime": "remote"})
    }
}
```

**关键**：`RemoteRuntime` 不重写 `execute()`——用默认实现（委托 executor）。executor 调度时调用 `HttpProxyTool::execute()`，其中包含 HTTP 请求逻辑。这样远程工具也能享受 DAG 拓扑排序和并发执行。

---

## 对现有代码的影响

| 文件 | 变更 |
|------|------|
| `discovery.rs` | DiscoveryModule → FsDiscovery + impl DiscoveryBackend |
| `remote.rs` | 拆分：HttpDiscovery + HttpProxyTool + RemoteRuntime |
| `node.rs` | LocalMcpNode → McpNode（合并 struct + connect + dispatch） |
| `runtime.rs` | 新增 RemoteRuntime（3 行） |
| `error.rs` | 不变 |
| 测试文件 | node_tests + codetidy_live 适配新 API |

**净增约 -30 行**（删 200+ 行重复代码，加 170 行 trait + 实现）。

---

## 迁移对照

```rust
// 旧 → 新
LocalMcpNode::new("ns", root_dir)          → McpNode::local("ns", root_dir)
LocalMcpNode::with_runtime("ns", root, rt) → McpNode::local_with_runtime("ns", root, rt)
RemoteMcpNode::new("ns", config)           → McpNode::remote("ns", config).await
```
