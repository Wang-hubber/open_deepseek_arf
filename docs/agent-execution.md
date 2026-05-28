# Agent Execution — Lifecycle, Loop Control & Multi-Agent Orchestration

ARF 将 OS 进程管理的三个核心机制——进程创建（fork/exec）、进程调度（scheduler）和 IPC（进程间通讯）——适配到 Agent 运行时。Agent 是进程，会话是地址空间，工具调用是系统调用，Handoff 是 IPC。

---

## 1. OS 方案演进

> 本章描述 OS 如何处理进程生命周期与调度，作为 ARF 设计思路的参考。非严格技术对标。

### 1.1 进程创建：fork + exec

Unix 的进程创建分为两步：`fork()` 复制当前进程（包括文件描述符、地址空间、信号处理），`exec()` 用新程序镜像替换当前地址空间。这个分离设计的核心价值在于 **fork-exec 间隙**——子进程可以在 exec 之前修改环境（重定向 stdin/stdout、关闭文件描述符、设置资源限制），这些修改对新程序透明。

Windows 的 `CreateProcess()` 则将创建和加载合并为一次系统调用，通过 `STARTUPINFO` 结构体传递环境修改。两种设计代表了配置注入的两种范式：继承+覆写 vs 显式传参。

### 1.2 进程调度

**CFS（Completely Fair Scheduler）** — Linux 2.6.23 引入，替代 O(1) 调度器。核心数据结构是红黑树，key 为 `vruntime`（虚拟运行时间，实际运行时间按 nice 值加权）。每次调度选择 vruntime 最小的进程——保证 CPU 时间在公平意义上的均等分配。

**抢占** — 时钟中断触发 scheduler_tick()，若当前进程的 vruntime 超过最小 vruntime + 阈值（通常 6ms），设置 `TIF_NEED_RESCHED` 标志。返回用户态前夕检查该标志，若置位则调用 schedule() 切换。

**cgroup** — 控制组将进程组织为层级树，CPU 子系统按权重分配时间片。本质是对调度器的配置抽象——不改变调度算法，而是在其之上叠加配额管理。

### 1.3 IPC 与进程间通讯

Unix 提供多种 IPC 机制：管道（pipe，无名字节流，亲缘进程间传递）、信号（signal，异步通知，SIGKILL/SIGSTOP 不可捕获）、共享内存（mmap MAP_SHARED，最快但需同步）、消息队列（POSIX mq，按优先级出队）。每种机制的取舍——延迟 vs 吞吐 vs 耦合——直接对应 ARF 中 Agent 间通讯的多种模式。

### 1.4 对 ARF 的启发

| OS 概念 | ARF 对应 |
|---------|----------|
| fork + exec 间隙 | `BaseAgent.__init__` DI 组装 — 创建引擎 → 注入全部协议实现 → 启动 |
| CFS vruntime | `LoopStrategy` — 每 turn 判断是否继续，等价于调度器的 `should_continue` |
| cgroup 配额 | `max_turns` — 会话断路器，防止失控循环消耗资源 |
| 抢占标志 | `cancel_event` — 异步信号，在循环边界检测并响应 |
| IPC 管道/信号/共享内存 | HandoffManager/AgentBus/PeerAgent/DictWorkspace |

---

## 2. ARF 当前实现

Agent 执行体系分为三层：**引擎层**（循环控制 + 工具编排）、**装配层**（DI 组装全部协议实现）、**多 Agent 层**（Handoff 切换 + 状态隔离）。

### 2.1 架构总览

```
会话启动
    │
    ▼
BaseAgent.__init__()
    │  加载 memory/memory.md → 注入 {{MEMORY}}（一次性）
    │
    ▼
用户消息进入
    │
    ▼
BaseAgent.chat() / astream()
    │  加载已有 State（恢复对话历史、context_summary）
    │  创建 AgentState → begin_round（检查点）
    │  [Hook] round_start
    ▼
GraphEngine.invoke() / astream()
    │
    ├─ [Hook] session_start
    │
    ├─ while LoopStrategy.should_continue(state):
    │   │
    │   ├─ [取消检查] _cancelled() → break
    │   ├─ [模型路由] ModelRouter → current_model
    │   ├─ [压缩判断] Compaction.should_compact()
    │   ├─ [Hook] pre_model_call
    │   ├─ [模型调用] _call_model / _stream_model
    │   ├─ [Hook] post_model_call
    │   ├─ [输出检查] GuardRunner.check_output
    │   ├─ [工具解析] _pars_tool_calls
    │   │   ├─ 无工具调用 → append assistant msg → break
    │   │   └─ 有工具调用 → 继续
    │   ├─ [工具守卫] Guard + Pipeline + Permissions + Approval
    │   ├─ [Hook] pre_tool_exec
    │   ├─ [工具执行] ToolExecutor.execute()
    │   ├─ [Hook] post_tool_exec
    │   ├─ [Handoff 检测] HandoffManager.detect()
    │   └─ [检查点] StateStore.put()
    │
    ├─ [Hook] round_end
    └─ [Hook] session_end → 返回最终 State
```

### 2.2 双模主循环：invoke / astream

`GraphEngine`（`arf/engine/graph.py`）提供两条执行路径，共享完全相同的 Agent Loop 逻辑：

| 方法 | 返回方式 | 适用场景 |
|------|---------|----------|
| `invoke()` | 同步返回最终 `AgentState` | CLI 对话、评测回放、后台任务 |
| `astream()` | `AsyncGenerator[AgentEvent]` | SSE 流式响应、实时 UI 更新 |

两条路径的唯一差异在模型调用层：`astream` 使用 `_stream_model` 产出 token 级 `thinking_delta` 事件，`invoke` 使用 `_call_model` 一次性获取完整响应。`ModelAdapter.chat_stream_full()` 统一产出 `chunk` / `tool_call` / `usage` / `error` 四种流事件，引擎负责组装。

工具守卫流水线（Guard + Pipeline + Permissions + Approval）由共享方法 `_step_classify_tool_calls()` 实现，两条路径复用同一逻辑。同样共享的还有：`_close_tool_calls()`（消息序列完整性保证）、handoff 检测与执行、记忆检索/写入、压缩判断。

### 2.3 循环策略

`LoopStrategy` 协议（`arf/core/protocols/engine.py`）定义循环的继续条件：

```python
class LoopStrategy(Protocol):
    def should_continue(self, state: AgentState) -> bool: ...
    def next_step(self, state: AgentState) -> str: ...
```

当前唯一实现是 `ReActStrategy`（`arf/engine/loop_strategies/react.py`）：

```python
class ReActStrategy:
    def should_continue(self, state: AgentState) -> bool:
        return state.get("current_turn", 0) < self.max_turns

    def next_step(self, state: AgentState) -> str:
        last = state["messages"][-1]
        if last.get("role") == "tool":
            return "call_model"      # 工具结果返回 → 模型继续思考
        return "execute_tools"       # 模型产出了 tool_calls → 执行工具
```

ReAct 模式：模型思考（产出 tool_calls 或最终回复）→ 工具执行（产出结果）→ 模型再思考。循环在以下条件终止：

1. 模型返回纯文本（无 tool_calls）→ 用户得到最终回复
2. `turn >= max_turns` → 会话断路器触发
3. `cancel_event.is_set()` → 用户主动中断

### 2.4 取消机制

`asyncio.Event` 作为取消信号，非阻塞检测：

```python
def _cancelled(self) -> bool:
    return self._cancel_event is not None and self._cancel_event.is_set()
```

每次 while 循环迭代开始前检查。取消信号由 `POST /api/chat/cancel` 或 SSE 客户端断连触发，类似硬件中断在当前指令边界响应——当前 turn 的执行不会被中途打断，而是在循环边界安全退出。

### 2.5 会话断路器：max_turns

`max_turns` 是每条 Agent 会话的硬限制，防止模型陷入"工具调用→失败→重试→失败"的失控循环。默认 50 轮，在 `agent.yaml` 中配置：

```yaml
advanced:
  max_turns: 50
```

对多 Agent 场景，每个子 Agent 可有独立的 `max_turns`（通过 `AgentConfig.advanced.max_turns`），引擎在执行时使用 `_active_config(state)["max_turns"]` 动态获取当前活跃 Agent 的限制。子 Agent 的 turn 从 0 重新计数（`_execute_handoff()` 中 `state["current_turn"] = 0`），handoff 回主 Agent 后恢复主 Agent 的 turn 计数。

### 2.6 工具执行

`ConcurrentToolExecutor`（`arf/engine/tool_executor.py`）接收 `_step_classify_tool_calls` 过滤后的合法工具调用，按配置策略执行：

| 策略 | 行为 |
|------|------|
| `parallel` | 所有工具并发执行（`asyncio.gather`），适合无依赖的独立工具 |
| `sequential` | 按顺序逐个执行，适合有数据依赖的工具链 |

配置在 `agent.yaml` 中：

```yaml
advanced:
  concurrency:
    strategy: parallel      # parallel | sequential
    max_concurrency: 5
```

工具执行结果统一封装为 `ToolResult`（success/data/error/duration_ms），引擎将其注入消息历史并 emit trace 事件。

### 2.7 BaseAgent — DI 装配

`BaseAgent`（`arf/agent/base.py`）负责将所有 Protocol 实现组装为可运行的 Agent。构造函数按固定顺序初始化 10 个子系统：

| 步骤 | 子系统 | 默认实现 | 可注入替代 |
|------|--------|----------|-----------|
| 1 | EventBus | `InMemoryEventBus` | `event_bus=` |
| 2 | StateStore | `FileStateStore` | `state_store=` |
| 3 | Resources | `ToolProvider` + `SkillProvider` + `ModelProvider` → `ResourceResolver` | `tool_resolver=` |
| 4 | Memory | `FileMemoryStore` + `LLMMemoryWriter`/`LLMMemoryRetriever` | `memory_store/writer/retriever=` |
| 5 | Compaction | `SlidingWindowCompactor` | `compaction=` |
| 6 | Guardrails | `DefaultGuardRunner` | `guard_runner=` |
| 7 | Error Policy | `DefaultErrorPolicy` | `error_policy=` |
| 8 | Hooks | `SubprocessHookRunner` | `hook_runner=` |
| 9 | Tool Executor | `ConcurrentToolExecutor` | `tool_executor=` |
| 10 | Loop Strategy | `ReActStrategy` | `loop_strategy=` |

所有 Protocol 实现通过 `**override_protocols` 参数可替换，支持测试注入 InMemory* doubles 或生产环境替换自定义实现。

`BaseAgent` 还负责：
- **系统提示词构建**：`_build_system_prompt()` 按 pipeline 优先级（若配置）组装 prompt 分区，填充 `{{AGENT_NAME}}`、`{{INVENTORY}}` 等占位符
- **模型适配器创建**：`_inject_model_calls()` 为每个模型配置创建 `ModelAdapter`，注入 `_call_model` / `_stream_model` 闭包，可选包裹 `ModelCallProtector`（rate limit + circuit breaker）
- **子 Agent 创建**：遍历 `config.agents` 为每个子 Agent 创建独立的 system prompt 和模型适配器
- **HandoffManager 创建**：从 `config.handover.rules` 构建 handoff 规则表

### 2.8 多 Agent Handoff

`HandoffManager`（`arf/engine/handoff.py`）实现会话内的 Agent 切换：

**检测**：每 turn 工具执行完毕后，`detect()` 扫描 `tool_results`，查找 `{"handoff": true}` 信号。支持多种返回格式：`ToolResult` 对象、嵌套 `{"data": {...}}` 字典、`FunctionBackend` 的 `{"result": {...}}` 包装。

**切换流程**（`GraphEngine._execute_handoff()`）：
1. 保存当前 Agent 状态到 `StateStore`（key: `{session_id}/{from_agent}`）
2. 通过 HandoffManager 解析目标 Agent（单规则直接匹配，多规则 LLM 选择）
3. `RoundManager.record_handoff()` 记录切换（不创建新检查点）
4. 加载目标 Agent 的持久化状态（存在则恢复，否则构建初始上下文）
5. 构造目标上下文：截取最近 N 轮对话 + 可选的任务摘要（由 system model 生成）
6. 重置 `current_turn = 0`，清除 `tool_results`，切换 `active_agent`
7. Emit `agent_switch` trace 事件

**返回流程**（`_restore_from_handoff()`）：子 Agent 产出 handoff 信号后，引擎提取子 Agent 最后一条 assistant 消息作为结果，替换主 Agent 的原始 handoff 工具结果，恢复主 Agent 的消息历史和 turn 计数。

### 2.9 检查点与回滚

`RoundManager`（`arf/engine/round_manager.py`）维护滚动窗口的 `RoundTransaction`，每个 round 代表一次用户交互：

- **begin_round()**：在 `BaseAgent.chat()/astream()` 入口调用，深拷贝对话状态 + 工作区文件快照
- **record_handoff()**：记录 Agent 切换但不创建新检查点——一个 round 内无论多少次 handoff，undo 都回退整个 round
- **undo(steps)**：恢复到目标 round 的检查点，文件和状态双回滚，emit `undo_executed` trace 事件

默认保留 3 个检查点（`max_undo_depth: 3`）。

### 2.10 配置

```yaml
# agent.yaml
agent:
  name: main
  role: 用户助理
  task: 帮助用户完成日常任务

models:
  - type: quick
    model: deepseek-v4-flash
    context_window: 800000
  - type: deep
    model: deepseek-v4-pro
    context_window: 1000000

advanced:
  loop_strategy: react        # react | plan_execute
  max_turns: 50               # 会话断路器
  max_undo_depth: 3           # undo 检查点窗口大小
  concurrency:
    strategy: parallel
    max_concurrency: 5

# 多 Agent 配置（可选）
agents:
  - name: sys_agent
    role: 系统工程师
    task: 资源创建、模型配置、工具/技能生成
    routing:
      strategy: static
      default: deep

handover:
  rules:
    - from_agent: main
      to_agent: sys_agent
      trigger: "创建工具 生成技能 配置模型"
      context:
        raw_turns: 4
        task_summary: true
    - from_agent: sys_agent
      to_agent: main
      trigger: "完成 返回"
      context:
        raw_turns: 4
        task_summary: true
```

---

## 3. 演进方向

### 3.1 多 Agent DAG 编排

当前的多 Agent 模式是链式的——主 Agent ↔ 子 Agent 的 request-response。复杂任务可能需要多个 Agent 并发执行不同子任务，最后汇总结果。

参考 OS 的 `fork + waitpid` 模式：
1. 主 Agent 分解任务后，同时 handoff 到多个子 Agent（fork）
2. 子 Agent 并行执行各自子任务
3. 主 Agent 收集所有子 Agent 结果（waitpid），合并后继续

实现要点：
- DAG 依赖声明：子任务间的依赖关系图（任务 B 依赖 A 的输出）
- 并发调度：无依赖的子任务并发执行，有依赖的等待前驱完成
- 结果合并策略：concatenate / vote / summarize 多种合并模式

### 3.2 暂停/恢复/检查点

当前取消是"终止型"的——一旦取消，Agent 循环退出且不可恢复。完整的暂停/恢复机制可以类比 SIGSTOP/SIGCONT：

- **暂停**：在当前循环边界安全停止，完整序列化 engine 状态（包括 pending approvals、active pipelines、handoff 中间状态）
- **恢复**：从序列化状态重建 engine 上下文，继续执行
- **跨进程恢复**：状态可迁移到另一进程/机器，支持负载均衡和故障转移

### 3.3 plan-execute 循环策略

当前 `ReActStrategy` 是思考-行动-观察的单一循环，模型每步自行判断是否继续。对于需要多步规划的任务，可引入 plan-execute 模式：

- **Plan 阶段**：收到用户任务后，先用 system model 生成步骤化的执行计划
- **Execute 阶段**：按计划顺序推进，每完成一步检查是否偏离计划（divergence detection）
- **Replan 阈值**：偏离超过阈值时重新触发 plan 阶段，生成修正计划

`Planner` 协议已在 `arf/core/protocols/engine.py` 中定义，当前由 `arf/plugins/planner/` 插件提供框架级实现。引擎侧的 `LoopStrategy` 需新增 `PlanExecuteStrategy` 实现以支持此模式。

### 3.4 探索性方向

**抢占式中断**：当前取消在循环边界响应，若模型调用耗时较长（长推理），用户需等待当前 turn 完成。可引入 LLM 调用的流式中断——收到取消信号后立即中止 HTTP 请求，而非等待完整响应。

**会话迁移**：将完整的 Agent 会话状态（消息历史、记忆、检查点、usage 统计）打包为可迁移的归档，支持跨设备/跨用户迁移。类似 CRIU 的进程 checkpoint/restore。

**子 Agent 资源配额**：类似 cgroup 的层级资源控制——每个子 Agent 有独立的 `max_turns`、token 预算、工具调用次数上限。父 Agent 的配额自动分配给子 Agent，子 Agent 超限时由父 Agent 决定是否追加配额。
