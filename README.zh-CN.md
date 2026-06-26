<p align="center">
  <h1 align="center">ARF — AI Resources & Runtime Framework</h1>
</p>

<p align="center">
  <a href="./pyproject.toml"><img src="https://img.shields.io/badge/python-3.11+-5fa04e?style=flat-square&labelColor=161b22&logo=python&logoColor=white" alt="Python 3.11+"/></a>
  <a href="./LICENSE"><img src="https://img.shields.io/badge/license-MIT-8b949e?style=flat-square&labelColor=161b22" alt="license"/></a>
  <a href="https://github.com/Wang-hubber/open_deepseek_arf/stargazers"><img src="https://img.shields.io/github/stars/Wang-hubber/open_deepseek_arf.svg?style=flat-square&color=dbab09&labelColor=161b22&logo=github&logoColor=white" alt="GitHub stars"/></a>
</p>

<br/>

<p align="center">
  ▎ 从解决具体的业务问题到形而上学的抽象探索，再从抽象探索回归工程落地。
  <br/>
  ▎ 本项目是一次关于 Agent 的完整的从具体到抽象再到具体的一次实践。
</p>

<br/>

---

## 纸上得来终觉浅，绝知此事要躬行。 What I cannot create, I do not understand.

过去两年，每做一个 AI 应用，都在重复发明同一套基础设施——Agent 怎么调度？多轮对话的状态怎么管理？上下文超长怎么压缩？怎么知道改了一行 prompt 是变好了还是变坏了？子 Agent 怎么委派、怎么通讯、怎么等结果？这些问题与业务无关，但每个项目都绕不开。

与其每个项目各自造轮子，不如抽象出一套框架。两件事一起做了——**[ARF](https://github.com/Wang-hubber/open_deepseek_arf)（本仓库）** 提供引擎和基础设施，**[arf_app](https://github.com/Wang-hubber/arf_app)** 提供 14 个单元的渐进式实战教程。一个抽象，一个验证；一个造轮子，一个教人用轮子。

---

## 抽象：Agent 运行时的三层模型

ARF 将 Agent 运行拆分为三层——Agent 持有状态，Harness 驱动执行，Resources 提供能力：

```
┌──────────────────────────────────────────────────────┐
│                    Agent                             │
│  name + system_prompt + models                       │
│  被动的消息状态机，由 Harness 驱动执行                   │
│  input() / model_call() / wait() / finish_wait()     │
└────────────────────────┬─────────────────────────────┘
                         │ 依赖注入（DI）
┌────────────────────────┴─────────────────────────────┐
│                    Harness                           │
│  Engine — ReAct 主循环                                │
│  Plugin 调度 — 10 个生命周期检查点                      │
│  Park/Resume — 等待/唤醒                               │
│  Trace — JSONL 事件流                                 │
└────────────────────────┬─────────────────────────────┘
                         │ 文件系统 + 资源发现
┌────────────────────────┴─────────────────────────────┐
│                    Resources                         │
│  tools/ · skills/ · models/ · hooks/                 │
│  FileWatcher 热加载 · ResourceResolver 覆盖解析        │
│  Sandbox 安全边界 · Guardrails 权限校验                 │
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

## 回归：工程落地

> 截至 2026 年 6 月，v0.8.0 — 1,492 次提交，~18,000 行框架代码，~12,500 行测试（723 个测试用例）

### 核心模块

| 模块 | 目录 | 说明 |
|------|------|------|
| **Engine** | `arf/engine/` | ReAct 主循环、park/resume、cancel/undo |
| **Agent** | `arf/agent/` | BaseAgent DI 组装、PrimitiveAgent 状态机 |
| **Core** | `arf/core/` | Protocol 定义、ModelAdapter（OpenAI/DeepSeek 格式适配） |
| **Resources** | `arf/resources/` | FileWatcher 热加载、三种 Provider、覆盖解析 |
| **Memory** | `arf/memory/` | FileMemoryStore、异步 LLM 提取/检索 |
| **Compaction** | `arf/compaction/` | SlidingWindowCompactor、Tool Output Externalization |
| **Routing** | `arf/routing/` | TwoTierRouter 快慢模型调度 |
| **Guardrails** | `arf/guardrails/` | PathCheckToolGuard、ToolPermissionChecker |
| **Sandbox** | `arf/sandbox/` | PathSandbox 路径合法性校验 |
| **Communication** | `arf/communication/` | AgentBus、PeerAgent、Supervisor |
| **Human Loop** | `arf/human_loop/` | ApprovalPoint、ConsoleChannel 审批流 |
| **Observability** | `arf/observability/` | TracePlugin JSONL 写入/读取 |
| **Streaming** | `arf/streaming/` | SSE 事件流适配与序列化 |
| **Evaluation** | `arf/evaluation/` | EvalRunner、BenchmarkBuilder、EvalComparator |
| **Concurrency** | `arf/concurrency/` | SequentialScheduler |
| **Skills** | `arf/skills/` | SkillPipeline 工具依赖执行时序 |

### 插件系统

| 插件 | 说明 |
|------|------|
| `filesystem` | 文件读写删工具 |
| `memory` | LLM 记忆提取/检索 |
| `compaction` | 渐进式上下文压缩 |
| `approval` | 人机审批流转 |
| `tool_guard` | 工具权限校验（deny/allow/ask） |
| `error_handler` | 错误恢复与降级 |
| `a2a_subagents` | 一次性子代理委派 |
| `a2a_teammates` | 持久化对等队友通讯 |
| `eval` | 评测运行器、判定器、自动标注 |
| `trace` | Hook-mounted JSONL 事件追踪 |

### Session > Round > Turn

```
session  >  round  >  turn
  │           │          └─ ReAct 步骤：一次 model_call [+ tool_calls]
  │           └─ 一次 user_input → final_output
  └─ 多轮对话，跨多次 chat()，独立 state_store 和 trace 文件
```

### 实战验证：arf_app

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
| 12 | 子 Agent | delegate_task 派发临时子 Agent、并行加速 |
| 13 | Agent Team | PM+Data+Viz 三人团队、AgentBus 对等协作 |
| 14 | Skill | 单 Agent + Skill + Subagents vs 多人团队对比 |

14 个单元全部跑通。框架的每个模块都有对应的实战场景验证——**不是"实现了功能"，是"有人用它跑通了完整业务"。**

---

## 躬行方知：踩过的坑

1,492 次提交中，418 次 bug fix（28%），156 次重构（10%）。这个数字本身就是故事——**边写边踩坑，边踩坑边打补丁。** 几个最深的：

**Engine — 50 次 fix。** ReAct 主循环的正确性比预期更难保证。`break` 语句让 turn loop 不可达——通过了所有测试，但在特定消息序列下整个 turn 被静默跳过。park/resume 统一后连续三次回归：消息注入后再次触发 park 导致死循环、partial wakeup 时消息丢失、cancel_event 未清理导致跨 round 污染。**状态机的正确性不取决于 happy path 的测试覆盖率，而取决于对隐式副作用（break/cancel/park/message injection）的穷举建模。**

**A2A + Teammates — 36 次 fix。** 死锁、race condition、消息消费归属混乱。park 位置在 `before_model`、`after_round`、`before_round` 之间反复迁移至少 5 次——每次修一个 bug，引入一个新 bug。根因只有一个：**Agent 和 Harness 的边界不够干净。** Park 散落在引擎、插件、Agent 三个层级，多 Agent 并发时相互交织，无法推理全局状态。

**路径处理。** double-join（`abspath` + `join`）静默产生错误路径，相对路径在沙箱白名单中匹配失败。文件路径不同于 API 调用——不是报错即失败，而是"看起来能工作，换个目录就崩"。

**Memory 静默失败。** LLM 记忆提取从未触发——参数名 `model=` 改成了 `model_name=`，异常被异步任务吞没。目录不存在时直接崩溃（`mkdir` 缺失）。后台任务需要显式的错误传播，静默失败是最危险的失败模式。

**ModelAdapter 错误吞没。** API 异常被静默吞噬，空字符串 api_key 被 SDK 拒绝，`"false"`（字符串）被 Python 判为 truthy 启用了 thinking 模式。**Python 的动态类型 + 第三方 SDK 的隐式行为 = 类型系统捕获不到的错误。** 解决之道是强制显式化——显式的 error propagation、显式的 falsy 检查、显式的字段默认值。

**核心教训：框架的正确性不是测试跑出来的，是边界条件穷举出来的。** 测试覆盖率证明"已知场景过了"，不证明"没有遗漏的场景"。真正的质量来自对每个条件分支的穷举审视——哪些状态组合可能出现？每个副作用是否被正确地清理和重置？

---

## 第二轮：从具体回归抽象

第一个循环完成了——从业务痛点出发，抽象出框架，落地为代码。现在站在代码的废墟上（418 个 fix 的伤疤还热着），进入第二个循环。核心矛盾很清晰：**Harness 承担了太多不该它承担的责任。**

- **Park/Resume 散落三级。** Engine checkpoint → Plugin wait 注册 → Agent wait() 调用，没有统一的等待状态抽象。
- **A2A 与 Harness 耦合过深。** 消息注入时机、消费归属、reply 组装——嵌在 Engine 和 Plugin 实现中，而非独立通讯层。
- **Agent 边界模糊。** PrimitiveAgent 对外暴露了 `wait()` / `finish_wait()`——Agent 本不该知道自己被挂起。
- **State 不够鲁棒。** 当前只是消息列表 + waiting 字典的序列化快照。缺少对状态转换的显式建模——无法回答"当前处于什么阶段""为什么进入这个状态""哪些副作用已处理"。
- **Trace 只监控 Agent，不监控调度器。** Harness 自身的调度决策——为什么 park、消息何时注入——对排查 A2A 问题至关重要却不可见。
- **Log 系统性缺失。** 大量排查时间花在"不知道发生了什么"上。关键路径（park/wake、消息注入、状态落盘）没有统一的 debug 粒度日志。

下一轮的方向：

1. **更彻底的抽象。** Agent 不感知挂起，Harness 统一调度。AgentBus 独立为通讯层。State 从消息快照提升为状态机摘要。
2. **更干净的分离。** Engine 只做"取消息→调模型→执行工具→写结果"。Park/Resume 是调度器的事。A2A 是通讯层的事。Trace 双视角覆盖 Agent + Scheduler。Log 是独立基础设施。
3. **更通用可扩展。** 模型调度策略、Agent 通讯拓扑——从具体实现抽象为可插拔接口。
4. **Rust 重写核心。** Engine 主循环、State 状态机、Park/Resume 调度器、AgentBus 消息传递——这四个性能敏感且正确性要求极高的模块，用 Rust 重写，Python 通过 PyO3 绑定。Rust 的类型系统和所有权模型天然适合状态机验证——Python 原型证明了什么是对的，Rust 会让它无法是错的。系统性的 Log 层也在 Rust 侧统一实现，确保关键路径零遗漏。

这不是重写计划，这是 **"把已验证的设计翻译成更严格的语言"。**

---

## 文档

| 文档 | 内容 |
|------|------|
| [`docs/agent.md`](docs/agent.md) | Agent 配置、组装、模型适配器、工具执行 |
| [`docs/a2a-communication.md`](docs/a2a-communication.md) | Subagents vs Teammates 机制对比与案例 |
| [`docs/park-resume.md`](docs/park-resume.md) | Park/Resume 等待唤醒机制 |
| [`docs/eval-benchmark.md`](docs/eval-benchmark.md) | 评测系统：Benchmark 构建、Runner、Comparator |

---

## 开发

```bash
pip install -e ".[dev]" -i https://pypi.mirrors.ustc.edu.cn/simple
pytest tests/ -q                       # 全部测试
pytest tests/ -q -m "not slow"         # 跳过慢测试
```

提交格式：`type(scope): description`（如 `feat(engine):`、`fix(a2a):`、`docs:`、`test:`）。

---

## 许可

MIT — 详见 [LICENSE](LICENSE)

<p align="center">
  <sub>ARF 框架 · <a href="https://github.com/Wang-hubber/open_deepseek_arf">GitHub</a> &nbsp;|&nbsp; 配套教程 · <a href="https://github.com/Wang-hubber/arf_app">arf_app</a></sub>
  <br/>
  <sub>Built with Python · DeepSeek · LangGraph</sub>
</p>
