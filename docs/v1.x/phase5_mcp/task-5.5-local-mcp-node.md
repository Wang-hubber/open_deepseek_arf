# 任务 5.5：LocalMcpNode

> Phase 5 — MCP 第五项任务
> 父文档：`docs/v1.x/phase5_mcp/phase5-mcp-design.md`
> 依赖：Task 5.6 (DiscoveryModule), Task 5.7 (RuntimeModule), Phase 1 (Bus)

## 设计思路

`LocalMcpNode` 是本地 MCP 的集成点——把 DiscoveryModule + RuntimeModule + Bus 接在一起。构造时扫描文件系统，`connect()` 时上线 Bus 并广播 `node_online`，内部消息循环按 `msg_type` 分派请求。

**Engine 如何发现 MCP 节点**——两步走，覆盖全部时序：

```
Engine::start(bus)
  1. graph = bus.graph()    // 一次性主动感知当前所有在线 MCP
  2. subscribe()             // 订阅后续 node_online / node_offline
```

Step 1 覆盖"Engine 启动晚于 MCP"的场景——engine 上线时 MCP 可能已经在线很久了。Step 2 覆盖"Engine 运行期间 MCP 动态增删"的场景。`bus.graph()` 返回 `BusGraph { nodes }`，每个 node 的 `NodeInfo.capabilities` 包含 tools + skills 元数据。零等待。

**消息分派**：

| msg_type | → 组件 | → 响应 |
|----------|--------|--------|
| `tool_call_set` | RuntimeModule.execute() | `tool_result_set` |
| `use_skill` | DiscoveryModule | `skill_loaded` |
| `load_skill_resource` | DiscoveryModule | `skill_resource_loaded` |
| `run_skill_script` | DiscoveryModule.run_skill_tool() | `skill_script_result` |

**所有权模型**：`connect(self: &Arc<Self>, bus)` 需要 `Arc`——消息循环在 `tokio::spawn` 中持有 Arc 引用以保持节点存活。`NodeHandle` 用 `tokio::sync::Mutex` 保护——`recv()` 持锁跨 await，响应发送时短暂获取锁。

| 文件 | 操作 | 内容 |
|------|------|------|
| `Cargo.toml` | 更新 | 添加 `arf-bus` 依赖 |
| `node.rs` | 新建 | `LocalMcpNode` + Bus 生命周期 + 消息分派 |

---

## 代码实现

### `crates/arf-mcp/Cargo.toml` 更新

```toml
[dependencies]
arf-core = { path = "../arf-core" }
arf-bus = { path = "../arf-bus" }     # 新增
# ... existing ...
```

### `crates/arf-mcp/src/node.rs` — 新建

```rust
use std::path::PathBuf;
use std::sync::Arc;

use arf_bus::Bus;
use arf_core::{Message, MessageFilter, NodeId, NodeInfo};
use serde_json::Value;
use tokio::sync::Mutex;

use crate::discovery::DiscoveryModule;
use crate::error::McpError;
use crate::runtime::RuntimeModule;
use crate::types::ToolCallSet;

/// A local MCP node — discovers tools and skills from the filesystem.
///
/// ## Lifecycle
/// ```text
/// LocalMcpNode::new(ns, root)  → scan filesystem
///   .connect(&bus)             → Bus connect + node_online broadcast + spawn loop
/// ```
///
/// Use `Arc<LocalMcpNode>` when calling `connect()` — the message loop
/// holds an Arc reference to keep the node alive.
pub struct LocalMcpNode {
    pub namespace: String,
    pub node_id: NodeId,
    discovery: DiscoveryModule,
    runtime: Box<dyn RuntimeModule>,
    handle: Mutex<Option<arf_bus::NodeHandle>>,
}

impl LocalMcpNode {
    /// Scan filesystem with default LocalRuntime.
    pub fn new(namespace: impl Into<String>, root_dir: PathBuf) -> Result<Self, McpError> {
        let ns: String = namespace.into();
        let discovery = DiscoveryModule::scan(root_dir)?;
        Ok(Self {
            node_id: NodeId::new(&format!("mcp/{ns}")),
            namespace: ns,
            discovery,
            runtime: Box::new(crate::runtime::LocalRuntime),
            handle: Mutex::new(None),
        })
    }

    /// Scan filesystem with custom RuntimeModule.
    pub fn with_runtime(
        namespace: impl Into<String>,
        root_dir: PathBuf,
        runtime: Box<dyn RuntimeModule>,
    ) -> Result<Self, McpError> {
        let ns: String = namespace.into();
        let discovery = DiscoveryModule::scan(root_dir)?;
        Ok(Self {
            node_id: NodeId::new(&format!("mcp/{ns}")),
            namespace: ns,
            discovery,
            runtime,
            handle: Mutex::new(None),
        })
    }

    /// Connect to Bus, broadcast `node_online`, and start the message loop.
    ///
    /// Takes `Arc<Self>` so the spawned message loop can hold a reference.
    pub async fn connect(self: &Arc<Self>, bus: &Bus) -> Result<(), McpError> {
        let info = self.build_node_info();

        let filter = MessageFilter {
            types: None,                     // accept all message types
            to_match: arf_core::ToMatch::All, // receive broadcast + directed
        };

        let handle = bus.connect(info, filter).await.map_err(|e| {
            McpError::BusConnect {
                reason: format!("{e}"),
            }
        })?;

        *self.handle.lock().await = Some(handle);

        let this = self.clone();
        tokio::spawn(async move { this.message_loop().await });

        Ok(())
    }

    // ── Internals ──────────────────────────────────────────────────

    fn build_node_info(&self) -> NodeInfo {
        let tools: Vec<Value> = self
            .discovery
            .list_tools()
            .iter()
            .map(|t| serde_json::json!({"name": t.name, "description": t.description}))
            .collect();

        let skills: Vec<Value> = self
            .discovery
            .list_skills()
            .iter()
            .map(|s| serde_json::json!({"name": s.name, "description": s.description}))
            .collect();

        NodeInfo {
            node_id: self.node_id.clone(),
            node_type: "mcp".into(),
            capabilities: serde_json::json!({
                "runtime": self.runtime.capabilities(),
                "tools": tools,
                "skills": skills,
            }),
            online_since: std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap_or_default()
                .as_millis() as u64,
        }
    }

    async fn message_loop(self: Arc<Self>) {
        loop {
            // Receive — hold lock only during recv
            let msg = {
                let mut guard = self.handle.lock().await;
                match guard.as_mut() {
                    Some(handle) => match handle.recv().await {
                        Ok(m) => Some(m),
                        Err(_) => None,
                    },
                    None => break,
                }
            };

            let msg = match msg {
                Some(m) => m,
                None => break,
            };

            // Only respond to directed messages or broadcasts targeting us
            if !msg.is_for(&self.node_id) && !msg.is_broadcast() {
                continue;
            }

            let response = self.dispatch(&msg).await;

            if let Some((msg_type, payload)) = response {
                let from = msg.from.clone();
                let guard = self.handle.lock().await;
                if let Some(handle) = guard.as_ref() {
                    let _ = handle.send(&msg_type, vec![from], payload).await;
                }
                // guard dropped here — lock released before next recv
            }
        }
    }

    async fn dispatch(&self, msg: &Message) -> Option<(String, Value)> {
        let payload = msg.payload.clone();

        match msg.msg_type.as_str() {
            "tool_call_set" => {
                let call_set: ToolCallSet = match serde_json::from_value(payload) {
                    Ok(cs) => cs,
                    Err(e) => {
                        return Some(("tool_result_set".into(), serde_json::json!({
                            "session_id": "",
                            "results": [{"call_id": "", "name": "", "status": "error",
                                "result": null, "error": format!("invalid payload: {e}")}],
                        })));
                    }
                };

                let result_set = self.runtime.execute(&call_set, self.discovery.tool_map()).await;
                Some(("tool_result_set".into(), serde_json::to_value(&result_set).unwrap_or_default()))
            }

            "use_skill" => {
                let name = payload.get("name").and_then(|v| v.as_str()).unwrap_or("");
                let body = self.discovery.load_skill_body(name);
                let resources = self.discovery.load_skill_resources(name);
                match (body, resources) {
                    (Some(b), Some(r)) => {
                        let entry = self.discovery.resolve_skill(name);
                        Some(("skill_loaded".into(), serde_json::json!({
                            "namespace": self.namespace, "name": name,
                            "description": entry.map(|e| e.description.as_str()).unwrap_or(""),
                            "body": b,
                            "resources": {"tools": r.tools, "references": r.references, "assets": r.assets},
                        })))
                    }
                    _ => Some(("skill_error".into(), serde_json::json!({
                        "namespace": self.namespace, "name": name,
                        "error": format!("skill not found: {name}"),
                    }))),
                }
            }

            "load_skill_resource" => {
                let sn = payload.get("skill_name").and_then(|v| v.as_str()).unwrap_or("");
                let rp = payload.get("resource_path").and_then(|v| v.as_str()).unwrap_or("");
                match self.discovery.load_resource_file(sn, rp) {
                    Ok(loaded) => Some(("skill_resource_loaded".into(), serde_json::json!({
                        "namespace": self.namespace, "skill_name": sn, "resource_path": rp,
                        "content": loaded.content,
                        "description": loaded.description,
                        "params_schema": loaded.params_schema,
                    }))),
                    Err(e) => Some(("skill_resource_error".into(), serde_json::json!({
                        "namespace": self.namespace, "skill_name": sn, "resource_path": rp, "error": e,
                    }))),
                }
            }

            "run_skill_script" => {
                let sn = payload.get("skill_name").and_then(|v| v.as_str()).unwrap_or("");
                let tn = payload.get("tool_name").and_then(|v| v.as_str()).unwrap_or("");
                let cid = payload.get("call_id").and_then(|v| v.as_str()).unwrap_or("");
                let params = payload.get("params").cloned().unwrap_or(Value::Null);

                let (status, result, error): (String, Value, Option<String>) =
                    match self.discovery.run_skill_tool(sn, tn, params).await {
                        Ok(val) => ("success".into(), val, None),
                        Err(e) => ("error".into(), Value::Null, Some(e)),
                    };

                Some(("skill_script_result".into(), serde_json::json!({
                    "session_id": payload.get("session_id"),
                    "call_id": cid, "name": format!("{sn}/{tn}"),
                    "status": status, "result": result, "error": error,
                })))
            }

            _ => None,
        }
    }
}
```

逐行解释：
- `connect(self: &Arc<Self>, ...)` — 用 `Arc` 接收 self，消息循环通过 `self.clone()` 持有引用
- `MessageFilter { types: None, to_match: All }` — 接收所有消息类型和广播+定向。`types: None` 表示不过滤类型；`to_match: All` 表示接收所有 to 匹配规则
- `handle: Mutex<Option<arf_bus::NodeHandle>>` — `tokio::sync::Mutex` 因为 `handle.recv()` 在锁内跨 await。`Option` 表示"是否已连接"
- `message_loop` — 先锁住 handle 做 recv，收到消息后立即释放锁（guard drop），dispatch 不持锁。发送响应时短暂获取锁
- `dispatch` — 按 msg_type 分支。`tool_call_set` 反序列化 payload → `runtime.execute()` → 组装 `tool_result_set`。skill 相关调用 DiscoveryModule
- 类型注解 `(String, Value, Option<String>)` 解决 `.into()` 的类型推断歧义（`From<&str>` 被多个 crate 实现）

---

## 测试

### 测试策略

两个集成测试需要真实 Bus + subprocess：

| 测试 | 覆盖 |
|------|------|
| `connect_broadcasts_node_online` | 节点上线后 Bus graph 包含该节点 |
| `tool_call_set_roundtrip` | Engine 发 tool_call_set → MCP 执行 → 收 tool_result_set |

### 实施记录

**1. subscribe 必须在 connect 前**

`bus.subscribe()` 创建的 Receiver 只接收订阅之后的消息。如果先 `connect()` 再 `subscribe()`，`node_online` 广播已经发出，订阅者收不到。

```rust
// ✅ 正确顺序
let mut rx = bus.subscribe();         // 先订阅
let node = Arc::new(LocalMcpNode::new(...));
node.connect(&bus).await.unwrap();    // 后连接 → node_online 被 rx 捕获

// ❌ 错误顺序
node.connect(&bus).await.unwrap();    // node_online 已发出
let mut rx = bus.subscribe();         // 晚了，收不到
```

**2. 原始 subscribe 不过滤心跳**

`Bus::subscribe()` 返回原始 `broadcast::Receiver`——包括 `heartbeat_request`。而 `NodeHandle::recv()` 内部拦截心跳并自动 ack。测试使用 `bus.subscribe()` 需要手动跳过心跳消息。

**3. 发送响应时 lock 嵌套**

`message_loop` 中两次获取 `self.handle.lock()`：一次 for recv，一次 for send response。关键是在两次之间 `guard` 已 drop，不会死锁：

```rust
let msg = { let mut guard = self.handle.lock().await; guard.as_mut()?.recv().await };
// guard dropped ← 锁释放
let response = self.dispatch(&msg).await;
// 短暂获取锁发送响应
let guard = self.handle.lock().await;
handle.send(...).await;
// guard dropped ← 锁释放，下一轮 recv 可以获取
```

---

## 验证命令

```bash
. "$HOME/.cargo/env" && cargo test -p arf-mcp -- node_tests
. "$HOME/.cargo/env" && cargo test --workspace
```

---

## 测试覆盖摘要

| 文件 | 新增测试 | 覆盖角度 |
|------|---------|---------|
| `node_tests.rs` | 2 | `[集成]` — Bus 连接 + node_online 广播(1)、tool_call_set 往返(1) |
| **合计** | **2** | 累计 arf-mcp: 166 + 2 = **168 tests** |
