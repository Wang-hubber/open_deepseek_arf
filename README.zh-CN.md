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

与其每个项目各自造轮子，不如抽象出一套框架。两件事一起做——**[ARF](https://github.com/Wang-hubber/open_deepseek_arf)（本仓库）** 提供引擎和基础设施，配套教学应用提供渐进式实战教程。配套的 Python 版教程（`arf_app`，14 单元，v0.x 版本）作为历史参考保留；**Rust 重写后的教学应用开发中**。

---

## 抽象：Agent 运行时的三层模型

ARF V1.x 把 Agent 运行拆分为三层——**Agent** 持有状态，**Engine** 驱动 ReAct 主循环，**Bus Actors** 提供能力。三者之间**全部通过 Bus 消息交互**，不存在直接调用：

```
┌──────────────────────────────────────────────────────┐
│                       Agent                           │
│  State — messages + tasks（含双向锁 blocked_by/blocking）│
│  被动状态机，由 Engine 经 Bus 消息驱动                  │
└────────────────────────┬─────────────────────────────┘
                         │ Bus: ActionMessage（Query / Command）
┌────────────────────────┴─────────────────────────────┐
│                       Engine                          │
│  GraphEngine — ReAct 主循环（turn / round / session） │
│  CheckpointRule — 订阅式生命周期规则（替代硬编码 hook）│
│  Route — Strict（Capability → NodeId）                │
│        / Discovery（按消息类型广播）                    │
│  Park/Resume — Query 消息自动挂起，回复到达自动唤醒     │
└────────────────────────┬─────────────────────────────┘
                         │ Bus: Node 上线 / 心跳 / Capability 广播
┌────────────────────────┴─────────────────────────────┐
│                 Bus Actors（能力提供者）                 │
│  ModelAdapter · MCP · Pool · Memory · Eval · ...     │
│  每个 Actor 上线时声明自身 Capability，订阅对应消息类型   │
└──────────────────────────────────────────────────────┘
```

**核心原则：Engine 不直接调用任何组件——所有交互都是 Bus 上的消息。**

### 设计要点

**Protocol 接口 + EngineBuilder 组装。** 所有能力通过 `Protocol` / Rust trait 定义契约（`ActionMessage`、`Resource`、`OnMemberFailedHandler` 等），由 `EngineBuilder` 按 `AgentConfig` 组装。`EngineBuilder::new(bus).build(config).await?` 返回可运行 Engine；用户通过 `AgentConfig`（声明式 models / tools / checkpoint_rules / on_member_failed 等）描述系统，框架只认配置，不暴露具体类。

**Engine = Bus 上的智能节点。** Engine 自身是 Bus 上的一个节点。它不持有 MCP / ModelAdapter / 其他 Actor 的引用——它**订阅 `CheckpointRule`、监听 State 变化、产出 `ActionMessage` 经 Route 投递到目标 Actor**。这是 V1.x 的核心转变：Engine 与其他组件之间没有直接方法调用，所有协作都是异步消息。

**CheckpointRule 替代硬编码 Hook。** v0.x 的 10 个检查点是引擎内硬编码的事件触发点；V1.x 改成**可订阅规则**——`CheckpointRule { trigger, when, build, route }` 四元组，全部由 `AgentConfig` 声明。Engine 在 State 变化时 `evaluate(rules, routes, graph, cache)`，纯函数地返回 `(ActionMessage, recipients)` 列表，然后逐条派发。`trigger` 仍是熟悉的 `SessionStart` / `BeforeRound` / `BeforeModel` / `AfterModel` / `BeforeTools` / `AfterTools` / `AfterRound` / `BeforeBreak` / `OnError` / `SessionEnd`——但语义从"框架触发回调"变成"声明订阅的规则"，应用可以自由组合。

**Route：Strict 与 Discovery。** `Route` 决定 `ActionMessage` 的接收方：
- **Strict** — 按 `Capability` 解析到具体 `NodeId`（如 `model_call` → 某个 `MiniMaxProvider` 节点）。Engine 维护 `DiscoveryCache`（Capability → Vec\<NodeId\>），节点上下线时 `invalidate()`。
- **Discovery** — 按消息类型广播到所有声明订阅该类型的节点。

**MessageIntent：Query 与 Command。** `ActionMessage::intent()` 是 `Query` 或 `Command`，决定 Engine 派发后的行为：
- **Query** — Engine 挂起（park）当前 ReAct turn，等待响应；响应到达时自动 resume 并继续。这是 `model_call` 的典型语义——必须等模型回复才能继续。
- **Command** — fire-and-forget，Engine 不等待响应，立即进入下一步。适合副作用型消息（trace 写入、状态广播）。

**Park/Resume 由 Query 驱动。** 与 v0.x 的 `WaitItem` + 手动 `wait()` / `finish_wait()` 不同，V1.x 的 park/resume 是**消息意图的自然结果**——Engine 发出 Query 后自动 park；任意匹配的响应到达时自动 resume。Engine 状态机里不再有"显式等待中"的状态变量。

**Bus 即注册中心。** Actor 通过 `bus.connect(node_online_with_capabilities)` 上线、广播自身 `Capability` 列表；Engine 通过 `DiscoveryCache` 缓存 `Capability → NodeId` 映射。节点 `node_offline` 时 `invalidate()`。整个系统没有文件系统扫描、没有 YAML 加载、没有 hot-reload——Bus 节点的生命周期就是注册中心的更新机制。

**Multi-Bus 协调：BarrierReceipt。** 跨 Bus 场景（顶层 Bus ↔ MCP 子 Bus）通过 `Bus::barrier(messages)` 同步跨 Bus 消息，返回 `BarrierReceipt` 确认全部投递；用于 `domain_controller` 示例中的 facade 转发。

---

## 工程落地：V1.x 的 Rust 重写

> v0.x 的 Python 原型证明了什么是对的；v1.x 的 Rust 类型系统和所有权模型让它无法是错的。

v0.x 以纯 Python 框架交付后，V1.x 把性能敏感且正确性要求极高的核心用 Rust 重写，Python 通过 PyO3 绑定。框架从此被**一条消息总线**驱动——一切皆消息，一切皆可观测。

### 仓库结构

```
crates/             # Rust workspace（核心框架）
  arf-core/         #   Protocol 定义 + 核心类型
  arf-bus/          #   消息总线（广播 + 过滤 + 心跳）
  arf-state/        #   messages + tasks 生命周期、双向锁
  arf-model-adapter/#   OpenAI / Anthropic / DeepSeek / MiniMax
  arf-mcp/          #   MCP 工具桥接（Local + Remote + Script）
  arf-engine/       #   ReAct 主循环 + Checkpoint + Pool
  arf-agent/        #   DI 组装全部 Protocol 实现
  arf-pool/         #   节点池化
  arf-e2e/          #   Rust 端到端测试

py-arf/             # Python 绑定（PyO3 + maturin，零运行时依赖）
examples/
  rust/             # Cargo workspace 成员示例
  python/           # py-arf 用法演示

docs/
  api/              # 用户 API 参考（PyTorch/LangGraph 风格）
  dev/              # 开发者文档（Phase 设计、workflow）
  architecture/     # 高层架构说明（session/round/turn、hooks、eval）
```

### V1.x 六要素

| 要素 | 职责 |
|------|------|
| **Bus** | J-RPC 广播，维护在线节点图，心跳/上线/下线报文 |
| **Engine** | 收消息 → 调模型 → 得 action → 发消息。不直接调任何组件 |
| **Agent** | 状态机骨架。不知道 Bus/MCP/其他 Agent 的存在 |
| **State** | `messages` + `tasks`，task 含双向锁（`blocked_by` / `blocking`），沿依赖链级联释放 |
| **MCP** | 监听 `tool_call` 消息，执行后发 result；上线广播工具列表 |
| **ModelAdapter** | 框架消息 ↔ 外部 API 格式。监听 `model_call` 消息 |

### 架构约束：零黑障

对开发者和调试者，**一切都是透明的，有迹可循的。** 节点上下线、task 创建/阻塞/唤醒/失败、model call 发出/返回、tool 请求/结果——全部以消息形式流经 Bus，天然可 trace、可 debug、可回放。没有隐式状态转换，没有静默失败。

### 为什么是 Rust

Bus / Engine / State / AgentBus —— 四个性能敏感且正确性要求极高的模块用 Rust 实现，Python 通过 PyO3 绑定。**Python 原型证明了什么是对的，Rust 的类型系统会让它无法是错的。**

完整设计见：**[docs/dev/v1.x-design.md](docs/dev/v1.x-design.md)**

---

## 快速开始

### Rust

```bash
cargo test --workspace
cargo run --bin domain_controller
cargo run --bin recovery
```

### Python

```bash
pip install -e ".[dev]"
. "$HOME/.cargo/env" && maturin develop --release
pytest tests/ -q
python examples/python/ex01_minimal_mock.py
```

---

## 文档

| 文档 | 内容 |
|------|------|
| [`docs/api/`](docs/api/) | **用户 API 参考** — Bus、ModelAdapter、MCP、Engine、AgentConfig、State |
| [`docs/dev/`](docs/dev/) | 开发者文档 + Phase 设计（Phase 0-6） |
| [`docs/architecture/`](docs/architecture/) | 架构概念 — session/round/turn、hooks、eval |

---

## 躬行方知：踩过的坑

提交历史中，近三成是 bug fix。这个比例本身就是故事——**边写边踩坑，边踩坑边打补丁。** 几个最深的：

**Engine — 修复密度最高的模块。** ReAct 主循环的正确性比预期更难保证。`break` 语句让 turn loop 不可达——通过了所有测试，但在特定消息序列下整个 turn 被静默跳过。park/resume 统一后连续三次回归：消息注入后再次触发 park 导致死循环、partial wakeup 时消息丢失、`cancel_event` 未清理导致跨 round 污染。**状态机的正确性不取决于 happy path 的测试覆盖率，而取决于对隐式副作用（break / cancel / park / message injection）的穷举建模。**

**A2A + Teammates — 打补丁最多的领域。** 死锁、race condition、消息消费归属混乱。park 位置在 `before_model`、`after_round`、`before_round` 之间反复迁移——每次修一个 bug，引入一个新 bug。根因只有一个：**Agent 和 Engine 的边界不够干净。** Park 散落在引擎、插件、Agent 三个层级，多 Agent 并发时相互交织，无法推理全局状态。

**路径处理。** double-join（`abspath` + `join`）静默产生错误路径，相对路径在沙箱白名单中匹配失败。文件路径不同于 API 调用——不是报错即失败，而是"看起来能工作，换个目录就崩"。

**Memory 静默失败。** LLM 记忆提取从未触发——参数名 `model=` 改成了 `model_name=`，异常被异步任务吞没。目录不存在时直接崩溃（`mkdir` 缺失）。后台任务需要显式的错误传播，静默失败是最危险的失败模式。

**ModelAdapter 错误吞没。** API 异常被静默吞噬，空字符串 `api_key` 被 SDK 拒绝，`"false"`（字符串）被 Python 判为 truthy 启用了 thinking 模式。**Python 的动态类型 + 第三方 SDK 的隐式行为 = 类型系统捕获不到的错误。** 解决之道是强制显式化——显式的 error propagation、显式的 falsy 检查、显式的字段默认值。

**核心教训：框架的正确性不是测试跑出来的，是边界条件穷举出来的。** 测试覆盖率证明"已知场景过了"，不证明"没有遗漏的场景"。真正的质量来自对每个条件分支的穷举审视——哪些状态组合可能出现？每个副作用是否被正确地清理和重置？

---

## 实战验证：教学应用（开发中）

> **状态（2026-07-01）：** V1.x 配套教学应用正在开发。框架核心（Rust crates + py-arf 绑定）已完成并通过测试，教学单元正在按 V1.x 架构编写。

Python 版教程（[arf_app](https://github.com/Wang-hubber/arf_app)，14 单元，v0.x 版本）作为历史参考保留。Rust 重写后的新版教程将渐进覆盖：

| 单元（计划） | 主题 | 验证的框架能力 |
|--------------|------|---------------|
| 01 | Hello ARF（V1.x） | Engine 组装、ReAct 启动 |
| 02 | Bus 基础 | 消息收发、在线节点图 |
| 03 | State 生命周期 | messages + tasks、双向锁 |
| 04 | ModelAdapter | OpenAI / Anthropic / DeepSeek / MiniMax 适配 |
| 05 | MCP 集成 | Local + Remote + Script 工具 |
| 06 | Hook 系统 | 10 个检查点、blocking vs side 模式 |
| 07 | Park/Resume | WaitItem、跨 session 恢复 |
| 08 | Checkpoint + Route | ActionMessage、Strict vs Discovery 路由 |
| 09 | Pool | 节点池化、资源租约 |
| 10 | Eval | 规则 + LLM-judge 指标 |

> 以上单元列表为草案，将随 V1.x 教学应用开发进度调整。

新版教程发布后，框架的每个模块都会有对应的实战场景验证——**不是"实现了功能"，是"有人用它跑通了完整业务"**，与 v0.x 教程同样的标准。

---

## License

MIT — 详见 [LICENSE](LICENSE)

<p align="center">
  <sub>ARF 框架 · <a href="https://github.com/Wang-hubber/open_deepseek_arf">GitHub</a> &nbsp;|&nbsp; 配套教程 · V1.x 开发中</sub>
  <br/>
  <sub>Built with Rust · Python · DeepSeek · MiniMax</sub>
</p>