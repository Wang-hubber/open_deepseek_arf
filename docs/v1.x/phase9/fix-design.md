# Phase 9 Fix Design — 22 病灶修复整合方案

> **来源**：本设计整合 phase 9 各 task 探查跑出的病灶（详见 `lesion-registry.md`），按"共同根因 / 互相干涉"归并为 8 个 cluster，逐 cluster 选定 fix 方案。
>
> **关联文档**：
> - 病灶登记册：`docs/v1.x/phase9/lesion-registry.md`（22 OPEN + 1 重新 framing + 2 WONTFIX）
> - 探查 task 列表：见 `lesion-registry.md` §1 §2 各 `触发 task` 字段
> - 设计 spec：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`（信条 + signal + 病灶判定规则）
>
> **修复顺序**：C8 → C9 → C5 → C7 → C3 → C6 → C4 → C1 → C2（按工作量小到大；C2 依赖 C3 + C4，最后做）
>
> **本设计通过条件**：每个 cluster fix 完成后，须按 `lesion-registry.md` §4 接口契约更新病灶状态为 `FIXED` + 附 fix commit hash，并重跑触发该病灶的 task audit-probe 确认命中消失。

---

## §0 Scope 总览

| 项 | 数值 |
|---|---|
| 病灶总数（phase 9 探查结束） | 25（2 A + 23 F）|
| 本设计覆盖 | 22 OPEN fix + 1 F-003 重新 framing |
| **WONTFIX** | F-001（误判）+ F-004（app 自订阅已 work）|
| **Cluster 数** | 8（C1-C7 + C8/F-003 + C9/F-015）|
| **总改动量估算** | ~1500 行 Rust（不含测试 + doc）|
| **总测试新增** | ~25 个 e2e/单测 |
| **文档更新** | spec 5 处 + lesion-registry + 部分 task doc |

---

## §1 WONTFIX 病灶（2 个）

### F-001 — EnginePool 抽象缺失

**状态**：**WONTFIX**

**理由**：
- 用户 2026-07-03 明确"Pool 内节点同质化 + Engine 是有状态客户端单例 → Engine 不应有 pool"
- 原病灶"framework 缺 EnginePool 抽象"是基于"N 个 Engine 共享 model config"假设，但真实需求是 **N 个 Engine 实例 × 共享 1 个 Pool**（不是 EnginePool）
- 真实生产场景"N 用户并发咨询"应通过 C2 F-018 (`EngineBuilder.with_agent_id`) + C1 F-002 (Pool 弹性) 共同解决
- F-001 的存在是 **认知误判**，不是缺失 primitive

**取代方案**：
- F-018 fix 提供多 Engine 实例支持
- C1 fix 让 Pool 自身可弹性扩容
- 用户层"N Engine × 共享 1 Pool"模式作为应用层 pattern 文档化（无需 framework 强制抽象）

### F-004 — Framework 缺 stream event callback API

**状态**：**WONTFIX**

**理由**：
- chunks 已在 bus 上正常流动（model-adapter/node.rs:130-165，`model_response_chunk` 消息正常 emit）
- 用户 2026-07-03 round 7 明确："Engine 只消费最终结果用于下一步推理。chunks 交给 App 的前端去消费。这影响 Engine 推理吗？答：不影响"
- app 端消费 chunks 已有可行路径（`bus.subscribe()` + 过滤 `model_response_chunk` 类型）
- 引入 `EngineBuilder.on_stream_chunk` 闭包会 **强制 framework 接管 stream UX**，反而限制 app 灵活性（不同 app 流式 UX 需求不同：打字机 vs 分块 vs SSE）
- 框架应保持中性：提供 bus-level chunk delivery，不强制 engine-level callback

**用户层模式文档化**（作为 follow-up doc，不在 fix scope）：
- 在 `docs/api/` 加 recipe："How to stream model_response_chunk to frontend via bus.subscribe"

---

## §2 Cluster C8：F-003 — Pool facade 串行调度（病灶重新 framing）

### 原病灶描述（作废）
> "Facade 的 `sub_id = "model/pool-{i}/sub"` 模式（pool_node.rs:65）阻断 ModelAdapterNode 集成；facade forward model_call 时 `to=this sub_id`；任何想在此 id 注册 `ModelAdapterNode` 会被 bus 拒绝（`AlreadyConnected`）"

### 重新 framing 后真病灶
- 重读 `crates/arf-model-adapter/src/pool_node.rs:84-150` 后确认：
  - 实际代码 `to=vec![]`（broadcast），**不是** `to=sub_id`
  - 代码库**无任何** ModelAdapterNode 尝试注册成 `*/sub` id（都用自己的 node_id）
  - `crates/arf-e2e/tests/pool.rs` 已成功跑过 facade + ModelAdapterNode on sub-bus 的 end-to-end 流程
- 真实问题：**`run_loop` 串行调度**（一个 await 阻塞整个 loop）
  - N 个 Engine 并发发 model_call 时，第 2+ 个在 `pool.acquire().await`（line 102）阻塞等第 1 个 lease 释放
  - Pool 内 N 个同质 resource **未被并发利用**（即使 Pool max_size=4，最多只 1 个 active model_call）

### Fix 方向

**方案**：facade run_loop 改为 dispatcher，`tokio::spawn` 每个 model_call 到独立 task

**改动点**（`crates/arf-model-adapter/src/pool_node.rs`）：
```rust
// 重构前（lines 84-150）：单 loop 串行
async fn run_loop(self: Arc<Self>, mut top_handle: NodeHandle, mut sub_handle: NodeHandle) {
    loop {
        let req = top_handle.recv().await;
        // ... acquire → forward → wait response → drop lease ...
    }
}

// 重构后：dispatcher + spawn per-request task
async fn run_loop(self: Arc<Self>, mut top_handle: NodeHandle, mut sub_handle: NodeHandle) {
    loop {
        let req = match top_handle.recv().await { Ok(m) => m, Err(_) => return };
        if req.msg_type != "model_call" { continue; }
        let me = self.clone();
        let sub_handle = sub_handle.clone();  // 假设支持 clone，或 sub_handle 改为共享 Arc
        tokio::spawn(async move {
            me.handle_one_model_call(req, sub_handle).await;
        });
    }
}

async fn handle_one_model_call(self: Arc<Self>, req: Message, mut sub_handle: NodeHandle) {
    let lease = match self.pool.acquire().await {
        Ok(l) => l,
        Err(e) => {
            // 回 model_response{error} 给 req.from（避免 silent failure）
            let _ = self.top_bus.send(Message::with_from_bus(
                "model_response".into(),
                self.node_id.clone(),
                vec![req.from.clone()],
                json!({"error": format!("pool acquire: {e}"), "correlation_id": req.correlation_id()}),
                self.top_bus.id,
            )).await;
            return;
        }
    };
    // forward to sub-bus broadcast (to=[]) + wait for model_response match correlation_id
    // ...
    drop(lease);
}
```

**关键决策**：
1. spawn-per-task 让 N 个并发 model_call 真正用满 Pool N 个 resource
2. acquire 失败回 `model_response{error}`（含 correlation_id）—— 防止 silent failure
3. lease 仍 drop 在 task 内（task 结束 = lease 释放）

**测试**：
- `facade_spawns_per_request_concurrent`：N=4 Pool + 4 并发 model_call，验证 4 个 resource 真并发（总耗时 ≈ 单个耗时，不是 4×）
- `facade_acquire_error_returns_error_response`：Pool 关闭后 facade 收到 model_call，回 `model_response{error}` 给原 Engine
- 回归：`crates/arf-e2e/tests/pool.rs` 应继续通过

**风险**：
- spawn 后 task 数量无界（Engine spam）→ 可加 per-facade semaphore 限制 active task 数（如 `max_inflight: usize`）
- `NodeHandle` 是否支持 clone / shared：需确认 bus 实现，必要时把 `NodeHandle` 改为 `Arc<NodeHandle>` 共享

---

## §3 Cluster C9：F-015 — Summarizer trait 签名误导

### Fix 方向

**方案**：rename trait param → `CompactionRequest { instruction, messages }` 结构体

**改动点**（`crates/arf-session/src/compaction.rs` 或类似路径）：
```rust
// 重构前
trait Summarizer {
    fn summarize(&self, messages_to_summarize: &[ModelMessage]) -> impl Future<...>;
}

// 重构后
pub struct CompactionRequest<'a> {
    pub instruction: String,             // 来自 with_instruction 注入
    pub messages: &'a [ModelMessage],     // raw conversation
}

trait Summarizer {
    fn summarize(&self, req: CompactionRequest<'_>) -> impl Future<...>;
}
```

**Compactor 调用点**：改传 `CompactionRequest { instruction: self.instruction.clone(), messages: &raw_messages }`

**破坏性**：trait 是 v1.x 未稳定，OK

**测试**：
- `summarizer_receives_instruction_and_messages_separately`：mock Summarizer 断言收到的 `req.instruction` 是 with_instruction 注入值，`req.messages` 是 raw conversation
- 回归：现有 Compactor + Summarizer 集成测试

---

## §4 Cluster C5：F-012 + F-013 + F-014 — SessionStore 契约清晰化

### Fix 方向（三病灶一并修）

**F-013 — `save()` 写 4 字段（含 `last_checkpoint`）**

```rust
// crates/arf-session/src/lib.rs:337
// 重构前
trait SessionStore {
    fn save(&self, data: SessionData) -> impl Future<Output = Result<(), SessionStoreError>>;
    fn snapshot(&self, session_id: SessionId, checkpoint: Checkpoint) -> impl Future<...>;
}

// 重构后
trait SessionStore {
    /// 持久化 SessionData 全部 4 字段（meta + state + config_snapshot + last_checkpoint）
    /// 注：app 调 save() 后不再需要额外调 snapshot() 持久化 checkpoint
    fn save(&self, data: SessionData) -> impl Future<Output = Result<(), SessionStoreError>>;

    /// 暂不持久化 checkpoint 的 save（用于"先存 meta+state，checkpoint 后续单独 snapshot"场景）
    fn save_partial(&self, data: SessionDataWithoutCheckpoint) -> impl Future<...>;

    /// Append a checkpoint, run 4 副作用（见 SnapshotEffects）
    fn snapshot(&self, session_id: SessionId, checkpoint: Checkpoint) -> impl Future<Output = Result<SnapshotEffects, SessionStoreError>>;
}
```

**F-014 — trait doc 列 4 副作用 + `SnapshotEffects` struct**

```rust
/// `snapshot()` 实际跑 4 个副作用（custom impl 必须全部实现）：
/// 1. write checkpoint row (insert into checkpoints table)
/// 2. UPDATE sessions.state_json (推 state 到最新)
/// 3. UPDATE sessions.updated_at (更新时间戳)
/// 4. force sessions.status='interrupted' (kill signal 标记)
pub struct SnapshotEffects {
    pub checkpoint_written: CheckpointId,
    pub state_updated: SessionStateSnapshot,
    pub updated_at: DateTime<Utc>,
    pub status_forced: SessionStatus,  // 总是 Interrupted
}
```

**F-012 — Engine.snapshot fail-fast**

```rust
// crates/arf-engine/src/engine.rs chat() 入口
async fn chat(&mut self, session_id: SessionId, user_input: String, ...) -> Result<...> {
    // 入口检查 session_id 是否已在 store 中
    if !self.session_store.exists(&session_id).await? {
        return Err(EngineError::SessionNotPreSaved(session_id));
    }
    // ... 现有逻辑 ...
}

async fn snapshot_internal(&self, session_id: SessionId, checkpoint: Checkpoint) -> Result<...> {
    self.session_store.snapshot(session_id, checkpoint).await
        .map_err(|e| {
            // 失败强制 abort 当前 round
            self.abort_current_round(session_id, &e);
            e
        })?
}
```

**兼容性**：
- 旧 `save() + snapshot()` 仍 work（snapshot 幂等覆盖 last_checkpoint）
- 旧 custom `SessionStore` impl 缺 4 副作用 → 编译期通过（trait 不强制），需 doc 警告"必须实现 4 副作用"

**测试**：
- `engine_fails_fast_on_unpreloaded_session`：EngineBuilder + with_session_store + Engine.chat() 之前不调 save() → `Err(SessionNotPreSaved)`
- `save_persists_last_checkpoint`：`save(data with last_checkpoint=Some(c1))` → reload → last_checkpoint == Some(c1)
- `snapshot_returns_4_effects`：SqliteSessionStore.snapshot() 返回 SnapshotEffects 4 字段全填
- `snapshot_failure_aborts_round`：mock store 让 snapshot fail → Engine 当前 round abort
- 回归：现有 SessionStore 测试

---

## §5 Cluster C7：F-010 + F-011 — MCP 集成完整化

### Fix 方向

**F-010 — `McpNode::with_discovery` 构造器**

```rust
// crates/arf-mcp/src/node.rs
pub struct McpNode {
    pub node_id: NodeId,
    pub namespace: String,
    pub discovery: Box<dyn DiscoveryBackend>,  // 改 pub(crate) 或加访问器
    pub runtime: Box<dyn RuntimeModule>,
    pub handle: NodeHandle,
}

impl McpNode {
    pub fn local(namespace: impl Into<String>, bus: Arc<Bus>) -> Self { /* FsDiscovery */ }
    pub fn remote(namespace: impl Into<String>, bus: Arc<Bus>) -> Self { /* HttpDiscovery */ }
    pub fn local_with_runtime(namespace: impl Into<String>, bus: Arc<Bus>, runtime: Box<dyn RuntimeModule>) -> Self { /* ... */ }

    /// NEW: 注入自定义 DiscoveryBackend + RuntimeModule
    pub fn with_discovery(
        namespace: impl Into<String>,
        discovery: Box<dyn DiscoveryBackend>,
        runtime: Box<dyn RuntimeModule>,
    ) -> Self {
        Self {
            node_id: NodeId::new(format!("mcp/{}", namespace.into())),
            discovery,
            runtime,
            handle: NodeHandle::unbound(),  // 后续 connect 填充
        }
    }

    /// 加访问器供外部读取 discovery
    pub fn discovery(&self) -> &dyn DiscoveryBackend { &*self.discovery }
}
```

**F-011 — `HttpProxyTool` 读 `isError` 字段**

```rust
// crates/arf-mcp/src/http_proxy.rs (推测位置)
fn convert_tool_result(mcp_response: serde_json::Value) -> ToolExec {
    let content = mcp_response.get("content").cloned().unwrap_or(json!({}));
    let is_error = mcp_response.get("isError")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);

    ToolExec {
        status: if is_error { ToolStatus::Failed } else { ToolStatus::Success },
        content,
        error: if is_error { Some(format!("tool returned isError: {content}")) } else { None },
    }
}
```

**测试**：
- `mcp_node_with_custom_discovery`：用 `InMemoryDiscovery` 注入 McpNode → message_loop 路径走 custom discovery
- `http_proxy_tool_propagates_is_error`：mock MCP server 返回 `{content: "...", isError: true}` → ToolExec.status == Failed
- cascade cancel 链路回归：parent tool 触发 child tool cancel

---

## §6 Cluster C3：A3-001 + A4-001 + F-020 — 消息协议契约统一

### Fix 方向（消息契约整套重整）

**A3-001 — `arf-core::msg_type` 常量模块**

```rust
// crates/arf-core/src/msg_type.rs (NEW)
pub const NODE_ONLINE: &str = "node_online";
pub const NODE_OFFLINE: &str = "node_offline";
pub const HEARTBEAT_REQUEST: &str = "heartbeat_request";
pub const HEARTBEAT_ACK: &str = "heartbeat_ack";
pub const BARRIER_REQUEST: &str = "barrier_request";
pub const BARRIER_ACK: &str = "barrier_ack";
pub const MODEL_CALL: &str = "model_call";
pub const MODEL_RESPONSE: &str = "model_response";
pub const MODEL_RESPONSE_CHUNK: &str = "model_response_chunk";
pub const TOOL_CALL: &str = "tool_call";
pub const TOOL_RESULT: &str = "tool_result";
pub const PERMISSION_REQUEST: &str = "permission_request";
pub const PERMISSION_RESPONSE: &str = "permission_response";
pub const PEER_MESSAGE: &str = "peer_message";
pub const PEER_REPLY: &str = "peer_reply";
pub const SESSION_SAVE: &str = "session_save";
pub const SESSION_SNAPSHOT: &str = "session_snapshot";
```

**使用规则**：engine/bus/model-adapter/mcp 全用这些常量，**禁止**裸字面量

**A4-001 — `Message` trait 加 `with_correlation_id(Uuid)` 对称 API**

```rust
// crates/arf-core/src/message.rs
pub trait Message {
    // ... 现有方法 ...
    fn correlation_id(&self) -> Option<Uuid>;

    /// NEW: 对称 API
    fn with_correlation_id(&self, cid: Uuid) -> Message;

    /// NEW: 直接构造带 cid 的 Message
    fn new_with_correlation_id(
        msg_type: impl Into<String>,
        from: NodeId,
        to: Vec<NodeId>,
        payload: serde_json::Value,
        bus_id: BusId,
        cid: Uuid,
    ) -> Message;
}

// 所有 json!({"correlation_id": cid.to_string()}) 改用：
let msg = req.with_correlation_id(cid);
// engine.rs:689 wait_for 改用：
if msg.correlation_id() == Some(target_cid) { ... }
```

**F-020 — `Message` 加 `routing` 字段**

```rust
// crates/arf-core/src/message.rs
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum MessageRouting {
    Directed(Vec<NodeId>),
    Broadcast,
}

pub struct Message {
    pub msg_type: String,
    pub from: NodeId,
    pub to: Vec<NodeId>,           // 现有
    pub routing: MessageRouting,   // NEW
    pub payload: serde_json::Value,
    pub bus_id: BusId,
    pub correlation_id: Option<Uuid>,
}

// bus.send 对 Broadcast 跳过 online 验证
impl Bus {
    pub async fn send(&self, msg: Message) -> Result<(), SendError> {
        match &msg.routing {
            MessageRouting::Broadcast => {
                // 跳过 online 验证，直接 dispatch
                self.dispatch(msg).await
            }
            MessageRouting::Directed(targets) => {
                // 现有逻辑：每个 target 验证 online
                for target in targets {
                    if !self.is_online(target).await {
                        return Err(SendError::NodeOffline(target.clone()));
                    }
                }
                self.dispatch(msg).await
            }
        }
    }
}
```

**handler reply 用法**：
```rust
// 之前（silent NodeOffline 风险）
let reply = Message::new("model_response".into(), self.node_id.clone(), vec![msg.from.clone()], payload, bus.id);
let _ = bus.send(reply).await;  // 若 msg.from 不在线 → silent error

// 之后
let reply = Message::new_broadcast("model_response".into(), self.node_id.clone(), payload, bus.id);
bus.send(reply).await.ok();  // broadcast 跳过 online check
```

**破坏性**：
- `Message` API 变更（加 `routing` 字段 + 新构造方法）
- 现有 ~30 处构造 Message 的调用点需更新（grep `Message::new` / `Message::with_from_bus` / `json!` 拼 Message）

**测试**：
- `msg_type_constants_match_existing_strings`：所有 16 个常量与当前裸字面量比对一致
- `correlation_id_roundtrip_typed`：`with_correlation_id(cid)` → `correlation_id() == Some(cid)` 双向一致
- `broadcast_skips_online_check`：构造 broadcast Message → target offline 仍发送成功
- `directed_to_offline_returns_node_offline`：directed Message → target offline → `Err(NodeOffline)`
- 回归：所有现有 Message 路径测试

---

## §7 Cluster C6：F-005 + F-006 — Engine 传 thinking_enabled（F-004 WONTFIX）

### Fix 方向

**F-006 — spec `thinking_visible` → `thinking_enabled`**

更新 5 处 spec：
1. `docs/v1.x/phase9/capability-matrix-and-audit-design.md` §1.1 L1
2. 同上 §5
3. `docs/v1.x/phase9/task-9.2.1.md`
4. `docs/v1.x/phase9/audit-probe-9.2.1.md`
5. `docs/v1.x/phase9/task-9.3.2.md`
6. `docs/v1.x/phase9/audit-probe-9.3.2.md`（如有）

**F-005 — `ModelCallPayload` 必有 `model_params: ModelParams` 字段**

```rust
// crates/arf-model-adapter/src/types.rs (推测)
pub struct ModelCallPayload {
    pub messages: Vec<ModelMessage>,
    pub tools: Vec<ToolDef>,
    pub stream: bool,
    pub model_params: ModelParams,  // 从 Option<ModelParams> 改必有
}

// ModelParams 已有 thinking_enabled 字段（types.rs:38），保持不变

// crates/arf-engine/src/engine.rs 序列化时
fn build_model_call(&self, decl: &ModelDecl) -> ModelCallPayload {
    ModelCallPayload {
        messages: self.context.messages.clone(),
        tools: self.context.tools.clone(),
        stream: decl.stream,
        model_params: ModelParams {
            thinking_enabled: decl.thinking_enabled,
            temperature: decl.temperature,
            max_tokens: decl.max_tokens,
            // ... 其他 params 从 decl 复制 ...
        },
    }
}
```

**兼容性**：
- `ModelCallPayload` 字段从 Option 改必有，所有构造点需更新
- model_call wire format 兼容性：JSON 序列化字段名不变（`model_params` 现在 always present）

**测试**：
- `engine_propagates_thinking_enabled`：cfg.thinking_enabled=true → 发出的 model_call payload.model_params.thinking_enabled == true
- `engine_propagates_thinking_disabled`：cfg.thinking_enabled=false → payload.model_params.thinking_enabled == false
- `spec_uses_thinking_enabled_consistently`（可选 doc 测试）：grep docs `thinking_visible` == 0 hits，grep `thinking_enabled` >= N hits

---

## §8 Cluster C4：F-007 + F-008 — ResourceRegistry 路由确定性

### Fix 方向

**F-008 — `BusGraph.nodes` 改 `Vec<NodeInfo>`**

```rust
// crates/arf-bus/src/graph.rs
pub struct BusGraph {
    // 重构前
    pub nodes: HashMap<NodeId, NodeInfo>,
    pub edges: HashMap<NodeId, Vec<NodeId>>,

    // 重构后（key 序确定）
    pub nodes: BTreeMap<NodeId, NodeInfo>,
    pub edges: BTreeMap<NodeId, Vec<NodeId>>,
}

// 或保留 HashMap 性能但对外暴露 sorted 视图
impl BusGraph {
    pub fn nodes_sorted(&self) -> impl Iterator<Item = (&NodeId, &NodeInfo)> {
        self.nodes.iter().collect::<Vec<_>>().into_iter()  // 拷贝后排序
    }
}
```

**F-007 — `resolve_model` 加 `model_name` 检查**

```rust
// crates/arf-engine/src/registry.rs:253-269
impl ResourceRegistry {
    pub async fn resolve_model(&self, decl: &ModelDecl) -> Result<NodeId, ResolveError> {
        let candidates: Vec<&NodeInfo> = self.graph.nodes_sorted()  // F-008 修复后用确定序
            .filter(|(_, n)| {
                let caps = &n.capabilities;
                caps.get("provider").and_then(|v| v.as_str()) == Some(&decl.provider)
            })
            .map(|(_, n)| n)
            .collect();

        if candidates.is_empty() {
            return Err(ResolveError::NoNodeForProvider(decl.provider.clone()));
        }

        // NEW: 检查 model_name 是否在 supports 中
        let supported: Vec<&NodeInfo> = candidates.into_iter().filter(|n| {
            n.capabilities.get("models")
                .and_then(|v| v.as_array())
                .map(|arr| arr.iter().any(|m| m.as_str() == Some(&decl.model_name)))
                .unwrap_or(false)
        }).collect();

        match supported.len() {
            0 => Err(ResolveError::UnsupportedModel {
                provider: decl.provider.clone(),
                model_name: decl.model_name.clone(),
                supported: candidates.iter().flat_map(|n|
                    n.capabilities.get("models")
                        .and_then(|v| v.as_array())
                        .map(|arr| arr.iter().filter_map(|m| m.as_str().map(String::from)).collect())
                        .unwrap_or_default()
                ).collect(),
            }),
            1 => Ok(supported[0].node_id.clone()),
            _ => {
                // 多 node 支持同一 model，按 priority_hint 选
                supported.into_iter()
                    .max_by_key(|n| n.capabilities.get("priority_hint").and_then(|v| v.as_u64()).unwrap_or(0))
                    .map(|n| n.node_id.clone())
                    .ok_or_else(|| ResolveError::NoNodeForModel(decl.model_name.clone()))
            }
        }
    }
}
```

**附加**：`NodeInfo.capabilities` 加 `priority_hint: Option<u32>`（已有 capabilities json map，约定 key）

**兼容性**：
- `BusGraph.nodes` 类型变化（HashMap → BTreeMap），所有依赖 `nodes.iter()` 的代码（5-10 处）需更新类型注解或调用 `nodes_sorted()`
- `resolve_model` 错误类型新增变体 `UnsupportedModel { provider, model_name, supported }`

**测试**：
- `resolve_model_picks_by_model_name`：2 节点同 provider 不同 supported_models，cfg.model_name=只在 node2 supports → 选 node2
- `resolve_model_errors_on_unsupported`：cfg.model_name 不在任一节点 supports → `Err(UnsupportedModel { ... })`
- `bus_graph_iteration_is_deterministic`：插入 N 节点，迭代 100 次顺序一致
- `resolve_model_priority_hint`：2 节点都 supports model_name，按 priority_hint 选高的

---

## §9 Cluster C1：F-002 (CRITICAL) + F-009 + F-021 — Pool 弹性语义 ⚠️

### Fix 方向（实现弹性语义 + Queue 真实 + facade auto-provision）

**F-002 — `PoolConfig` 加 `min_size` + `auto_provision` + `provisioner`**

```rust
// crates/arf-pool/src/lib.rs:79
#[derive(Debug, Clone)]
pub struct PoolConfig {
    pub max_size: usize,
    pub overflow: Overflow,
    pub idle_timeout: Option<Duration>,

    // NEW: 弹性语义
    pub min_size: usize,                                      // 初始保证 resource 数
    pub auto_provision: bool,                                 // load > total 时自动扩容
    pub provisioner: Option<Arc<dyn Fn() -> R + Send + Sync>>, // 扩容 factory
}

// Pool::new 强制 provision 到 min_size
impl<R: Resource> Pool<R> {
    pub fn new(config: PoolConfig) -> Self {
        let pool = Self { /* ... */ };
        if let Some(provisioner) = &config.provisioner {
            for _ in 0..config.min_size {
                let r = provisioner();
                pool.inner.state.lock().await.idle.push(Arc::new(r));
            }
        }
        pool
    }
}

// acquire 时若 auto_provision && total < max_size → 调 provisioner + retry
impl<R: Resource> Pool<R> {
    pub async fn acquire(&self) -> Result<Lease<R>, PoolError> {
        // 现有逻辑尝试 acquire
        match self.try_acquire().await {
            Ok(lease) => Ok(lease),
            Err(PoolError::Full) if self.config.auto_provision => {
                let mut state = self.inner.state.lock().await;
                if state.total < self.config.max_size {
                    if let Some(provisioner) = &self.config.provisioner {
                        let r = provisioner();
                        state.idle.push(Arc::new(r));
                        state.total += 1;
                        drop(state);
                        // retry acquire
                        self.acquire().await
                    } else {
                        Err(PoolError::Full)
                    }
                } else {
                    Err(PoolError::Full)
                }
            }
            Err(e) => Err(e),
        }
    }
}
```

**F-009 — `Overflow::Queue(N)` 真实语义**

```rust
// crates/arf-pool/src/lib.rs:199-205
impl<R: Resource> Pool<R> {
    pub async fn acquire(&self) -> Result<Lease<R>, PoolError> {
        match self.config.overflow {
            Overflow::Reject => {
                // try_acquire fast path
                self.inner.sem.clone().try_acquire_owned()
                    .map_err(|_| PoolError::Full)?
                    .forget();  // 实际实现见 lib.rs
                // ... pop from idle ...
            }
            Overflow::Block(timeout) => {
                tokio::time::timeout(timeout, self.inner.sem.clone().acquire_owned()).await
                    .map_err(|_| PoolError::AcquireTimeout(timeout))??;
                // ... pop from idle ...
            }
            Overflow::Queue(max_pending) => {
                // 先 try_acquire fast path
                if let Ok(_permit) = self.inner.sem.clone().try_acquire_owned() {
                    // got permit, pop from idle
                } else {
                    // 检查 pending < max_pending
                    let mut state = self.inner.state.lock().await;
                    if state.pending < max_pending {
                        state.pending += 1;
                        drop(state);
                        // await notify（lease drop 时 notify）
                        self.inner.notify.notified().await;
                        state.pending -= 1;
                        // retry acquire
                        return Box::pin(self.acquire()).await;
                    } else {
                        return Err(PoolError::Full);
                    }
                }
            }
        }
        // ... pop from idle and return Lease ...
    }
}
```

**F-021 — `MCPPoolNode` 接 `provisioner` 闭包**

```rust
// crates/arf-mcp/src/pool_node.rs
impl MCPPoolNode {
    pub fn new(
        node_id: NodeId,
        pool: Arc<Pool<MCPResource>>,
        provisioner: Option<Arc<dyn Fn() -> MCPResource + Send + Sync>>,  // NEW
    ) -> Self {
        Self { node_id, pool, provisioner }
    }

    async fn run_loop(self: Arc<Self>, ...) {
        // 收到 tool_call → spawn per-task（与 C8 模式一致）
        loop {
            let req = match top_handle.recv().await { Ok(m) => m, Err(_) => return };
            if req.msg_type != "tool_call" { continue; }
            let me = self.clone();
            tokio::spawn(async move { me.handle_one_tool_call(req).await });
        }
    }

    async fn handle_one_tool_call(self: Arc<Self>, req: Message) {
        // acquire 失败时若 provisioner 存在则调 + retry
        let lease = match self.pool.acquire().await {
            Ok(l) => l,
            Err(PoolError::Full) => {
                if let Some(p) = &self.provisioner {
                    let r = p();
                    self.pool.add_resource(r);  // 需要 Pool::add_resource API
                    self.pool.acquire().await
                } else {
                    Err(PoolError::Full)
                }
            }
            Err(e) => Err(e),
        };
        // ... forward to mcp sub-bus ...
    }
}
```

**兼容性**：
- `PoolConfig` 加 3 字段，破坏性变更
- `Pool::new` 行为变化（强制 provision min_size）—— 旧调用若没 provisioner + min_size=0 应保持原行为
- trait 是 v1.x 未稳定，OK

**测试**：
- `pool_grows_to_min_size_on_new`：Pool::new(config with min_size=3, provisioner) → pool.idle.len() == 3
- `pool_auto_provisions_under_load`：max_size=4 + auto_provision + provisioner + 发 4 并发 acquire → pool 实际 grow 到 4（不是 acquire 失败）
- `queue_n_buffers_pending_correctly`：max_size=1 + Overflow::Queue(2) → 第 2/3 个 acquire 进入 pending；第 4 个 Err(Full)
- `queue_zero_returns_immediately`：max_size=1 + Overflow::Queue(0) + 第 2 个 acquire → Err(Full) 立即
- `queue_max_buffers_lots`：max_size=1 + Overflow::Queue(usize::MAX) → 第 2+ 个永久等，直到 l1 drop
- `mcp_pool_auto_provisions_on_first_acquire`：MCPPoolNode + provisioner + 1 acquire → 内部 pool grow 到 1 + 成功
- 回归：现有 Pool 测试（含 `crates/arf-e2e/tests/pool_overflow_*` 系列）

---

## §10 Cluster C2：F-016 + F-017 + F-018 + F-019 — Engine 多实例 + 主循环扩展点

> ⚠️ C2 依赖 C3（Message 协议稳定）+ C4（resolve_model SwitchTo 能力）。最后做。

### Fix 方向（一次性打通 4 个扩展点）

**F-018 — `EngineBuilder::with_agent_id(NodeId)`**

```rust
// crates/arf-engine/src/builder.rs
pub struct EngineBuilder {
    buses: Vec<Arc<Bus>>,
    cfg: AgentConfig,
    node_id: Option<NodeId>,           // NEW: 显式 agent_id
    on_member_failed: Option<Box<dyn OnMemberFailedHandler>>,
    action_subscribers: Vec<ActionMessage>,  // NEW
    on_stream_chunk: Option<Box<dyn FnMut(&ModelResponseChunk)>>,  // F-004 WONTFIX 后删
}

impl EngineBuilder {
    pub fn with_agent_id(mut self, node_id: NodeId) -> Self {
        self.node_id = Some(node_id);
        self
    }
}

// crates/arf-engine/src/engine.rs:59 重构
pub fn build(cfg: AgentConfig) -> Engine {
    let node_id = builder.node_id.unwrap_or_else(|| NodeId::new(format!("engine/{}", cfg.model.provider)));
    Engine { node_id, /* ... */ }
}
```

**F-019 — `EngineBuilder::auto_subscribe_action_messages`**

```rust
impl EngineBuilder {
    pub fn auto_subscribe_action_messages(mut self, types: &[ActionMessage]) -> Self {
        self.action_subscribers.extend_from_slice(types);
        self
    }
}

// Engine 主循环
async fn run_loop(&mut self, ...) {
    // auto-subscribe ActionMessage types → 自动 dispatch 到 self.dispatch_incoming
    for action in &self.action_subscribers {
        let filter = MessageFilter {
            types: Some(vec![action.msg_type()]),
            to_match: ToMatch::BroadcastAndDirectedToMe,
        };
        let handle = self.bus.connect(self.node_id.clone(), filter).await?;
        let me = self.clone();
        tokio::spawn(async move {
            while let Ok(msg) = handle.recv().await {
                me.dispatch_incoming(msg, action.clone()).await;
            }
        });
    }
    // ... 主 loop ...
}
```

**F-016 — `OnMemberFailedHandler.handle()` 真正调用**

```rust
// crates/arf-engine/src/engine.rs:81-92 重构
async fn on_member_offline(&self, member: NodeId, reason: OfflineReason) {
    // 1. invalidate cache
    self.resource_cache.invalidate(&member).await;

    // 2. 调 handler（NEW）
    let action = match &self.on_member_failed {
        Some(h) => h.handle(&member, &reason).await,
        None => MemberFailedAction::FailSession,  // 默认 fail session
    };

    // 3. 按 action 分支
    match action {
        MemberFailedAction::FailSession => {
            self.abort_current_session(member).await;
        }
        MemberFailedAction::SwitchTo => {
            // 重新 resolve 替代节点（依赖 C4 F-007 fix）
            if let Some(alt) = self.resolve_alternative(&member).await {
                self.resource_cache.replace(&member, &alt).await;
            } else {
                self.abort_current_session(member).await;
            }
        }
        MemberFailedAction::Retry { max_attempts, backoff } => {
            // 重试
            for attempt in 0..max_attempts {
                tokio::time::sleep(backoff).await;
                if self.resource_cache.is_online(&member).await {
                    return;
                }
            }
            self.abort_current_session(member).await;
        }
    }
}
```

**F-017 — 合并 `AgentConfig` + `ToolSpec.permission` + Engine 主循环 permission 分支**

```rust
// crates/arf-core/src/agent.rs (NEW unified AgentConfig)
pub struct AgentConfig {
    pub model: ModelDecl,
    pub system_prompt_template: String,
    pub initial_memory: Vec<ModelMessage>,
    pub allowed_paths: Vec<PathBuf>,
    pub resources: Vec<ResourceSpec>,
    pub tools: Vec<ToolSpec>,           // 来自 arf_agent::AgentConfig
    pub engine: EngineConfig,            // 来自 arf_engine::AgentConfig
}

// crates/arf-core/src/tool.rs
pub struct ToolSpec {
    pub name: String,
    pub description: String,
    pub input_schema: serde_json::Value,
    pub permission: ToolPermission,      // NEW
}

pub enum ToolPermission {
    Allow,
    Ask,
    Deny,
}

// Engine 主循环 tool_call 触发时
async fn check_tool_permission(&self, tool_name: &str) -> Result<ToolPermission, EngineError> {
    let tool = self.cfg.tools.iter().find(|t| t.name == tool_name)
        .ok_or(EngineError::UnknownTool(tool_name.into()))?;
    Ok(tool.permission.clone())
}

async fn execute_tool_call(&self, call: ToolCall) -> ToolResult {
    match self.check_tool_permission(&call.tool_name).await? {
        ToolPermission::Allow => {
            // 正常执行
            self.dispatch_tool_call(call).await
        }
        ToolPermission::Ask => {
            // 发 permission_request，等 permission_response
            let cid = Uuid::new_v4();
            self.bus.send(Message::new(
                msg_type::PERMISSION_REQUEST.into(),
                self.node_id.clone(),
                vec![/* app node id */],
                json!({"tool_name": call.tool_name, "correlation_id": cid}),
                self.bus.id,
            )).await?;
            // 等 response with timeout
            match self.wait_for_permission(cid, Duration::from_secs(60)).await {
                Some(true) => self.dispatch_tool_call(call).await,
                Some(false) | None => ToolResult::error("permission denied"),
            }
        }
        ToolPermission::Deny => {
            ToolResult::error("tool denied by config")
        }
    }
}
```

**兼容性**：
- `AgentConfig` 合并破坏性（v1.x 未稳定，OK）
- `ToolSpec.permission` 加字段（Default = Allow 向后兼容）
- Engine 主循环扩 ~30 行
- arf_agent crate 是否保留：可保留为 deprecated alias（`pub use arf_core::AgentConfig`），或彻底删

**测试**：
- `engine_supports_multiple_instances_same_provider`：2 Engine 同 provider 不同 agent_id → 都成功注册
- `engine_auto_dispatches_action_messages`：注册 peer_message auto-subscribe → Engine 自动 dispatch
- `member_failed_handler_invoked_on_offline`：node offline 触发 `OnMemberFailedHandler.handle()`
- `member_failed_action_fail_session_aborts`：handler 返回 FailSession → session abort
- `member_failed_action_switch_to_resolves_alternative`：handler 返回 SwitchTo → resolve_model 找替代节点
- `tool_permission_allow_executes_normally`：ToolSpec.permission=Allow → 直接执行
- `tool_permission_ask_blocks_until_response`：ToolSpec.permission=Ask → 发 permission_request + 等 response
- `tool_permission_deny_returns_error`：ToolSpec.permission=Deny → ToolResult.error
- 回归：现有所有 Engine 测试

---

## §11 跨 cluster 依赖与开工顺序

```
C8 (F-003) → C9 (F-015) → C5 (F-012/13/14) → C7 (F-010/11)
                                              ↓
                                             C3 (A3/A4/F-020)   ← 消息协议底层
                                              ↓
                                  C6 (F-005/06) → C4 (F-007/08)  ← 路由
                                              ↓
                                  C1 (F-002/09/21) ⚠️ CRITICAL
                                              ↓
                                  C2 (F-016/17/18/19)  ← 依赖 C3 + C4
```

**关键依赖**：
- C2.F-016 (SwitchTo action) 依赖 C4.F-007 (resolve_model 替代节点能力)
- C2.F-017 (ToolPermission permission_request message) 依赖 C3.A3-001 (msg_type 常量 `PERMISSION_REQUEST`)
- C2.F-018 (EngineBuilder.with_agent_id) 依赖 C3.A3-001 (message 协议稳定后 Engine 多实例才可靠)
- C8.F-003 (facade spawn-per-task) 为 C1.F-021 (MCPPoolNode auto-provision) 提供 pattern 借鉴

**关于 F-002 CRITICAL**：虽然排在 C2 前完成，但 **强烈建议每个 PR release 都包含 F-002 验证测试**，避免漏修。

---

## §12 向后兼容性总览

| Cluster | 破坏性变更 | 兼容策略 |
|---|---|---|
| C8 | 无（run_loop 内部重构） | API 不变；并发行为从串行变真并发 → 旧 app 可能看到"原本排队现在并发" |
| C9 | `Summarizer::summarize` trait param 类型 | trait v1.x 未稳定，OK |
| C5 | `SessionStore::save` 写 last_checkpoint（旧 app 漏 save 后 checkpoint 仍 work）；新增 `save_partial` | 旧 `save() + snapshot()` 仍 work |
| C7 | 无（新增构造器 + 字段读多） | 完全向后兼容 |
| C3 | `Message` 加 `routing` 字段（破坏性）；新增 `with_correlation_id` | ~30 处构造点需更新；现有 `Message::new` 仍可用 |
| C6 | `ModelCallPayload.model_params` 从 Option 改必有 | wire JSON 字段名不变，向后兼容 |
| C4 | `BusGraph.nodes` 类型变化；`resolve_model` 错误新增变体 | 5-10 处调用点需更新 |
| C1 | `PoolConfig` 加 3 字段；`Pool::new` 行为变化（强制 provision min_size） | trait v1.x 未稳定，OK |
| C2 | `AgentConfig` 合并；`ToolSpec.permission` 新字段；EngineBuilder API 扩展 | 旧 `arf_agent::AgentConfig` 可保留为 deprecated alias |

---

## §13 风险评估

| 风险 | 影响 | 缓解 |
|---|---|---|
| C8 spawn 无界 task | Engine spam 导致 task 爆炸 | per-facade `max_inflight: usize` semaphore |
| C3 Message API 变更面大 | ~30 处构造点遗漏 | grep `Message::new\|Message::with_from_bus` 全仓 review |
| C1 F-002 漏修 production 影响 | pool 实际无弹性，用户排队 | 每个 PR 含 F-002 验证测试；release 前 audit |
| C2 AgentConfig 合并破坏性 | 旧 app 调用 `arf_agent::AgentConfig` 编译失败 | 保留 deprecated alias + 提供 migration guide |
| C8 `NodeHandle` clone 需求 | bus 当前 handle 不可共享 | 把 NodeHandle 改为 `Arc<NodeHandle>` 共享 |

---

## §14 不在本次 fix scope 的事项

1. **F-001** → WONTFIX（认知误判，参见 §1）
2. **F-004** → WONTFIX（stream UX 由 app 层负责，参见 §1）
3. **docs/api/ 新增**"How to stream model_response_chunk to frontend" recipe → follow-up doc task
4. **app 层 N Engine × 共享 1 Pool helper** 文档化 → follow-up doc task（替代 F-001 EnginePool 抽象）
5. **Python binding 同步更新**（`py-arf/`）→ fix 完成后统一同步

---

## §15 验收清单

每个 cluster fix 完成后须验证：

- [ ] `cargo build --workspace` 通过
- [ ] `cargo test --workspace` 通过（含新增测试 + 回归测试）
- [ ] `cargo clippy --workspace` 无新警告
- [ ] 对应 audit-probe 重跑后病灶命中消失（如 9.4.1 重跑 `pool_does_not_auto_provision` 测试应通过）
- [ ] lesion-registry.md 中病灶状态改 `FIXED` + 附 fix commit hash
- [ ] CHANGELOG.md 记录 fix
- [ ] 若有破坏性 API 变更，CHANGELOG 标注 `BREAKING:` + migration guide