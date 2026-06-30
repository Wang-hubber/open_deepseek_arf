//! AgentConfig — Engine 的全量声明式配置（Phase 6 §5.2）。

use std::collections::HashMap;
use std::sync::Arc;

use arf_core::{CheckpointRule, NodeId, ResponseProcessor, Route};

/// Provider + model 标识（Phase 6 §5.2）。
#[derive(Debug, Clone)]
pub struct ModelConfig {
    pub provider: String,
    pub model: String,
}

/// 权限配置（Phase 6 §5.2）。EngineBuilder 暂不使用；6.8 集成权限系统时接入。
#[derive(Debug, Clone, Default)]
pub struct PermissionConfig {
    pub allow_paths: Vec<String>,
    pub denied_paths: Vec<String>,
}

/// 完整 Agent 配置（Phase 6 §5.2 字段集）。
///
/// **不 derive Clone/Debug/Serialize/Deserialize**：
/// - CheckpointRule 含 `Box<dyn Fn>` 闭包，不支持 Clone/Debug
/// - `Arc<dyn ResponseProcessor>` / `Arc<dyn OnMemberFailedHandler>` 不支持 Debug/Serialize
///   （dyn-trait 默认没有这些 impl）
/// 序列化（如需）由 6.8 加 `#[serde(skip)]` 或手写 `Serialize` 实现。
pub struct AgentConfig {
    pub agent_id: String,
    pub model_config: ModelConfig,

    /// System prompt 模板，含 `{{skills}}` 占位符（build 时替换）。
    pub system_prompt_template: String,
    /// build 时附加到 messages 前缀（system role）。
    pub initial_memory: Vec<String>,

    pub max_turns: u32,
    pub tool_timeout_ms: Option<u64>,

    pub permissions: PermissionConfig,

    /// msg_type → Route（单源）。同时用于 Engine 自身的 Route 决定。
    pub routes: HashMap<String, Route>,

    /// 评估顺序由 App 在 build 时决定；run() 时逐个查 when。
    pub checkpoint_rules: Vec<CheckpointRule>,

    /// 非内置 msg_type 的响应处理。model_response/tool_result 走白名单。
    pub processors: HashMap<String, Arc<dyn ResponseProcessor>>,

    /// Node 掉线 hook。None 表示默认行为（FailSession）。
    /// 6.6 / 6.8 接入 `FailedReason` 与完整 handler trait；6.3 仅占位 trait。
    pub on_member_failed: Option<Arc<dyn OnMemberFailedHandler>>,

    pub tools_include: Option<Vec<String>>,
    pub tools_exclude: Vec<String>,
    pub skills_include: Option<Vec<String>>,
    pub skills_exclude: Vec<String>,
}

/// Node 掉线 hook trait — Phase 6 §2.P8。占位，6.6 实现完整。
pub trait OnMemberFailedHandler: Send + Sync {
    fn on_member_failed(&self, _agent: NodeId, _member: NodeId, _reason: &str) {
        // 6.3 占位实现。6.6 / 6.8 改写为返回 `MemberFailedAction`。
    }
}

impl Default for AgentConfig {
    fn default() -> Self {
        Self {
            agent_id: "agent".into(),
            model_config: ModelConfig {
                provider: "deepseek".into(),
                model: "deepseek-v4-flash".into(),
            },
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
