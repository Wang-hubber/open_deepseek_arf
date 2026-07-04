//! Engine 错误枚举（Phase 6 §4 / §5.2）。

use thiserror::Error;

#[derive(Debug, Error)]
pub enum BuildError {
    /// Strict route 的某个 NodeId 不在 BusGraph 上。
    #[error("Strict route NodeId 不在 BusGraph 上: {nodes:?}")]
    MissingNodes { nodes: Vec<String> },

    /// Discovery route 的 Capability 无任何节点匹配。
    #[error("Discovery route Capability 无任何节点匹配: {capability:?}")]
    MissingCapabilities {
        capability: std::collections::HashMap<String, String>,
    },

    /// `model_call` 既无显式 Route，且 BusGraph 上无 `node_type == "model"`
    /// 节点。Engine 会广播 model_call 后永久等待响应。修复方法：在 Bus 上
    /// 连接 ModelAdapterNode（`provider.connect_to_bus(bus, node_id)`），
    /// 或在 `AgentConfig.routes` 中显式设置 `model_call` 路由。
    #[error("no model_call responder: bus has no `node_type == \"model\"` node and no explicit `model_call` route in AgentConfig.routes — engine would broadcast and hang forever. Connect a ModelAdapterNode (`provider.connect_to_bus(bus, node_id)`) or set `routes[\"model_call\"]` explicitly.")]
    NoModelResponder,

    /// CheckpointRule name 重复。
    #[error("CheckpointRule name 重复: {name}")]
    DuplicateRuleName { name: String },

    /// F-023: auto_subscribe_message_types 含未在 `arf_core::msg_type`
    /// 常量表中的字符串。App 自定义类型需用 `msg_type` 模块常量化。
    #[error("auto_subscribe msg_type 未在 known constants 中: '{msg_type}'。known = {known:?}。如为 app 自定义类型，请用 arf_core::msg_type 模块常量或扩展常量表。")]
    UnknownAutoSubscribeType {
        msg_type: String,
        known: Vec<String>,
    },

    /// Template 含占位符但替换文本未出现。
    #[error("System prompt template 缺 {placeholder}: {reason}")]
    InvalidTemplate { placeholder: String, reason: String },

    /// Primary bus 不可用（fail to connect）。
    #[error("primary bus 连接失败: {0}")]
    PrimaryBusConnect(String),

    /// 两个 ResourceSpec 声明了同一工具名。
    #[error("ambiguous tool '{tool}': declared by both {providers:?}")]
    AmbiguousTool {
        tool: String,
        providers: Vec<String>,
    },
}

#[derive(Debug, Error)]
pub enum RunError {
    /// `state.over_view.turn_count >= max_turns`（由 6.4 完整 ReAct 检测；6.3 占位）
    #[error("超过 max_turns ({max_turns})")]
    MaxTurnsExceeded { max_turns: u32 },

    /// Cancel 触发（Phase 6 §2.P13）
    #[error("Engine stop signal received")]
    Stopped,

    /// Engine 内部错误（如 listener task 退出 / handle closed）
    #[error("Engine 内层错误: {0}")]
    Internal(String),

    /// 发送消息时 bus 返回的错误
    #[error("bus 端错误: {0}")]
    Bus(#[from] arf_core::SendError),

    /// CheckpointRule.build 输出的 msg_type() 不在 AgentConfig.routes 中
    #[error("Checkpoint 输出的 msg_type '{msg_type}' 未在 AgentConfig.routes 注册")]
    UndeclaredMsgType { msg_type: String },

    /// F-025 fix: ResponseProcessor.process() returned Err. Engine aborts
    /// the current round; the app catches this and decides retry / log /
    /// fatal. Previously this error was silently swallowed via `let _ = ...`.
    #[error("ResponseProcessor 处理 '{msg_type}' 失败: {reason}")]
    Processor { msg_type: String, reason: String },

    /// Node 掉线 / 超时且 handler 返回 FailSession。Phase 6 task 6.8.
    #[error("Agent {agent} lost member {member}: {reason}")]
    MemberFailed {
        agent: String,
        member: String,
        reason: String,
    },

    /// `run()` was called with a `SessionStore` configured but the session was
    /// never pre-saved. Persisting checkpoints for an unknown session would
    /// silently fail, so the Engine fails fast instead. Phase 9 F-012.
    #[error("session '{session_id}' not pre-saved: call SessionStore::save() before run()")]
    SessionNotPreSaved { session_id: String },

    /// A checkpoint snapshot failed to persist. Since checkpoints are the replay
    /// contract, the Engine aborts the current round rather than silently
    /// continuing with incomplete persistence. Phase 9 F-012.
    #[error("snapshot failed for session '{session_id}': {reason}")]
    SnapshotFailed { session_id: String, reason: String },
}
