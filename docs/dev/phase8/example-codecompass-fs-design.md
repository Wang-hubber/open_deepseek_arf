# Phase 8 示例 + 最小框架补丁 — codecompass-fs 设计

> **Date**: 2026-07-02
> **Status**: Design — 用户已批准，待 spec 自审 → 写实现计划 → 实施
> **父文档**: `docs/dev/phase8-abstractions-and-a2a-plan.md`
> **目标**: 用一个完整的端到端示例（codecompass-fs）验证 ARF 框架的 MVP 能力集，同时补足实施该示例必需的最少框架补丁

---

## 1. 范围

### 1.1 包含
- **示例 app**：codecompass-fs — 一个代码理解 agent，纯 Python CLI 单进程入口
- **MVP 能力**（用户已选）：多会话存档切换 / 多轮对话 / 中断恢复 / 多 MCP 节点 / DAG tool / subagents / peer agents / compact
- **框架最小补丁**：7 个（F1-F7），见 §3
- **端到端测试**：`tests/e2e/test_codecompass_fs.py` 覆盖全部 MVP 能力

### 1.2 不包含（Phase 8 后续）
- skill 的完整渐进披露机制（仅最小集成，证明通道存在）
- memory 完整语义（仅最小 ActionMessage，不做长期记忆检索）
- human_handoff（仅留 ActionMessage 占位，不接 UI）
- permission gating
- streaming UI 渲染
- 远程 MCP 真实 HTTP server（用本地 mock server 占位）
- Phase 8 全部 13 任务（推迟，按需补）

### 1.3 与既有 phase8 草案的关系
本 spec 是 `phase8-abstractions-and-a2a-plan.md` 的**实施子集**——只实施 §3.1（F1/F2/F4/F7）+ §3.4（F7 部分）+ §3.6（F3 部分）+ 新增 §3.7 任务（Compactor + SessionStore）。其余 phase8 任务保留为后续 spec。

---

## 2. 示例 app 设计

### 2.1 领域选择：代码理解 agent
理由：
1. 与 opencode 同领域，参考价值最高
2. 工具集合丰富（read/grep/edit/git），最适合展示 DAG 并发
3. 多轮对话价值大（重构任务上下文跨多次 tool 调用）
4. 与 `examples/python/ex03_tool_call.py` 等基础示例有清晰层次

### 2.2 进程模型：单进程多 Engine
```
[1 个 Python 进程]
   │
   ├─ 1 个主 Engine (NodeId=engine/codecompass-main)
   │    └─ 主对话循环
   ├─ 0~N 个 subagent Engine (NodeId=engine/subagent-{uuid})
   │    └─ 由 SubagentDelegate 触发 spawn
   ├─ 2 个 peer Engine (NodeId=engine/peer-{a,b})
   │    └─ 启动时随主 Engine 一起上线
   └─ 4 个 MCP Node
        ├─ mcp/fs (local, 4 tools)
        ├─ mcp/code (remote mock, 1 tool)
        ├─ mcp/git (local, 3 tools)
        └─ mcp/web (remote mock, 1 tool)
所有节点挂同一条 Bus（py-arf 的 in-process Bus）。
```

### 2.3 文件结构
```
examples/python/codecompass_fs/
├── __init__.py
├── cli.py                    # CLI 入口：session 选择 + 多轮对话 + Ctrl-C 恢复
├── app.py                    # 装配：Bus + EngineBuilder + 4 MCP + 2 peer
├── session_store.py          # SQLite session 持久化（py-arf 会话 store）
├── subagent_launcher.py      # 子 Engine spawn / wait / kill helper
├── peer_coordinator.py       # peer Engine 启动 + peer_message 发送
└── trace_view.py             # 可选：tail Bus trace 打印人类可读摘要

codecompass_fs/                # 运行时资源目录（cwd 相对）
├── engine.toml                # AgentConfig: model + tools + skills + subagents + peers
├── tools/                     # 本地 MCP tool 子进程实现
│   ├── read_file/{tool.toml,main.py}
│   ├── grep/{tool.toml,main.py}
│   ├── glob/{tool.toml,main.py}
│   └── edit_file/{tool.toml,main.py}
├── skills/                    # SKILL.md（框架渐进披露）
│   ├── refactor/SKILL.md
│   ├── debug/SKILL.md
│   └── architecture/SKILL.md
├── mcp/
│   ├── fs.toml               # namespace=fs, type=local, root_dir=./tools
│   ├── code.toml             # namespace=code, type=remote (mock)
│   ├── git.toml              # namespace=git, type=local
│   └── web.toml              # namespace=web, type=remote (mock)
├── sessions.db               # SQLite 运行时生成
└── traces/                   # JSONL 运行时生成

tests/e2e/
└── test_codecompass_fs.py    # 全部 MVP 能力 E2E
```

### 2.4 CLI 交互流程

```bash
$ cd examples/python/codecompass_fs && python cli.py

[sessions.db 列出现有 session]
┌──────────────────────────────────────────────────┐
│ # │ session_id           │ title            │ rounds │
├───┼──────────────────────┼──────────────────┼────────┤
│ 1 │ s-aa01..             │ 重构 cache       │   3/8  │ ← 未完成
│ 2 │ s-bb02..             │ 调试性能         │  12/12 │ ← 已完成
│ 3 │ + 新建                                       │        │
└──────────────────────────────────────────────────┘
> 1                                              # 选择 session 1

[Engine] 检测到 session 1 未完成 round 3，正在恢复...
[Engine] replay last turn: model_call → tool_exec (read_file) ✓
[Engine] round 3 恢复，继续上次任务
当前 round: 3/8 — "重构 cache 模块中的 LRU 淘汰策略"
> 继续

[Engine] turn 1/8: model_call → thinking → response
[Tool] fs.read_file src/cache.py ✓ (0.3s)
[Engine] turn 2/8: model_call → tool_calls [grep, glob]
[Tool] fs.grep "lru" --path src/ ✓ (0.1s) ┐ 并发
[Tool] fs.glob "**/*cache*" ✓ (0.05s)       ┘
[Engine] turn 3/8: model_call → response "建议替换为..."

> ^C                                            # Ctrl-C
[Engine] checkpoint snapshot 已写入 sessions.db (round 3 turn 4)
[Engine] bye

$ python cli.py
> 1                                             # 再次启动
[Engine] session 1 恢复 → round 3 turn 4 ...
```

---

## 3. 框架最小补丁（F1-F7）

### F1. 5 个新 ActionMessage
**位置**: `crates/arf-core/src/message.rs`（追加）

| ActionMessage | msg_type | response | intent | 字段 |
|---|---|---|---|---|
| `SubagentDelegate` | `subagent_delegate` | `subagent_result` | Query | `correlation_id, parent_session_id, subagent_spec (ResourceSpec-like), task, context` |
| `SubagentResult` | `subagent_result` | — | (response) | `correlation_id, status, output, trajectory` |
| `PeerMessage` | `peer_message` | `peer_reply` | Query | `correlation_id, from_session, to_session, content` |
| `PeerReply` | `peer_reply` | — | (response) | `correlation_id, status, content` |
| `MemoryOp` | `memory_op` | `memory_op_result` | Query | `correlation_id, op (read/write/delete), key, value` |
| `HumanHandoff` | `human_handoff` | `human_handoff_reply` | Query | `correlation_id, question, context` |
| `ModelResponseChunk` | `model_response_chunk` | — | (stream) | `correlation_id, content_delta, reasoning_delta, finish_reason` |

**测试**：每类 4 个测试（构造 / serde / intent / response_msg_type_for 映射）

### F2. Engine dispatcher 化
**位置**: `crates/arf-engine/src/engine.rs` + `crates/arf-engine/src/dispatcher.rs`（新文件）

**当前问题**: `engine.rs:run()` 是 monolithic if/else 写死 ModelCall/ToolExec。

**目标形态**:
```rust
pub struct Engine {
    config: AgentConfig,
    handle: NodeHandle,
    registry: ResourceRegistry,
    discovery_cache: Arc<DiscoveryCache>,
    handlers: HandlerRegistry,  // 新增
}

trait MessageHandler: Send + Sync {
    fn msg_type(&self) -> &'static str;
    fn handle(&self, engine: &mut Engine, msg: &Message) -> Result<HandlerOutcome, RunError>;
}

pub struct HandlerRegistry {
    handlers: HashMap<String, Arc<dyn MessageHandler>>,
}
```

**5 个内置 handler**:
1. `ModelCallHandler` — 内置（保持现有 model_call → model_response 流程）
2. `ToolExecHandler` — 内置
3. `SubagentHandler` — F1 注册
4. `PeerMessageHandler` — F1 注册
5. `MemoryOpHandler` — F1 注册
6. `HumanHandoffHandler` — F1 注册（reply 占位 "pending UI"）
7. `ModelChunkHandler` — F1 注册（聚合到 State.model_buffer）

**收益**: 新增消息类型只加 handler，不动主循环

**测试**: 8 个（registry 注册 / dispatch 路由 / handler 错误传递 / 多 handler 共存）

### F3. 并发 tool_exec（同 round 并行）
**位置**: `crates/arf-engine/src/engine.rs` `do_tool_turns()` 函数

**当前** (`engine.rs:192` 注释 "sequential；6.6 加并发"):
```rust
for tc in tool_calls {
    self.do_tool_turn(state, tc, cancel.clone()).await?;
}
```

**目标**:
```rust
// 同 model_response 返回的多个 tool_call 无相互依赖，并发执行
let futs = tool_calls.iter().map(|tc| self.do_tool_turn(state, tc.clone(), cancel.clone()));
let results = join_all(futs).await;
```

**范围澄清（避免与 DAG 混淆）**:
- **本任务做**：同一 model_response 返回的 N 个独立 tool_call 并发执行（如"同时读 3 个文件"）
- **不做**：DAG 拓扑并发。Engine 当前只发 `tool_exec`（单 tool），**不**发 `tool_call_set`。`tool_call_set` 是 MCP 内部 ActionMessage，由 MCP 自身处理 DAG，Engine 不参与
- 真正的 DAG 并发是 MCP 端已实现的能力（`crates/arf-mcp/src/executor.rs`），Engine 通过 tool_exec 触发的是"无依赖并发"层

**测试**: 4 个（独立 tool 并发 / 含 cancel 的并发 / 并发错误聚合 / max_concurrent 限制）

### F4. SessionStore trait + SQLite impl
**位置**: `crates/arf-session/`（新 crate）

```rust
// crates/arf-session/src/lib.rs
#[async_trait]
pub trait SessionStore: Send + Sync {
    async fn list(&self) -> Result<Vec<SessionMeta>>;
    async fn load(&self, session_id: &str) -> Result<Option<SessionData>>;
    async fn save(&self, data: &SessionData) -> Result<()>;
    async fn delete(&self, session_id: &str) -> Result<()>;
    async fn snapshot(&self, session_id: &str, state: &State, checkpoint: CheckpointSnapshot) -> Result<()>;
}

pub struct SqliteSessionStore { db: Arc<Mutex<Connection>>, path: PathBuf }

pub struct SessionMeta {
    pub session_id: String,
    pub title: String,
    pub created_at: i64,
    pub updated_at: i64,
    pub round_count: usize,
    pub status: SessionStatus,  // Active / Completed / Interrupted
}

pub struct SessionData {
    pub meta: SessionMeta,
    pub state: State,
    pub last_checkpoint: Option<CheckpointSnapshot>,
    pub config_snapshot: serde_json::Value,
}

pub struct CheckpointSnapshot {
    pub checkpoint: Checkpoint,        // which of 5 positions
    pub turn_index: usize,
    pub pending_messages: Vec<ModelMessage>,  // not yet committed
    pub wait_events: Vec<WaitEvent>,
    pub captured_at: i64,
}
```

**PyO3 绑定**: `py-arf/src/session.rs` 暴露 `SqliteSessionStore`

**测试**: 12 个（构造 / save+load roundtrip / list 排序 / delete / snapshot integrity / 并发写互斥）

### F5. Engine 接入 SessionStore
**位置**: `crates/arf-engine/src/builder.rs` + `engine.rs`

**注入方式（避免污染 AgentConfig）**: SessionStore 通过 Builder 注入而非 AgentConfig 字段——保持 AgentConfig 纯声明式、session_store 是运行期关注点。

```rust
impl EngineBuilder {
    pub fn with_session_store(mut self, store: Arc<dyn SessionStore>) -> Self { ... }
}

// engine.rs:run() 5 个 checkpoint 处调用：
//   if let Some(store) = &self.session_store {
//       store.snapshot(&self.session_id, state, snapshot)?;
//   }

// 新增 public API
impl Engine {
    pub async fn restore_or_create(
        buses: Vec<Arc<Bus>>,
        config: AgentConfig,
        registry: ResourceRegistry,
        session_store: Option<Arc<dyn SessionStore>>,
        session_id: Option<String>,
    ) -> Result<(Self, State, String), BuildError>;
}
```

**恢复策略**: `restore_or_create(session_id)` 加载 → 若 `last_checkpoint.pending_messages` 非空 → `do_replay()`（重放未完成的 turn）→ 继续 `run()`

**测试**: 6 个（builder.with_session_store / restore_or_create / replay 已发出 model_call / replay 已发出 tool_exec / 无 store 时不 snapshot / snapshot 异常不中断主循环）

### F6. 内置 Compactor Node
**位置**: `crates/arf-compactor/`（新 crate）

```rust
pub struct CompactorNode {
    model_target: NodeId,  // 调 model_call 用的目标 NodeId
    threshold: f64,         // 0.7 — 触发 compact 的 context_utilization
}

impl CompactorNode {
    pub async fn compact(&self, state: &mut State, bus: &Bus) -> Result<CompactResult, CompactError> {
        // 1. 取 state.messages 全部
        // 2. 构造 model_call: "请总结以下对话，保留任务上下文..."
        // 3. 等 model_response（target = self.model_target）
        // 4. 把 summary 作为 system message 插入 state.messages[0]
        // 5. 删掉除 summary 外的旧 messages
        // 6. 返回 summary + before/after token 数
    }
}

// 同时提供 CheckpointRule 默认实现
pub fn when_context_over(threshold: f64) -> CheckpointRule {
    CheckpointRule::new(Checkpoint::BeforeModelCall)
        .when(move |state| state.over_view.context_utilization() > threshold)
        .build_message(|state| CompactRequest::new(...))
}
```

**收益**: App 只需 `engine.add_checkpoint_rule(when_context_over(0.7))` 一行

**测试**: 8 个（compact 后 messages 减少 / context_tokens 更新 / summary 注入 system 位置 / threshold 边界 / 无 model_target 报错 / compact 失败回滚）

### F7. subagent_launcher helper (Python 侧)
**位置**: `examples/python/codecompass_fs/subagent_launcher.py`

```python
class SubagentLauncher:
    def __init__(self, bus: arf.Bus, parent_engine: arf.Engine):
        self.bus = bus
        self.parent = parent_engine

    def delegate(self, spec_name: str, task: str, context: dict) -> str:
        """发 subagent_delegate，等 subagent_result，返回 output 文本"""
        sub_node_id = self._resolve_spec(spec_name)  # 从 parent config 的 subagents 字段
        delegate = arf.SubagentDelegate(
            parent_session_id=self.parent.session_id,
            subagent_node_id=sub_node_id,
            task=task,
            context=context,
        )
        result = self.bus.publish_and_query(delegate, target=sub_node_id)
        return result.output

    def _spawn_subagent_engine(self, spec: arf.ResourceSpec) -> arf.Engine:
        """首次遇到 spec 时 spawn 一个新的 Engine 实例作为子节点"""
        # 用同一 Bus，独立 AgentConfig + State
        sub_config = arf.AgentConfig(...).with_resources_from_spec(spec)
        sub_engine = arf.EngineBuilder(...).with_session(...).build(self.bus)
        return sub_engine
```

**测试**: 6 个（delegate 成功 / 委派后子 engine 在 bus 上可见 / 子 engine 失败父收到 status=Failed / 嵌套 subagent 限制）

---

## 4. 端到端能力映射

每项 MVP 能力都有可观察的产物（trace / 副作用 / 测试断言）：

| 能力 | 实现 | 验证 |
|---|---|---|
| **多会话存档切换** | `SessionStore.list()/load()` + CLI 启动列表 | `sessions.db` 含 ≥2 行；CLI 输入 session id 切换；切换后 messages 不混 |
| **多轮对话** | 现有 Engine ReAct，每 `chat()` 一 round | CLI 连续输入 5 轮，`state.messages.len()` 单调增；每 round `round_count += 1` |
| **中断恢复** | Checkpoint 后 snapshot；restart 时 `restore_or_create` + `do_replay()` | Ctrl-C → 重启 → trace 含 `RECOVERY` 标记；未完成 round 续跑 |
| **多 MCP 节点** | 4 namespace × Local/Remote 注册为独立 Node | trace 含 4 个 `node_online{mcp}`；tool name 前缀含 namespace |
| **DAG tool** | 现有 `tool_call_set` + MCP 拓扑排序；F3 加同 round 并发 | trace 时间戳重叠；多个 tool_result 同帧 |
| **subagents** | 主 Engine 发 `subagent_delegate` → 子 Engine 上 Bus → 完成后 `subagent_result` | trace 含父子 engine node；子 engine 独立 session_id |
| **peer agents** | 2 Engine 互发 `peer_message` | trace 含 `peer_message` 双向；每 peer 有独立 session_id |
| **compact** | `when_context_over(0.7)` CheckpointRule → Compactor.compact() | 构造大 messages → 触发 compact → trace 含 `compact_done`；context_utilization 下降 |

---

## 5. 文档分层

| 文件 | 用途 |
|---|---|
| `docs/dev/phase8/example-codecompass-fs-design.md` | 本 spec（设计依据） |
| `docs/dev/phase8/task-F1-action-message-protocol.md` | F1 任务文档（含 Rust 代码逐行解释 + 测试） |
| `docs/dev/phase8/task-F2-engine-dispatcher.md` | F2 |
| `docs/dev/phase8/task-F3-concurrent-tool-exec.md` | F3 |
| `docs/dev/phase8/task-F4-session-store.md` | F4 |
| `docs/dev/phase8/task-F5-engine-session-integration.md` | F5 |
| `docs/dev/phase8/task-F6-compactor-node.md` | F6 |
| `docs/dev/phase8/task-F7-subagent-launcher.md` | F7（Python） |
| `docs/dev/phase8/task-E1-codecompass-app.md` | 示例 app.py |
| `docs/dev/phase8/task-E2-codecompass-cli.md` | 示例 cli.py |
| `docs/dev/phase8/task-E3-codecompass-tools-skills.md` | tool/skill 资源 |
| `docs/dev/phase8/task-E4-codecompass-peers.md` | peer 协同 |
| `docs/dev/phase8/task-E5-codecompass-e2e-test.md` | tests/e2e/test_codecompass_fs.py |
| `docs/dev/phase8/task-E6-codecompass-user-doc.md` | docs/api/codecompass_fs.md 用户文档 |
| `docs/api/session_store.md` | SessionStore API 参考 |
| `docs/api/codecompass_fs.md` | 示例运行指南 |

---

## 6. 测试策略

### 6.1 单元测试（按 framework task）
- F1: 5 × 4 = 20 tests（5 ActionMessage 各 4 个）
- F2: 8 tests
- F3: 4 tests
- F4: 12 tests
- F5: 6 tests
- F6: 8 tests
- F7: 6 tests（Python helper，与 Rust 测试分开计数）
- **Rust 框架补丁合计**: ~58 tests
- **Python helper 合计**: ~6 tests

### 6.2 示例 E2E 测试
- `tests/e2e/test_codecompass_fs.py` — pytest，~15 tests：
  1. test_session_list_empty
  2. test_session_create_and_load
  3. test_multi_round_chat
  4. test_interrupt_and_recover (mock Ctrl-C)
  5. test_mcp_multi_node_online (4 node_online)
  6. test_dag_tool_concurrent_execution
  7. test_subagent_delegate (含 mock model)
  8. test_subagent_failure_propagation
  9. test_peer_message_roundtrip
  10. test_compact_triggered_at_threshold
  11. test_compact_reduces_context_utilization
  12. test_session_switch_isolates_state
  13. test_trace_contains_all_8_msg_types
  14. test_app_full_flow_smoke (所有能力顺序跑一遍)
  15. test_app_shutdown_clean

### 6.3 验收命令
```bash
make test                    # 全部 Rust + Python
make test-rust               # 58 + 既有
make test-py                 # 15 + 既有
make lint                    # 无 warning
```

---

## 7. 风险与权衡

| 风险 | 缓解 |
|---|---|
| 7 个补丁互相依赖（F4 → F5 → 示例） | 按 F1→F2→F3→F4→F5→F6→F7 顺序实施；每步独立 commit+test |
| Engine dispatcher 化破坏现有 ReAct | 保持 ModelCall/ToolExec handler 行为完全不变；F2 只重构调度，新增 handler 不影响 |
| SQLite snapshot 性能 | checkpoint 增量写（仅写变更字段）；E2E 测试覆盖 100 round 性能 |
| Compactor 触发时机 | 仅 BeforeModelCall 触发，避免 tool 中途打断 |
| 远程 MCP 无真实 server | 起本地 mock HTTP server（`tests/mocks/mcp_http.py`）实现 JSON-RPC `tools/list` + `tools/call`；参考既有 `crates/arf-mcp/tests/fixtures/` 模式 |
| Subagent 嵌套深度失控 | `EngineConfig.max_subagent_depth=2`；超限拒绝 spawn |
| Peer 启动顺序依赖 | peer_coordinator 先启动 peer engine（bus.connect），再启动主 engine |

---

## 8. 验收标准（完成定义）

- [ ] 7 个框架补丁全部完成，对应 task 文档完成
- [ ] 7 个示例组件全部完成
- [ ] `cargo test --workspace` 全过（既有 + 新增 ~58）
- [ ] `pytest tests/e2e/test_codecompass_fs.py` 15 个全过
- [ ] `make lint` 无 warning
- [ ] 13 个 task 文档 + 1 个本 spec 全部 commit + push 到 Gitee
- [ ] `docs/api/codecompass_fs.md` 用户文档可用（照跑一次 codecompass-fs）
- [ ] trace JSONL 中可 grep 到 8 类 ActionMessage

---

## 9. 不在本 spec 范围（Phase 8 后续）

- skill 完整渐进披露（L1→L2→L3 加载机制）
- memory 完整语义（long-term retrieval / embedding）
- human_handoff 实际 UI 集成
- permission gating
- streaming UI
- 多 Bus 重复 NodeId 语义
- backpressure 约定

这些保留为后续 phase8 task docs，按需独立 spec。