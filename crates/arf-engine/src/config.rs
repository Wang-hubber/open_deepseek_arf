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

    /// System prompt 模板，**原样发送**到模型（不再做 `{{skills}}` 替换）。
    /// 详见 `docs/api/explanation/上下文拼装机制.md` — Engine 在每轮 do_model_turn
    /// 时按 [system_prompt_template, *initial_memory, skills(现采), *conversation]
    /// 顺序拼装 system prefix。
    pub system_prompt_template: String,
    /// 会话内相对稳定的记忆条目，每条作为独立 system message 注入到 system_prompt
    /// 之后、skills 之前。build 时一次性读入；运行时变更不在 v1 范围。
    pub initial_memory: Vec<String>,

    pub max_turns: u32,
    pub tool_timeout_ms: Option<u64>,

    pub permissions: PermissionConfig,

    /// 用户自定义 action message 的路由表。
    /// 键是 msg_type（如 `"my_custom_action"`），值是 `Route`。
    ///
    /// 注意：ARF 的内置 action（`model_call`、`tool_exec*`）**不需要**在这里配置 — Engine
    /// 自动按节点能力广播 `model_call`，按 tool name 查 owner 直接路由 `tool_exec`。
    /// 这些条目保留在此处仅作 build-time hint（让 `EngineBuilder.build` 在 Strict NodeId
    /// 模式下能校验节点在线）。
    ///
    /// 真正的"checkpoint 派发的用户自定义 action"应该定义自己的 msg_type（如
    /// `"summarize"`、`"send_email"`）并在此处配置路由。
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
