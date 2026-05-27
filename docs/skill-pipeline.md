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

### 1.5 并发基础概念

> 线程、进程、协程是操作系统和编程语言中三种核心的并发模型。理解它们的本质区别，有助于理解 ARF 为何在不同场景选择不同机制。

**进程（Process）**

进程是 OS 资源分配的基本单位。`fork()` 创建一个新进程时，OS 复制完整的地址空间（代码、数据、堆、栈），新进程拥有独立的虚拟内存页表。进程间天然隔离——一个进程崩溃不会影响另一个，但通信需要 IPC（管道、socket、共享内存），开销较大。

```
进程 A                         进程 B (fork 自 A)
┌─────────────────┐            ┌─────────────────┐
│ 代码段 (只读)    │            │ 代码段 (COW 共享) │
│ 数据段           │            │ 数据段 (写时拷贝) │
│ 堆               │            │ 堆 (独立)        │
│ 栈               │            │ 栈 (独立)        │
│ 文件描述符       │            │ 文件描述符 (独立) │
│ GIL (一个)       │            │ GIL (另一个)     │
└─────────────────┘            └─────────────────┘
        ↕  IPC (管道/socket/共享内存)
```

**线程（Thread）**

线程是 CPU 调度的基本单位。同一进程内的多个线程共享地址空间（代码段、数据段、堆），各自拥有独立的栈和寄存器上下文。共享内存使通信极快，但也带来竞态条件——两个线程同时写一个变量，结果不确定。

CPython 的 GIL（Global Interpreter Lock）是一个互斥锁，保证同一时刻只有一个线程执行 Python 字节码。这意味着 Python 多线程**无法实现 CPU 并行**——即使有 8 个核，8 个线程也只能交替执行。但 IO 操作（文件读写、网络请求、subprocess 等待）会释放 GIL，让其他线程有机会执行。

```
进程 (一个 Python 解释器)
├─ 线程 1 ─── GIL ─── [Python 代码] ── [IO: 释放 GIL] ── [Python 代码] ──
├─ 线程 2 ─────────── [等待 GIL] ───── [获得 GIL] ── [Python 代码] ── [释放]
└─ 线程 3 ─────────── [等待 GIL] ─────────────────────────── [获得 GIL] ──...

多线程适合：IO 密集任务（网络请求、文件读写）
多线程不适合：CPU 密集任务（计算、正则、编解码）→ 考虑多进程
```

**协程（Coroutine）**

协程是用户态协作式调度的"轻量线程"。与线程不同，协程的切换由程序显式控制（`await`），不依赖 OS 抢占式调度，不需要上下文切换到内核态。一个线程内可以运行成千上万个协程，因为协程只是一个函数调用帧，不是 OS 线程。

`asyncio` 的 event loop 在单线程中轮询所有就绪协程：协程 A 遇到 `await` 时主动让出控制权，event loop 调度协程 B 执行，B 再 `await` 时让出……如此循环。由于所有协程在同一线程中，不需要锁——同一时刻只有一个协程在执行。

```
单线程 event loop
│
├─ 协程 A: 执行...await IO ────[等待 IO]──────── 恢复执行...await ──
├─ 协程 B: 等待.................执行...await IO ────[等待]── 恢复...
├─ 协程 C: 等待................................执行...返回结果
└─ 协程 D: 等待................................................执行...

协程适合：高并发 IO（成千上万个连接）
协程不适合：CPU 密集任务（阻塞 event loop，其他协程饿死）
```

**三者的本质差异**：

| 维度 | 多进程 | 多线程 | 协程 (asyncio) |
|------|--------|--------|---------------|
| 调度者 | OS 抢占式 | OS 抢占式 | 用户态协作式 |
| 切换开销 | 大（页表、TLB flush） | 中（寄存器、栈切换） | 小（函数调用帧） |
| 地址空间 | 隔离 | 共享 | 共享 |
| 通信方式 | IPC | 共享内存（需锁） | 直接变量访问（无需锁） |
| GIL 影响 | 无（各自独立 GIL） | **有**（同一时刻一个线程） | 无（单线程内） |
| 内存开销 | 大（每个进程独立地址空间） | 中（每个线程独立栈 ~8MB） | 小（每个协程 ~KB） |
| 崩溃影响 | 隔离 | 可能影响整个进程 | 可能影响整个 event loop |
| 适合任务 | CPU 密集 / 隔离需求 | IO 密集（线程池） | 高并发 IO（大量连接） |

### 1.6 ARF 的选型逻辑

ARF 选择协程作为主力并发模型，原因是：

1. **Agent 的瓶颈是 IO，不是 CPU**。99% 的等待时间花在模型 API 调用（网络 IO）和工具执行（文件 IO、HTTP 请求）上，而非 Python 代码运算。协程在 IO 密集场景下效率最高，且无锁开销。

2. **单线程模型天然安全**。Agent 的状态（`AgentState`、`memory.json`、工具执行顺序）是共享可变状态。多线程需要加锁保护每一个读写点——这是 bug 的温床。协程的单线程模型保证了"同一时刻只有一件事在执行"，天然避免竞态。

3. **需要隔离时才用进程**。Hook 是外部脚本，不受信，且可能崩溃、超时。`create_subprocess_shell` 提供了 OS 级别的隔离——Hook 死了不影响 Agent，超时了就用 SIGKILL 杀掉。这正是"需要隔离时用进程"的策略。

4. **不用线程**。ARF 没有 CPU 密集任务需要多核并行，IO 并发已经由协程覆盖。引入线程池只会增加 GIL 竞争和锁的复杂度，没有实际收益。

```
ARF 并发选型决策树:

任务类型 ── 受信（框架内工具）── IO 密集 ── asyncio 协程 (ConcurrentToolExecutor)
          │                    └─ CPU 密集 ── 未来考虑 asyncio.to_thread()
          │
          └─ 不受信（外部脚本）─────────── OS 子进程 (SubprocessHookRunner)
          
协程之间需要顺序约束？── SkillPipeline.depends_on (DAG 拓扑序)
```

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

### 2.7 并发策略考量

ARF 内部两处并发使用了不同机制——工具执行用协程，Hook 执行用子进程。本节分析两者的差异、适用边界，以及 Python 版本演进（GIL）对此的影响。

#### 2.7.1 工具并发：`ConcurrentToolExecutor`

```
LLM 返回 N 个 tool_calls
    │
    ├─ tc_1 ──┐
    ├─ tc_2 ──┤  asyncio.gather
    ├─ tc_3 ──┤      │
    └─ tc_4 ──┘  asyncio.Semaphore(max_concurrency=5)
                     │
                     ▼
              ResourceResolver.execute(name, params)
                     │
                     ▼
              FunctionBackend.execute_with_fn(fn, params)
                     │
                     ▼
              fn(**params)   ← 工具函数在同一进程/event loop 中执行
```

关键代码路径：

```python
# tool_executor.py — 并发入口
sem = asyncio.Semaphore(max_concurrency)
async def _run(tc):
    async with sem:
        return tc["id"], await self._resolver.execute(tc["name"], params)
tasks = [_run(tc) for tc in tool_calls]
resolved = await asyncio.gather(*tasks, return_exceptions=True)
```

```python
# function.py — 工具实际执行
result = fn(**params)              # 调用工具函数
if hasattr(result, "__await__"):   # 如果是 async def
    result = await result           # await 协程
```

**并发模型**：协程并发（`asyncio.gather` + `asyncio.Semaphore`），所有工具共享同一个 event loop。

**对 GIL 的依赖**：工具函数（`file_reader`、`web_search`、`python_exec` 等）绝大多数是 IO 密集——读文件、HTTP 请求、子进程调用。IO 操作在 CPython 中会释放 GIL，因此即使多个工具"同时"执行，实际 IO 等待是真正并行的。但如果工具函数包含 CPU 密集的同步代码（如大文件解析、正则扫描），则会阻塞 event loop，其他协程无法被调度。

**`async def` 的实际语义**：ARF 的工具函数约定为 `async def`，但内部通常使用同步 stdlib（`Path.read_text()`、`urllib.request.urlopen()`）。这意味着：
- `await fn()` 将协程加入 event loop
- 协程体内调用同步 IO → GIL 在 IO 时释放 → event loop 可调度其他协程
- 协程体内有 CPU 密集计算 → GIL 持有 → event loop 阻塞 → 其他工具等待

**`max_concurrency` 的作用**：`asyncio.Semaphore(5)` 限制同时"执行中"的协程数量。对于纯 IO 的工具，限制并发的意义不大（event loop 本就可以管理大量协程）。但对于有资源约束的场景——如 API rate limit、文件句柄限制——Semaphore 是有意义的限流手段。

#### 2.7.2 Hook 并发：`SubprocessHookRunner`

```
Hook 触发事件
    │
    ├─ hook_1 ──┐
    ├─ hook_2 ──┤  asyncio.gather
    └─ hook_3 ──┘      │
                        ▼
              asyncio.create_subprocess_shell(cmd)
                        │
                        ▼
              OS fork() → 独立进程，独立地址空间
                        │
                        ▼
              await proc.communicate() → 非阻塞等待
```

关键代码路径：

```python
# runner.py — 并发启动子进程
proc = await asyncio.create_subprocess_shell(cmd, ...)
stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=tout)
```

```python
# 多 Hook 并发
tasks = [_run_hook(h) for h in hooks]
resolved_list = await asyncio.gather(*tasks, return_exceptions=True)
```

**并发模型**：进程级并发。每个 Hook 通过 `fork()` 创建独立 OS 进程，asyncio event loop 通过 `await proc.communicate()` 非阻塞等待进程退出。这不是线程池——没有线程创建开销，也没有 GIL 竞争。

**与工具并发的根本差异**：

| 维度 | `ConcurrentToolExecutor` | `SubprocessHookRunner` |
|------|--------------------------|------------------------|
| 执行单元 | Python 协程（同一进程） | OS 子进程（独立进程） |
| 地址空间 | 共享 | 隔离 |
| GIL 影响 | CPU 密集时阻塞 event loop | 无影响 |
| 隔离性 | 无（工具可访问 Agent 内存） | 强（子进程看不到 Agent 内存） |
| 通信方式 | 函数返回值 | stdout/stderr + 退出码 |
| 超时处理 | 无内置超时 | `asyncio.wait_for` + SIGKILL |
| 失败隔离 | `return_exceptions=True` 吞异常 | 单进程崩溃不影响其他 |
| 适用场景 | 框架内工具（受信、需共享状态） | 外部脚本（不受信、无需共享状态） |

#### 2.7.3 Python GIL 与版本差异

**Python 3.11（ARF 最低要求）**：
- GIL 始终启用。同一时刻仅一个线程执行 Python 字节码。
- IO 操作（文件读写、socket、subprocess）在系统调用层面释放 GIL。
- `asyncio` 协程在单线程中基于 event loop 协作调度，不涉及多线程。

**Python 3.13+ 的 free-threaded 模式（PEP 703）**：
- 通过 `--disable-gil` 编译标志开启，默认构建仍带 GIL。
- 移除 GIL 后，`ThreadPoolExecutor` 可实现真正的 CPU 并行。
- 对协程模型影响极小——asyncio 仍运行在单线程 event loop 中。

**对 ARF 的影响评估**：

| ARF 组件 | 机制 | 受 GIL 影响？ | free-threaded 下有变化？ |
|----------|------|-------------|----------------------|
| `ConcurrentToolExecutor` | asyncio 协程 | IO 操作无影响；CPU 密集代码阻塞 event loop | 不变——除非改用线程池执行工具 |
| `SubprocessHookRunner` | OS 子进程 | 无影响 | 不变 |
| `GraphEngine` 主循环 | 单协程 | 无影响 | 不变 |
| Model API 调用 | HTTP（httpx/openai） | IO 释放 GIL | 不变 |

结论：ARF 的并发策略在 Python 3.11 到 3.14 各版本下行为一致，GIL 的存在与否不影响当前架构。如果未来需要 CPU 密集的工具并行（如图像处理、大规模计算），可引入 `asyncio.to_thread()` 或将工具改为 subprocess 执行。

#### 2.7.4 并发策略选型指南

```
需要并发执行多个任务，应该用什么机制？

任务是否受信（框架内工具）？
├─ 是 → 任务是否 IO 密集？
│       ├─ 是 → ConcurrentToolExecutor (asyncio.gather + Semaphore)
│       └─ 否（CPU 密集）→ 考虑 asyncio.to_thread() 或改为 subprocess
└─ 否（外部脚本/不受信代码）→ SubprocessHookRunner (create_subprocess_shell)

是否需要在任务间保证执行顺序？
├─ 是 → SkillPipeline 声明 depends_on 依赖链
└─ 否 → 自由并发

是否需要限制并发数量？
├─ 是 → ConcurrencyConfig.max_concurrency（工具）/ Hook 无限制
└─ 否 → 使用默认值
```

### 2.8 工具调用闭环（Tool Call Closure）

#### 问题

LLM 的一次响应可能包含多个 `tool_calls`。引擎在执行前需要经过多层检查——Pipeline 依赖、路径沙箱、权限配置、人工审批。任何一个环节拒绝某个工具调用时，`state["messages"]` 中已经追加了该 assistant 消息（含 `tool_calls`），但没有对应的 `role: "tool"` 结果消息。

这导致两个问题：

1. **API 格式非法**：模型 API 要求每个 `tool_calls` 都有配对的 `tool` 消息。缺少配对时，下一轮 API 请求返回 `400 invalid_request_error`。
2. **LLM 认知断裂**：LLM 不知道它的工具调用被拒绝了——对话历史里缺少反馈，它可能重复尝试同一个被阻断的操作。

#### 解决方案

`GraphEngine` 在 `invoke()` 和 `astream()` 双路径中，对被拒绝的工具调用注入 synthetic（合成）tool result：

```python
# arf/engine/graph.py — invoke 路径
for tc in tool_calls:
    name = tc.get("name", "")
    matched = next((reason for dname, reason in denied_calls if dname == name), None)
    if matched is None:
        continue  # valid call, handled by normal execution
    tc_id = tc.get("id", "")
    # 注入合成结果，闭环该 tool_call
    state["messages"].append({
        "role": "tool",
        "tool_call_id": tc_id,
        "content": f"[Blocked] {matched}",  # 拒绝原因对 LLM 可见
    })
```

被拒绝的常见原因及其 `[Blocked]` 消息：

| 拒绝来源 | 消息 | 含义 |
|----------|------|------|
| Pipeline 依赖未满足 | `[Blocked] tool 'X' depends on 'Y'` | 前序步骤未完成 |
| PathCheckToolGuard | `[Blocked] Path traversal blocked` | 路径穿越/绝对路径 |
| ToolPermissionChecker | `[Blocked] denied by permission config` | 工具在 `deny` 列表 |
| 审批超时 | `[Blocked] approval timed out` | 60s 内无人审批 |
| 审批拒绝 | `[Blocked] denied by user` | 用户点击拒绝 |

此外，`_close_tool_calls()` 方法在每次模型调用前做兜底扫描——如果存在未被上述逻辑覆盖的孤立 `tool_calls`（如异常退出导致的状态不完整），同样注入 `(tool result unavailable)` 占位结果。

#### 与 Pipeline 的配合

闭环注入和 Pipeline 依赖追踪**不冲突**，各自维护不同的状态维度：

```
        ┌─────────────────────────────────────────────┐
        │          tool_calls 处理流程                  │
        │                                              │
        │  LLM 输出 [file_writer, resource_loader]     │
        │         │                                    │
        │         ├─ guard/approval 检查               │
        │         │     │                              │
        │         │     ├─ valid_calls (放行)          │
        │         │     └─ denied_calls (拒绝)         │
        │         │                                    │
        │         ▼                                    │
        │  ┌──────────────────┬──────────────────┐     │
        │  │   messages 层    │   pipeline 层     │     │
        │  │ (API 格式合法性)  │ (执行正确性)      │     │
        │  ├──────────────────┼──────────────────┤     │
        │  │ valid: tool call │ valid: 加入       │     │
        │  │ + tool result    │ completed 列表    │     │
        │  ├──────────────────┼──────────────────┤     │
        │  │ denied: tool call│ denied: 不加入    │     │
        │  │ + [Blocked] 注入 │ completed 列表    │     │
        │  └──────────────────┴──────────────────┘     │
        │                                              │
        │  效果：                                       │
        │  • API 不报 400（messages 始终配对）         │
        │  • LLM 知道哪个工具被拒（[Blocked] 可见）     │
        │  • 依赖工具被拒 → completed 中无记录          │
        │    → 后续依赖检查继续阻断，防止跳步执行       │
        └─────────────────────────────────────────────┘
```

示例流程：

```
Turn N:   LLM 调用 file_writer → 审批超时 denied
          → messages 追加 assistant(tool_calls=[file_writer])
          → messages 追加 tool([Blocked] approval timed out)
          → completed 不变（仍为 []）

Turn N+1: LLM 看到 [Blocked] 消息，知道 file_writer 失败
          可能尝试 resource_loader
          → can_execute(resource_loader, completed=[])
          → file_writer ∉ completed → BLOCKED
          → messages 追加 [Blocked] tool 'resource_loader' depends on 'file_writer'
          → LLM 理解：需要先成功执行 file_writer

Turn N+2: LLM 重试 file_writer（更合适的参数）→ 审批通过 → 执行成功
          → messages 追加 tool(result) 
          → completed = ["file_writer"]
          → resource_loader 现在可以执行了
```

#### 配置

无需额外配置。闭环逻辑在 `GraphEngine.invoke()` 和 `astream()` 中自动运行，覆盖所有拒绝路径。

---

## 3. 演进方向

### 3.1 对标 OS 最佳实践：多 Agent DAG 分析

当前是单 Agent 顺序循环 + 单轮工具并行。对标 OS 从单核到多核的演进，可以将独立子任务分派给多个 Agent 并行执行。

**DAG 调度**：复杂任务（如"研究 X 并写报告"）可拆分为子任务 DAG——研究阶段可以并行（查资料、搜网页、读文件），写作阶段依赖研究阶段完成。框架分析 DAG 后，独立子任务分派到不同 Agent（或同一 Agent 的不同 worktree），有依赖的子任务等待前置完成。

### 3.2 Worktree 隔离

对标 OS 进程的独立地址空间。多个 Agent 或子任务并发执行时，各自拥有独立的工作区（git worktree 或临时目录）。任务完成后再合并结果。这避免了"两个 Agent 同时修改同一个文件"的竞态问题。

### 3.3 探索性方向

**并发度动态调整**：根据 API 速率限制和系统负载动态调整 `max_concurrency`。类似 OS 的 CPU 频率调节（DVFS）——负载低时降低并发节省资源，负载高时提高并发。

**事务性文件操作**：对标事务内存。工具调用的文件修改进入事务——写操作先在临时区进行，所有工具成功后原子提交（rename），任何失败则回滚。`FunctionBackend` 在当前 turn 内已支持单 tool 失败时的 rollback 回调，但跨 tool 的原子事务尚未实现。
