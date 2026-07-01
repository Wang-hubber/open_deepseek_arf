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

与其每个项目各自造轮子，不如抽象出一套框架。两件事一起做了——**[ARF](https://github.com/Wang-hubber/open_deepseek_arf)（本仓库）** 提供引擎和基础设施，**[arf_app](https://github.com/Wang-hubber/arf_app)** 提供 14 个单元的渐进式实战教程。一个抽象，一个验证；一个造轮子，一个教人用轮子。

---

## 抽象：Agent 运行时的三层模型

ARF 将 Agent 运行拆分为三层——Agent 持有状态，Engine 驱动执行，Resources 提供能力：

```
┌──────────────────────────────────────────────────────┐
│                       Agent                           │
│  name + system_prompt + models                       │
│  被动的消息状态机，由 Engine 驱动执行                  │
│  input() / model_call() / wait() / finish_wait()     │
└────────────────────────┬─────────────────────────────┘
                         │ 依赖注入（DI）
┌────────────────────────┴─────────────────────────────┐
│                       Engine                          │
│  GraphEngine — ReAct 主循环                           │
│  Hook 调度 — 10 个生命周期检查点                      │
│  Park/Resume — 等待/唤醒                              │
│  Trace — JSONL 事件流                                 │
└────────────────────────┬─────────────────────────────┘
                         │ 文件系统 + 资源发现
┌────────────────────────┴─────────────────────────────┐
│                    Resources                          │
│  tools/ · skills/ · models/ · hooks/                 │
│  FileWatcher 热加载 · ResourceResolver 覆盖解析       │
│  Guardrails 路径安全 · path_check 权限校验             │
└──────────────────────────────────────────────────────┘
```

**核心原则：框架提供 mechanism（怎么做），应用通过 configuration + instantiation 决定做什么。**

### 设计要点

**依赖注入 + Protocol 接口隔离。** 所有能力通过 Protocol 定义契约，通过 DI 组装。`BaseAgent.__init__(**override_protocols)` 可替换任意实现——框架不 import 具体类，只认接口。

**Hook：10 个检查点 × 2 种模式。** 插件不修改引擎代码。引擎在 10 个检查点触发事件，每个检查点均支持两种模式：

| 检查点 | 触发时机 |
|--------|---------|
| `session_start` | 会话开始 / resume 恢复 |
| `before_round` | 每轮 `chat()` 入口，park 在此 |
| `before_model` | 模型调用前 |
| `after_model` | 模型响应后 |
| `before_tools` | 工具执行前 |
| `after_tools` | 工具执行后、结果 commit 前（externalization 在此） |
| `after_round` | 本轮结束 |
| `before_break` | 引擎 break 前（task_complete 校验在此） |
| `on_error` | 异常发生时 |
| `session_end` | 会话结束，cleanup |

- **blocking** — 顺序执行，可修改 ctx 注入数据或中断流程
- **side** — `asyncio.create_task` 并发执行，fire-and-forget

**文件系统即注册中心。** 工具、技能、模型都是目录加 YAML 配置。`FileWatcher` 热加载，无需重启。`git push` 即共享配置。

**两种 A2A 通讯。** Subagents（父子委派，一次性用完即焚）+ Teammates（对等队友，整个 session 的 park/wake 协作循环）。

**Park/Resume 等待唤醒。** Agent 等待外部输入（用户审批、peer 回复、子 agent 完成）时挂起，事件到达时唤醒。`WaitItem` 记录原因和 `resume_key`，支持跨 session 恢复。

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

## 实战验证：arf_app

框架的正确性不能靠自说自话。**[arf_app](https://github.com/Wang-hubber/arf_app)** 用 14 个渐进单元，从零构建一个可评估、可进化的 Agent，每一轮走完"教学目标 → 文档 → 代码 → 验证"的闭环：

| 单元 | 主题 | 验证的框架能力 |
|------|------|---------------|
| 01 | Hello ARF | Agent 组装、系统提示词注入、会话创建 |
| 02 | 会话管理 | 多会话生命周期、LLM 自动标题生成 |
| 03 | 工具引入 | 文件读写工具、ReAct 思考→调用→执行→回复 |
| 04 | 工具审批 | session_mode、审批事件处理、运行时策略切换 |
| 05 | 安全体系 | deny 黑名单、正则拦截、PathSandbox |
| 06 | 长期记忆 | Memory 插件、跨会话身份信息持久化 |
| 07 | 收敛 Agent | 温度、提示词与运行时优化 |
| 08 | Trace 轨迹 | JSONL 产物、trace 命令、为 Eval 铺路 |
| 09 | Eval 评估 | 规则型指标、golden session → 标注 → 构建 → 对比 |
| 10 | LLM Judge | 模型评估模型、自动标注半自动化流水线 |
| 11 | 版本持久化 | 版本存档、自动回归检测 |
| 12 | 子 Agent | `delegate_task` 派发临时子 Agent、并行加速 |
| 13 | Agent Team | PM + Data + Viz 三人团队、AgentBus 对等协作 |
| 14 | Skill | 单 Agent + Skill + Subagents vs 多人团队对比 |

14 个单元全部跑通。框架的每个模块都有对应的实战场景验证——**不是"实现了功能"，是"有人用它跑通了完整业务"。**

---

## License

MIT — 详见 [LICENSE](LICENSE)

<p align="center">
  <sub>ARF 框架 · <a href="https://github.com/Wang-hubber/open_deepseek_arf">GitHub</a> &nbsp;|&nbsp; 配套教程 · <a href="https://github.com/Wang-hubber/arf_app">arf_app</a></sub>
  <br/>
  <sub>Built with Rust · Python · DeepSeek · MiniMax</sub>
</p>