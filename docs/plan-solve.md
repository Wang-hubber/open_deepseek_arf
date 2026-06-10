# Plan-Solve — 依赖图驱动的任务执行

ARF 将复杂任务分解为有向无环图（DAG），通过契约校验强制执行顺序，借助子 Agent 隔离执行每个步骤，最后汇总为统一结果。Plan-Solve 不是引擎内置的循环策略，而是模型通过 tool calling 自主驱动的 plugin + tools 组合。

---

## 1. OS 方案演进

> 本章描述 OS 和分布式系统如何解决任务分解与依赖管理，作为 ARF 设计思路的参考。非严格技术对标。

### 1.1 进程调度 — 就绪队列与阻塞队列

**问题**：多进程系统中，某些进程等待 I/O 完成前无法执行，如何高效利用 CPU？

**解决方案**：就绪队列（ready queue）和阻塞队列（wait queue）。进程等待资源时移入阻塞队列，资源就绪后移回就绪队列。调度器只从就绪队列取进程运行。

**对 ARF 的启发**：Plan-Solve 的 `depends_on` 就是"阻塞队列"——步骤 2 依赖步骤 1 的结果，在步骤 1 完成前步骤 2 处于 blocked 状态。`blocks` 是反向索引——步骤 1 完成后自动"唤醒"步骤 2（将其依赖条件移除）。

### 1.2 Makefile — 声明式依赖与增量构建

**问题**：大型 C 项目中，如何只重新编译变更的文件而非全量重编？

**解决方案**：`make` 读取 `Makefile` 中的依赖规则（target: prerequisites）。如果 `foo.o` 依赖 `foo.c`，且 `foo.c` 的 mtime 比 `foo.o` 新，则执行编译命令。Make 自动拓扑排序，并行编译无依赖的目标（`make -j4`）。

**对 ARF 的启发**：这正是 Plan-Solve 的依赖模型——`make -j` 的"无依赖目标可并行构建"对应 Plan-Solve 的"无阻塞步骤可同时 dispatch"。不同的是，Make 通过文件时间戳判断是否需要重建，Plan-Solve 通过步骤状态（done/failed）判断执行许可。

### 1.3 DAG 调度 — MapReduce 到 Spark

**问题**：分布式任务中，如何表达计算步骤间的依赖并自动并行化？

**Spark DAG Scheduler**：将 RDD 转换操作（map、filter、join）构建为 DAG Stage。宽依赖（shuffle）形成 Stage 边界，窄依赖（map/filter）可在同一 Stage 内流水线执行。Stage 间串行，Stage 内并行。

**对 ARF 的启发**：Plan-Solve 的 `depends_on` 是宽依赖——步骤 2 必须等待步骤 1 完全输出后才能开始。`blocks` 是 Stage 边界，引擎据此判断哪些步骤可并行 dispatch。与 Spark 不同的是，Plan-Solve 的每个步骤是 LLM 驱动的 Agent 而非确定性的数据转换。

### 1.4 两阶段提交 — 全部就绪才提交

**分布式事务**：协调者向所有参与者发送 prepare 请求，全部回复 yes 后才发送 commit。任何参与者回复 no 则全部 abort。

**对 ARF 的启发**：`plan_summarize` 对标 commit 阶段——所有步骤 done/failed（prepare 完成）后才允许汇总（commit）。如果有步骤仍在 pending/running，summarize 被拒绝，模型收到 error 后自行决定重试或跳过。

---

## 2. ARF 当前实现

### 2.1 架构总览

```
模型决定使用 Plan-Solve
    │
    ▼
plan_create(task, steps)  ──→  校验 DAG（循环/对称/索引）
    │                         写入 plan.json
    ▼
plan_dispatch(step_index=N) ──→ Plugin 检查 depends_on 全部 done
    │                          创建子 ControlPlane → 执行 → 记录结果
    │                          (可并行 dispatch 无依赖步骤)
    ▼
plan_summarize()  ──→  Plugin 检查全部 steps done/failed
                      调模型汇总 → plan.status = "done"
```

### 2.2 文件结构

```
arf/plugins/plan_solve/
├── plugin.yaml              # name: plan_solve
├── plugin.py                # PlanSolvePlugin（pre_action + round_start）
├── validation.py            # DAG 校验（索引/对称/循环）
├── tools/
│   ├── plan_create/         # 分解任务 → 校验 → 写 plan.json
│   ├── plan_dispatch/       # 校验依赖 → 子引擎执行 → 记结果
│   ├── plan_summarize/      # 校验全部完成 → 汇总
│   └── plan_status/         # 只读进度快照
└── skills/
    └── plan_solve.yaml      # 教模型使用工具族
```

### 2.3 数据模型

**plan.json** — 持久化到 workspace，支持断点续传：

```json
{
    "plan_id": "plan-1718000000",
    "task": "原始任务描述",
    "status": "executing",
    "created_at": 1718000000.0,
    "updated_at": 1718000000.0,
    "steps": [
        {
            "index": 1,
            "description": "读取配置文件",
            "tool_hint": "read",
            "status": "pending",
            "depends_on": [],
            "blocks": [2],
            "sub_session_id": null,
            "result": null,
            "error": null,
            "started_at": null,
            "finished_at": null
        },
        {
            "index": 2,
            "description": "分析配置并生成报告",
            "tool_hint": "bash",
            "status": "pending",
            "depends_on": [1],
            "blocks": [],
            "sub_session_id": null,
            "result": null,
            "error": null,
            "started_at": null,
            "finished_at": null
        }
    ]
}
```

**关键字段**：

| 字段 | 说明 |
|------|------|
| `depends_on` | 此步骤依赖的前置步骤列表，全部 done 后才可 dispatch |
| `blocks` | 此步骤完成后解锁的后续步骤列表 |
| `status` | `pending` → `running` → `done` / `failed` |
| `sub_session_id` | dispatch 时创建的子 Agent 会话 ID，用于 trace 关联 |
| `result` / `error` | 步骤执行的产出或失败原因 |

### 2.4 DAG 校验

`plan_create` 写入前执行 `validate_steps()`：

1. **索引有效性** — `depends_on` 和 `blocks` 中引用的步骤必须存在
2. **对称性** — 若步骤 A 的 `blocks` 包含 B，则 B 的 `depends_on` 必须包含 A
3. **无自引用** — 步骤不能依赖或阻塞自身
4. **无环** — Kahn 拓扑排序检测循环依赖

校验失败返回 `{ok: false, error, suggestion}`，模型看到后自行修正。

### 2.5 契约强制执行

`PlanSolvePlugin` 通过两个 Hook 点介入引擎生命周期：

**pre_action（blocking）**：
- `plan_dispatch` 调用时 → 检查 depends_on 全部 done、步骤自身为 pending
- `plan_summarize` 调用时 → 检查所有步骤为 done/failed
- 校验失败 → 注入 `tool_result` error 消息，从 `_pending_tool_calls` 中移除。不抛异常，模型收到 error 后自主决定下一步

**round_start（side）**：
- 检测 workspace 中存在 `status=executing` 的 plan.json
- 存在 → emit `plan_resumable` 事件（含 plan_id、pending_steps、completed_steps）
- 消费端（App/TUI）据此决定是否提示用户恢复

### 2.6 子 Agent 执行

`plan_dispatch` 为每个步骤创建独立的 `ControlPlane`：

```
plan_dispatch(step_index=N)
  → 读取 step 的 description 作为 user prompt
  → 创建 InMemoryStateStore + InMemoryEventBus
  → ControlPlane(max_turns=10, call_model=父引擎的 _call_model)
  → invoke() → 从最后的 assistant 消息提取结果
  → 写回 plan.json（status/result/sub_session_id/时间戳）
  → emit plan_step_started / plan_step_finished 事件
```

子引擎共用父引擎的 `_call_model`（模型连接）和 `tool_executor`（工具执行器），但拥有独立的 state/messages 上下文。子引擎内部有自身的 `GateChecker(max_turns=10)` 防止无限执行。

### 2.7 事件

| 事件 | Hook 点 | 数据 |
|------|---------|------|
| `plan_resumable` | round_start | plan_id, task, pending_steps, completed_steps |
| `plan_step_started` | dispatch 开始时 | plan_id, step_index, description |
| `plan_step_finished` | dispatch 结束时 | plan_id, step_index, status, content |

所有事件通过 `event_bus` 流出，`TraceStore` 自动消费。

### 2.8 与引擎循环的关系

Plan-Solve 完全由模型通过 tool calling 驱动，引擎不做任何"策略"判断：

```
引擎简化循环:
  model_call()
    → 模型输出: plan_create(...)  ← 工具调用
    → 引擎执行工具 → 结果返回
  model_call()
    → 模型输出: plan_dispatch(1), plan_dispatch(2)  ← 并行工具调用
    → 引擎执行工具 → Plugin 校验 → 子引擎执行
  model_call()
    → 模型输出: "步骤 2 失败了，我重试一下"
    → 引擎执行: plan_dispatch(2)
  model_call()
    → 模型输出: plan_summarize()
    → 引擎执行工具 → Plugin 校验全部 done → 汇总
  model_call()
    → 模型输出: "最终结果: ..."  ← 纯文本，循环结束
```

引擎只提供 `model_call` 和 `tool_call` 两个原语，Plan-Solve 的所有"智能"——何时规划、何时分发、何时总结——都由模型通过调用对应工具来表达。

---

## 3. 演进方向

### 3.1 步骤间数据传递（TFlow）

当前步骤间通过 plan.json 的 `result.content` 传递文本结果。后续可通过 communication 模块的 TFlow（weight-space perturbation）直接传递步骤产出的结构化数据，避免序列化/反序列化开销。

### 3.2 自动重试与降级

当前步骤失败后，模型手动决定是否重试。后续可加入 `plan_retry` 工具或 Plugin 级别的自动重试策略（指数退避、最大重试次数）。

### 3.3 动态插入步骤

当前 plan.json 在 `plan_create` 时固定。后续可加入 `plan_revise` 工具允许在执行过程中动态插入/删除/重排步骤，支持模型根据中间结果调整计划。

### 3.4 嵌套 Plan-Solve

当前 `plan_dispatch` 的子引擎不自动加载 plan_solve 工具。后续可支持子引擎也使用 Plan-Solve，实现任意深度的任务树递归分解。

---

## 对比：Plan-Solve vs 旧 LoopStrategy

| | 旧 PlanExecuteStrategy | 新 Plan-Solve |
|---|---|---|
| **策略归属** | 引擎硬编码，框架决定 HOW | 模型自主选择，工具表达 WHAT |
| **启用方式** | `loop_strategy: "plan_execute"` 配置 | 模型主动调用 `plan_create` / `plan_dispatch` |
| **依赖管理** | 无 | depends_on + blocks 双向 DAG |
| **状态持久化** | 无（仅内存） | plan.json（断点续传） |
| **契约强制** | 无 | Plugin pre_action 校验 |
| **并行执行** | 不支持 | 无依赖步骤可同时 dispatch |
| **代码位置** | 引擎核心 `loop_strategies/` | 插件目录 `plugins/plan_solve/` |
