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

/// Engine-driven action when a Node fails (node_offline or timeout).
/// Phase 6 task 6.8.
#[derive(Debug, Clone, PartialEq)]
pub enum MemberFailedAction {
    /// Fail the current session (default behavior). Engine returns `Err(MemberFailed)`.
    FailSession,
    /// Mark the WaitEvent as failed and continue with partial responses.
    /// 6.8 simplified: not actually retried; 6.x full retry semantics.
    Retry { delay_ms: u64 },
    /// Switch to a different NodeId for future requests.
    /// 6.8 simplified: intent recorded only; 6.x full switch implementation.
    SwitchTo { alternative: NodeId },
}

impl Default for MemberFailedAction {
    fn default() -> Self {
        Self::FailSession
    }
}

/// Node failure handler — invoked by Engine when a member goes offline or times out.
/// Phase 6 task 6.8.
pub trait OnMemberFailedHandler: Send + Sync {
    fn handle(
        &self,
        agent: &NodeId,
        member: &NodeId,
        reason: &str,
    ) -> MemberFailedAction;
}

/// Blanket impl: closures are valid handlers (App ergonomics).
impl<F> OnMemberFailedHandler for F
where
    F: Fn(&NodeId, &NodeId, &str) -> MemberFailedAction + Send + Sync,
{
    fn handle(&self, agent: &NodeId, member: &NodeId, reason: &str) -> MemberFailedAction {
        self(agent, member, reason)
    }
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
    /// 6.8 完整实现：返回 `MemberFailedAction`。
    pub on_member_failed: Option<Arc<dyn OnMemberFailedHandler>>,

    pub tools_include: Option<Vec<String>>,
    pub tools_exclude: Vec<String>,
    pub skills_include: Option<Vec<String>>,
    pub skills_exclude: Vec<String>,
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
