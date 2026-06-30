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

    /// CheckpointRule name 重复。
    #[error("CheckpointRule name 重复: {name}")]
    DuplicateRuleName { name: String },

    /// Template 含占位符但替换文本未出现。
    #[error("System prompt template 缺 {placeholder}: {reason}")]
    InvalidTemplate { placeholder: String, reason: String },

    /// Primary bus 不可用（fail to connect）。
    #[error("primary bus 连接失败: {0}")]
    PrimaryBusConnect(String),
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
}
