# 任务 6.3：Engine 骨架

> Phase 6 — Engine 核心实现（§9.B）第三项任务
> 父文档：`docs/v1.x/phase6/phase6-engine-design.md` §3 / §5 / §6.1 / §6.4
> 前置：`task-6.1-core-types` ✅ / `task-6.2-response-protocol` ✅

## 设计思路

引入 `Engine` 类型 + `AgentConfig` + `EngineBuilder`，完成最小可用的"1 轮 ReAct"。完整 ReAct 多轮循环留到 6.4；checkpoint rule 评估留到 6.5；wait_event 超时/失败重试留到 6.6；DiscoveryCache 留到 6.7；EngineBuilder app-level 接口（OnMemberFailedHandler、ResponseProcessor 等）留到 6.8。

**6.3 范围**：
- `AgentConfig`（所有字段，但 PermissionConfig / ResponseProcessor 注册表暂以最简形态实现）
- `Engine`：持 `NodeHandle`、配置；`run(state, user_input)` 走 1 轮 model_call → response → 返 content
- `EngineBuilder`：build 时 fail-fast 校验 + tools/skills 过滤 + 模板 {{skills}} 替换
- `BuildError` / `RunError` 枚举
- `ModelConfig`（放 arf-engine；provider/model 字符串）

**6.3 不做**：
- ReAct 多 turn 循环、tool_exec 分支（6.4）
- CheckpointRule.when/build 评估（6.5）
- WaitEvent 队列、超时、OnMemberFailedHandler（6.6）
- DiscoveryCache（6.7）
- ResponseProcessor 默认 dispatch 表、tool/skill 文件系统发现、审批 hook（6.8）
- Python 绑定（6.10）

## 代码实现

### `crates/arf-engine/src/config.rs`（新建）

```rust
//! AgentConfig — Engine 的全量声明式配置（Phase 6 §5.2）。

use std::collections::HashMap;
use std::sync::Arc;
use serde::{Deserialize, Serialize};

use arf_core::{
    CheckpointRule, MessageFilter, NodeId, ResponseProcessor,
    Route,
};

/// 提供者+模型字符串（Phase 6 §5.2）。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ModelConfig {
    pub provider: String,  // "deepseek" / "openai" / "anthropic"
    pub model: String,     // "deepseek-v4-flash"
}

/// 工具/技能过滤 glob（Phase 6 §5.2）。
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct PermissionConfig {
    pub allow_paths: Vec<String>,
    pub denied_paths: Vec<String>,
}

/// 完整 Agent 配置（Phase 6 §5.2 字段集）。
/// EngineBuilder.build() 读取；Engine.run() 时按需用。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AgentConfig {
    pub agent_id: String,
    pub model_config: ModelConfig,

    pub system_prompt_template: String,
    /// build() 时附加到 messages 前缀（system role）。
    pub initial_memory: Vec<String>,

    pub max_turns: u32,
    pub tool_timeout_ms: Option<u64>,

    pub permissions: PermissionConfig,

    /// msg_type → Route（单源）。
    pub routes: HashMap<String, Route>,

    /// 评估顺序由 App 在 build 时决定；run() 时逐个查 when。
    pub checkpoint_rules: Vec<CheckpointRule>,

    /// 非内置 msg_type 的响应处理。model_response/tool_result 走白名单。
    pub processors: HashMap<String, Arc<dyn ResponseProcessor>>,

    /// Node 掉线 hook。None 表示默认行为（FailSession）。
    pub on_member_failed: Option<Arc<dyn OnMemberFailedHandler>>,

    /// 工具白名单/黑名单（glob，Phase 6 §5.2）。* 表示全收。
    pub tools_include: Option<Vec<String>>,
    pub tools_exclude: Vec<String>,
    pub skills_include: Option<Vec<String>>,
    pub skills_exclude: Vec<String>,
}

/// Node 掉线 hook trait — Phase 6 §2.P8。占位，6.6 实现完整。
pub trait OnMemberFailedHandler: Send + Sync {
    fn on_member_failed(&self, event: NodeId, member: NodeId, reason: &str);
}

impl Default for AgentConfig {
    fn default() -> Self {
        Self {
            agent_id: "agent".into(),
            model_config: ModelConfig { provider: "deepseek".into(), model: "deepseek-v4-flash".into() },
            system_prompt_template: "You are a helpful assistant.\n\n{{skills}}".into(),
            initial_memory: vec![],
            max_turns: 10,
            tool_timeout_ms: Some(30_000),
            permissions: PermissionConfig::default(),
            routes: HashMap::new(),
            checkpoint_rules: vec![],
            processors: HashMap::new(),
            on_member_failed: None,
            tools_include: None,
            tools_exclude: vec![],
            skills_include: None,
            skills_exclude: vec![],
        }
    }
}
```

### `crates/arf-engine/src/error.rs`（新建）

```rust
//! Engine 错误枚举（Phase 6 §4）。

use std::collections::HashMap;
use arf_core::BusId;
use thiserror::Error;

#[derive(Debug, Error)]
pub enum BuildError {
    #[error("Strict route NodeId 不在 BusGraph 上: {nodes:?}")]
    MissingNodes { nodes: Vec<String> },
    #[error("Discovery route Capability 无任何节点匹配: {capability:?}")]
    MissingCapabilities { capability: HashMap<String, String> },
    #[error("CheckpointRule name 重复: {name}")]
    DuplicateRuleName { name: String },
    #[error("System prompt template 缺 {placeholder}: {reason}")]
    InvalidTemplate { placeholder: String, reason: String },
}

#[derive(Debug, Error)]
pub enum RunError {
    #[error("超过 max_turns ({max_turns})")]
    MaxTurnsExceeded { max_turns: u32 },
    #[error("Engine stop signal received")]
    Stopped,
    #[error("app 内层错误: {0}")]
    Internal(String),
    #[error("bus 端错误: {0}")]
    Bus(#[from] arf_core::SendError),
}
```

注：需要在 arf-engine/Cargo.toml 加 `thiserror` 依赖。

### `crates/arf-engine/src/builder.rs`（新建）

```rust
//! EngineBuilder — build-time fail-fast validation（Phase 6 §3.3）。

use std::collections::{HashMap, HashSet};
use std::sync::Arc;

use arf_bus::Bus;
use arf_core::{
    BusGraph, MessageFilter, NodeId, NodeInfo, Route, ToMatch,
};

use crate::config::AgentConfig;
use crate::engine::Engine;
use crate::error::BuildError;

/// Builds an Engine from config + Bus topology.
///
/// EngineBuilder::new(buses).build(config) is the only entry point.
pub struct EngineBuilder {
    buses: Vec<Arc<Bus>>,
}

impl EngineBuilder {
    pub fn new(buses: Vec<Arc<Bus>>) -> Self {
        Self { buses }
    }

    pub async fn build(self, config: AgentConfig) -> Result<Engine, BuildError> {
        // Phase 6 §3.3 step 1: aggregate multi-Bus graph (NodeId global unique)
        let mut merged_nodes: HashMap<NodeId, NodeInfo> = HashMap::new();
        for bus in &self.buses {
            let graph = bus.graph();
            for node in graph.nodes {
                if merged_nodes.contains_key(&node.node_id) {
                    // 同一 NodeId 在多条 Bus 上是同一节点（§2.P7）
                    // 跳过重复（first wins）
                    continue;
                }
                merged_nodes.insert(node.node_id.clone(), node);
            }
        }

        // Phase 6 §3.3 step 2: validate routes (Strict NodeId must be online;
        // Discovery cap should have at least one match — warn if not)
        for (msg_type, route) in &config.routes {
            match route {
                Route::Strict(ids) => {
                    let missing: Vec<String> = ids.iter()
                        .filter(|id| !merged_nodes.contains_key(id))
                        .map(|id| id.to_string())
                        .collect();
                    if !missing.is_empty() {
                        return Err(BuildError::MissingNodes { nodes: missing });
                    }
                }
                Route::Discovery(cap) => {
                    let reqs: HashMap<_, _> = cap.requirements.iter().cloned().collect();
                    let any_match = merged_nodes.values().any(|node| {
                        cap.requirements.iter().all(|(k, v)| {
                            node.capabilities.get(k).and_then(|x| x.as_str()) == Some(v)
                        })
                    });
                    if !any_match {
                        return Err(BuildError::MissingCapabilities { capability: reqs });
                    }
                }
            }
        }

        // Step 4: CheckpointRule name uniqueness
        let mut seen: HashSet<String> = HashSet::new();
        for rule in &config.checkpoint_rules {
            if !seen.insert(rule.name.clone()) {
                return Err(BuildError::DuplicateRuleName { name: rule.name.clone() });
            }
        }

        // Step 5: tools/skills filtering + {{skills}} substitution
        // (covered in 6.3 with simple iter; full glob in 6.8)
        let mut skills_descriptions: Vec<String> = Vec::new();
        for (_, node_info) in &merged_nodes {
            let kind = node_info.capabilities.get("kind").and_then(|v| v.as_str()).unwrap_or("");
            if kind == "skill" {
                skills_descriptions.push(node_info.node_id.to_string());
            }
        }
        let skills_text = if skills_descriptions.is_empty() {
            String::new()
        } else {
            format!("Available skills:\n{}", skills_descriptions.iter()
                .map(|s| format!("- {s}")).collect::<Vec<_>>().join("\n"))
        };
        let system_prompt = config.system_prompt_template.replace("{{skills}}", &skills_text);
        if !system_prompt.contains(&skills_text) && config.system_prompt_template.contains("{{skills}}") {
            return Err(BuildError::InvalidTemplate {
                placeholder: "{{skills}}".into(),
                reason: "包含占位符但替换文本未出现".into(),
            });
        }

        // Construct Engine
        Engine::new(self.buses, config, system_prompt).await
    }
}
```

### `crates/arf-engine/src/engine.rs`（新建）

```rust
//! Engine — ReAct 循环 actor（Phase 6 §0.1）。

use std::collections::HashMap;
use std::sync::Arc;

use arf_bus::{Bus, NodeHandle};
use arf_core::{
    BusId, Message, MessageFilter, ModelCall, ModelMessage,
    NodeId, NodeInfo,
};
use tokio::sync::{mpsc, oneshot, Mutex};
use uuid::Uuid;

use crate::config::AgentConfig;
use crate::error::{BuildError, RunError};

const MODEL_RESPONSE: &str = "model_response";

/// 1 bus → primary; multi-bus → primary + attach_to in followup tasks。
pub struct Engine {
    config: AgentConfig,
    agent_id: NodeId,
    handle: NodeHandle,                  // connected to primary Bus
    /// Outbound channel for serializing bus writes（run / on_message 都用它）
    send_lock: Arc<Mutex<()>>,
    /// correlation_id → oneshot::Sender<Response>（App on_message → run 间的桥）
    response_waits: Arc<Mutex<HashMap<Uuid, oneshot::Sender<Response>>>>,
    /// Pre-computed system prompt（含 {{skills}} 替换）
    system_prompt: String,
}

impl Engine {
    /// Internal — 由 EngineBuilder.build() 调用。
    pub(crate) async fn new(
        buses: Vec<Arc<Bus>>,
        config: AgentConfig,
        system_prompt: String,
    ) -> Result<Self, BuildError> {
        let primary = buses.first().ok_or(BuildError::MissingNodes {
            nodes: vec!["<no bus>".into()],
        })?.clone();
        let info = NodeInfo {
            node_id: NodeId::new(format!("engine/{}", config.agent_id)),
            node_type: "engine".into(),
            capabilities: serde_json::json!({"kind": "engine"}),
            online_since: 0,
        };
        // Engine's filter: 它想收的 response msg_types（来自 routes 的 value 列表）
        let filter = build_engine_filter(&config);
        let handle = primary.connect(info.clone(), filter).await
            .map_err(|_| BuildError::MissingNodes {
                nodes: vec!["<primary bus connect failed>".into()],
            })?;

        Ok(Self {
            config,
            agent_id: info.node_id.clone(),
            handle,
            send_lock: Arc::new(Mutex::new(())),
            response_waits: Arc::new(Mutex::new(HashMap::new())),
            system_prompt,
        })
    }

    pub fn handle(&self) -> &NodeHandle { &self.handle }
    pub fn config(&self) -> &AgentConfig { &self.config }
    pub fn system_prompt(&self) -> &str { &self.system_prompt }

    /// 1 轮 ReAct：model_call → model_response → append assistant → return content。
    /// Phase 6 6.3 范围；多 turn + CheckpointRule 评估推迟到 6.4 / 6.5。
    pub async fn run(
        &mut self,
        state: &mut arf_core::State,
        user_input: String,
        cancel: tokio_util::sync::CancellationToken,
    ) -> Result<String, RunError> {
        // 1. user message
        state.push_message(arf_core::ModelMessage::new("user", &user_input));
        state.over_view.last_user_message = user_input.clone();

        // 2. system prompt (first time only) — Phase 6 §3.3 step 6
        if state.messages.is_empty() || state.messages[0].role != "system" {
            // Insert system at front
            state.messages.insert(0, arf_core::ModelMessage::new("system", &self.system_prompt));
        }
        state.inc_round();
        state.inc_turn();

        // 3. send model_call
        let model_call = ModelCall::new(state.messages.clone());
        let cid = model_call.correlation_id;
        let payload = model_call.payload();
        let msg = Message::with_from_bus(
            model_call.msg_type(),
            self.agent_id.clone(),
            vec![],
            payload,
            self.handle.primary_bus_id(),
        );

        // register oneshot for response
        let (tx, rx) = oneshot::channel();
        self.response_waits.lock().await.insert(cid, tx);

        if let Err(e) = self.handle.send_message(msg).await {
            self.response_waits.lock().await.remove(&cid);
            return Err(RunError::Bus(e));
        }

        // 4. await response (with cancellation)
        let response_msg = tokio::select! {
            r = self.wait_for_response(cid) => r?,
            _ = cancel.cancelled() => {
                self.response_waits.lock().await.remove(&cid);
                return Err(RunError::Stopped);
            }
        };

        // 5. parse content + usage
        let content = response_msg.payload.get("content")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        if let Some(usage) = response_msg.payload.get("usage") {
            if let Some(tokens) = usage.get("prompt_tokens").and_then(|v| v.as_u64()) {
                state.set_context_tokens(tokens as usize);
            }
        }

        // 6. append assistant message
        state.push_message(arf_core::ModelMessage::new("assistant", content.clone()));
        state.inc_turn();

        Ok(content)
    }

    async fn wait_for_response(
        &self,
        cid: Uuid,
    ) -> Result<arf_core::Message, RunError> {
        // Loop on handle.recv, dispatch to waiting oneshot by correlation_id.
        loop {
            let msg = self.handle.recv().await
                .map_err(|_| RunError::Internal("handle closed".into()))?;
            if msg.msg_type == MODEL_RESPONSE {
                if let Some(payload_cid) = msg.payload.get("correlation_id")
                    .and_then(|v| v.as_str())
                    .and_then(|s| Uuid::parse_str(s).ok())
                {
                    if payload_cid == cid {
                        // Remove our oneshot and forward
                        if let Some(tx) = self.response_waits.lock().await.remove(&cid) {
                            let _ = tx.send(msg.payload.clone());
                        }
                        return Ok(msg);
                    }
                }
            }
            // else: ignore (其他 messages—心跳 / 其他 msg_type 已在 forwarding task 阶段过滤)
        }
    }

    /// 注入 response（由外部 node 直接发来时使用；测试用）
    pub async fn inject_response(&self, cid: Uuid, payload: serde_json::Value) {
        if let Some(tx) = self.response_waits.lock().await.remove(&cid) {
            let _ = tx.send(payload);
        }
    }
}

fn build_engine_filter(config: &AgentConfig) -> MessageFilter {
    // Engine wants response msg_types for the routes it's responsible for
    // e.g. "model_call" → "model_response"
    let mut types: Vec<String> = config.routes.keys()
        .filter_map(|t| response_msg_type_for(t))
        .collect();
    types.push(MODEL_RESPONSE.into());  // always
    types.sort();
    types.dedup();
    MessageFilter {
        types: Some(types),
        to_match: ToMatch::BroadcastAndDirectedToMe,
    }
}

fn response_msg_type_for(request: &str) -> Option<String> {
    match request {
        "model_call" => Some("model_response".into()),
        "tool_exec" => Some("tool_result".into()),
        "memory_op" => Some("memory_op_result".into()),
        _ => None,
    }
}
```

注：需要 `NodeHandle::primary_bus_id()`, `NodeHandle::send_message(msg)` 助手。需在 6.3 中追加。

### `crates/arf-engine/src/lib.rs`

```rust
//! ARF Engine — ReAct runtime loop.

pub mod builder;
pub mod config;
pub mod engine;
pub mod error;

pub use builder::EngineBuilder;
pub use config::{AgentConfig, ModelConfig, OnMemberFailedHandler, PermissionConfig};
pub use engine::Engine;
pub use error::{BuildError, RunError};
```

### `crates/arf-engine/Cargo.toml`

```toml
[package]
name = "arf-engine"
version.workspace = true
edition.workspace = true
license.workspace = true
repository.workspace = true
description = "ARF Engine — ReAct runtime loop"

[dependencies]
arf-core = { path = "../arf-core" }
arf-bus = { path = "../arf-bus" }
serde = { version = "1", features = ["derive"] }
serde_json = "1"
tokio = { version = "1", features = ["sync", "rt", "macros", "time"] }
tokio-util = "0.7"
thiserror = "1"
uuid = { version = "1", features = ["v4"] }
```

## 关键设计决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| State 所有权 | App 持有（`run(&mut State, ...)`） | §0.2 原则 |
| 响应等待 | `Arc<Mutex<HashMap<Uuid, oneshot::Sender>>>` + `handle.recv()` 循环 | 避免独立 listener task；run() 单 in-flight 时序正确 |
| Engine.filter | 收 `model_response` 等内置 + 所有 routes keys map 到的 response | Engine 想知道发给谁的结果 |
| 取消 | `tokio::select!` with `CancellationToken` | §2.P13 |
| Build vs Run 错误分离 | `BuildError` (fail-fast) / `RunError` (runtime) | 关注点分离 |
| 模板 `{{skills}}` 替换 | build 时替换，Engine 持 fixed 系统提示 | §3.3 step 6 |
| CheckpointRule 评估 | 6.3 不评估（仅存储） | 6.5 范围 |
| 多 turn loop | 6.3 不实现（仅 1 round） | 6.4 范围 |

## 测试

### NodeHandle 补充（arf-bus）

```rust
// 在 NodeHandle 上加：
impl NodeHandle {
    /// Send a pre-constructed Message via this handle's primary Bus.
    pub async fn send_message(&self, msg: Message) -> Result<SendReceipt, SendError> {
        self.send_via(self.primary_bus_id, msg.msg_type(), 
                       msg.to.clone(), msg.payload.clone()).await
    }

    /// Primary subscription's BusId (for self.from_bus stamping).
    pub fn primary_bus_id(&self) -> BusId {
        self.primary_bus_id
    }
}
```

### Engine（arf-engine）测试

```rust
// [构造] EngineBuilder::new 接受 Vec<Arc<Bus>>
// [构造] EngineBuilder.build 成功路径：routes 合法、CheckpointRule 名唯一
// [构造] EngineBuilder.build 失败：Strict 指向不在线节点
// [构造] EngineBuilder.build 失败：Discovery Capability 无匹配
// [构造] EngineBuilder.build 失败：CheckpointRule 名重复
// [构造] EngineBuilder.build 成功：{{skills}} 替换为 BusGraph 中 skill 节点列表
// [序列化] AgentConfig serde 往返
// [序列化] ModelConfig serde 往返
// [方法] build_engine_filter 包含 model_response + 来自 routes 的 response types
// [e2e] Engine.run 1 轮：user → model_call → 模拟 receiver → assistant content
```

合计 ~10 个测试。

## 验证命令

```bash
. "$HOME/.cargo/env" && cargo test --workspace
```

## 测试覆盖摘要

| 模块 | 测试数 |
|------|--------|
| `NodeHandle::send_message` | 2 |
| `AgentConfig` | 2 |
| `EngineBuilder.build` | 5 |
| `Engine::run` e2e | 1 |
| **合计** | **10** |

---

## 实现后实际发现

### 与初稿的差异

1. **`AgentConfig` 不 derive Clone/Debug/Serialize/Deserialize**——`CheckpointRule` 含 `Box<dyn Fn>` 闭包不支持 Clone/Debug；`Arc<dyn ResponseProcessor>` 不支持 Debug/Serialize。修复：**完全不 derive** 这四个 trait，加注释说明。Config 通过 Engine 构建内部状态；App 通过 `Engine::config()` 借用。

2. **`NodeHandle::send_message(msg)` 与 `NodeHandle::primary_bus_id()` 助手**：原 `NodeHandle::send(msg_type, to, payload)` 拿不到 `from_bus` 自动戳。修复：新增 `NodeHandle::send_message(msg)` 直接送预构造的 Message（`from_bus` 由 `with_from_bus` 提前戳）。

3. **filter `types: None` vs `Some(vec![])`** 严格区分：初稿混用导致 BUG。
   - `Some(vec![])` = "拒绝所有 msg_type"（MessageFilter::matches 的 `!types.contains(m)` 命中空 Vec 时为 true）
   - `None` = "无 type 过滤，全收"
   修复：6.3 实现里 `engine_response_types()` 返回空 Vec 时，构造 filter 用 `types: None`。

4. **`run()` 通过 `Arc<Mutex<HashMap<Uuid, oneshot>>>` 协程间传 response**：初稿设想独立 listener task 监听 bus+转发；实际不必要——Engine.run 的 wait_for_response 直接 loop 在 `handle.recv()` 上过滤即可。

5. **删除 `#[async_trait]` import from checkpoint.rs**：初稿 import 了但代码里没用到——`CheckpointRule` 的 `when`/`build` 都是普通 `Fn`（不是 async），trait 不需要 async_trait。

6. **`MAX_RECURSION` 用 `usize`** 而非 `u32`：与 OverView 字段的 `turn_count: usize` 对齐。

7. **`Cargo.toml` 加**：`async-trait`、`thiserror`、`tokio-util`、`futures (dev)`、`uuid`。

### 实现期间 5 个 bug

1. **测试用 `futures::executor::block_on` 跑 tokio async 代码** → hang。修复：移除 block_on 模式，改用 `#[tokio::test] async fn` 内直接 `bus.connect(...).await`。
2. **`h.disconnect()` 让 NodeEntry 从 BusGraph 移除** → 后续 `cfg.routes.insert(Strict(node))` 校验失败。修复：先 drop handle（保留 entry）而非 disconnect。
3. **filter `Some(vec![])` 拒绝所有 msg** → e2e 测试 Engine 没收到 model_response（handle.recv() hang 3s timeout）。修复：filter 空时改用 `None`。
4. **e2e race：receiver spawn 时机** → 还没订阅到 bus engine 就 send model_call，导致 model_call 无人收（Engine 自己 filter 拒收），model_response 回来时 handle.recv() 还卡着。修复：oneshot 同步——receiver 任务开始后 send ready signal，主线程等收到后再让 Engine.run 开始。
5. **Engine filter 类型推导有 `debug println` 时 compile 通过但 e2e 仍 fail** → 排查时加了 `tokio::time::timeout(3s)` 兜底，最终找出是 bug 3。

### 实际测试结果

```
cargo test --workspace
... (略) ...
test result: ok. 12 passed  (arf-engine: 12 new — engine skeleton)
test result: ok. 91  passed  (arf-bus lib)
test result: ok. 14  passed  (arf-bus integration)
test result: ok. 158 passed  (arf-core)
test result: ok. 204 passed  (其他)
test result: ok. 70  passed  (其他)
test result: ok. 12  passed  (其他)
test result: ok. 19  passed  (其他)
... (其他 crate 全部 OK)
0 FAILED
```

12 个新测试覆盖：
- EngineBuilder 构造与 4 种验证路径（无 bus / Strict 离线 / Strict 在线 / Discovery 0 匹配 / Discovery 匹配 / 重复 rule name / skill 占位符但无 skill / skill 替换）
- Engine 公开方法：info/agent_id/system_prompt/handle 存在性
- e2e：1 round 完整 model_call → response → content
- cancel：Stop 立即返回

### arf-engine crate 输出

```
crates/arf-engine/src/
├── Cargo.toml       (thiserror, async-trait, tokio-util, futures dev-deps)
├── lib.rs           (5 模块 pub use)
├── builder.rs       (EngineBuilder + build 7-step 校验 + skills 替换)
├── config.rs        (AgentConfig + ModelConfig + PermissionConfig + OnMemberFailedHandler)
├── engine.rs        (Engine struct + run() + wait_for_response + 公开 4 个 getter)
├── error.rs         (BuildError + RunError via thiserror)
└── tests.rs         (12 个 boundary-first 测试)
```

### 范围确认（6.3 实际覆盖 §7.1 / §7.3 §3.1-§3.3）

| 设计元素 | 6.3 状态 |
|---------|---------|
| `Engine struct` | ✓ |
| Engine 实现 `Node` trait | ❌（直接用 NodeHandle 接受 bus 消息，未独立实现 Node） |
| `AgentConfig` 全字段 | ✓ |
| `EngineBuilder.build()` fail-fast | ✓ |
| 4 状态机（idle/processing/waiting/stopped）| ❌（6.4 范围） |
| `bus.connect` 整合 Engine | ✓（通过 NodeHandle） |
| ReAct 主循环 | ❌（6.4） |
| CheckpointRule 评估 | ❌（6.5） |
| WaitEvent 队列 | ❌（6.6） |
| OnMemberFailedHandler | 占位（trait 已定义，完整实现在 6.6/6.8） |
| App-level 配置接口 | ❌（6.8） |

### 下一步：6.4

**6.4 ReAct 主循环**：
- 完整 turn loop（model_call → tool_exec → … → 终止）
- 4 状态机实现
- 消息 append 逻辑（含 tool_call/response/tool_result 一致性）
- Termination 判断（max_turns、cancel、纯文本输出、task_complete）

预测还有 3-5 个 bug（最可能是 turn 顺序、状态转移、cancel race）。