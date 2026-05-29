# A2A Communication — Agent 进程间通讯

ARF 将 OS 进程间通讯（IPC）的经典原语——信号、管道、共享内存、消息队列——适配到多 Agent 场景。Handoff 是信号，AgentBus 是管道，DictWorkspace 是共享内存，PeerAgent 是 P2P 网络。

---

## 1. OS 方案演进

> 本章描述 OS 如何解决进程间通讯问题，作为 ARF 设计思路的参考。非严格技术对标。

### 1.1 信号 — 异步通知

**问题**：进程 A 如何通知进程 B 发生了某事件，而不需要 B 轮询？

**Unix 信号**（Unix V1，1971）是最原始的 IPC 原语。内核向目标进程递送信号（SIGINT、SIGTERM、SIGUSR1 等），进程注册 handler 或执行默认动作。信号携带的信息量极少（一个整数），但异步递送的机制深刻影响了所有后来的 IPC 设计。

**信号 vs 轮询**：轮询消耗 CPU 且响应延迟取决于轮询间隔。信号实现"事件发生时立即通知"，零 CPU 开销，零延迟。

**实时信号**（POSIX.1b，1993）：`SIGRTMIN–SIGRTMAX` 支持队列化和携带 `sigval` 数据指针，弥补了传统信号无数据的缺陷。队列深度有限（`/proc/sys/kernel/rtsig-max`），但语义从"通知"升级为"通知 + 数据"。

### 1.2 管道 — 单向字节流

**问题**：两个进程需要传输数据，而不是仅一个信号。

**匿名管道**（Unix V3，1973）：`pipe()` 创建一对 fd——`fd[0]` 读，`fd[1]` 写。数据传输是单向的，使用方必须在 fork 后关闭不需要的一端。数据在内核缓冲区中流动，`read()` 阻塞直到有数据或写端关闭。shell 的 `|` 操作符直接用匿名管道连接两个进程。

**命名管道（FIFO）**（System III，1982）：`mkfifo()` 创建一个文件系统节点，无亲缘关系的进程也可以打开它进行读写。数据同样在内核缓冲区，不经过磁盘。

管道的局限性决定了它的使用场景：单向、流式、无消息边界、容量受限于内核缓冲区（Linux 默认 64KB）。这直接启发了消息队列的设计——需要消息边界和更大容量时，管道不够。

### 1.3 共享内存 — 零拷贝共享

**问题**：管道和消息队列都要经过内核拷贝（用户态 ↔ 内核态），大块数据传输开销大。

**System V 共享内存**（System V，1983）：`shmget()` + `shmat()` 将同一块物理内存映射到多个进程的虚拟地址空间。进程 A 写入，进程 B 立即可见——不需要系统调用，不需要拷贝。代价是必须自行处理竞争条件。

**POSIX 共享内存**（POSIX.1b，1993）：`shm_open()` + `mmap()`，用文件描述符接口替代 SysV 的 key 机制，与文件系统命名空间统一。

**mmap 跨进程**：`mmap(MAP_SHARED)` 将同一文件映射到两个进程，写入操作最终由内核回写磁盘。多进程同时读写时需要 `msync()` 保证一致性。

共享内存的核心权衡：速度最快（零拷贝），但编程模型最难（无同步原语）。这直接推动了信号量/互斥锁的出现。

### 1.4 消息队列 — 结构化异步消息

**问题**：管道无消息边界，信号只能带一个整数，共享内存无同步——需要一种"带消息边界的异步通道"。

**System V 消息队列**（System V，1983）：`msgsnd()` / `msgrcv()` 收发带类型标签的消息。内核维护消息边界，接收方可以按类型选择性接收。消息有长度上限（`/proc/sys/kernel/msgmax`，默认 8KB），队列有总容量上限。

**POSIX 消息队列**（POSIX.1b，1993）：`mq_open()` / `mq_send()` / `mq_receive()`，支持优先级、超时、异步通知（`mq_notify()` 注册信号/线程回调）。解决了 SysV 消息队列无法与 `select/poll` 集成的痛点。

消息队列的语义——有边界的、带元数据的、异步可缓冲的消息——直接映射到 AgentBus 的 `AgentMessage` 数据模型。

### 1.5 套接字 — 跨机器通讯

**问题**：以上所有 IPC 仅限于单机。进程需要与另一台机器上的进程通讯。

**BSD 套接字**（4.2BSD，1983）：统一了网络和本地通讯的 API。`socket()` + `bind()` + `connect()` / `listen()` + `accept()`。TCP 提供可靠字节流，UDP 提供不可靠数据报，Unix Domain Socket 提供单机高性能 IPC。

**gRPC**（Google，2015）：基于 HTTP/2 和 Protocol Buffers 的 RPC 框架。流式双向通讯、强类型契约、多语言代码生成。gRPC 之于 Agent 通讯，正如 TCP 之于分布式系统——提供可靠的、类型安全的传输层。

### 1.6 同步与共识

**信号量**（Dijkstra，1965）：P 操作（减 1，为负则等待）和 V 操作（加 1，唤醒等待者）。保护共享资源免于竞争条件。POSIX semaphore 支持进程间（`sem_open`）和线程间（`sem_init`）。

**分布式锁**：将单机互斥扩展到分布式环境。Chubby（Google，2006）提供粗粒度锁服务 + 小文件存储，用于 GFS 和 BigTable 的 leader 选举。etcd（CoreOS，2013）和 ZooKeeper 基于共识协议（Raft / ZAB）提供一致性的分布式锁和配置管理。

**共识协议**：Paxos（Lamport，1989）解决"多个 proposer 就一个值达成一致"的问题。Raft（2014）以可理解性为目标重新设计——leader 选举、日志复制、安全性保证三部分独立。多数投票是共识的最简形式：N 个节点独立投票，取多数意见。

### 1.7 对 ARF 的启发

| OS 原语 | 传输语义 | ARF 对应 |
|---------|----------|----------|
| 信号 (SIGUSR1) | 异步通知，无数据 | `HandoffManager` — 工具返回 `{"handoff": True}` 触发引擎切换 Agent |
| 管道 (pipe/FIFO) | 单向字节流 | `InMemoryAgentBus` — `asyncio.Queue` 背压缓冲的点对点消息通道 |
| 共享内存 (shm/mmap) | 零拷贝共享 | `DictWorkspace` — 多 Agent 共享键值存储 |
| 消息队列 (POSIX mq) | 有边界、带标签的消息 | `AgentBus.send/receive` — `AgentMessage` 强类型消息 |
| 套接字 (TCP/UDP) | 跨机器流/数据报 | 演进 → 网络 A2A（gRPC） |
| 信号量/互斥锁 | 互斥访问 | `InMemoryLock` — TTL 保护的分布式锁 |
| 共识 (Paxos/Raft) | 分布式一致性 | `MajorityVoteConsensus` — 提案 + 投票 |

---

## 2. ARF 当前实现

A2A 通讯分为四条通路：**信号式 Handoff**（引擎自动切换）、**消息总线**（异步消息传递）、**去中心化 P2P**（PeerAgent 协商与发现）、**集中式编排**（Supervisor + Consensus）。

### 2.1 架构总览

```
┌─────────────────────────────────────────────────────────────┐
│                      Agent Session                          │
│                                                             │
│  ┌──────────────┐     handoff signal     ┌──────────────┐  │
│  │   Agent A    │ ────────────────────→  │   Agent B    │  │
│  │              │ ←────────────────────  │              │  │
│  └──────┬───────┘     handoff back       └──────┬───────┘  │
│         │                                       │          │
│         │    AgentBus (asyncio.Queue)            │          │
│         ├───────────────────────────────────────┤          │
│         │  send / receive / broadcast / discover │          │
│         │                                       │          │
│         │    DictWorkspace (shared dict)         │          │
│         ├───────────────────────────────────────┤          │
│         │  write(key, value) / read(key)        │          │
│         │                                       │          │
│         │    InMemoryLock (TTL-protected)       │          │
│         ├───────────────────────────────────────┤          │
│         │  acquire / release                    │          │
│         │                                       │          │
│         │    MajorityVoteConsensus              │          │
│         └───────────────────────────────────────┘          │
│              propose / vote                                │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 HandoffManager — 信号式 Agent 切换

**文件**：`arf/engine/handoff.py`

对标 OS 信号机制：工具调用返回 `{"handoff": True}` 触发引擎异步切换活跃 Agent。不同于传统的函数调用栈——Handoff 是跨 Agent 的上下文切换。

**检测**：`HandoffManager.detect(tool_results)` 扫描每轮工具执行结果，查找 `{"handoff": True}` 信号。兼容四种返回格式——`ToolResult.data`、嵌套 dict `{"data": ...}`、FunctionBackend 包装 `{"result": ...}`、直接 dict。

**目标解析**（`HandoffManager.resolve`）：四级递进策略，后一级在前一级无法确定目标时生效：

```
1. 单候选 — len(candidates) == 1 → 直接返回 candidates[0].to_agent

2. LLM 语义匹配 — system_model 可用时，LLM 将 task 文本与各 candidate trigger
   做语义匹配，选择最佳规则 → 返回对应的 to_agent

3. 关键词 fallback — system_model 不可用或匹配失败 → trigger 文本分词后与
   task 做交集，首个命中规则生效

4. 默认回退 — 以上均无法匹配 → 返回 candidates[0].to_agent（第一个候选）
```

**上下文构建**（`HandoffManager.build_target_context`）：根据 `HandoverContextConfig` 决定传递给目标 Agent 的信息：

- `raw_turns`：携带最近 N 轮对话作为上下文（`-1` = 全部，`0` = 不给）
- `task_summary`：用 system_model 生成任务单句摘要，注入 system 消息
- 目标 Agent 的 system_prompt 前置
- handoff 消息作为 user role 放入

**引擎集成**（`arf/engine/graph.py` — `_execute_handoff` 方法，在 invoke/astream 两条路径中调用）：

```
invoke() / astream() 主循环:
  → model call → tools execute
  → HandoffManager.detect(tool_results)
  → 检测到 handoff?
    → state_store.put(session_id/from_agent, state)  # 保存当前
    → HandoffManager.resolve(from_agent, handoff_data)
    → HandoffManager.build_target_context(...)
    → state["active_agent"] = to_agent
    → state["messages"] = new_messages
    → emit("agent_switch", {from, to, task})
    → continue  # 下一轮自动用新 Agent 配置
```

反向 handoff（System Agent → User Agent）走完全相同的检测和解析流程。`_execute_handoff` 发现目标 Agent 的 state 已在 store 中存在时，直接恢复而非重新构建。

### 2.3 InMemoryAgentBus — 消息总线

**文件**：`arf/communication/in_memory_bus.py`

对标 OS 管道 + 消息队列。每个 Agent 拥有独立的 `asyncio.Queue`，发送方通过 `name` 指定目标，总线负责路由投递。

```python
class InMemoryAgentBus:
    _queues: dict[str, asyncio.Queue]   # 每个 Agent 一个队列（maxsize=100）
    _agents: dict[str, AgentInfo]       # 注册的 Agent 能力清单

    async def send(message: AgentMessage):
        if message.receiver is None:
            → broadcast: 投递到所有已注册队列
        else:
            → targeted: 投递到指定 Agent 的队列

    async def receive(agent_name: str):
        → 从自己的队列中 yield AgentMessage

    async def register(agent: AgentInfo):
        → 创建队列 + 记录能力清单

    async def discover(capability: str | None):
        → 按能力筛选已注册 Agent
```

**AgentMessage 数据模型**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `sender` | `str` | 发送方 Agent 名 |
| `receiver` | `str \| None` | 接收方（None = broadcast） |
| `type` | `Literal["task_delegate", "info", "query", "handoff"]` | 消息类型 |
| `payload` | `dict` | 消息体 |
| `correlation_id` | `str` | 请求-响应匹配 ID |
| `reply_to` | `str \| None` | 回复目标 Agent 名 |

**背压机制**：`asyncio.Queue(maxsize=100)` 限制队列深度。队列满时 `put()` 阻塞，自然施加背压——对标 OS 管道的数据流控制。

### 2.4 PeerAgent — 去中心化 P2P 通讯

**文件**：`arf/communication/peer.py`

对标 OS 套接字编程——每个 Agent 是独立的 peer endpoint。不依赖 Supervisor，**任何 Agent 都可以直接发现其他 Agent 并通讯**。

```python
class PeerAgent:
    # 注册
    async def start():
        → bus.register(AgentInfo(name, description, capabilities))

    # 广播
    async def broadcast(msg_type, payload):
        → bus.send(AgentMessage(receiver=None, ...))

    # 定向发送
    async def send_to(target, msg_type, payload):
        → bus.send(AgentMessage(receiver=target, ...))

    # 协商
    async def negotiate(proposal, peers, timeout=30s):
        → 向一组 peer 发送 query → 收集响应（在超时前）
        → 返回 {peer_name: response_payload}

    # 发现
    async def discover_peers(capability=None):
        → bus.discover(capability)  # 按能力筛选

    # 高层 Handoff
    async def handoff(task, context, target_capability, timeout=60s):
        → find_peer(capability) → send_to(peer, "handoff", {task, context})
        → 等待响应
```

**与 HandoffManager 的关系**：`HandoffManager` 是引擎集成的信号式切换（基于 `agent.yaml` 静态配置 + `{"handoff": True}` 工具信号）；`PeerAgent` 是框架库层面的去中心化 P2P 通讯（基于 AgentBus 动态发现 + 消息传递）。两者互补——HandoffManager 处理"当 User Agent 的工具说 handoff 时切换到谁"，PeerAgent 处理"当 Agent 需要跟其他 Agent 通讯时怎么收发消息"。

### 2.5 DictWorkspace — 共享内存

**文件**：`arf/communication/shared_workspace.py`

对标 OS 共享内存：多个 Agent 共享一个 `dict` 存储，写入立即可见，零拷贝。由 `SharedWorkspace` Protocol 定义接口：

```python
class SharedWorkspace(Protocol):
    async def write(key: str, value: dict, owner: str) -> None
    async def read(key: str) -> dict | None
```

用于跨 Agent 传递结构化数据——如 System Agent 创建的工具 schema、Skill 生成结果、中间推理状态。

### 2.6 InMemoryLock — 同步锁

**文件**：`arf/communication/lock.py`

对标 OS 信号量/互斥锁，带 TTL 保护防死锁：

```python
class Lock(Protocol):
    async def acquire(key: str, owner: str, ttl: float = 30.0) -> bool
    async def release(key: str, owner: str) -> None

class InMemoryLock:
    # 单字典实现，ttl 到期自动释放
    # acquire() 返回 bool — 成功获取或已被占用
```

防止两个 Agent 同时修改同一共享资源——如同时写 `tools/` 下的同名文件、同时更新 `DictWorkspace` 的同一 key。

### 2.7 MajorityVoteConsensus — 分布式共识

**文件**：`arf/communication/consensus.py`

对标 Paxos/Raft 的最简形式——多数投票：

```python
class MajorityVoteConsensus:
    def __init__(threshold=0.5):
        # 默认超过半数即通过

    async def propose(proposal, voters) -> dict:
        # 创建提案，返回 {"proposal_id": ..., "status": "open"}

    async def vote(proposal_id, vote):
        # 记录投票
```

当多个 Agent 需要对"下一步做什么"达成一致时使用——如多个 System Agent worker 竞争创建同一工具的权限、多轮协商后选择最佳方案。

### 2.8 RoundRobinSupervisor — 集中式编排

**文件**：`arf/communication/supervisor.py`

对标 OS 进程调度器——轮询分派任务给可用的工作 Agent：

```python
class RoundRobinSupervisor:
    async def route_task(task, agents) -> str:
        # agents[self._index % len(agents)].name
        # _index += 1

    async def should_intervene(handle_id, progress) -> bool:
        # 检测是否需要干预（当前总是 False）

    async def synthesize(results) -> str:
        # 合并多个 Agent 的结果
```

`Supervisor` Protocol 定义了编排接口——任何实现了 `route_task` / `should_intervene` / `synthesize` 的类都可以作为 Supervisor。`RoundRobinSupervisor` 是最简单的实现；更复杂的策略（优先级调度、负载感知）可在不改变接口的情况下替换。

### 2.9 协议层

**文件**：`arf/core/protocols/communication.py`

六个 Protocol 类定义全部 A2A 通讯抽象：

| Protocol | 对标 OS | 抽象能力 |
|----------|---------|----------|
| `AgentBus` | 管道 + 消息队列 | `send` / `receive` / `register` / `discover` |
| `PeerAgent` | 套接字 + P2P | `broadcast` / `negotiate` |
| `TaskDelegator` | RPC | `delegate` / `get_result` |
| `Supervisor` | 进程调度器 | `route_task` / `should_intervene` / `synthesize` |
| `SharedWorkspace` | 共享内存 | `write` / `read` |
| `Lock` | 信号量/互斥锁 | `acquire` / `release` |
| `ConsensusProtocol` | Paxos/Raft | `propose` / `vote` |

框架通过 Protocol 做 DI——所有实现类可替换。`InMemoryAgentBus` → 未来 `GrpcAgentBus`，`MajorityVoteConsensus` → 未来 `RaftConsensus`，上层代码无需感知。

### 2.10 引擎集成

A2A 通讯在引擎循环中的介入点：

1. **Handoff 检测** — 每次工具执行后（`invoke` 和 `astream` 两条路径均覆盖）
2. **Agent 切换** — `_execute_handoff` → `active_agent` 变化 → 下一轮自动使用新 Agent 的 system_prompt / tools / skills / max_turns
3. **`_agent_mode` 注入** — `state["active_agent"]` → `tool_executor.execute(agent_mode=...)` → `params["_agent_mode"]`
4. **`agent_switch` 事件** — emit 到 `EventBus` 落盘 `FileTraceStore`，SSE stream 可消费此事件通知上层

### 2.11 配置

```yaml
# agent.yaml

# 子 Agent 定义
agents:
  - name: sys_agent
    role: 系统工程师
    system_prompt:
      template: |
        You are the ARF System Engineer.
        {{INVENTORY}}
        ## Critical Rules
        {{CRITICAL_RULES}}
    models:
      - type: deep
        model: deepseek-v4-pro
        ...
    tools:
      - name: file_writer
        activation: kernel
      ...
    skills:
      - name: resource_scaffold
        activation: kernel
      ...
    advanced:
      max_turns: 15
      routing:
        strategy: static
        default: deep

# 交接规则
handover:
  rules:
    - from_agent: arf_assistant
      to_agent: sys_agent
      trigger: "创建或修改 resources(tools/skills/models) 目录下的资源文件"
      context:
        raw_turns: 5        # 携带最近 5 轮对话上下文
        task_summary: true   # system_model 生成任务摘要
    - from_agent: sys_agent
      to_agent: arf_assistant
      trigger: "资源操作完成或需要用户确认"
      context:
        raw_turns: 0        # 返回时不给原始上下文
        task_summary: true
```

---

## 3. 演进方向

### 3.1 网络 A2A — 跨进程 / 跨机器通讯

当前所有 A2A 通讯均基于 `asyncio.Queue` 实现（`InMemoryAgentBus`），局限于同一进程。对标 OS 从 Unix Domain Socket 演进到 TCP/IP 的路径——将 AgentBus 扩展到跨进程、跨机器的网络通讯。

**GrpcAgentBus**：实现 `AgentBus` Protocol，底层用 gRPC `bidirectional_stream` 替代 `asyncio.Queue`。每个 Agent 是 gRPC server + client，通过 service discovery 找到对端。`AgentMessage` 映射为 protobuf 消息。

**优势**：
- 同一台机器上的多个 Agent 进程可以独立部署、独立升级
- 不同机器上的 Agent 可以借助网络通讯——System Agent 部署在高算力 GPU 节点上
- gRPC 自带负载均衡、重试、超时、TLS 加密——框架无需重新实现

**过渡方案**：先实现 `UnixDomainSocketAgentBus`——单机跨进程，零网络开销，与 `InMemoryAgentBus` 共享同一 Protocol，上层代码无感知。

### 3.2 发布/订阅事件总线 — 动态 Agent 发现

当前 `InMemoryAgentBus.discover()` 依赖静态注册（Agent 启动时 `register(AgentInfo)`），无法处理 Agent 的动态上下线。

对标 OS udev 的设备热插拔发现 + MQTT/Redis PubSub 的消息模式——Agent 上线时 announce，下线前 farewell，其他 Agent 通过订阅得知拓扑变化。

**三步演进**：

1. **Agent 心跳**：`AgentBus` 新增 `heartbeat(agent_name, ttl)` ——定期发送，超时自动从注册表中移除
2. **事件订阅**：`AgentBus` 新增 `subscribe(topic)` / `publish(topic, message)` ——Agent 不只按名称通讯，也按主题通讯。如"resource_created" topic 下所有订阅者收到通知
3. **拓扑感知**：Agent 可查询"当前谁在线，各自有什么能力"，不依赖预配置的 Agent 列表。类似 `getent hosts` 之于 DNS——运行时发现而非启动时写死

### 3.3 DAG 多 Agent 调度

当前 `HandoffManager` 的 handover 模型是**链式**的（A → B → A），`RoundRobinSupervisor` 是**轮询**的。对标 OS 进程调度从简单 FIFO 演进到 DAG 任务图——多个 Agent 并行执行，有依赖关系的串行，无依赖的并发。

**Supervisor 演进**：

```
当前: RoundRobinSupervisor(agents) → 轮询分派一个任务给一个 Agent

目标: DAGSupervisor
  task_graph = {
    "design":    {"agent": "architect",    "depends_on": []},
    "implement": {"agent": "sys_agent_1",  "depends_on": ["design"]},
    "validate":  {"agent": "sys_agent_2",  "depends_on": ["design"]},
    "activate":  {"agent": "loader",       "depends_on": ["implement", "validate"]},
  }
  → 调度: design 先跑 → implement 和 validate 并行 → activate 汇总
```

这需要：
- `TaskDelegator` Protocol 扩展支持 `delegate_batch(tasks: list[dict])`
- DAG 分析器自动检测依赖环
- 每个 Agent 任务完成后 emit `task_complete`，Supervisor 检查哪些后继任务的前置条件已满足

### 3.4 探索性方向

**Agent 注册中心**：当前 Agent 定义在 `agent.yaml` 的 `agents:` 段——静态、预配置。未来支持 Agent 运行时注册——类似 DNS SRV 记录，Agent 启动后向注册中心宣告自己的名称和能力。其他 Agent 通过查询注册中心发现。对标 etcd/ZooKeeper 的服务注册。

**多 Agent 事务**：当 handoff 涉及多个 Agent 的状态变更时，需要两阶段提交保证一致性。对标 OS 的文件系统 journaling——所有参与 Agent 的 state 变更要么全部生效，要么全部回滚。

**Agent 通信安全**：网络 A2A 场景下需要加密和认证。gRPC 自带 TLS + JWT token 注入——`GrpcAgentBus` 建设时直接纳入。

**死锁检测**：当多个 Agent 通过 `InMemoryLock` 竞争同一组资源时可能出现死锁。参考 Linux `lockdep` 的锁依赖图——运行时检测锁获取顺序，发现潜在环时告警而非卡死。
