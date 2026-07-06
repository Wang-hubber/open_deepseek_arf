//! AgentConfig — Engine 的全量声明式配置（Phase 7 §2）。

use std::collections::HashMap;
use std::sync::Arc;

use arf_agent::{ModelDecl, ResourceSpec};
use arf_core::{CheckpointRule, NodeId, ResponseProcessor, Route};

// ── EngineConfig ──────────────────────────────────────────────────────────

/// Engine 运行期配置——嵌套在 AgentConfig 内。
pub struct EngineConfig {
    /// 仅存自定义 msg_type 的路由（checkpoint 派发的 "summarize" / "send_email" 等）。
    /// model_call / tool_exec 的路由由 Registry 从 resources 推导。
    #[allow(unused)]
    pub routes: HashMap<String, Route>,

    #[allow(unused)]
    pub checkpoint_rules: Vec<CheckpointRule>,

    /// 非内置 msg_type 的响应处理。model_response/tool_result 走白名单。
    pub processors: HashMap<String, Arc<dyn ResponseProcessor>>,

    /// Node 掉线 hook。None = FailSession。
    pub on_member_failed: Option<Arc<dyn OnMemberFailedHandler>>,

    pub max_turns: u32,
    pub tool_timeout_ms: Option<u64>,

    /// Task 19: LRU capacity for `InboundDedupCache`. Default: 1024.
    /// Sized for typical session lifetimes; raise for high-throughput async
    /// reply patterns.
    pub inbound_dedup_capacity: usize,
}

impl Default for EngineConfig {
    fn default() -> Self {
        Self {
            routes: HashMap::new(),
            checkpoint_rules: vec![],
            processors: HashMap::new(),
            on_member_failed: None,
            max_turns: 10,
            tool_timeout_ms: Some(30_000),
            inbound_dedup_capacity: 1024,
        }
    }
}

// ── OnMemberFailedHandler ─────────────────────────────────────────────────

#[derive(Debug, Clone, PartialEq)]
pub enum MemberFailedAction {
    FailSession,
    Retry { delay_ms: u64 },
    SwitchTo { alternative: NodeId },
}

impl Default for MemberFailedAction {
    fn default() -> Self { Self::FailSession }
}

pub trait OnMemberFailedHandler: Send + Sync {
    fn handle(&self, agent: &NodeId, member: &NodeId, reason: &str) -> MemberFailedAction;
}

impl<F> OnMemberFailedHandler for F
where
    F: Fn(&NodeId, &NodeId, &str) -> MemberFailedAction + Send + Sync,
{
    fn handle(&self, agent: &NodeId, member: &NodeId, reason: &str) -> MemberFailedAction {
        self(agent, member, reason)
    }
}

// ── AgentConfig ───────────────────────────────────────────────────────────

/// 完整 Agent 配置——声明 + 运行期。
///
/// **不 derive Clone/Debug/Serialize/Deserialize**：
/// - CheckpointRule 含 `Box<dyn Fn>` 闭包
/// - `Arc<dyn ResponseProcessor>` / `Arc<dyn OnMemberFailedHandler>` 不支持 Debug/Serialize
pub struct AgentConfig {
    /// 单模型声明。
    pub model: ModelDecl,

    /// 统一资源声明。
    /// - node_type="mcp"          → Engine 提取 tools/skills 子集
    /// - node_type="mcp/pool"     → NodePool（内部 sub-bus）
    /// - 其他 node_type            → 自定义节点，存入路由表
    pub resources: Vec<ResourceSpec>,

    pub system_prompt_template: String,
    /// 会话内相对稳定的记忆条目。build 时一次性读入；运行时变更不在 v1 范围。
    pub initial_memory: Vec<String>,
    pub allowed_paths: Vec<String>,

    /// Tool-level permission overrides (Phase 9 F-017). When the LLM emits a
    /// `tool_call` for `name`, the Engine looks up its `permission` here:
    /// - `Allow` (default): proceed with `tool_exec` as usual
    /// - `Ask`: send `permission_request` to bus, await `permission_response`,
    ///   then either proceed (`allow` reply) or short-circuit (`deny`)
    /// - `Deny`: short-circuit immediately with a `tool_result` error
    pub tools: Vec<arf_core::ToolSpec>,

    pub engine: EngineConfig,
}

impl Default for AgentConfig {
    fn default() -> Self {
        Self {
            model: ModelDecl {
                provider: "deepseek".into(),
                model_name: "deepseek-v4-flash".into(),
                ..Default::default()
            },
            resources: vec![],
            system_prompt_template: "You are a helpful assistant.".into(),
            initial_memory: vec![],
            allowed_paths: vec![],
            tools: vec![],
            engine: EngineConfig {
                routes: HashMap::new(),
                checkpoint_rules: vec![],
                processors: HashMap::new(),
                on_member_failed: None,
                max_turns: 10,
                tool_timeout_ms: Some(30_000),
                inbound_dedup_capacity: 1024,
            },
        }
    }
}
