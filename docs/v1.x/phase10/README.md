# Phase 10 — ARFV1 × DeepAgents 深度对标

> **目的**：以资深 Agent 架构师 / Agent 全栈开发者视角，对 ARFV1（1.0.0-alpha.0）与 langchain-ai/deepagents（0.6.12，截至 2026-07-06）做**原子级对标**，识别 ARFV1 的功能缺失点与不足之处。
>
> **范围**：覆盖多 Agent 编排、Agent 运行时、Trace、Eval、可扩展性五大维度，进一步细分为 13 个原子子维度。
>
> **方法论**：基于 Phase 9 `capability-matrix-and-audit-design.md` 的能力矩阵方法，扩展到跨框架对比维度。

---

## 文档结构

| # | 文件 | 维度 | 关键问题 |
|---|------|------|---------|
| 01 | [multi-agent-orchestration.md](./01-multi-agent-orchestration.md) | 多 Agent 编排 | 跨引擎联邦、Team 抽象、Barrier、节点故障恢复 |
| 02 | [agent-runtime-engine-loop.md](./02-agent-runtime-engine-loop.md) | Agent 运行时 / 引擎循环 | ReAct 循环、CheckpointRule、Middleware 替代 |
| 03 | [state-persistence.md](./03-state-persistence.md) | 状态 / 持久化 | DeltaChannel、Checkpoint 增长、Resume 语义 |
| 04 | [context-management.md](./04-context-management.md) | 上下文管理 | 摘要、Offload、Tool Output 外置 |
| 05 | [filesystem-tool-surface.md](./05-filesystem-tool-surface.md) | 文件系统 / 工具面 | Pluggable Backend、Path 权限、Tool DAG |
| 06 | [trace-observability.md](./06-trace-observability.md) | Trace / 可观测性 | Event 模型、Pending Outbound、流式事件 |
| 07 | [evaluation.md](./07-evaluation.md) | Eval | LLM-as-judge、Benchmark、Rubric 中间件 |
| 08 | [extensibility-plugin-model.md](./08-extensibility-plugin-model.md) | 可扩展性 / 插件模型 | Protocol DI、Entry Point、Profile 注册 |
| 09 | [provider-profile-system.md](./09-provider-profile-system.md) | Provider / Profile | ProviderProfile、HarnessProfile、按模型调优 |
| 10 | [subagent-patterns.md](./10-subagent-patterns.md) | Subagent 模式 | Declarative / Compiled / Async / Pool 四类 |
| 11 | [human-in-the-loop.md](./11-human-in-the-loop.md) | 人在环 | Interrupt 模式、权限中断 |
| 12 | [skills-memory.md](./12-skills-memory.md) | 技能 / 记忆 | SKILL.md、AGENTS.md、Prompt Cache 协同 |
| 13 | [sandbox-shell.md](./13-sandbox-shell.md) | 沙箱 / Shell | SandboxBackend、远程沙箱、Execute Offload |
| 99 | [gap-summary-recommendations.md](./99-gap-summary-recommendations.md) | 缺口汇总 / 推荐路线图 | 总览缺口表 + Phase 11-15 路线图 + 决策矩阵 |

---

## 核心结论（TL;DR）

ARFV1 与 DeepAgents 代表了 **两条不同的 Agent 框架哲学**：

| 维度 | ARFV1 哲学 | DeepAgents 哲学 |
|------|----------|----------------|
| 运行时 | **多 Actor 总线**（cross-engine federation） | **单 LangGraph Runtime**（per-agent graph） |
| 扩展点 | **CheckpointRule 管道**（同步、声明式） | **Middleware 包装器**（wrap/hook，多时机） |
| 状态 | **Session > Round > Turn** + Outbound 重传 | **DeepAgentState + DeltaChannel**（线性增长） |
| Subagent | **SubagentPool**（bus actor，semaphore 限流） | **3 种 subagent**（declarative / compiled / async remote） |
| 文件系统 | **MCP 节点** + Tool DAG | **6 个 Pluggable Backend** + Path 权限 |
| 容错 | **OnMemberFailedHandler**（Fail/Retry/Switch）+ Barrier | **LangGraph Checkpointer** + Interrupt |
| Eval | **Python e2e + Rust 单测** | **129 evals + LLM-as-judge + RubricMiddleware** |

### ARFV1 的核心优势（DeepAgents 没有）

1. **真正的跨引擎联邦**：J-RPC 广播总线 + 心跳 + Barrier + InboundDedupCache —— DeepAgents 完全依赖 LangGraph runtime，**没有跨进程协调原语**
2. **节点故障自动恢复**：`OnMemberFailedHandler` 枚举 Fail/Retry/Switch —— LangGraph 无对应概念
3. **Tool DAG 执行**：Kahn 拓扑排序的 `blocked_by/blocking` —— LangChain tools 顺序执行
4. **持久化 Outbound 重传**：`resend_pending_outbound` + InboundDedupCache LRU —— DeepAgents 依赖 checkpoint 恢复
5. **Protocol 优先 DI**：每个类型都是 `trait Node/ActionMessage/Route` —— DeepAgents 是 class-based
6. **强类型 Bus Message**：`ModelCall` / `ToolExec` / `SubagentDelegate` 等结构化 `ActionMessage` —— DeepAgents 是 dict
7. **多语言运行时**：Rust core + PyO3 binding —— DeepAgents 仅 Python

### ARFV1 的核心缺失（DeepAgents 有）

按严重程度排序（详见 [99-gap-summary-recommendations.md](./99-gap-summary-recommendations.md)）：

| 严重度 | 缺失项 | 影响范围 |
|-------|-------|---------|
| 🔴 严重 | **Pluggable Backend 抽象**（无 StateBackend / FilesystemBackend / CompositeBackend / StoreBackend） | 文件系统、记忆、shell、跨会话持久化 |
| 🔴 严重 | **Middleware 包装层**（无 wrap_model_call / before_agent / modify_request hook） | 工具动态增删、Prompt 注入、Tool Output Offload |
| 🔴 严重 | **LLM-as-judge Eval Harness** | 能力回归检测、Rubric 评测 |
| 🟠 重要 | **DeltaChannel（线性 Checkpoint 增长）** | 长会话 O(N²) 存储膨胀 |
| 🟠 重要 | **AsyncSubAgent / 远程 subagent** | 跨主机 subagent 联邦 |
| 🟠 重要 | **Provider/Harness Profile 系统** | 按 provider/model 自动调优（Prompt Cache、Excluded Tools 等） |
| 🟠 重要 | **RubricMiddleware（自评分循环）** | Agent 内置质量门控 |
| 🟡 有用 | **SKILL.md + AGENTS.md on-demand 加载** | Skills / Memory 渐进披露 |
| 🟡 有用 | **AnthropicPromptCaching / Bedrock Prompt Cache** | Provider 特定成本优化 |
| 🟡 有用 | **PatchToolCallsMiddleware**（缺失 ToolMessage 修复） | 健壮性 |
| 🟡 有用 | **Rubric 评测 / Tau Bench / Terminal Bench 集成** | 标准 benchmark |

---

## 对标方法论

### 原子级对标（Atomic-level）

每个对比点按以下格式记录：

```markdown
## <能力点>

### ARFV1
- 文件: <path:line>
- 实现: <一句话>
- 优点: <...>
- 缺点: <...>

### DeepAgents
- 文件: <path:line>
- 实现: <一句话>

### Gap 分析
- 是否对等: ✅ / ⚠️ 部分 / ❌ 缺失
- 优先级: 🔴 严重 / 🟠 重要 / 🟡 有用
- 建议: <具体修复路径>
```

### 覆盖维度（13 个原子子维度）

按维度归类到 5 大类：

1. **多 Agent 编排** → 01
2. **Agent 运行时** → 02, 03, 04
3. **工具 / 资源** → 05, 13
4. **可观测性** → 06, 07
5. **可扩展性** → 08, 09, 10, 11, 12

---

## 参考资料

### ARFV1 内部
- `Cargo.toml:3-17` — workspace 声明
- `crates/arf-core/src/` — Protocol 定义
- `crates/arf-engine/src/engine.rs:295-417` — `Engine::run` ReAct 循环
- `crates/arf-bus/src/lib.rs:104-121` — `Bus` 结构
- `crates/arf-subagent-pool/src/subagent-pool.rs:76-128` — `SubagentPool`
- `crates/arf-session/src/lib.rs:236-437` — `SessionStore` trait
- `crates/arf-model-adapter/src/provider.rs:22-69` — `Provider` trait
- `crates/arf-mcp/src/node.rs:16-22` — `McpNode`
- `crates/arf-compactor/src/lib.rs:99-156` — `Compactor::compact`
- `docs/v1.x/phase9/capability-matrix-and-audit-design.md` — Phase 9 方法论

### DeepAgents（已克隆至 `/tmp/deepagents`）
- `libs/deepagents/deepagents/graph.py:353-1025` — `create_deep_agent()` 装配函数
- `libs/deepagents/deepagents/middleware/` — 18 个中间件
- `libs/deepagents/deepagents/backends/` — Pluggable Backend Protocol + 7 实现
- `libs/deepagents/deepagents/profiles/` — Provider/Harness Profile 注册
- `libs/deepagents/deepagents/_messages_reducer.py:31-90` — `DeltaChannel` reducer
- `libs/ARCHITECTURE.md:1-113` — 三层架构
- `libs/evals/` — 129 evals / 8 categories
- `examples/` — 14 个示例

---

## 版本与时间戳

- **ARFV1**: `1.0.0-alpha.0`（2026-07-01），workspace 声明在 `Cargo.toml:3-17`
- **DeepAgents**: `0.6.12`（2026-06-25），CHANGELOG 最近条目
- **对标时间**: 2026-07-06
- **作者**: ARF V1.x 维护团队