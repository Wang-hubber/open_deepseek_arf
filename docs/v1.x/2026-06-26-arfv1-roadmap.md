# ARF V1.x 实现路线图

> 从零实现 ARF V1.x 框架设计，分 8 个 Phase 逐块交付。
> 设计依据：`docs/v1.x-design.md`

## 总体约束

- **语言**：最终交付 Rust，Python 仅作中间验证，不作为最终产出
- **节奏**：质量优先，无硬性时间限制
- **交付标准**（每个 Phase）：Rust 代码 + PyO3 绑定 + 单测 + 集成测试 + 本地 CI 通过 + 文档 + 教学示例
- **分支**：`arfv1`

---

## 路线图总览

| Phase | 主题 | 一句话目标 | 依赖 |
|-------|------|-----------|------|
| **0** | 脚手架 | Cargo workspace + maturin + Makefile CI + 文档框架 | — |
| **1** | Bus | Rust J-RPC 广播总线：消息收发 + 节点生命周期 + 在线图 | 0 |
| **2** | State | messages + tasks 生命周期 + 双向锁 + 级联释放 | 1 |
| **3** | AgentConfig | 纯数据声明式配置骨架：models / tools / subagents / teammates | 1 |
| **4** | Engine | 收消息→调模型→得 action→发消息，Park/Resume | 1, 2, 3 |
| **5** | ModelAdapter | 内部格式 ↔ OpenAI/DeepSeek/Anthropic API | 1 |
| **6** | MCP | 工具发现/注册/执行，资源广播 | 1 |
| **7** | 集成 | E2E 测试 + 性能基准 + 完整文档 | 0-6 |

## 依赖关系

```
Phase 0 ──→ Phase 1 (Bus) ──→ Phase 2 (State)
    │            │                  │
    │            ├──────→ Phase 3 (AgentConfig)
    │            │                  │
    │            ├──────→ Phase 5 (ModelAdapter)
    │            │                  │
    │            ├──────→ Phase 6 (MCP)
    │            │                  │
    │            └──────→ Phase 4 (Engine) ── 依赖 Bus + State + AgentConfig
    │
    └─────────────────────────────────────→ Phase 7 (集成)
```

Bus 是唯一地基。AgentConfig/State/ModelAdapter/MCP 可在 Bus 完成后并行推进，Engine 需等 Bus+State+AgentConfig。

---

## 各 Phase 概要

### Phase 0 — 项目脚手架

- Cargo workspace：`arf-core`、`arf-bus`、`arf-state`、`arf-agent`、`arf-engine`
- maturin 项目 `py-arf/`，PyO3 绑定
- `Makefile`：`lint`（cargo fmt --check + cargo clippy）、`test`（cargo test + pytest）、`ci`（lint + test）
- 文档目录 `docs/v1.x/`

### Phase 1 — Bus 消息总线

- 消息格式 `{id, type, from, to?, payload}`
- 节点生命周期：`node_online` / `node_offline` / `heartbeat`
- 在线节点图：心跳超时自动标记 offline
- 消息路由：广播（to=None）+ 定向（to=Some，消费后 drain）
- 并发模型：tokio + broadcast channel

### Phase 2 — State 状态管理

- `messages`：完整消息流
- `tasks`：生命周期 `created → in_progress → blocked → resolved / failed / cancelled`
- 双向锁：`blocked_by` + `blocking`
- 级联释放：task 完成→沿 blocking 唤醒 / task 取消→沿 blocked_by 级联取消 / 节点离线→级联释放+注入通知

### Phase 3 — AgentConfig 声明式配置

- `AgentConfig` 纯数据结构：声明 agent 需要哪些资源
- `ModelSpec` / `ToolSpec` / `ResourceSpec` — 全部用逻辑名，不感知 Bus
- `ToolPermission`: Allow / Ask / Deny 三级权限
- 1:N 资源映射语义：一个 `ResourceSpec` 可匹配多个 Bus 节点，全部注册，Engine 运行时选第一个在线的
- 支持 YAML/JSON 反序列化，支持代码构造，支持 Default
- 依赖：仅 `serde` + `serde_json`，不依赖任何 ARF crate

### Phase 4 — Engine 运行引擎

- 监听 Bus 消息，按 session_id 过滤归属
- 读取 `AgentConfig`，上 Bus 做 discovery，将逻辑 ResourceSpec 解析为 `ResolvedManifest`（逻辑名 → `Vec<NodeId>`）
- model_call → Bus → ModelAdapter → model_response → Bus → Engine
- action 决策通过 Bus 发出
- Park/Resume：收到 interrupt/resume 消息 → 暂停/恢复
- State 持久化由 Engine 管理

### Phase 5 — ModelAdapter

- 内部标准消息格式 → 各供应商 API 格式
- 支持：OpenAI / DeepSeek / Anthropic
- 可插拔，新增供应商不影响其他组件
- 纯 Python 实现

### Phase 6 — MCP 资源管理

- 监听 Bus 上的 tool_call 消息，执行后发 result
- 上线时广播 `node_online{type=mcp, tools=[...]}`
- 工具执行结果回到 Bus，Engine 按 session_id 收走
- 纯 Python 实现（Rust 核心不感知 MCP 细节）

### Phase 7 — 集成与收尾

- E2E 集成测试（完整消息链路）
- 性能基准（Bus 吞吐、延迟）
- 完整 API 文档
- 教学示例集合
