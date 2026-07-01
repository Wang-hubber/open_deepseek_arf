<p align="center">
  <h1 align="center">ARF — AI Resources & Runtime Framework</h1>
</p>

<p align="center">
  <a href="./pyproject.toml"><img src="https://img.shields.io/badge/python-3.11+-5fa04e?style=flat-square&labelColor=161b22&logo=python&logoColor=white" alt="Python 3.11+"/></a>
  <a href="./Cargo.toml"><img src="https://img.shields.io/badge/rust-1.81+-dea584?style=flat-square&labelColor=161b22&logo=rust&logoColor=white" alt="Rust 1.81+"/></a>
  <a href="./LICENSE"><img src="https://img.shields.io/badge/license-MIT-8b949e?style=flat-square&labelColor=161b22" alt="license"/></a>
  <a href="https://github.com/Wang-hubber/open_deepseek_arf/stargazers"><img src="https://img.shields.io/github/stars/Wang-hubber/open_deepseek_arf.svg?style=flat-square&color=dbab09&labelColor=161b22&logo=github&logoColor=white" alt="GitHub stars"/></a>
</p>

<br/>

<p align="center"><a href="./resume.md"><em>对 Agent 理解的实践和思考（关于作者）</em></a></p>

<br/>

<p align="center">
  ▎ 两年做 AI 应用，每个项目都在解决类似的问题。<br/>
  ▎ 本项目是一次关于 Agent 的完整的从具体到抽象再到具体的实践。
</p>

<br/>

---

## 纸上得来终觉浅，绝知此事要躬行。

过去两年，每做一个 AI 应用，都在解决类似的共性问题——Agent 怎么调度？多轮对话的状态怎么管理？上下文超长怎么压缩？怎么知道改了一行 prompt 是变好了还是变差了？子 Agent 怎么委派、怎么通讯、怎么等结果？这些问题与业务无关，但每个项目都绕不开。

与其每个项目各自造轮子，不如抽象出一套框架。本仓库提供**框架 + 基础设施层**。配套教学应用（在另一个仓库，14 单元渐进式实战教程）尚在开发中；早期的 Python 版原型在 [arf_app](https://github.com/Wang-hubber/arf_app) 留作历史参考。

---

## 设计意图：Agent 运行时的三层模型

ARF V1.x 试图把 Agent 运行拆分为三层——**Agent** 持有状态，**Engine** 驱动 ReAct 主循环，**Bus Actors** 提供能力。三者之间**全部通过 Bus 消息交互**，不存在直接调用：

```
┌──────────────────────────────────────────────────────┐
│                       Agent                           │
│  State — messages + tasks（含双向锁 blocked_by/blocking）│
│  被动状态机，由 Engine 经 Bus 消息驱动                  │
└────────────────────────┬─────────────────────────────┘
                         │ Bus: ActionMessage（Query / Command）
┌────────────────────────┴─────────────────────────────┐
│                       Engine                          │
│  EngineBuilder — 由 AgentConfig 组装                  │
│  ReAct 主循环（turn / round / session）               │
│  CheckpointRule — 订阅式生命周期规则（替代硬编码 hook）│
│  Route — Strict（Capability → NodeId）                │
│        / Discovery（按 Capability 匹配广播）           │
│  Park/Resume — Query 消息自动挂起，回复到达自动唤醒     │
└────────────────────────┬─────────────────────────────┘
                         │ Bus: Node 上线 / 心跳 / Capability 广播
┌────────────────────────┴─────────────────────────────┐
│                 Bus Actors（能力提供者）                 │
│  ModelAdapter · MCP · Pool · ...                      │
│  每个 Actor 上线时声明自身 Capability，订阅对应消息类型   │
└──────────────────────────────────────────────────────┘
```

**核心原则：Engine 不直接调用任何组件——所有交互都是 Bus 上的消息。**

### 实际落地状态（Phase 6，2026-07-01）

上图是**设计意图**。现实比这落后一步——实现已向此模型收敛，但并非所有承诺都已兑现：

| 设计承诺 | 落地状态 |
|---|---|
| Engine 是 Bus 节点；不直接调用任何组件 | ✅ 所有 `model_call` / `tool_exec` 都走 Bus |
| Protocol 接口 + DI 组装 | ✅ `ActionMessage`、`Resource`、`Provider`、`CheckpointRule` 等 trait |
| `AgentConfig` 是声明式单一真源 | ✅ Rust struct，提供 Default impl；尚无 Python 包装 |
| `CheckpointRule` 替代硬编码 hook | ✅ 5 个触发点：`BeforeModelCall`、`AfterModelCall`、`BeforeToolExec`、`AfterToolExec`、`RoundEnd` |
| `Route::Strict(Vec<NodeId>)` | ✅ |
| `Route::Discovery(Capability)` — 广播给所有匹配节点 | ✅ 经 `DiscoveryCache`（Capability → Vec\<NodeId\>）解析，节点上下线时 `invalidate()` |
| `MessageIntent` — Query 自动 park，Command fire-and-forget | ✅ 两种 Intent 都已实现；`WaitStrategy::Any`/`All`/`Count(n)` 控制 park 行为 |
| Park/Resume 由 Query 驱动，无显式等待状态 | ✅ `publish_and_await_query` 是唯一的 park/resume 路径 |
| 多 Bus 协调：`Bus::barrier(...) → BarrierReceipt` | ✅ `barrier(participants: Vec<NodeId>, timeout: Duration) → BarrierReceipt` |
| `OnMemberFailedHandler` 在节点失败时被调用 | ⚠️ Trait + `AgentConfig.on_member_failed` 字段存在，但 **`Engine.run()` 尚未调用它**。`MemberFailedAction::Retry` / `SwitchTo` 已声明，目前仅 `FailSession` 完整实现 |
| 10 个 V0.x 风格生命周期 hook（session_start、before_round、on_error…） | ❌ 已被上面 5 个 Checkpoint 触发点取代。V0.x 的 10-hook 系统完全移除 |

### 设计要点（按当前实现）

**Protocol 接口 + EngineBuilder 组装。** 所有能力通过 Rust trait 定义契约（`ActionMessage`、`Resource`、`Provider`、`OnMemberFailedHandler` 等），由 `EngineBuilder` 按 `AgentConfig` 组装。`EngineBuilder::new(buses=[bus]).build(config).await?` 返回可运行 Engine；用户通过 `AgentConfig`（`agent_id`、`model_config`、`routes`、`checkpoint_rules`、`on_member_failed`、`tools_include` / `tools_exclude` 等）描述系统，框架只认配置，不暴露具体类。

**Engine = Bus 上的智能节点。** Engine 自身是 Bus 上的一个节点。它不持有 MCP / ModelAdapter / 其他 Actor 的引用——它**订阅 `CheckpointRule`、监听 State 变化、产出 `ActionMessage` 经 Route 投递到目标 Actor**。这是 V1.x 的核心转变：Engine 与其他组件之间没有直接方法调用，所有协作都是异步消息。

**CheckpointRule 替代硬编码 Hook。** V1.x 提供 5 个固定注入点——`BeforeModelCall`、`AfterModelCall`、`BeforeToolExec`、`AfterToolExec`、`RoundEnd`——而非 v0.x 的 10 个硬编码生命周期 hook。一条规则是 4 元组：

```rust
pub struct CheckpointRule {
    pub name: String,
    pub trigger: Checkpoint,                              // 上面 5 个之一
    pub when:    Box<dyn Fn(&State) -> bool + Send + Sync>,
    pub build:   Box<dyn Fn(&State) -> Box<dyn ActionMessage> + Send + Sync>,
}
```

**没有 `route` 字段**——路由单源化在 `AgentConfig.routes: HashMap<String, Route>`（msg_type → Route）。Engine 把每条规则的 `build(state)` 产物按照消息 `msg_type` 查到的 Route 派发。State 变化时，Engine 评估匹配规则，每条 emit 的消息按 Route 派发。

**Route：Strict 与 Discovery。** `Route` 决定 `ActionMessage` 的接收方：
- **Strict** — 预解析的 `NodeId` 列表。调用者精确知道哪些节点该收到。
- **Discovery** — Engine 查 `DiscoveryCache(capability)`，扇出给所有匹配且在线的节点。缓存节点上下线时 `invalidate()`。**Discovery 按 Capability 解析，不是按 msg_type 广播**——节点仍通过 `MessageFilter` 订阅，但 Engine 看待接收方的视角是 capability。

**MessageIntent：Query 与 Command。** `ActionMessage::intent()` 是 `Query` 或 `Command`，决定 Engine 派发后的行为：
- **Query** — Engine 挂起（park）当前 ReAct turn，等待响应（受 `WaitStrategy` 控制）；响应到达时自动 resume 并继续。这是 `model_call` 的典型语义。
- **Command** — fire-and-forget，Engine 不等待响应，立即进入下一步。适合副作用型消息（trace 写入、状态广播）。内置的 `MemoryOp::extract` 就是这种用法。

**Park/Resume 由 Query 驱动。** 与 v0.x 的 `WaitItem` + 手动 `wait()` / `finish_wait()` 不同，V1.x 的 park/resume 是**消息意图的自然结果**——Engine 发出 Query 后自动 park；任意匹配的响应到达（受 `WaitStrategy` 控制）时自动 resume。Engine 状态机里不再有"显式等待中"的状态变量。

**Bus 即注册中心。** Actor 通过 `bus.connect(node_online_with_capabilities)` 上线、广播自身 `Capability` 列表；Engine 通过 `DiscoveryCache` 缓存 `Capability → NodeId` 映射。节点 `node_offline` 时 `invalidate()`。整个系统没有文件系统扫描、没有 YAML 加载、没有 hot-reload——Bus 节点的生命周期就是注册中心的更新机制。

**Multi-Bus 协调：BarrierReceipt。** 跨 Bus 场景（顶层 Bus ↔ MCP 子 Bus）通过 `Bus::barrier(participants: Vec<NodeId>, timeout: Duration)` 同步，返回 `BarrierReceipt { correlation_id, acked, missing, timed_out }`。用于 `domain_controller` 示例中的 facade 转发。

---

## 工程落地：V1.x 的 Rust 重写

> v0.x 的 Python 原型证明了什么是对的；v1.x 的 Rust 类型系统和所有权模型让它无法是错的。

v0.x 以纯 Python 框架交付后，V1.x 把性能敏感且正确性要求极高的核心用 Rust 重写，Python 通过 PyO3 绑定。框架从此被**一条消息总线**驱动——一切皆消息，一切皆可观测。

### 仓库结构

```
crates/              # Rust workspace（框架核心）
  arf-core/          #   Protocol trait + 核心类型（Message、NodeId、Route、Checkpoint…）
  arf-bus/           #   消息总线（广播 + 过滤 + 心跳 + barrier）
  arf-state/         #   messages + tasks 生命周期、双向锁
  arf-model-adapter/ #   OpenAI / Anthropic / DeepSeek / MiniMax Provider + Bus 节点
  arf-mcp/           #   MCP 工具桥接（Local + Remote + Script）
  arf-engine/        #   ReAct 主循环 + Checkpoint 评估 + Route 解析
  arf-pool/          #   通用 Resource 池化 + PoolNode 集成
  arf-agent/         #   DI 组装全部 Protocol 实现（脚手架）
  arf-e2e/           #   Rust 端到端测试

py-arf/              # Python 绑定（PyO3 + maturin，零运行时依赖）
  src/               #   PyO3 模块源码
  python/arf/        #   Python 包（re-export arf._arf）
  tests/             #   Python 绑定单测 + 集成测试

examples/
  rust/              # Cargo workspace 成员
    domain_controller/   # McpFacade 示例（顶层 Bus ↔ MCP 子 Bus 转发）
    recovery/            # App 级恢复（AppCheckpoint + Bus::barrier + 文件持久化）
  python/            # py-arf 用法演示（ex01-ex08 + phase0/1/4/6_overview）

docs/
  api/               # 用户 API 参考（PyTorch/LangGraph 风格）
  dev/               # 开发者文档 + Phase 设计（Phase 0-6）
  architecture/      # 高层架构说明（session data layout、A2A、park-resume、eval）
```

**`arf-agent` 当前是脚手架**——实际的 Engine 组装在 `arf-engine` 的 `EngineBuilder` 里。`AgentConfig` 出现在两处（`arf-agent` 和 `arf-engine`），以 engine 层的为运行时真源。

### V1.x 六要素

| 要素 | 职责 | crate |
|---|---|---|
| **Bus** | J-RPC 广播，维护在线节点图，心跳/上线/下线报文；`barrier()` 跨 Bus 同步 | `arf-bus` |
| **Engine** | 收消息 → 调模型 → 得 action → 发消息。不直接调任何组件 | `arf-engine` |
| **Agent** | 状态机骨架（`State`、`OverView`）。不知道 Bus/MCP/其他 Agent 的存在 | `arf-state` + `arf-core` |
| **State** | `messages` + `tasks`，task 含双向锁（`blocked_by` / `blocking`），沿依赖链级联释放 | `arf-state` |
| **MCP** | 监听 `tool_exec` 消息，执行后发 `tool_result`；上线广播工具列表 | `arf-mcp` |
| **ModelAdapter** | 框架消息 ↔ 外部 API 格式。监听 `model_call` 消息 | `arf-model-adapter` |

### 架构约束：零黑障

对开发者和调试者，**一切都是透明的，有迹可循的**。节点上下线、task 创建/阻塞/唤醒/失败、model call 发出/返回、tool 请求/结果——全部以消息形式流经 Bus，天然可 trace、可 debug、可回放。没有隐式状态转换，没有静默失败。

### 为什么是 Rust

Bus / Engine / State / ModelAdapter —— 四个性能敏感且正确性要求极高的模块用 Rust 实现，Python 通过 PyO3 绑定。**Python 原型证明了什么是对的，Rust 的类型系统会让它无法是错的。**

完整设计草稿：**[docs/dev/v1.x-design.md](docs/dev/v1.x-design.md)**（文档明确标注为"设计草案"——反映原始愿景，与当前实现细节不完全一致）。

---

## 快速开始

### Rust

```bash
cargo test --workspace                        # 全部 Rust crate + examples
cargo run --bin domain_controller            # McpFacade 多 Bus 示例
cargo run --bin recovery                     # App 级恢复示例
```

### Python

```bash
pip install -e ".[dev]"                      # 装 Python 包 + dev 依赖
. "$HOME/.cargo/env" && maturin develop --release   # 编译 PyO3 绑定
pytest py-arf/tests/ -q                      # 跑 Python 绑定测试
python examples/python/ex01_minimal_mock.py  # 最简 Engine 示例
make test                                    # 或：跑 Rust + Python 全部测试
```

> 注意：从仓库根目录跑 `pytest tests/ -q` 命中的是占位的 `tests/` 目录（预留给将来的跨语言集成测试，目前为空）。实际的 Python 绑定测试在 `py-arf/tests/`。

---

## 文档

| 文档 | 内容 |
|------|------|
| [`docs/api/`](docs/api/) | **用户 API 参考** — Bus、ModelAdapter、MCP、Engine、AgentConfig、State |
| [`docs/dev/`](docs/dev/) | 开发者文档 + Phase 设计（Phase 0-6） |

> 架构概览见 [`README.md`](README.md)（本仓库的根 README）以及 `docs/dev/v1.x-design.md` 与 `docs/dev/phase*/` 各 crate 设计文档。原 `docs/architecture/` 是 V0.x 专属文档，已删除。

---

## 躬行方知：v0.x 踩过的坑

v0.x 提交历史中，近三成是 bug fix。下面这些是 **v0.x Python 框架的教训**——它们塑造了 V1.x 的 Rust 设计方向，但不是 V1.x 实现自身的 bug：

**Engine — 修复密度最高的模块（v0.x）。** ReAct 主循环的正确性比预期更难保证。`break` 语句让 turn loop 不可达——通过了所有测试，但在特定消息序列下整个 turn 被静默跳过。park/resume 统一后连续三次回归：消息注入后再次触发 park 导致死循环、partial wakeup 时消息丢失、`cancel_event` 未清理导致跨 round 污染。**教训：** 状态机的正确性不取决于 happy path 的测试覆盖率，而取决于对隐式副作用（break / cancel / park / message injection）的穷举建模。V1.x 的回应：让 park/resume 成为消息 Intent 的函数，而非显式状态变量。

**A2A + Teammates — 打补丁最多的领域（v0.x）。** 死锁、race condition、消息消费归属混乱。park 位置在 `before_model`、`after_round`、`before_round` 之间反复迁移——每次修一个 bug，引入一个新 bug。根因：**Agent 和 Engine 的边界不够干净。** Park 散落在引擎、插件、Agent 三个层级，多 Agent 并发时相互交织，无法推理全局状态。V1.x 的回应：统一到一条 Bus，砍掉 v0.x 的 subagent/teammate 两种独立模式——peer message = 定向 Bus 消息。

**路径处理（v0.x）。** double-join（`abspath` + `join`）静默产生错误路径，相对路径在沙箱白名单中匹配失败。文件路径不同于 API 调用——不是报错即失败，而是"看起来能工作，换个目录就崩"。V1.x 把路径处理下放给 MCP 节点（每个节点拥有自己的 root），消除这一横切关注点。

**Memory 静默失败（v0.x）。** LLM 记忆提取从未触发——参数名 `model=` 改成了 `model_name=`，异常被异步任务吞没。目录不存在时直接崩溃（`mkdir` 缺失）。**后台任务需要显式的错误传播，静默失败是最危险的失败模式。** V1.x 用 `Result` 返回值让这在 safe Rust 里成为不可表达的状态。

**ModelAdapter 错误吞没（v0.x）。** API 异常被静默吞噬，空字符串 `api_key` 被 SDK 拒绝，`"false"`（字符串）被 Python 判为 truthy 启用了 thinking 模式。**Python 的动态类型 + 第三方 SDK 的隐式行为 = 类型系统捕获不到的错误。** 解决之道是强制显式化——V1.x 的 `Provider` trait 在类型层面要求显式 `Result` 返回、显式字段默认值。

**核心教训：框架的正确性不是测试跑出来的，是边界条件穷举出来的。** 测试覆盖率证明"已知场景过了"，不证明"没有遗漏的场景"。真正的质量来自对每个条件分支的穷举审视——哪些状态组合可能出现？每个副作用是否被正确地清理和重置？Rust 的类型系统收窄了搜索空间（没有 `except: pass`、没有字符串布尔值），但不能消除它。

---

## 实战验证：教学应用（开发中）

> **状态（2026-07-01）：** V1.x 配套教学应用正在开发。框架核心（Rust crates + py-arf 绑定）已实现并经 Phase 6 完整测试（`docs/dev/phase6/`）；教学单元待开发启动后再撰写。

Python 版教程（[arf_app](https://github.com/Wang-hubber/arf_app)，14 单元，v0.x 版本）作为历史参考保留。V1.x 教程的单元大纲暂时收起，待开发启动后再公开——避免把草案当 roadmap 展示。

新版教程发布后，框架的每个模块都会有对应的实战场景验证——**不是"实现了功能"，是"有人用它跑通了完整业务"**，与 v0.x 教程同样的标准。

---

## License

MIT — 详见 [LICENSE](LICENSE)

<p align="center">
  <sub>ARF 框架 · <a href="https://github.com/Wang-hubber/open_deepseek_arf">GitHub</a> &nbsp;|&nbsp; 配套教程 · V1.x 开发中</sub>
  <br/>
  <sub>Built with Rust · Python · DeepSeek · MiniMax</sub>
</p>
