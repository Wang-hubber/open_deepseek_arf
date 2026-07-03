# Phase 9 病灶登记册（Lesion Registry）

> **用途**：统一收集 phase 9 各 task 探查跑出的**待修复病灶**，供后续 fix phase 使用。
> 本文档是 phase 9 病灶的**单一权威汇总**——各 `audit-probe-9.X.Y.md` 的 §D 病灶登记在此**去重汇总**。
>
> **与 spec 的关系**：
> - 病灶判定规则见父 spec `capability-matrix-and-audit-design.md` §4（四信条 + find signals + §4.3 登记 schema）
> - 每个 task 的 `audit-probe-9.X.Y.md` 是病灶的**首次现场登记**；本册是**跨 task 汇总 + 状态跟踪**
>
> **更新约定**：
> - 每当某 task 的 audit-probe 判出"信号构成病灶 Y"，将该病灶**追加**到本册（总表 + 详情）
> - 病灶 ID 按信条分组顺序编号（A1-00N / A2-00N / A3-00N / A4-00N），全 phase 9 唯一、不复用
> - `状态` 字段：`OPEN`（待修复）/ `FIXED`（fix phase 已修，附 commit）/ `WONTFIX`（评估后不修，附理由）
> - 本册**不含时间字段**（遵循 spec §5.2）

---

## §1 病灶总表

| 病灶 ID | 信条 | Signal | 触发 task | 命中摘要 | 状态 | 修复归属 |
|---|---|---|---|---|---|---|
| **A4-001** | A4 处理集中 | A4-S4（convert 散落） | 9.1.4（barrier）；9.2.1 精确化；9.2.2 真实 payload 复测 | `correlation_id` Uuid↔JSON string 转换散落；**typed 访问器 Message::correlation_id 已存在却未一致采用**（engine.rs:689 仍手挖）；9.2.2 真实 DashScope qwen 端到端下匹配工作（tool loop 5 消息实证），病灶形态未在真实流量下恶化 | **FIXED（81280dd）** | 后续 fix phase |
| **A3-001** | A3 数据唯一 | A3-S1（同名标识跨 crate） | 9.1.5（异常）；9.2.1 加剧；9.2.2 真实 payload 复测 | lifecycle + model_call/model_response 消息类型名裸字面量散落 arf-bus/core/engine/model-adapter，局部 const 摆设，无跨 crate 声明；9.2.2 真实 payload 下路由工作（tool message 正确归位），病灶形态未在真实流量下恶化 | **FIXED（81280dd）** | 后续 fix phase |
| **F-001** | (F-category) | 缺 primitive | 9.4.1（pool facade） | **framework 缺 `EnginePool` 抽象**——N 个 Engine 共享 model config 的 production 场景，framework 需 app 层用 "N facade 共享 1 pool" 模式手动 virtualize。Engine::new 时 `NodeId = "engine/{provider}"`（engine.rs:59），多 Engine 同 provider 必然 NodeId 冲突。**production 真实需求**（user 2026-07-03 round 3） | **OPEN** | 后续 fix phase / 独立 task |
| **F-002** | (F-category) | 缺 primitive | 9.4.1（pool facade） | **framework 实现偏离设计意图（CRITICAL）**——pool 设计意图：`min_size` + `max_size` + `auto_provision`（load 增长时自动扩容），超 max_size 才开始排队；当前实现只有 fixed `max_size`，**无 min_size、无 auto-provision**，load 来时只能 Block/Queue/Reject。**不是隐藏 BUG，是 design 文档明示的 dynamic expansion code 完全没做**（user 2026-07-03 round 5 判定）。**production 真实需求**：N 用户同时咨询时，pool 需扩到 N（≤ max_size）才能保证所有用户不排队 | **OPEN（CRITICAL）** | 后续 fix phase / 独立 task |
| **F-003** | (F-category) | framework 设计 quirk | 9.4.1（pool facade） | **Facade 的 sub_id 模式阻断 ModelAdapterNode 集成**——`ModelAdapterPoolNode::connect` 在 sub-bus 注册 listener `node_id = "model/pool-{i}/sub"`（pool_node.rs:65）；facade forward model_call 时 `to=this sub_id`；任何想在此 id 注册 `ModelAdapterNode` 会被 bus 拒绝（`AlreadyConnected`）。**唯一可工作的 sub-bus handler 是 manual broadcast subscriber**（如 `crates/arf-pool/tests/integration.rs` 既有 pattern）。后果：N 个 facade 共享 1 pool 时，每 facade 的 sub-bus 只能配 1 manual subscriber，**无法用 ModelAdapterNode 共享 sub-bus**（即"facade × N 真实 qwen 节点"模式不可行）。这是 framework 仍在开发中的设计 quirk，**不**算 F-category 的"缺 primitive"，但**实质上阻止了 9.4.1 设计意图的真并发实证**（user 2026-07-03 round 6 判定："框架还在开发中，有 BUG 是正常的"） | **OPEN（design quirk，development-stage）** | framework 演化时修（建议：facade 用 broadcast 替代 directed-to-sub_id 的 forward 模式） |
| **F-004** | (F-category) | 缺 streaming API | 9.3.1（streaming） | **Framework 缺 stream event callback API**——Engine 推理用 `model_response`（final response）端到端正确（**不**是 bug，user 2026-07-03 round 7 明确："Engine 只消费最终结果用于下一步推理。chunks 交给 App 的前端去消费。这影响 Engine 推理吗？答：不影响"）；**chunks 在 bus 上流动**（`ModelAdapterNode` stream 分支 node.rs:130-165 正常发 `model_response_chunk`），**真实 LLM 实证**（9.3.1 真实 qwen stream 1 query → 215 chunks in 16s）；但 **app 想消费 chunks 必须自订阅 bus**（`bus.subscribe()`），framework **未提供** stream event callback hook（无 `Engine::on_chunk` 闭包 / 无 `ResponseProcessor` 触点 / 无 `EngineBuilder::on_stream_response` 钩子）。后果：production streaming UX 需 app 层写 bus 订阅 + chunks 累积 + 推送前端，**framework 没简化这条路** | **OPEN** | 后续 fix phase（建议：EngineBuilder 加 `on_stream_chunk(impl FnMut(&ModelResponseChunk))`） |
| **F-005** | (F-category) | 缺 thinking 传播 | 9.3.2（reasoning） | **Engine 不传播 `ModelDecl.thinking_enabled` 到 `ModelCallPayload.model_params`**——9.3.2 `f005_engine_does_not_propagate_thinking_enabled` 实证：设 `ModelDecl.thinking_enabled: true`，但 Engine 发出的 `model_call` 消息 `model_params` 字段为 `None`（**根本不传** model_params 字段），而非 `Some({thinking_enabled: true})`。后果：app 端 `ModelDecl.thinking_enabled` 配置**完全无效**——Engine 实际从不触发 thinking mode（仅靠 provider 默认行为，qwen 默认开 thinking 所以 work，DeepSeek/Anthropic 不一定）。framework 缺 Engine 主循环对 `ModelDecl.thinking_enabled` 的传播链路（ModelCall 应有 `model_params: ModelParams` 字段，engine 序列化时应包含） | **OPEN** | 后续 fix phase（建议：ModelCall 加 `model_params: ModelParams` 字段 + Engine 序列化时填入 thinking_enabled） |
| **F-006** | (F-category) | spec/code naming 不一致 | 9.3.2（reasoning） | **spec 提 `thinking_visible` 字段，framework code 实际用 `thinking_enabled`——naming inconsistency**——9.3.2 `f006_thinking_visible_naming_inconsistency` 实证：code `grep -rn thinking_visible crates/` = 0 命中（无 thinking_visible 字段），docs `grep -rn thinking_visible docs/` = 5 命中（capability-matrix §1.1 L1 / §5 + 9.2.1 task/audit + 9.3.2 task）。后果：app 读 spec 期望 `thinking_visible` 字段但 framework 用 `thinking_enabled`——**spec 误导**。**修复方向**：A) 把 spec 改为 `thinking_enabled`（与 code 一致）；B) 把 code 字段重命名为 `thinking_visible`（与 spec 一致）；C) 加 alias（同时支持两个名字）。建议 A（改 spec）—— code 已有 `thinking_enabled` 字段（agent/model.rs:21 + model-adapter/types.rs:38），覆盖更广 | **OPEN（naming inconsistency）** | 后续 fix phase 选 A/B/C 之一 |
| **F-007** | (F-category) | 缺 model-level 路由 | 9.4.2（capability 路由） | **Engine 不按 `model_name` 路由，只按 `provider`——`Provider::supported_models()` 在 routing 完全无效**——9.4.2 `engine_routes_by_provider_not_model_name` + `engine_ignores_unsupported_model_name` 实证：2 节点同 provider="openai" 但不同 `supported_models`（["qwen3.7"] vs ["qwen3.5"]），cfg.model.model_name="qwen3.5-turbo"（只在 node 2 supports）→ engine **静默**路由到任一 provider 节点（**不**按 model_name 选 node 2）；`engine_ignores_unsupported_model_name` 进一步实证 model_name 不在 supports 时 engine **不**报错。`ResourceRegistry::resolve_model`（registry.rs:253-269）只匹配 `n.capabilities.get("provider")`，**不**匹配 `model_name` 或 `supported_models`。`Provider::supported_models()` 仅作元数据塞 `NodeInfo.capabilities.models`（node.rs:38），在 routing **完全无效**。后果：app 端 model_name 拼写错误**静默**路由到错 model，**production 风险** | **OPEN** | 后续 fix phase（建议：resolve_model 应同时检查 `model_name` 是否在 `n.capabilities.models` 中，不匹配则报清晰 error） |
| **F-008** | (F-category) | 缺确定性路由 | 9.4.2（capability 路由） | **`BusGraph.nodes` 用 `HashMap.values()`（graph.rs:60）—— HashMap 迭代顺序非确定，`ResourceRegistry::resolve_model.find()` 继承此非确定**——9.4.2 `engine_routes_by_provider_not_model_name` 实证：2 节点同 provider，HashMap 顺序**可能**返回任一节点（实测有时 node 1，有时 node 2）。**production 风险**：同一 app 代码在两次启动间路由到不同 model node（虽然都"对"，但**非确定**）；app 端想"prefer node A"或"负载均衡"**无 framework 钩子**。后果：app 部署后**不可预测**——同样是 2 节点同 provider，今天 A 优先明天 B 优先。修复方向：BusGraph 改用 `Vec<NodeId>`（插入序）或 `BTreeMap`（key 序），resolve_model.find 改用确定序 | **OPEN** | 后续 fix phase（建议：BusGraph.nodes 改 Vec + resolve_model 暴露 priority hint） |
| **F-009** | (F-category) | spec/code 行为不一致 | 9.4.3（pool overflow 三策略） | **`Overflow::Queue(N)` 的 `N` 参数是 dead code —— 实际行为不区分 `Queue(0)` vs `Queue(usize::MAX)`**——9.4.3 `queue_zero_or_max_boundary` 实证：pool N=1 已 leased 1 个，`Overflow::Queue(0)` 期望"立即 Full"（按 spec 描述），但实际**永久 block on semaphore**（2s timeout 测出 TIMEOUT）；`Overflow::Queue(usize::MAX)` 期望"永不 Full 直到 l1 drop"，实测"永不 Full 直到 l1 drop" ✓（巧合 work，因为 Queue(N) 全部走 `acquire_owned().await` 阻塞分支）。根因（`crates/arf-pool/src/lib.rs:199-205`）：`Overflow::Queue(_)` 分支**忽略 `N`**，全部走 `sem.acquire_owned().await`（**未用** `try_acquire` 或 pending 计数控制）。后果：app 端写 `Overflow::Queue(K)` 想"超过 K 入队就 Full"，**实际**永远不 Full（仅当 l1 drop 才拿到）；Queue(N) 的语义与 spec 描述（"buffer up to N"）不符。9.4.1 的 Queue(2) 满测试**恰好**能过（因为 l1 drop 后 l2 拿到，断言成功），掩盖了 F-009 | **OPEN** | 后续 fix phase（建议：Queue(N) 改为"先 try_acquire_owned，失败则检查 `pending < N`，是则 `pending+=1 + await notify`，否则 `Err(Full)`"——需要新增 `PoolState.pending` 字段已存在但未被读，lib.rs:141 warning `field pending is never read` 印证） |
| **F-010** | (F-category) | 缺注入构造器 | 9.5.3 + 9.12.1（custom DiscoveryBackend） | **`McpNode` 缺 `with_discovery` 构造器 + `discovery/runtime/handle` 字段私有——app 无法注入自定义 `DiscoveryBackend`**——9.5.3 + 9.12.1 双实证：McpNode 仅有 `local` / `remote` / `local_with_runtime` 3 个 public 构造器（node.rs:28-68），全部"discovery = 固定 FsDiscovery 或 HttpDiscovery"，app 实现 custom DiscoveryBackend（如 InMemoryDiscovery / ChainedDiscovery）只能**外部持有** + 手动调 `discovery.list_tools()`，无法注入到 McpNode 内部走 message_loop 路径。后果：framework "DiscoveryBackend 是扩展点" 承诺破灭——MCP node 的 discovery 路径是封闭的，custom discovery **无法** 走 framework 的 tool_call dispatch / skill 注册 / NodeInfo 上报。**production 影响**：第三方 protocol（gRPC MCP / 自定义二进制 protocol）无法以 custom DiscoveryBackend 形式集成 | **FIXED（c983213）** | 后续 fix phase（建议：加 `pub fn with_discovery(namespace, Box<dyn DiscoveryBackend>, Box<dyn RuntimeModule>)` 构造器 + 改 3 个字段 `pub(crate)` 或加 `pub fn discovery(&self) -> &dyn DiscoveryBackend` 访问器） |
| **F-011** | (F-category) | error 信息丢失 | 9.5.5（RemoteRuntime + HttpDiscovery） | **`HttpProxyTool` 不识别 MCP `isError` 字段 —— remote logical failure 在 `tool_exec` response 上报为 success**——9.5.5 `http_proxy_tool_ignores_is_error_field` 实证：MCP JSON-RPC spec 规定 `tool result` 含 `isError: bool` 字段（区分"tool ran successfully but returned error"vs "tool call failed entirely"）；HttpProxyTool 转 JSON 时**只读 `content` 字段**，忽略 `isError`。后果：remote MCP server 返回 `{content: "tool blocked", isError: true}` 时，bus 上的 tool_result 消息 status=success（**不是** failed），engine 下一步会**拿这个"成功"的结果继续推理**——silent failure。后果放大：cascade cancel 链路（脚本 tool 中断 child tool）**无法工作**——parent 看 child "成功"，不 cancel | **FIXED（c983213）** | 后续 fix phase（建议：HttpProxyTool 转 JSON 时读 `isError` 字段映射到 ToolExec.status） |
| **F-012** | (F-category) | 隐式 contract | 9.10.1（SqliteSessionStore） | **Engine.snapshot() 假设 session 已 pre-saved —— app 必须先调 store.save() 否则 snapshot 静默 NotFound**——9.10.1 `engine_snapshot_assumes_session_pre_exists` 实证：EngineBuilder + with_session_store + Engine.chat() → checkpoint 触发时 Engine.snapshot() → SessionStore.snapshot(session_id, ...) → SqliteSessionStore.snapshot 内 `SELECT * FROM sessions WHERE session_id = ?` → None 时只 eprintln（不报错），Engine 继续（**不**返回 Err）——silent drop。后果：app 端忘记调 save() 之前 Engine 就 chat()，**所有 checkpoint 静默失败**，但 session 继续推进（消息、tool call 都 work），session 终止时 snapshot 数据**不完整**。production debugging nightmare | **FIXED（6c854cf）** | 后续 fix phase（建议：Engine::chat 入口检查 session_id 是否已在 store 中，无则 fail-fast；或 snapshot 失败时强制 abort 当前 round） |
| **F-013** | (F-category) | save/snapshot 职责分裂 | 9.10.2（SessionData 字段） | **`SessionStore::save()` 不持久化 `last_checkpoint` —— 必须额外调 `snapshot()`**——9.10.2 `save_does_not_persist_last_checkpoint` 实证：SessionData 4 字段（meta + state + config_snapshot + last_checkpoint）；`save()` 仅写前 3 字段（session/lib.rs:337），`last_checkpoint` 静默 dropped。后果：app 调 `store.save()` 后 reload，**last_checkpoint 永远为 None**——必须额外调 `store.snapshot()`。框架内 SqliteSessionStore 自身就是这么用的（save + snapshot 分两步），但 trait docstring 只说 `save(session_data)` 没说"不持久化 last_checkpoint"。**契约不清晰**——custom SessionStore impl 容易遗漏 last_checkpoint 字段 | **FIXED（6c854cf）** | 后续 fix phase（建议：A) save() 也写 last_checkpoint；B) trait docstring 显式说明 last_checkpoint 必须 snapshot() 持久化；C) 加 SessionStore::save_with_checkpoint() composite method） |
| **F-014** | (F-category) | trait doc 缺失 | 9.10.5（自定义 SessionStore） | **`SessionStore::snapshot()` trait doc 只说"append a checkpoint"，实际 `SqliteSessionStore::snapshot()` 跑 4 个副作用**——9.10.5 `snapshot_undocumented_4_side_effects` 实证：trait docstring 单行注释；SqliteSessionStore::snapshot 实际：1) write checkpoint row + 2) UPDATE sessions.state_json + 3) UPDATE sessions.updated_at + 4) force status='interrupted'。后果：custom impl 容易遗漏 2/3/4 副作用，写完测试通过但 reload 时 state_json 与 checkpoint **不一致**（state 推到最新但 checkpoint 反映更早版本），或 status 没改 'interrupted'（kill signal 失效）。**框架 trait doc 与实现行为脱节** | **FIXED（6c854cf）** | 后续 fix phase（建议：SessionStore trait doc 列出 4 个副作用清单 + 提供 `SnapshotStore::snapshot_v2(state, checkpoint) -> SnapshotEffects` 显式返回 4 个 SQL 改动） |
| **F-015** | (F-category) | trait 签名与实现不一致 | 9.11.3（自定义 Summarizer） | **`Summarizer::summarize(messages: &[ModelMessage])` trait 提示"raw messages"，实际 Compactor 传合成 `[system, user]` chat 格式**——9.11.3 `summarizer_trait_messages_param_misleading` 实证：trait param 名 `messages_to_summarize: &[ModelMessage]` 暗示 caller 给 raw conversation；Compactor 实际拼 `[system(用 with_instruction 注入的指令), user("[user]: msg1\n[assistant]: msg2\n...")]` 合成 chat 格式。后果：rule-based summarizer（如 keyword extraction / token-rank）**只看 user 消息**会完全工作；chat-format-aware summarizer（如 "summarize this conversation" prompt-style LLM）也会工作——但**两者行为不一致**取决于 summarizer 实现方式，framework 无统一期望。修复方向：trait param 改名 `compaction_request: CompactionRequest { instruction, messages }` 让调用契约清晰 | **FIXED（e718ea6）** | rename → `CompactionRequest { instruction, messages }`；Compactor 传 raw messages + instruction 分开；e2e summarizer_receives_instruction_and_messages_separately |
| **F-016** | (F-category) | 扩展点未接 | 9.13.1（Node 掉线） | **`OnMemberFailedHandler.handle()` 从未被 Engine 调用 —— node offline 时 framework 只清 cache 不 dispatch handler**——9.13.1 `on_member_failed_handler_never_invoked` 实证：Engine 在 `engine.rs:81-92` 注册 lifecycle listener，node offline 时**只** invalidate cache，**不**调 `OnMemberFailedHandler::handle(member, reason)`；framework 自身 test（tests.rs:2239）注释 "handler invocation 留 6.x" —— 6.x task 未完成。后果：app 端 register `OnMemberFailedHandler` 想做 FailSession / SwitchTo / Retry——**handler 永远收不到 callback**，node offline 静默失败；后续 Engine 推理仍向 offline 节点发 model_call → NodeOffline error → 整个 session abort。修复方向：engine.rs:81-92 调 `self.on_member_failed.handle(member, reason)` + dispatch on `MemberFailedAction::FailSession/SwitchTo/Retry` | **OPEN** | 后续 fix phase（建议：engine.rs:81-92 在 cache invalidate 前/后调 handler，按 handler 返回的 Action 分支：FailSession → session.abort() / SwitchTo → 重新 resolve 替代节点 / Retry → 重试） |
| **F-017** | (F-category) | 扩展点未接 | 9.13.3（Tool Permission Ask） | **Engine `ToolPermission` 路径完全未接 —— 两个 AgentConfig 类型不互操作**——9.13.3 `engine_tool_permission_completely_unwired` 实证：`arf_agent::AgentConfig.tools: Vec<ToolSpec>` 有 permission 字段，`arf_core::ToolSpec`（Engine 使用）**无** permission 字段；Engine 读 `arf_engine::AgentConfig`（无 tools 字段），**完全忽略** ToolSpec.permission。后果：所有 3 个 permission 状态（Allow/Ask/Deny）都是 dead code——Engine 永不触发 Ask 弹窗 / 永不 deny tool / 直接 allow 所有 tool。production 影响：app 想 deny 危险 tool（rm -rf / write_credential）**完全无效**——framework 默认 allow all。修复方向：unify 两个 AgentConfig 类型 + Engine 主循环查 ToolSpec.permission 做分支 | **OPEN** | 后续 fix phase（建议：合并 `arf_agent::AgentConfig` + `arf_engine::AgentConfig`；Engine 在 tool_call 触发时查 ToolSpec.permission：Allow → 正常执行 / Ask → 发 `permission_request` msg 等 `permission_response` / Deny → 返回 tool_result error） |
| **F-018** | (F-category) | 缺 agent_id 概念 | 9.9.1（双 agent 独立） | **`Engine::node_id = "engine/{provider}"` 硬编码 —— 1 个进程只能跑 1 个 Engine per provider**——9.9.1 `same_provider_engines_node_id_collision` 实证：Engine 构造时（engine.rs:59）`node_id = NodeId::new(&format!("engine/{provider}"))`，provider 来自 cfg.model.provider。同进程跑 2 Engine + 同 provider → 两个 Engine 注册**同一个** node_id，bus.connect 第二次返回 `AlreadyConnected` 错误。后果：multi-agent 同 provider 多 Engine 场景**完全无法实现**（与 F-001 EnginePool 抽象缺失同根因）。修复方向：EngineBuilder 加 `with_agent_id(NodeId)` 或 `with_agent_role(String)` 字段 | **OPEN** | 后续 fix phase（建议：加 `EngineBuilder::with_agent_id(NodeId)` + Engine 构造时优先用 builder 设的 agent_id，fallback "engine/{provider}"） |
| **F-019** | (F-category) | 缺 auto-dispatch | 9.9.2（双 agent peer） | **Engine 不自动 dispatch ActionMessage —— app 必须手订阅 bus + 手动 dispatch_incoming**——9.9.2 `engine_does_not_auto_dispatch_actions` 实证：peer 消息（`PeerMessage` / `PeerReply`）走 bus.send/bus.subscribe 路径，但 Engine 主循环**不**消费 `peer_message` 消息类型——app 必须 `bus.subscribe()` + 手动匹配 msg_type + `engine.dispatch_incoming(msg)` glue 代码。后果：每加 1 种 ActionMessage 类型，app 端 glue 代码 O(N) 增长（每 type 一段 match 分支）；multi-agent 全连通场景 glue 代码量 **爆炸**。修复方向：EngineBuilder 加 `auto_subscribe(&[msg_type, ...])` 或 Engine 主循环自动 dispatch 所有 type | **OPEN** | 后续 fix phase（建议：EngineBuilder 加 `auto_subscribe_action_messages(&[ActionMessage])` + Engine 主循环自动 dispatch） |
| **F-020** | (F-category) | handler reply contract 模糊 | 9.9.4（嵌套 subagent 2 层） | **`bus.send` 验证 `to` 节点必须 online，handler 用 `msg.from` 作 reply.to 时 sub-stub 节点未 online → silent NodeOffline**——9.9.4 `bus_send_to_msg_from_fails_when_handler_not_online` 实证：handler 在 nested subagent 链路中要 reply 时用 `msg.from` 作 `bus.send(msg, to=msg.from)`；但 sub-stub 是 child 进程的 ephemeral 节点，未在 top-bus 注册 → `bus.send` 返回 `NodeOffline(NodeId("engine/child-stub"))`，handler 调 `let _ = ...` 静默吞掉。后果：nested subagent 链路 **静默断连**——parent 发给 child 的消息 ack 路径 lost，session 状态机推进但 child 收不到 reply，**几 round 后 child 端 error accumulation**。修复方向：`Message::broadcast()` 标记 + bus 区分 broadcast/directed；或 handler reply 时用 `msg.correlation_id` + Engine 提供 reply endpoint 自动 lookup | **FIXED（81280dd）** | 后续 fix phase（建议：Message 加 `routing: Broadcast|Directed` enum + bus 对 broadcast 跳过 online 验证） |
| **F-021** | (F-category) | 缺 auto-provision | 9.8.1（MCPPoolNode facade） | **`MCPPoolNode::run_loop` 调 `pool.acquire()` 但无 auto-provisioning path —— app 必须先 `pool.provision()` 否则 `acquire()` 返 Err 后 run_loop silent exit**——9.8.1 `mcp_pool_node_no_auto_provisioning` 实证：MCPPoolNode::new 不接受 provisioner，pool 初始为空，run_loop spawn 后第一次 acquire → `Err(PoolError::Acquire("no idle resource and no provisioner registered"))` → run_loop match 错误后**直接退出**（无日志、无 retry、无 panic），整个 facade 死掉。后果：app 想用 `MCPPoolNode` 必须手动调 `pool.provision(McpResource::new(mcp_node))` **一次**，漏调则整个 pool facade 静默失效。**与 F-002 pool 缺 auto-provision 同根因**——pool 框架层缺此 primitive | **OPEN** | 后续 fix phase（建议：`MCPPoolNode::new` 加 `provisioner: impl Fn() -> McpResource` 闭包 + run_loop 在 acquire Err(Acquire) 时自动调 provisioner 重试） |

> 统计：OPEN 24 / FIXED 0 / WONTFIX 0（截至 task 9.13.4 探查结束——phase 9 全 55 task 完成：A 类别 2 病灶 [A3/A4]；F 类别 22 病灶 [F-001 EnginePool / F-002 pool 动态扩容 critical / F-003 facade sub_id quirk / F-004 缺 stream event callback API / F-005 缺 thinking 传播 / F-006 spec/code naming 不一致 / F-007 缺 model-level 路由 / F-008 缺确定性路由 / F-009 Queue(N) N 参数 dead code / F-010 McpNode 缺 with_discovery / F-011 HttpProxyTool 忽略 isError / F-012 Engine.snapshot 隐式 contract / F-013 save/snapshot 职责分裂 / F-014 SessionStore::snapshot 4 副作用未文档化 / F-015 Summarizer trait 签名误导 / F-016 OnMemberFailedHandler 从未调用 / F-017 ToolPermission 完全未接 / F-018 Engine::node_id 硬编码 / F-019 Engine 不 auto-dispatch / F-020 bus.send silent NodeOffline / F-021 MCPPoolNode 缺 auto-provision]）。**F-001 至 F-021 共 21 F-lesion**，含 critical 设计意图偏离（F-002），development-stage design quirks（F-003, F-016, F-017, F-021），隐式 contracts（F-012, F-013, F-015, F-020），spec/code 不一致（F-006, F-009），扩展点未接（F-016, F-017, F-019, F-021），缺 primitive（F-001, F-002, F-004, F-005, F-007, F-008, F-010, F-018, F-021），trait doc 缺失（F-014）

---

## §2 病灶详情

### A4-001 — correlation_id Uuid↔string 转换散落

```
病灶 ID       : A4-001
信条           : A4 处理集中
Signal         : A4-S4（convert 散落）
触发情景       : §2.0（barrier 协议，但根因贯穿全框架 request-response 协议）
首次登记       : audit-probe-9.1.4.md §D
状态           : OPEN
file:line      : 塞（Uuid→string）: connection.rs:105 / connection.rs:330 / lib.rs:303
                挖（string→Uuid）: lib.rs:333-338
                typed 端点         : lib.rs:56（BarrierReceipt.correlation_id: Uuid）
                                    connection.rs:325（barrier_ack 参数 Uuid）
命中形态       : correlation_id 作为跨协议关联 ID，在 API 边界是 typed Uuid、在 wire payload
                是 JSON string。两形之间的转换（Uuid.to_string 塞入 / payload.get+parse 挖出）
                无统一 envelope，散落在每个协议各自的构造/解析点。塞侧有 send_response
                （connection.rs:96-109）半集中；挖侧完全无 helper。
影响面         : 1) 全框架 request-response 协议（barrier / model_response / tool_result /
                   app_checkpoint_result / compaction 等）各自手写 correlation_id 的 to_string
                   塞与 as_str+parse 挖——correlation_id 出现于 12 个非 test src 文件。
                2) 隐式约定：每个新协议实现者须自知"correlation_id 要 to_string 塞、as_str+
                   parse 挖"，无类型强制；拼错 key / 忘 to_string / parse 失败均静默降级。
                3) app 层外溢：task 9.1.4 participant（barrier_multi.rs:32-37）被迫手挖
                   payload.get("correlation_id").and_then(as_str).and_then(Uuid::parse_str)，
                   framework 未提供 typed 提取入口。
修复方向       : 引入统一 correlation envelope，或在 Message 上提供 typed
                （供参考）      `correlation_id() -> Option<Uuid>` / `with_correlation_id(Uuid)` 接缝，
                将 Uuid↔string 转换集中到单一 convert 点，塞挖双侧对称。
修复方向       : 统一采用**已存在**的 typed 访问器 `Message::correlation_id`（arf-core/
                （供参考）      message.rs:28 trait + 11 impl），消灭挖出侧手挖回退（engine.rs:689
                wait_for 匹配仍 payload.get 手挖）；并补对称的塞入侧 `with_correlation_id(Uuid)`，
                将 Uuid↔string 转换集中到单一 convert 点。
                【9.2.1 修正】原以为"缺访问器"，实证发现访问器已存在却未一致采用——
                修复是"统一采用"而非"新建抽象"，成本更低。
Engine 层蔓延  : （9.2.1 实证）engine.rs:375 用 typed `msg.correlation_id()` ✓，
                但 engine.rs:689 wait_for 响应匹配绕过它、手挖 payload.get("correlation_id") ✗；
                塞入侧 lib.rs:303 / connection.rs:105,330 仍手写 json!。typed 与 stringly 混用。
复现命令       : grep -rln 'correlation_id' crates/*/src/ | grep -v test   # 12 个非 test src 文件
                grep -rn 'fn correlation_id' crates/arf-core/src/message.rs  # typed 访问器已存在
                grep -n 'correlation_id' crates/arf-engine/src/engine.rs | grep -v test  # :375 typed vs :689 手挖
```

### A3-001 — lifecycle 消息类型标识散落（无单一 const）

```
病灶 ID       : A3-001
信条           : A3 数据唯一
Signal         : A3-S1（同名字段/标识跨 crate 重叠）
触发情景       : §2.0（容错/异常路径，根因贯穿全框架 lifecycle 协议）
首次登记       : audit-probe-9.1.5.md（前身为 9.1.4 观察 J，本 task 升级）
状态           : OPEN
file:line      : "node_offline": lib.rs:553 / heartbeat.rs:55 / engine.rs:88
                "node_online" : lib.rs:528 / engine.rs:88
                "heartbeat_request": heartbeat.rs:30 / connection.rs:378
                "barrier_request": lib.rs:299   "barrier_ack": lib.rs:329 / connection.rs:327
                常量定义       : 无（grep 'const … : &str' 消息类型 = 空）
命中形态       : lifecycle 协议消息类型名（node_online / node_offline / heartbeat_request /
                barrier_request / barrier_ack）作为跨模块契约，以裸字符串字面量散落声明于
                arf-bus / arf-core / arf-engine 三 crate 生产代码，无单一 const/enum 声明。
                关键：arf-engine（engine.rs:88）消费判断 "node_online"/"node_offline" 用裸
                字面量，与 arf-bus（生产者 lib.rs:528/553）无共享常量——跨 crate 契约各自硬编码。
影响面         : 1) 消息类型名散落 arf-bus + arf-core + arf-engine 3 crate，改名须全仓手动同步，
                   无编译期防护。
                2) 跨 crate 静默失效：arf-bus 改 "node_online" 拼写，engine.rs:88 消费侧不报错、
                   缓存失效逻辑静默失灵。
                3) 拼写错误无防护：msg.msg_type == "node_onlien" 编译通过、运行时静默漏判。
修复方向       : 在 arf-core 定义消息类型常量模块（pub const NODE_ONLINE: &str = "node_online"）
                （供参考）      或 enum MsgType，arf-bus/arf-engine/arf-model-adapter 统一引用，消灭裸字面量。
Engine 层蔓延  : （9.2.1 实证）核心协议 model_call/model_response 散落更严重：engine.rs:19
                有局部 const MODEL_RESPONSE，但同文件 engine.rs:749 自身用裸字面量；
                model-adapter/node.rs（8 处）+ pool_node.rs（4 处）全裸字面量。
                局部 const 形同摆设，无跨 crate 共享。model_call/model_response 是 chat 高频协议，
                比 lifecycle 消息散落更广。
复现命令       : grep -rn '"node_offline"\|"node_online"\|"heartbeat_request"' crates/*/src/ | grep -v test
                grep -rn '"model_call"\|"model_response"' crates/arf-engine/src/ crates/arf-model-adapter/src/ | grep -v test
                grep -rn 'const .*: &str' crates/arf-bus/src/ crates/arf-core/src/   # 无跨 crate 消息类型常量
                sed -n '86,90p' crates/arf-engine/src/engine.rs

区分说明     : A4-001 是 correlation_id 值的 typed↔string 转换散落（convert 轴）；
                A3-001 是消息类型标识符声明不唯一（标识声明轴）。两者不同轴、独立修复。
```

### F-001 — EnginePool 抽象缺失

```
病灶 ID       : F-001
类别         : F（framework missing primitive）
Signal         : 缺 primitive（spec §1.2 F 等级）
触发情景       : §2.12（model pool 生产场景）
首次登记       : audit-probe-9.4.1.md §D
状态           : OPEN
file:line      : 缺 primitive——无 EnginePool struct / trait
                Engine::new NodeId 派生: crates/arf-engine/src/engine.rs:59
                resolve_model by provider: crates/arf-engine/src/registry.rs:253-269
命中形态       : **framework 缺 EnginePool 抽象**——真实生产场景需 N 个 Engine
                共享同一 model config（N 用户同时咨询 → N Engine → 同一 model 集群）。
                当前 framework 只能：
                1) N 个 Engine 各有独立 cfg.model.provider（不同 NodeId），各自解析
                   到不同 model 节点——**不是共享 model，是多 model 路由**
                2) 1 个 Engine 串行 K 轮 run——**不是真并发，是 sequential**
                3) N 个 ModelAdapterPoolNode facade 共享 1 个 Arc<Pool<...>>，
                   每个 facade advertised_provider 唯一 + N 个 Engine 各对应 facade——
                   **app 层手动 virtualize EnginePool 模式**（user 2026-07-03 round 3 提出）
                选项 3 是当前 framework 唯一能跑"N Engine 并发 model pool"的方案，
                但它**绕过 framework**，app 必须自己写 N 个 facade + N 个 engine 绑定。
影响面         : 1) production 真实需求"N 用户同时咨询共享 model 集群"在 framework
                   缺直接支持
                2) app 层被迫写 N facade + N engine boilerplate，重复工作
                3) Engine::new 时 NodeId = "engine/{provider}" 硬编码（engine.rs:59），
                   限制 engine_id 灵活性
修复方向       : 方案 A：framework 新增 `EnginePool` struct / trait，类似
（供参考）      `ModelAdapterPoolNode` 但 wrap N 个 Engine 实例——对外暴露单
                一 engine facade，对内分发 chat 到 N 个 Engine。
                方案 B：`Engine::new` 接受 Option<NodeId> 参数，允许 app
                自定义 engine_id，无需新增 EnginePool。
                方案 C：app 层 "N facade 共享 1 pool" 模式标准化为 helper
                （如 `EnginePoolBuilder`），framework 提供但不强制。
                【user 2026-07-03 round 3 决策】9.4 保持 model 侧 pool 专项，
                EnginePool 由独立 task 或 fix phase 解决——不在 9.4 范围。
Engine 层蔓延  : （9.4.1 实证）Engine::new NodeId 硬编码（engine.rs:59）
                是 framework 直接 cause；registry::resolve_model（registry.rs:253）
                按 provider 能力匹配也是 N Engine 共享 model 的次要限制。
复现命令       : grep -n 'NodeId::new.*engine/' crates/arf-engine/src/engine.rs
                # 显示 59: NodeId 自动派生，无 override 参数
                grep -rn 'EnginePool' crates/
                # 0 命中——无 EnginePool 抽象
```

### F-003 — Facade sub_id 模式阻断 ModelAdapterNode 集成（design quirk）

```
病灶 ID       : F-003
类别         : F（framework 设计 quirk，development-stage）
Signal         : framework 设计缺陷（非 signal 命中）
触发情景       : §2.12（model pool 生产场景）
首次登记       : audit-probe-9.4.1.md §C
状态           : FIXED（e79c64b）— 重新 framing：真病灶是 run_loop 串行调度
                （见 fix-design.md §2）。修复：run_loop 改为 dispatcher +
                spawn-per-task + demux（correlation_id 路由）；acquire 失败回
                model_response{error}。e2e: facade_spawns_per_request_concurrent
                （4 并发 ~300ms，原 ~1.2s）+ facade_acquire_error_returns_error_response。
file:line      : pool_node.rs:62-66（facade connect sub-bus 用 sub_id）
                pool_node.rs:107-115（facade forward 用 to=sub_id）
命中形态       : **Facade 的 sub_id 模式让 ModelAdapterNode 集成不可行**。
                `ModelAdapterPoolNode::connect` 在 sub-bus 注册 listener
                `node_id = "model/pool-{i}/sub"`（pool_node.rs:65）。
                Facade forward model_call 时 `to=this sub_id`。
                任何想在此 id 注册 `ModelAdapterNode` 会被 bus 拒绝
                （`AlreadyConnected` error，9.4.1 probe 实证）。
                **唯一可工作的 sub-bus handler 是 manual broadcast subscriber**
                （如 `crates/arf-pool/tests/integration.rs` 既有 pattern），
                用 `bus.subscribe()` 接收所有消息（不依赖 to 字段）。
影响面         : 1) N 个 facade 共享 1 pool 时，每 facade 的 sub-bus 只能配
                   1 manual subscriber，**无法用 ModelAdapterNode 共享 sub-bus**
                2) 9.4.1 设计意图的"facade × N 真实 qwen 节点"模式**当前不可行**
                3) 真并发 LLM call（多 facade 共享 pool）需要绕过 sub_id 冲突——
                   app 层必须写"每 facade 独立 sub-bus + manual subscriber"
                   而不是"N facade 共享 1 sub-bus + N ModelAdapterNode"
                4) framework 仍在开发中（user 2026-07-03 round 6 判定）
修复方向       : 方案 A（最小）：facade forward model_call 时用 `to=[]` (broadcast)
（供参考）      而非 `to=[sub_id]`，sub-bus 上所有 ModelAdapterNode 都可接收。
                方案 B：facade 的 sub_id 改为 dynamic discoverable（如 pool 资源
                自己 advertise），而非硬编码 listener。
                方案 C：保留 sub_id 设计但 doc 明确"sub-bus 必须配 manual
                broadcast subscriber"，current pattern 是设计意图。
                【user 2026-07-03 round 6】当前为开发期 design quirk，**不**
                阻塞 framework 使用（manual subscriber 可用），优先级低于 F-001/F-002。
复现命令       : grep -n 'sub_id' crates/arf-model-adapter/src/pool_node.rs
                # 65, 107, 115 — facade 三处使用 sub_id 模式
                # 9.4.1 probe 实证: matrix_*_queue 7 个测试全部因
                # "AlreadyConnected(NodeId(\"model/pool-0/sub\"))" 失败
                # 见 audit-probe-9.4.1.md §A
```

### F-002 — Pool 动态扩容缺失（CRITICAL：实现偏离设计意图）

```
病灶 ID       : F-002
类别         : F（framework missing primitive）— CRITICAL
Signal         : 缺 primitive + 实现偏离设计意图
触发情景       : §2.12（model pool 生产场景）
首次登记       : audit-probe-9.4.1.md §D
状态           : OPEN（CRITICAL）
file:line      : PoolConfig 缺字段: crates/arf-pool/src/lib.rs:79
                Pool 内部无 grow logic: crates/arf-pool/src/manager.rs
                无 auto_provision / dynamic_expansion 字段
                无 min_size 字段
命中形态       : **framework 实现偏离设计意图**（user 2026-07-03 round 5 判定）：
                - **设计意图**：每个 pool 有 `min_size` + `max_size`，load 增长时
                  **动态挂载扩容**（auto-provision 至 max_size），超 max_size 才开始排队
                - **当前实现**：只有 fixed `max_size`，**无 min_size，无 auto-provision**——
                  load 来时只能 Block/Queue/Reject，**根本不会扩 1 个 resource**
                - **finding 性质**：不是隐藏 BUG，是 design 文档明示的 dynamic expansion
                  code 完全没做。比"缺 feature"严重：直接说明 framework 当前不符合 spec。
影响面         : 1) production 真实需求"N 用户同时咨询共享 model 集群"——pool 需
                   扩到 N（≤ max_size）才能保证所有用户不排队；当前会直接
                   Block/Queue/Reject，无弹性伸缩能力
                2) 真实场景下 pool 资源数需预估+调参，无法应对 burst load
                3) spec §2.12 描述的"model_pool_overflow" capability 不完整——
                   spec 隐含 pool 应能动态扩容，当前实现不支持
修复方向       : 方案 A（最小改动）：PoolConfig 加 `min_size: usize` +
（供参考）      `auto_provision: bool`，Pool 内部加 grow logic
                (load > current_size 且 current_size < max_size → 调 provision
                factory 新增 resource)。
                方案 B（重构）：Pool 内部 state 改用 ResourceManager（已有
                crates/arf-pool/src/manager.rs），加 load → grow callback。
                方案 C（新 primitive）：引入 ElasticPool trait，类似
                `Resource + auto_provision`，分离 bounded vs elastic 语义。
                【user 2026-07-03 round 5 强调】这是 critical finding——直接说明
                framework as-shipped 不符合 spec，**修复优先级应高于 F-001**。
Engine 层蔓延  : N/A（pool 不在 engine 层）
复现命令       : grep -n 'min_size\|auto_provision\|grow' crates/arf-pool/src/ -r
                # 0 命中——pool 无 min_size / auto_provision / grow 字段
                grep -n 'pub struct PoolConfig' crates/arf-pool/src/lib.rs
                # 仅 max_size + overflow + idle_timeout 3 字段，缺 min_size
                # 实证测试: f002_pool_does_not_auto_provision
                # 发 K=4 到 N=2 pool，pool 大小严格保持 2，K=4 排队逐个 succeed
                # 证明：无动态扩容，与设计意图严重不符
```

### F-009 — Overflow::Queue(N) 的 N 参数 dead code

```
病灶 ID       : F-009
信条           : (F-category) — spec/code 行为不一致
Signal         : F-S1（spec describe ≠ code 行为）
触发 task     : 9.4.3（pool overflow 三策略）
首次登记       : audit-probe-9.4.3.md §D
状态           : OPEN
file:line      : crates/arf-pool/src/lib.rs:199-205
                Overflow::Queue(_) => self.inner.sem.clone()
                    .acquire_owned().await  ← 全 N 都走这分支
                    .map_err(|_| PoolError::Closed)?,
                crates/arf-pool/src/overflow.rs:10
                /// Buffer up to `n` pending acquirers. Excess callers
                /// get [`PoolError::Full`](crate::PoolError::Full).
                Queue(usize),  ← spec 描述"N 控制 buffer 大小"
                crates/arf-pool/src/lib.rs:141 (warning)
                pending: usize,  ← 字段存在但从不读，**印证 dead code**
命中形态       : **spec 描述与 code 行为完全不一致**——
                - spec（overflow.rs:8-15）：Queue(N) = "buffer N pending callers,
                  excess → PoolError::Full"
                - code（lib.rs:199-205）：Queue(_) 全部走 acquire_owned().await
                  阻塞分支，**N 完全被忽略**
                实证 1：`Overflow::Queue(0)` 期望立即 Full，实测永久 block
                （`queue_zero_or_max_boundary` 测出 2s TIMEOUT 而非 Full）
                实证 2：`Overflow::Queue(usize::MAX)` 期望"永不 Full 直到
                  l1 drop"，实测 work（巧合，因为 acquire_owned() 也会等 permit）
                实证 3：lib.rs:141 warning "field `pending` is never read"——
                  **编译器都看出 pending 字段被写了从未读**（PoolState.pending
                  本应为 Queue(N) 实现用，但 acquire 路径未触达）
                9.4.1 的 Queue(2) 满测试能过（l1 drop 后 l2 拿到）——**掩盖
                了 F-009**；9.4.3 边界 case（Queue(0) / Queue(MAX)）才暴露
影响面         : 1) app 端写 `Overflow::Queue(K)` 想"超过 K 排队就 Full"，
                   **实际**永远不 Full（仅当 l1 drop 才拿到）——spec 误导
                2) `Overflow::Queue(0)` 想要 fail-fast 行为，**实际**会永久
                   block（直到 l1 drop 或 pool 关闭）——潜在死锁风险
                3) 9.4.1 既有测试 Queue(2) 满**通过**（掩盖），但语义**错误**
                4) 与 F-002 复合：F-002 已无 min_size/auto-provision，
                   F-009 又让 Queue(N) 失效，**pool 实际只剩 2 种可用策略**：
                   Reject（try-acquire）和 Block(timeout)（timeout-wrap 阻塞）
                   ——Queue 完全失效
修复方向       : 方案 A（最小改动，3 行）：Queue(N) 分支改为先 try_acquire_owned
（供参考）      ，失败则检查 `state.pending < N`：是则 pending+=1 + await
                notify，pending-=1 后 retry；否则 Err(Full)。
                方案 B（重构）：在 PoolState 加 `pending: usize` 字段已存在，
                仅需 acquire 路径触达；notify 时 pending-=1。lib.rs:141
                warning 消失。
                方案 C（语义重定义）：把 Queue(usize) 重新定义为"最多 N 个
                waiter"，语义同方案 A，但**保留**当前 Block 行为作为 fallback。
                建议 A（最少改动 + 最直观）。
Engine 层蔓延  : N/A（pool 不在 engine 层）
复现命令       : grep -n 'Overflow::Queue' crates/arf-pool/src/lib.rs
                # 仅 1 处（lib.rs:199），N 完全未用
                grep -n 'pending' crates/arf-pool/src/lib.rs
                # 仅 PoolState.pending 字段定义（141）+ warning（never read）
                # 实证测试: queue_zero_or_max_boundary
                # Queue(0) → 期望 Full，实测 2s TIMEOUT
                # Queue(MAX) → 期望永不 Full（实测 work 但**巧合** work）
```

---

## §3 F 类别（framework missing primitive）— §3 后续 task 追加区

> 区别于 §1 A 类别（A1-A4 四信条违反），F 类别是 **framework 缺 primitive / trait**
> （spec §1.2 F 等级 = "缺 primitive + 缺扩展点"）。F lesion 不在 signal 命中路径上发现，
> 而是 task 探查**真实生产场景**时发现 framework 不能直接供 / 组合可达 / 扩展可达某能力。
>
> F lesion ID 格式：`F-NNN`（NNN = 该类别下顺序编号）。
> F 病灶状态语义与 A 类别一致（OPEN / FIXED / WONTFIX），但**修复方向不同**：
> A 病灶 → 信号修正（如统一采用 typed 访问器）；F 病灶 → **framework 新增 primitive/trait**。
>
> 9.1.5 之后，task 若发现 framework 缺 primitive，记入本节。
>
> **F 类别已登记**：
> - F-001（EnginePool 抽象缺失）—— 9.4.1 触发
> - F-002（Pool 动态扩容缺失，CRITICAL）—— 9.4.1 触发
> - F-003（Facade sub_id 模式阻断 ModelAdapterNode 集成，design quirk）—— 9.4.1 触发
> - F-004（Framework 缺 stream event callback API）—— 9.3.1 触发（user 2026-07-03 round 7 重新 framing：原"Engine 缺 chunks 消费"是误解设计意图，正确 finding 是 framework 缺 app 暴露 chunks 的 API）
> - F-005（Engine 不传 thinking_enabled 到 model_call）—— 9.3.2 触发
> - F-006（spec/code naming 不一致：thinking_visible vs thinking_enabled）—— 9.3.2 触发
> - F-007（Engine 不按 model_name 路由，静默错误）—— 9.4.2 触发
> - F-008（BusGraph HashMap 非确定，resolve_model 路由非确定）—— 9.4.2 触发
> - F-009（Overflow::Queue(N) N 参数 dead code）—— 9.4.3 触发
> - F-010（McpNode 缺 with_discovery 构造器，discovery 字段私有）—— 9.5.3 + 9.12.1 双触发
> - F-011（HttpProxyTool 不识别 isError 字段）—— 9.5.5 触发
> - F-012（Engine.snapshot() 隐式 contract：app 必须先 save()）—— 9.10.1 触发
> - F-013（save() 不持久化 last_checkpoint）—— 9.10.2 触发
> - F-014（SessionStore::snapshot() trait doc 缺 4 副作用清单）—— 9.10.5 触发
> - F-015（Summarizer trait signature 与 Compactor 实现不一致）—— 9.11.3 触发
> - F-016（OnMemberFailedHandler.handle() 从未被调用）—— 9.13.1 触发
> - F-017（ToolPermission 路径完全未接，两 AgentConfig 类型不互操作）—— 9.13.3 触发
> - F-018（Engine::node_id = "engine/{provider}" 硬编码）—— 9.9.1 触发
> - F-019（Engine 不自动 dispatch ActionMessage）—— 9.9.2 触发
> - F-020（bus.send silent NodeOffline，handler reply contract 模糊）—— 9.9.4 触发
> - F-021（MCPPoolNode 缺 auto-provisioning）—— 9.8.1 触发

---

## §4 与 fix phase 的接口契约

- 本册是 fix phase 的**唯一病灶输入源**——fix phase 逐 `OPEN` 病灶处理
- fix 完成后，将对应病灶 `状态` 改 `FIXED` 并附 fix commit hash
- 按 spec §4.4 探查回归：fix 后须重跑触发该病灶的 task audit-probe，确认命中消失
- **F 类别特别说明**：F 病灶的 fix 涉及 framework 抽象新增（不是信号修正），
  fix phase 应评估：**新 primitive 的 scope**（仅当前 task 缺失 / 多 task 共享）
  + **app 层使用契约**（如何让 app 用新 primitive）+ **向后兼容性**（是否破坏既有 app）。
