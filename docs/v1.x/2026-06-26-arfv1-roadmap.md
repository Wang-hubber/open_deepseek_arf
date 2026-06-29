# ARF V1.x 实现路线图

> 从零实现 ARF V1.x 框架设计，分 8 个 Phase 逐块交付。
> 设计依据：`docs/v1.x-design.md`
>
> **订正日期：2026-06-29** — 反映 Phase 5 MCP 设计定稿：LocalMcpNode + RemoteMcpNode + ScriptTool + SkillIndex + DAG executor + ModelAdapter 集成。

## 总体约束

- **语言**：核心用 Rust，经由 PyO3 向 Python 暴露 API
- **节奏**：质量优先，无硬性时间限制
- **交付标准**（每个 Phase）：Rust 代码 + PyO3 绑定 + 单测 + 集成测试 + 本地 CI 通过 + `docs/api/` 用户文档 + 教学示例
- **分支**：`arfv1`

---

## 路线图总览

| Phase | 主题 | 一句话目标 | 依赖 | 状态 |
|-------|------|-----------|------|------|
| **0** | 脚手架 | Cargo workspace + maturin + Makefile CI + 文档框架 | — | ✅ 完成 |
| **1** | Bus | 消息总线：收发 + 节点生命周期 + 在线图 + PyO3 绑定 | 0 | ✅ 完成 |
| **2** | State | messages + tasks 生命周期 + 双向锁 + 级联释放 | 1 | ✅ 完成 |
| **3** | AgentConfig | 纯数据声明式配置骨架：models / tools / subagents / teammates | 1 | ✅ 完成 |
| **4** | ModelAdapter | 内部格式 ↔ DeepSeek/OpenAI/Anthropic API + Bus 节点 + PyO3 绑定 | 1 | ✅ 完成 |
| **5** | MCP | 工具发现/注册/执行，资源广播，脚本 Tool 子进程执行，远程 MCP HTTP 代理 | 1, 4 | ✅ 设计完成 |
| **6** | Engine | 收消息→调模型→得 action→发消息，Park/Resume | 1, 2, 3 | 🔲 待实施 |
| **7** | 集成 | E2E 测试 + 性能基准 + 完整文档 | 0-6 | 🔲 待实施 |

> **订正说明**：原路线图 Phase 4 为 Engine、Phase 5 为 ModelAdapter。实际实施中 ModelAdapter 提前到 Phase 4（因 Engine 依赖 State + AgentConfig 均已完成，但 Engine 复杂度最高，先做 ModelAdapter 可提前验证 Provider 架构并积累 PyO3 绑定经验）。Engine 顺延至 Phase 6。MCP 提前到 Phase 5（与 Engine 并行推进，先夯实工具层）。

## 依赖关系

```
Phase 0 ──→ Phase 1 (Bus) ──→ Phase 2 (State)
    │            │                  │
    │            ├──────→ Phase 3 (AgentConfig)
    │            │                  │
    │            ├──────→ Phase 4 (ModelAdapter) ✅
    │            │            │     │
    │            │            │  arf-model-adapter → arf-mcp (ToolResultItem → ModelMessage)
    │            │            │     │
    │            ├──────→ Phase 5 (MCP) ── 依赖 Bus + ModelAdapter 集成
    │            │                  │
    │            └──────→ Phase 6 (Engine) ── 依赖 Bus + State + AgentConfig
    │
    └─────────────────────────────────────→ Phase 7 (集成)
```

Bus 是唯一地基。AgentConfig/State/ModelAdapter/MCP 可在 Bus 完成后并行推进。Engine 需等 Bus+State+AgentConfig——顺序上 Phase 6 必须在 Phase 2 和 Phase 3 之后。Phase 5 MCP 与 Phase 4 ModelAdapter 有交叉依赖——ModelAdapter 需要 `ToolResultItem` 类型做转换，因此 Phase 5 的类型定义与 ModelAdapter 的转换函数在此阶段同步交付。

---

## 各 Phase 概要

### Phase 0 — 项目脚手架 ✅

- Cargo workspace：`arf-core`、`arf-bus`、`arf-state`、`arf-agent`、`arf-engine`、`arf-model-adapter`
- maturin 项目 `py-arf/`，PyO3 绑定
- `Makefile`：`lint`（cargo fmt --check + cargo clippy）、`test`（cargo test + pytest）、`ci`（lint + test）
- 文档目录 `docs/v1.x/`、`docs/api/`

### Phase 1 — Bus 消息总线 ✅

**Rust 实现**（`arf-bus` + `arf-core`）：
- 消息格式 `{id, type, from, to?, payload}`
- 节点生命周期：`node_online` / `node_offline` / `heartbeat`
- 在线节点图：心跳超时自动标记 offline
- 消息路由：广播 + 定向（消费后 drain）
- 并发模型：tokio mpsc + broadcast channel

**PyO3 绑定**（task-1.10）：
- 9 个 Python 类：`Bus`、`NodeHandle`、`NodeId`、`Message`、`NodeInfo`、`MessageFilter`、`ToMatch`、`SendReceipt`、`BusGraph`
- 异步桥接：`future_into_py` (tokio → asyncio)
- 用户文档：`docs/api/bus.md`

**测试**：Rust 87 + Python 66 = 153 tests

### Phase 2 — State 状态管理 ✅

- `messages`：完整消息流
- `tasks`：生命周期 `created → in_progress → blocked → resolved / failed / cancelled`
- 双向锁：`blocked_by` + `blocking`
- 级联释放：task 完成→沿 blocking 唤醒 / task 取消→沿 blocked_by 级联取消 / 节点离线→级联释放+注入通知
- 用户文档：`docs/api/state.md`

### Phase 3 — AgentConfig 声明式配置 ✅

- `AgentConfig` 纯数据结构：声明 agent 需要哪些资源
- `ModelSpec` / `ToolSpec` / `ResourceSpec` — 全部用逻辑名，不感知 Bus
- `ToolPermission`: Allow / Ask / Deny 三级权限
- 1:N 资源映射语义
- 支持 YAML/JSON 反序列化，支持代码构造，支持 Default
- 用户文档：`docs/api/agent-config.md`

### Phase 4 — ModelAdapter 模型适配器 ✅

> **订正**：原路线图 Phase 5。因 Engine 依赖链最长且复杂度最高，先实施 ModelAdapter 提前验证多供应商 Provider 架构。

**Rust 实现**（`arf-model-adapter`，tasks 4.1–4.7）：
- `Provider` trait：`name()` / `supported_models()` / `chat()` / `chat_stream()`
- 三个 Provider：`DeepSeekProvider`（OpenAI 兼容格式）、`OpenAIProvider`（标准 OpenAI）、`AnthropicProvider`（Messages API）
- `ModelAdapterNode`：Bus 被动节点，监听 `model_call` → 调用 Provider → 回复 `model_response`
- 消息类型：`ModelParams`、`ToolDef`、`ModelCallPayload`、`ModelResponsePayload`、`ModelResponseChunk` 等
- 流式支持：SSE 解析（OpenAI `data:` + Anthropic `event:` 双格式）
- 思考模式：DeepSeek `thinking` 显式开关 + `reasoning_content` 提取
- 重试逻辑：429/5xx 指数退避（最多 3 次）

**PyO3 绑定**（task-4.7）：
- 14 个 Python 类：3 Config + 3 Provider + 1 Node + 7 数据类
- `provider.connect_to_bus()` 模式：避免 PyO3 trait object 提取难题
- 异步桥接：`future_into_py` (tokio → asyncio)

**用户文档**：`docs/api/model-adapter.md`

**测试**：Rust 299 (61 unit + 18 integration) + Python 59 (27 imports + 14 node + 18 live) = 358 tests

### Phase 5 — MCP 资源管理 ✅

> 设计文档：`docs/v1.x/phase5_mcp/phase5-mcp-design.md`（13 个任务 5.1–5.13）
> 与 Phase 4 ModelAdapter 集成：`tool_result_to_model_message()` 在 `arf-model-adapter/src/convert.rs`

**核心架构**：一个 MCP 实例 = 一个 namespace = Bus 上一个节点。Engine 对 LocalMcpNode 和 RemoteMcpNode 无区别——都是 `node_online` 广播 + 响应 `tool_call_set`。

**两种节点**：
- `LocalMcpNode::new(namespace, root_dir)` — 扫描 `{root}/tools/*/tool.toml` 发现 ScriptTool + `{root}/skills/*/SKILL.md` 发现 Skill
- `RemoteMcpNode::new(namespace, RemoteConfig)` — HTTP `initialize` + `tools/list` 发现 + HTTP `tools/call` 代理执行

**ScriptTool**：框架不内置任何 Tool。所有本地 Tool 通过文件夹约定发现——`tool.toml` 声明元数据，入口脚本通过 stdin/stdout JSON 协议执行。支持 Python / Bash / Rust 三种 runtime。

**Skill**：纯数据 Markdown + YAML frontmatter，渐进式披露 L1→L2→L3。`SkillIndex` 扫描 `skills/*/SKILL.md` 构建索引，Engine 通过 `use_skill` / `load_skill_resource` 按需加载。

**DAG 执行器**：双向锁（blocked_by/blocking）→ 环检测 → 拓扑排序 → 分层并发 → 失败级联取消。

**多 namespace 隔离**：同一 namespace 内 tool/skill name 冲突 → panic（开发期错误）；跨 namespace 同名无影响。

**ModelAdapter 集成**（Phase 5 同步交付）：
- `ToolResultItem.name` 由 executor 从 `ToolCallItem.tool` 回填——ModelAdapter 无需 call_id→name 查表
- `tool_result_to_model_message()` 在 ModelAdapter 中定义——MCP 只产出数据，不感知 ModelMessage
- 依赖方向：`adapter → mcp`，单向

### Phase 6 — Engine 运行引擎 🔲

- 监听 Bus 消息，按 session_id 过滤归属
- 读取 `AgentConfig`，上 Bus 做 discovery，将逻辑 ResourceSpec 解析为 `ResolvedManifest`
- `model_call` → Bus → ModelAdapterNode → `model_response` → Bus → Engine
- action 决策通过 Bus 发出
- Park/Resume：收到 interrupt/resume 消息 → 暂停/恢复
- State 持久化由 Engine 管理

### Phase 7 — 集成与收尾 🔲

- E2E 集成测试（完整消息链路）
- 性能基准（Bus 吞吐、延迟）
- 完整 `docs/api/` 文档
- 教学示例集合
