# Concurrency & Deadlock Prevention — Skill Pipeline

ARF 的并发模型是：Agent 循环顺序执行，但单轮内的工具调用和 Hook 触发可并行。Skill Pipeline 通过依赖声明在并行中插入必要的顺序约束，防止竞态导致的不一致。

---

## 1. OS 方案演进

> 本章描述 OS 如何处理并发执行与依赖管理，作为 ARF 设计思路的参考。非严格技术对标。

### 1.1 超标量执行

**问题**：CPU 如何在一个时钟周期内执行多条指令？

**演进**：
- **标量**（8086）：每周期一条指令
- **流水线**（80486）：取指、译码、执行、访存、写回五级流水。指令重叠执行，每个周期完成一条
- **超标量**（Pentium, 1993）：双流水线（U-pipe 和 V-pipe），每周期可发射两条独立指令
- **乱序执行**（Pentium Pro, 1995）：指令按就绪顺序而非程序顺序执行。ReOrder Buffer（ROB）保证最终结果与顺序一致

**关键约束**：只有无依赖的指令才能并行。RAW（Read After Write）、WAW、WAR 三种数据冒险需要停顿或转发解决。

### 1.2 依赖图与拓扑排序

超标量 CPU 的指令调度器构建微指令间的依赖 DAG（有向无环图）。只有入度为 0 的节点（所有依赖已满足）才能发射到执行单元。这本质上就是拓扑排序——每次选无依赖的指令发射，执行完成后更新下游依赖计数。

**死锁**：依赖图中的环（A 等 B，B 等 A）导致死锁。CPU 调度器在构建依赖图时就能检测环并拒绝调度——与 ARF 的 SkillPipeline 初始化校验完全一致。

### 1.3 事务内存（Transactional Memory）

**问题**：多线程共享数据时，锁太粗则并发差，锁太细则容易死锁。

**事务内存**（IBM Blue Gene/Q, 2011；Intel TSX, 2013）：程序员标记代码块为事务，硬件跟踪读集/写集。事务提交时检查冲突——无冲突则原子提交，有冲突则回滚重试。核心思想：**乐观并发**——假设冲突少，先执行再说，提交时发现冲突再回滚。

### 1.4 对 ARF 的启发

超标量启示了单轮内工具调用的并行执行——无依赖的工具可以同时跑。依赖图启示了 Skill Pipeline 的声明式依赖——显式描述执行顺序约束而非硬编码顺序。事务内存启示了文件操作的快照回滚——检查点 + undo。

---

## 2. ARF 当前实现

### 2.1 并发模型

ARF 采用分层并发策略：

| 层级 | 模型 | 实现 |
|------|------|------|
| Agent 循环 | 顺序 | `GraphEngine` while 循环，每轮迭代一个 turn |
| 工具调用（单轮内） | 并行（默认） | `ConcurrentToolExecutor`，`asyncio.gather`，semaphore 限制并发 5 |
| Hook 触发 | 并行 | `SubprocessHookRunner`，`asyncio.gather` 同时启动所有子进程 |
| 多 Agent（未来） | — | 尚未实现，`SequentialScheduler` 已定义但未接入 |

### 2.2 ConcurrentToolExecutor

`arf/engine/tool_executor.py`。默认 `strategy="parallel"`，通过 semaphore 限制最多 5 个并发工具调用：

```python
class ConcurrentToolExecutor:
    async def execute(self, tool_calls, strategy="parallel", max_concurrency=5):
        if strategy == "sequential":
            # 一个接一个执行
            ...
        else:
            sem = asyncio.Semaphore(max_concurrency)
            async def _run(tc):
                async with sem:
                    return tc["id"], await self._resolver.execute(...)
            tasks = [_run(tc) for tc in tool_calls]
            resolved = await asyncio.gather(*tasks, return_exceptions=True)
```

**事实校验**：引擎调用时未传 `strategy` 参数（`graph.py`），因此默认走 parallel 路径。README 表中"顺序执行"的说法不准确——Agent 循环是顺序的，但单轮工具调用是并行的。

### 2.3 Hook 并行触发

`SubprocessHookRunner.fire()`（`arf/hooks/runner.py`）将所有匹配的 Hook 作为子进程并行启动：

```python
tasks = [_run_hook(h) for h in hooks]
resolved_list = await asyncio.gather(*tasks, return_exceptions=True)
```

Hook 间无依赖关系——一个 Hook 的退出码不影响其他 Hook 的启动（仅退出码非 0 时中断**该 Hook 自身**的后续命令）。`asyncio.gather` 的 `return_exceptions=True` 确保单个 Hook 异常不影响其他。

**事实校验**：README 中"Hook 线程池并行"的说法不准确——Hook 使用的是 `asyncio.create_subprocess_shell` + `asyncio.gather` 协程并发，不是线程池。

### 2.4 Skill Pipeline — 在并行中插入顺序约束

`arf/skills/pipeline.py`。当 Skill 声明了 `pipeline` 字段时，框架强制执行工具调用的依赖顺序——这是一个**硬保证**，不是建议：

```yaml
skills:
  - name: resource_scaffold
    tools: [file_writer, resource_loader]
    pipeline:
      - tool: file_writer
      - tool: resource_loader
        depends_on: [file_writer]
```

引擎在每次工具调用前检查（`graph.py`）：

```python
sp = SkillPipeline(pipeline_data.get("steps", []))
if not sp.can_execute(tool_name, completed):
    denied_calls.append((tool_name, sp.validation_error(...)))
    continue  # 阻断
```

**Pipeline 行为**：

| 场景 | 结果 |
|------|------|
| pipeline 为空或无 pipeline | 所有工具自由并行 |
| 调用 pipeline 外工具（如 web_search） | 不受限 |
| 依赖未满足时调用 | 阻断 + 错误反馈 |
| 所有步骤完成后 | 后续调用自由 |

**初始化校验**：`SkillPipeline.__init__()` 在创建时立即检查——缺失依赖（A depends_on B 但 B 不在 pipeline）抛 `ValueError`；循环依赖（A→B→A）抛 `ValueError`。类似 CPU 调度器在构建依赖图时检测环。

**状态追踪**：`state["active_pipeline"]` 保存 steps 定义和 completed 列表。引擎在每次合法调用后更新 completed，作为下一轮的依赖判断依据。

### 2.5 配置

```yaml
# 并行度（可通过 AdvancedConfig.concurrency 配置）
advanced:
  concurrency:
    strategy: parallel         # parallel | sequential
    max_concurrency: 5

# Pipeline 在 Skill YAML 中声明
skills:
  - name: resource_scaffold
    tools: [file_writer, resource_loader]
    pipeline:
      - tool: file_writer
      - tool: resource_loader
        depends_on: [file_writer]
```

### 2.6 当前限制

- **无跨 Agent 并发**：单 Agent 循环内，多 Agent 协作（DAG 分析/调度）尚未实现
- **无 Worktree 隔离**：并发任务共享文件系统，存在竞态风险
- **并发度可配**：通过 `AdvancedConfig.concurrency`（`ConcurrencyConfig`）配置，详见 `advanced` 配置段
- **SequentialScheduler**（`arf/concurrency/sequential.py`）已定义但未在任何地方使用

---

## 3. 演进方向

### 3.1 对标 OS 最佳实践：多 Agent DAG 分析

当前是单 Agent 顺序循环 + 单轮工具并行。对标 OS 从单核到多核的演进，可以将独立子任务分派给多个 Agent 并行执行。

**DAG 调度**：复杂任务（如"研究 X 并写报告"）可拆分为子任务 DAG——研究阶段可以并行（查资料、搜网页、读文件），写作阶段依赖研究阶段完成。框架分析 DAG 后，独立子任务分派到不同 Agent（或同一 Agent 的不同 worktree），有依赖的子任务等待前置完成。

### 3.2 Worktree 隔离

对标 OS 进程的独立地址空间。多个 Agent 或子任务并发执行时，各自拥有独立的工作区（git worktree 或临时目录）。任务完成后再合并结果。这避免了"两个 Agent 同时修改同一个文件"的竞态问题。

### 3.3 探索性方向

**并发度动态调整**：根据 API 速率限制和系统负载动态调整 `max_concurrency`。类似 OS 的 CPU 频率调节（DVFS）——负载低时降低并发节省资源，负载高时提高并发。

**事务性文件操作**：对标事务内存。工具调用的文件修改进入事务——写操作先在临时区进行，所有工具成功后原子提交（rename），任何失败则回滚。类似 `SnapshotRollback`（`arf/errors/transaction.py`）已实现但仅用于状态回滚，不涉及文件。
