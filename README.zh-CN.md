<p align="center">
  <h1 align="center">ARF — AI Resources & Runtime Framework</h1>
</p>

<p align="center">
  <a href="./pyproject.toml"><img src="https://img.shields.io/badge/python-3.11+-5fa04e?style=flat-square&labelColor=161b22&logo=python&logoColor=white" alt="Python 3.11+"/></a>
  <a href="./LICENSE"><img src="https://img.shields.io/badge/license-MIT-8b949e?style=flat-square&labelColor=161b22" alt="license"/></a>
  <a href="https://github.com/Wang-hubber/open_deepseek_arf/stargazers"><img src="https://img.shields.io/github/stars/Wang-hubber/open_deepseek_arf.svg?style=flat-square&color=dbab09&labelColor=161b22&logo=github&logoColor=white" alt="GitHub stars"/></a>
</p>

<br/>

<h3 align="center">Harness = OS 内核 &nbsp;·&nbsp; Model = CPU &nbsp;·&nbsp; Agent = 进程</h3>
<p align="center">Token 是指令，Agent 会话是进程，工具调用是系统调用。</p>
<p align="center">一套以操作系统理念构建的 Agent 运行时框架——依赖注入组装所有能力，Protocol 定义接口隔离，插件系统贯穿全部生命周期。</p>

<br/>

---

## 架构概览

ARF 将 Agent 的运行抽象为操作系统的三层结构：

```
┌──────────────────────────────────────────────────────┐
│                  🧠 Agent（大脑）                      │
│  name + system_prompt + models                       │
│  被动的消息状态机，由 Harness 驱动执行                   │
│  input() / model_call() / wait() / finish_wait()     │
└────────────────────────┬─────────────────────────────┘
                         │ 依赖注入（DI）
┌────────────────────────┴─────────────────────────────┐
│                  ⚙️ Harness（脊椎）                    │
│  GraphEngine — ReAct 主循环 + HandoffManager          │
│  Plugin 调度 — 7 个生命周期 Hook 事件                   │
│  park / resume — 进程级阻塞/唤醒                        │
│  Trace 落盘 — JSONL 事件流                             │
└────────────────────────┬─────────────────────────────┘
                         │ 文件系统 + 资源发现
┌────────────────────────┴─────────────────────────────┐
│                  📦 Resources（身体）                  │
│  tools/ · skills/ · models/ · hooks/                 │
│  FileWatcher 热加载 · ResourceResolver 覆盖解析        │
│  Sandbox 安全边界 · Guardrails 权限校验                 │
└──────────────────────────────────────────────────────┘
```

**核心原则：框架提供 mechanism（怎么做），应用通过 configuration + instantiation 决定做什么。** 依赖注入优先，不硬编码具体实现。Protocol 优先于 ABC 继承。

---

## 当前完成进度

> 截至 2026 年 6 月，ARF v0.8.0 — 1,492 次提交，~18,000 行框架代码，~12,500 行测试（723 测试函数）

### 核心模块

| 模块 | 目录 | 说明 |
|------|------|------|
| **Engine** | `arf/engine/` | ReAct 主循环、HandoffManager、park/resume、cancel/undo |
| **Agent** | `arf/agent/` | BaseAgent DI 组装、PrimitiveAgent 状态机、AgentConfig 配置模型 |
| **Core** | `arf/core/` | Protocol 定义、ModelAdapter（OpenAI/DeepSeek 格式适配）、配置基类 |
| **Resources** | `arf/resources/` | FileWatcher 热加载、三种 Provider、ResourceResolver 覆盖 |
| **Memory** | `arf/memory/` | FileMemoryStore、LLMMemoryWriter（异步提取）、LLMMemoryRetriever |
| **Compaction** | `arf/compaction/` | SlidingWindowCompactor、token 感知窗口、Tool Output Externalization |
| **Routing** | `arf/routing/` | TwoTierRouter 快慢模型调度 |
| **Guardrails** | `arf/guardrails/` | PathCheckToolGuard、ToolPermissionChecker |
| **Hooks** | `arf/hooks/` | 7 个生命周期事件、InProcess/Subprocess 两种 Runner |
| **Sandbox** | `arf/sandbox/` | PathSandbox 路径合法性校验、DirectoryBoundary |
| **Communication** | `arf/communication/` | AgentBus、PeerAgent、Supervisor、Lock、Consensus |
| **Human Loop** | `arf/human_loop/` | ApprovalPoint、ConsoleChannel、park/resume 审批流 |
| **Observability** | `arf/observability/` | TracePlugin JSONL 写入/读取 |
| **Streaming** | `arf/streaming/` | SSE 事件流适配与序列化 |
| **Evaluation** | `arf/evaluation/` | EvalRunner、BenchmarkBuilder、EvalComparator、版本化存档 |
| **Concurrency** | `arf/concurrency/` | SequentialScheduler |
| **Skills** | `arf/skills/` | SkillPipeline 工具依赖执行时序 |

### 插件系统

| 插件 | 目录 | 说明 |
|------|------|------|
| `filesystem` | `arf/plugins/filesystem/` | 文件读写删工具 |
| `memory` | `arf/plugins/memory/` | LLM 记忆提取/检索 |
| `compaction` | `arf/plugins/compaction/` | 渐进式上下文压缩 |
| `approval` | `arf/plugins/approval/` | 人机审批流转 |
| `tool_guard` | `arf/plugins/tool_guard/` | 工具权限校验（deny/allow/ask） |
| `error_handler` | `arf/plugins/error_handler/` | 错误恢复与降级 |
| `a2a_subagents` | `arf/plugins/a2a_subagents/` | 一次性子代理委托 |
| `a2a_teammates` | `arf/plugins/a2a_teammates/` | 持久化对等队友通讯 |
| `eval` | `arf/plugins/eval/` | 评测运行器、判定器、自动标注 |
| `trace` | `arf/plugins/trace/` | Hook-mounted JSONL 事件追踪 |

---

## 设计理念

### 1. 依赖注入 + Protocol 接口隔离

所有能力通过 Protocol 定义契约，通过依赖注入组装。`BaseAgent.__init__(**override_protocols)` 支持替换任意实现。框架不 import 具体类——调用方决定传入什么，框架只认接口。

```python
# 应用层组装
agent = BaseAgent(
    engine=GraphEngine(...),
    model_adapter=DeepSeekAdapter(...),
    memory_store=FileMemoryStore(...),
    agent_bus=InMemoryAgentBus(...),
    # 任何 Protocol 实现均可替换
)
```

### 2. Hook 生命周期（10 个检查点）

插件不修改引擎代码。引擎在 10 个检查点触发事件，每个检查点均支持 `blocking` 和 `side` 两种模式——插件按需声明，引擎不做硬编码限制。

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

- **blocking** — 顺序执行，可修改 ctx 注入数据或中断流程（park、approval 等）
- **side** — `asyncio.create_task` 并发执行，fire-and-forget（trace、metrics 等）

每个插件在其 `plugin.yaml` 的 `hooks` 字段声明自己要订阅的检查点和模式：`{before_round: blocking, after_model: side}`。

### 3. 文件系统即注册中心

工具、技能、模型都是目录加 YAML 配置。无需数据库迁移——`git push` 即共享配置。`FileWatcher` 监听文件变更，运行时热加载，无需重启。

```
tools/
├── read_file/
│   ├── tool.yaml          # name, description, parameters
│   └── function.py        # async def execute(...) -> dict
├── grep/
│   ├── tool.yaml
│   └── function.py
```

### 4. Agent-to-Agent：Subagents vs Teammates

ARF 提供两种 A2A 通讯机制，覆盖不同的协作场景：

| | a2a_subagents | a2a_teammates |
|---|---|---|
| **关系** | 父子（层级制） | 对等（P2P） |
| **生命周期** | 一次性，完成即销毁 | 整个 session，park/wake 循环 |
| **创建** | `delegate_task(agent="name", task="...")` | `send_peer_message(to="session_id", message="...")` |
| **并发** | 多个子 agent 并行 | N 个 agent 各自独立 harness |
| **park** | 父等子（dispatch + before_model park） | 全体在 after_round park，peer/user 唤醒 |

### 5. Park/Resume — 进程级阻塞与唤醒

借鉴操作系统的进程阻塞语义：Agent 在等待外部输入（用户审批、peer 回复、子 agent 完成）时挂起（park），事件到达时唤醒（resume）。`WaitItem` 记录等待原因和 `resume_key`，支持 session 恢复时重建等待状态。

```
agent.wait(reason="waiting_for_approval", resume_key="approval_123")
  → engine park → state_store.save()
  → 用户审批 → engine.resolve_wait(resume_key="approval_123")
  → engine resume → agent.finish_wait()
```

---

## Session > Round > Turn

ARF 采用三级时间结构：

```
session  >  round  >  turn
  │           │          └─ ReAct 步骤：一次 model_call [+ tool_calls]
  │           │             最后一步仅 model_call，无 tool_calls
  │           └─ 一次 user_input → final_output
  └─ 多轮对话，跨多次 chat()，有独立 state_store 和 trace 文件
```

| 层级 | 引擎变量 | Trace JSONL | Eval |
|------|---------|-------------|------|
| session | `session_id` | `session_id` | `case.session_id` |
| round | `_interaction_round` | `round` | case 边界过滤 |
| turn | `current_turn` | `turn` | `max_turns`, 效率评估 |

---

## 个人总结与下一步计划

### 从 0 到 1

我最近尝试从 0 到 1 搭建了一套 Agent 框架，涵盖 Agent 运行时、State 管理、Trace 和 Eval、Peer Agents 和 Subagents、上下文压缩、Memory。这是我近两年来在 AI 应用工程落地领域遇到的共性问题——每做一个 AI 应用都要重新发明这些基础设施。ARF 是对这些共性问题的形而上抽象：如果 Agent 是一个操作系统里的进程，那么它需要 CPU（Model）、内存（Memory）、文件系统（Tools）、IPC（A2A 通讯）、中断（Park/Resume）、调度器（Routing）——框架就是那个 OS 内核。

随着开发的深入，我对 Agent 的理解也在不断加深。目前框架的能力模块已经基本完善——18,000 行代码，12,500 行测试，723 个测试用例。最小可行 App（`arf_app` 教学项目）也已跑通，覆盖了单 Agent、Subagents 委派、Teammates 协作、Eval 评测等场景。作为一个玩具，它合格了。

### 踩过的坑

1,492 次提交中，有 418 次是 bug fix（28%），156 次是重构（10%）。这个数字本身就是故事——**我们边写边踩坑，边踩坑边打补丁**。

**Engine 是修复密度最高的模块（50 次 fix）。** ReAct 主循环的正确性比预期更难保证。`break` 语句让 turn loop 不可达——这行代码通过了所有测试，但在特定消息序列下整个 turn 被跳过。park/resume 统一后连续三次回归：消息注入后再次触发 park 导致死循环、partial wakeup 时消息丢失、cancel_event 未清理导致跨 round 污染。**状态机的正确性不取决于 happy path 的测试覆盖率，而取决于对隐式副作用（break/cancel/park/message injection）的穷举建模。** 这些副作用交织在一起时，组合爆炸远超直觉预期。

**A2A + Teammates 是打补丁最多的领域（36 次 fix）。** 死锁、race condition、消息消费归属混乱。最典型的是 park 位置的选择——在 `before_model`、`after_round`、`before_round` 之间反复迁移了至少 5 次。每次迁移修复一个问题，又引入一个新问题。根本原因在于：**Agent 和 Harness 的边界不够干净。** Park 应该是一个明确的"进程状态切换"，但实际实现中它散落在引擎的多个检查点，每个插件还要各自注册 wait。当多个 Agent 同时运行时，这些分散的 park 逻辑互相交织，形成了难以推理的全局状态。

**路径处理问题是文件系统注册模型的固有复杂性。** 多次出现 double-join（`abspath` + `join` 双重拼接）、相对路径在沙箱白名单中匹配失败等 bug。文件路径作为全局命名空间时，相对/绝对路径的转换、规范化、边界检查每一步都可能产生静默错误——不同于 API 调用（报错即失败），路径问题往往表现为"看起来能工作，但在特定目录下崩溃"。

**Memory 的后台异步任务容易静默失败。** LLM 记忆提取从未触发——不是因为代码逻辑错误，而是因为模型参数名从 `model=` 变成了 `model_name=`，异常被异步任务吞没。目录不存在时直接崩溃（`mkdir` 缺失）。**后台任务需要显式的错误传播机制**——对于 Memory 这种"不阻塞主流程但错了会影响长期行为"的系统，静默失败是最危险的失败模式。

**ModelAdapter 的错误边界需要显式化。** API 错误被静默吞噬（exception 被 catch 但未 propagate），`model_call_end` 事件缺少 `usage` 字段，空字符串 api_key 被 `OpenAI()` 拒绝，`"false"`（字符串）被当作 truthy 启用了 thinking 模式。这些问题的共同点在于：**Python 的动态类型 + 第三方 SDK 的隐式行为 = 类型系统无法捕获的错误。** 解决方案是强制显式化——显式的 error propagation、显式的 falsy 检查、显式的字段默认值。

**核心经验：框架的正确性不是测试跑出来的，是边界条件穷举出来的。** 测试覆盖率只能告诉你"已知场景过了"，不能告诉你"没有遗漏的场景"。对于框架代码，真正的质量来自对每个条件分支的穷举审视——哪些状态组合可能出现？每个副作用是否被正确地清理和重置？

### 第二轮抽象

目前 Agent 和 Harness 的抽象还不够干净。特别是在从单 Agent 扩展到 Agent Teams 的过程中，多 Agent 之间的通讯、挂起、信息透传消耗了大量时间，并给框架反复打了很多补丁。这些补丁的本质是：**Harness 承担了太多它不该承担的责任。**

具体来说：
- **Park/Resume 散落在多个层级。** Engine 的 checkpoint、Plugin 的 wait 注册、Agent 的 wait() 调用——三个层级各自管理等待状态的一部分，没有统一的"进程状态"抽象。
- **A2A 消息传递与 Harness 耦合过深。** 消息的注入时机（before_round vs before_model）、消费归属（谁该 drain）、reply 的组装——这些逻辑嵌在 Engine 和 Plugin 的具体实现中，而非一个独立的 "IPC 层"。
- **Agent 的边界模糊。** PrimitiveAgent 理论上"只知道消息和模型调用"，但 `wait()` 和 `finish_wait()` 暴露了阻塞语义给上层。真正纯净的"进程"抽象应该由运行时统一管理阻塞，Agent 自身不感知 park/resume。

现在是时候进行第二轮的形而上思考设计了。这次我会尝试：

1. **更彻底的抽象**——Agent 就是进程，Harness 就是内核。进程不感知自己被挂起，内核统一管理调度。IPC（AgentBus）从 Harness 中剥离为独立层。
2. **更干净的分离**——每个模块只做一件事。Engine 只管"取消息→调模型→执行工具→写结果"这一个循环。Park/Resume 是调度器的事。A2A 是 IPC 层的事。Trace 是可观测层的事。
3. **更通用更易扩展的设计**——当前的两级路由（TwoTierRouter）和两种 A2A（Subagents/Teammates）是具体实现，应该抽象为"模型调度策略"和"Agent 通讯拓扑"的通用接口，允许用户自定义策略。
4. **用 Rust 实现核心**——Engine 主循环、Park/Resume 调度器、AgentBus 消息传递这三个性能敏感且正确性要求极高的模块，用 Rust 重写。Python 通过 PyO3 绑定调用。Rust 的类型系统和所有权模型天然适合状态机验证，很多 Python 中"靠测试和代码审查来保证"的正确性问题，在 Rust 中是编译期错误。

这不是一个重写计划——框架的整体架构和设计理念已经验证了可行性。这是一个 **"把已验证的设计翻译成更严格的语言"** 的计划。Python 原型证明了什么是对的，Rust 会让它无法是错的。

---

## 教学示例

框架功能的教学示例在独立项目 [arf_app](https://github.com/Wang-hubber/arf_app) 中——从单 Agent 到 Subagents 委派到 Teammates 协作的渐进式教程。

---

## 文档

| 文档 | 内容 |
|------|------|
| [`docs/agent.md`](docs/agent.md) | Agent 配置、组装、模型适配器、工具执行 |
| [`docs/a2a-communication.md`](docs/a2a-communication.md) | Subagents vs Teammates 机制对比与案例 |
| [`docs/park-resume.md`](docs/park-resume.md) | Park/Resume 阻塞唤醒机制 |
| [`docs/eval-benchmark.md`](docs/eval-benchmark.md) | 评测系统：Benchmark 构建、Runner、Comparator |

---

## 开发

```bash
# 安装
pip install -e ".[dev]" -i https://pypi.mirrors.ustc.edu.cn/simple

# 运行测试
pytest tests/ -q                       # 全部测试
pytest tests/ -q -m "not slow"         # 跳过慢测试
```

**提交信息格式：** `type(scope): description`（如 `feat(engine):`、`fix(a2a):`、`refactor(agent):`、`docs:`、`test:`）。

---

## 许可

MIT — 详见 [LICENSE](LICENSE)

<p align="center">
  <sub>Built with Python · DeepSeek · LangGraph</sub>
  <br/>
  <sub><a href="https://github.com/Wang-hubber/open_deepseek_arf">GitHub</a></sub>
</p>
