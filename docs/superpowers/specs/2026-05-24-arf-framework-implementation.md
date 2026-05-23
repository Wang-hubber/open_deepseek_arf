# ARF 框架实现现状

## 问题域（框架层职责）

| 域 | OS 类比 | 解决的致命问题 | 当前实现细节 | Framework 接口 |
|---|---|---|---|---|
| **core** | 内核类型系统 | 跨模块 Protocol 散落各处，engine 无法合法引用 | 21 个 Protocol 文件 + `AgentEvent`(9 事件类型, dataclass) + `AgentState`(TypedDict, 10 字段) + `TurnContext`(dataclass, 7 字段) + `MemoryEntry`(dataclass, 8 字段含 relevance_score) + `GuardResult`/`ToolResult`/`HookResult`。零实现逻辑，纯类型层 | 所有 Protocol + 核心数据结构，`arf/core/` 零依赖 |
| **agent** | 进程 | Agent 生命周期管理 | Pydantic 配置驱动。`AgentConfig` 12 字段(name/role/task/description/system_prompt/models/skills/tools/hooks/advanced/agents/handover)。`SystemPromptConfig`(template + critical_rules + `{{INVENTORY}}`占位符)。`AdvancedConfig`(loop_strategy/max_turns/compaction/memory/guardrails/errors/sandbox/streaming/tool_retrieval/reload/routing)。`create_agent(config)` 一行创建 | `create_agent(config=AgentConfig(...))` |
| **engine** | CPU 流水线 + 事务管理器 | 执行循环、checkpoint、并行tool调用、事务回滚、规划跟踪 | `GraphEngine`(436 行): invoke(同步) + astream(流式, 9 事件类型 yield + EventBus emit)。`ReActStrategy`: 标准 Think→Act→Observe 循环，max_turns 自动 break。`ConcurrentToolExecutor`: parallel/sequential 策略，max_concurrency=5，asyncio.Semaphore 控并发。`InMemoryStateStore`: dict 存储，6 个 checkpoint 点自动 put()(每 turn 结束/工具执行前后/human_loop 暂停前)。`SnapshotRollback`: begin 深拷贝 AgentState → commit/rollback 恢复。`PromptBasedPlanner`: generate_plan/update_progress/detect_divergence/revise 四步规划(已注入未调用)。`DefaultErrorPolicy`: 工具重试 2 次指数退避，模型 429 重试 3 次，5xx 降级 | `GraphEngine` + `LoopStrategy` + `StateStore` + `ToolExecutor` + `TransactionContext` + `Planner` |
| **observability** | 系统监控 + 录放机 | 框架黑盒，出问题无法定位且无法复现 | `InMemoryEventBus`(37 行): asyncio.Queue 广播, emit() + subscribe(async generator) + collect(event_type) + reset()。`FileTraceStore`(62 行): 订阅 EventBus, 过滤 session_start/end, 全量读取 JSON → append → 写回, `{dir}/{session_id}.json`。`UsageTracker`(91 行): 订阅 EventBus, 按 model 累计 prompt/completion/total tokens, 自动订阅+持久化到 usage.json。`OtelTracer`(26 行): AgentEvent → OTel Span 导出, 环境变量 OTEL_EXPORTER 选择 console/otlp/none。`FileReplayController`(45 行): 录制模式拦截 model/tool 输出写入轨迹文件, 回放模式按 turn 注入录制值, 支持 breakpoints 单步调试 | `EventBus` + `Tracer` + `TuiDashboard` + `ReplayController` |
| **streaming** | 管道 (pipe) | 用户盯白屏等结果 | `InMemoryEventBus` 作为共享事件源, streaming 和 observability 是两个消费者。`SseStream`: 适配 EventBus → SSE 格式, publish/listen。astream() 9 种事件类型: session_start, user_input, thinking_delta, model_call_start/end, tool_call_start/end, error, session_end | `EventBus` + `EventStream`（共享事件源） |
| **guardrails** | 防火墙 + 杀毒软件 | 模型输出不可信，缺少语义安全层 | `DefaultGuardRunner`: 组装 InputGuardrail + OutputGuardrail + ToolGuardrail, engine 两处调用(check_output 在模型输出后, check_tool_params 在工具执行前)。`NoneInputGuard`: 透传所有输入(6 行)。`RegexOutputGuard`: 正则清洗 PII(API key/手机号, 18 行)。`PathCheckToolGuard`: 检测路径穿越 ../ 和绝对路径(15 行)。⚠️ check_input 已注入但 engine 未调用 | `GuardRunner` (engine 统一入口，内部封装三种护栏) |
| **evaluation** | 基准测试 (benchmark) | 改了prompt/工具不知道变好变坏 | `DefaultEvalRunner`: 遍历 EvalDataset cases, 调 agent.chat(), 跑 MetricCalculator, 输出 EvalReport(pass_rate/avg_turns/tool_accuracy/duration, 含 baseline 对比)。内建 4 指标: SuccessRate/ToolAccuracy/TurnEfficiency/OutputContains | `EvalRunner` + `MetricCollector` |
| **compaction** | 虚拟内存 + 页交换 | 上下文窗口爆掉 | `SlidingWindow`: 75% 阈值触发, 旧轮次汇总为 context_summary, 注入 `{{MEMORY}}` 占位符。策略可配: sliding_window/summarization/none | `CompactionStrategy` |
| **memory** | 文件系统 + 搜索引擎 + 知识编辑器 | 只检索不写入，记忆无法生长 | `FileMemoryStore`(65 行): JSON 文件持久化 memory.json, save/load/delete。`RecentFirstRetriever`(25 行): 按时间戳倒序取 top_k, 字符数裁剪(≈tokens/3), 不使用语义检索。`RuleBasedMemoryWriter`(92 行): 中英文关键词匹配(偏好/事实/决策/规则), 4 类共 40+ 关键词, 分类存储, dedup by content, 留 model_call 参数供 LLM 提取升级。Engine invoke/astream 均接线: 每个 turn 前 retrieve() → 注入 context_summary, turn 后 extract_and_write() | `MemoryStore` + `MemoryRetriever` + `MemoryWriter` |
| **routing** | 多级缓存 (L1/L2) | 所有请求打同一个模型 | `TwoTierRouter`(已实现): 二级分类器(medium→quick_thinking, complex→deep_thinking), fallback 链(deep→quick→cheap), background 模型。⚠️ 已实现但未注入 engine, 当前所有请求直接打到 default model | `ModelRouter` |
| **hooks** | 系统调用 | 自定义扩展点 | `SubprocessHookRunner`(80 行): 6 事件节点(session_start/end, pre/post_model_call, pre/post_tool_exec), 同事件并行+内部串行, 退出码契约(0=继续,1=阻断,2=注入), 超时 SIGTERM→SIGKILL, 环境变量自动注入($ARF_SESSION_ID/$ARF_AGENT_NAME/$ARF_WORKSPACE)。Engine invoke/astream 均接线全部 6 个事件点 | `HookRunner` + `HookDefinition` |
| **sandbox** | 进程隔离 | 工具访问越界 | `PathSandbox`(20 行): validate_path(禁止 ../ 和绝对路径, 必须在 workspace 内), validate_command(命令白名单), allowed_dirs(可读写目录列表) | `ToolSandbox` |
| **concurrency** | 乱序执行 + 多核 | 任务层面并行调度 | `SequentialScheduler`: 顺序执行占位实现。实际并行在 ToolExecutor 层(一个 turn 内多 tool_calls 并行) | `TaskScheduler` |
| **human_loop** | 硬件中断 + 审批工作流 | 该停时停不下来，停了恢复不了 | `AlwaysAutoApprove`: 从不暂停(默认)。`ConsoleChannel`: 终端交互式审批(stdin/stdout), 超时默认拒绝。`ApprovalPoint`: tool_name_allowlist 策略, 仅白名单工具触发审批 | `ApprovalPoint` + `ApprovalChannel` |
| **communication** | IPC + 分布式共识 | 多Agent聋子, Supervisor中心化, 无共享状态并发保护 | `InMemoryAgentBus`(37 行): send/receive/register/discover, asyncio.Queue 路由, 支持广播(receiver=None)。`PeerAgent`(115 行): 去中心化 P2P, broadcast/send_to/listen/discover_peers/negotiate/handoff。`RoundRobinSupervisor`: 轮询任务分派。`DictWorkspace`: 内存 dict 共享黑板。`InMemoryLock`: acquire/release, ttl。`MajorityVoteConsensus`: propose/vote, 默认 threshold=0.5 | `AgentBus` + `PeerAgent` + `Supervisor` + `SharedWorkspace` + `Lock` + `ConsensusProtocol` |
| **resources** | 文件系统索引 + 远程挂载 | 工具只能本地 YAML，无法接入远程 | `DefaultToolResolver`(45 行): get_tool_definitions(query, top_k) → 内部聚合 Provider list_tools() → Retriever retrieve() → 返回 list[ToolDefinition]。内部封装: `StaticYamlProvider`(tools/ 目录扫描 tool.yaml, 58 行) + `SimpleRetriever`(关键词匹配) + `FunctionBackend`(直接调用 function.py execute, 28 行)。Engine 唯一入口, 不关心工具来源 | `ToolResolver` (engine 唯一 tool 接口) |
| **errors** | 异常处理 + 看门狗 | 工具/模型失败行为不可预测 | `DefaultErrorPolicy`: on_tool_error(retry 2 次, exponential backoff), on_model_error(429 retry 3 次, 5xx fallback), on_guardrail_block(abort), ErrorAction(action/delay/fallback_model/message)。`SnapshotRollback`: begin→深拷贝→commit→回滚快照+调工具自身 rollback 回调, 不可逆副作用标记 unresolved | `ErrorPolicy` + `TransactionContext` |

## Engine 注入状态总览

| 组件 | 注入 Engine? | Engine 调用? | 备注 |
|------|-------------|-------------|------|
| LoopStrategy (ReAct) | ✅ | ✅ should_continue() | max_turns 自动 break |
| StateStore (InMemory) | ✅ | ✅ 6 put + 2 get | 每 turn/工具/human_loop 边界 |
| ToolExecutor (Concurrent) | ✅ | ✅ 2 execute() | parallel/sequential |
| ToolResolver (Default) | ✅ | ✅ 2 get_tool_definitions() | invoke + astream |
| TransactionContext (SnapshotRollback) | ✅ | ✅ begin/commit/rollback | invoke 路径事务保护 |
| Planner (PromptBased) | ✅ | ❌ 0 调用 | generate_plan/update_progress/detect_divergence/revise 已实现未接线 |
| MemoryStore (File) | ✅ | ✅ load/save | 真实存储，已移除 DummyStore |
| MemoryRetriever (RecentFirst) | ✅ | ✅ 2 retrieve() | invoke + astream，注入 context_summary |
| MemoryWriter (RuleBased) | ✅ | ✅ 3 extract_and_write() | invoke(2) + astream(1)，中英文关键词 |
| HookRunner (Subprocess) | ✅ | ✅ 12 fire() | 全部 6 事件节点 × 2 路径 |
| GuardRunner (Default) | ✅ | ⚠️ 2/3 check_output + check_tool_params | check_input 待接线 |
| EventBus (InMemory) | ✅ | ✅ 22 emit | 9 事件类型全量发布 |
| ErrorPolicy (Default) | ✅ | ✅ on_tool_error/on_model_error/on_guardrail_block | 重试+降级+回滚 |
| ReplayController (File) | ❌ | - | 未注入 engine |
| ModelRouter (TwoTier) | ❌ | - | 未注入 engine |
| TaskScheduler | ❌ | - | 单 Agent，不需要 |

## 用户可见配置（agent.yaml）

```
用户只需配置 4 种资源:
  models:  模型定义(name/api_type/model/api_base/api_key_env/kwargs)
  tools:   工具定义(name/description/parameters/activation)
  skills:  技能定义(name/description/prompt/tools/activation)
  hooks:   钩子定义(name/type/run/timeout/env)

框架自动处理:
  system_prompt 管道 → {{INVENTORY}} 渐进式披露 → ToolResolver 发现
  → GraphEngine 执行循环 → StateStore checkpoint → Memory 读写
  → EventBus trace → FileTraceStore 持久化 → HookRunner 6 事件
  → GuardRunner 护栏 → ErrorPolicy 错误恢复 → Transaction 事务回滚
```
