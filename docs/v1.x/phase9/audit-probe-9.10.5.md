# audit-probe-9.10.5：自定义 SessionStore impl trait 端到端探查

> Task 9.10.5 探查产出 — **app 自定义 `impl SessionStore for CustomStore` 能否被 EngineBuilder 接受 + 端到端 work？**
> 父 task doc：`docs/v1.x/phase9/task-9.10.5.md`
> 父 spec：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.10.1（EngineBuilder + SqliteSessionStore 端到端）
> **本 task 探查：trait 5 方法扩展性 + Engine 集成 + snapshot 调用次数**

---

## §A 探查环境

- working tree：HEAD `abd8287`（task 9.10.4）+ uncommitted `crates/arf-e2e/tests/session_custom_store.rs`
- 测试文件：`crates/arf-e2e/tests/session_custom_store.rs`（3 test cases）
- 驱动：自定义 InMemoryStore（`Mutex<HashMap>`）+ 自定义 RecordingStore（计数器）+ Engine E2EHarness
- 测试命令：
  ```bash
  cargo test -p arf-e2e --test session_custom_store -- --nocapture --test-threads=1
  ```
- 结果：**`3 passed; 0 failed; 0.61s`**
- 关键运行输出：
  ```
  test in_memory_store_impl_trait ...
  [custom] list 3 sessions ✓
  [custom] load each sid ✓
  [custom] snapshot 累计 + load 返回 latest checkpoint ✓
  [custom] delete 单 session + 其他不受影响 ✓
  [custom] delete nonexistent → NotFound ✓
  ok
  test custom_store_with_engine_round_trip ...
  [custom/engine] load OK: messages=2, last_checkpoint=RoundEnd
  [custom/engine] list 1 session with sid engine/scripted ✓
  ok
  test custom_recording_store_counts_snapshot_calls ...
  [custom/rec] snapshot calls = 3, kinds = [BeforeModelCall, AfterModelCall, RoundEnd]
  [custom/rec] 3 fires in order BMC → AMC → RE ✓
  ok
  ```

### 凭据安全（self-check 已通过）

```bash
$ git grep -n 'sk-' -- crates/arf-e2e/tests/session_custom_store.rs   # 无输出
```

---

## §B (capability, 情景) 单元判定

### 单元 1：InMemoryStore impl trait 5 方法端到端

```
单元              : custom_session_store × §2.8（trait 扩展性）
能力等级           : E (Extensible，PASS)
判定依据          : 自定义 InMemoryStore (Mutex<HashMap<String, SessionData>> +
                   Mutex<Vec<(String, CheckpointSnapshot)>>) impl SessionStore 5 方法
                   (list/load/save/delete/snapshot) — 端到端 OK
                   - save 3 session → list 3 ✓
                   - load 各 sid + load nope → None ✓
                   - snapshot 累计 + load 返回 latest checkpoint（max captured_at）✓
                   - delete 单 session + 其他不受影响 ✓
                   - delete nonexistent → NotFound ✓
file:line         : crates/arf-session/src/lib.rs:154-177   SessionStore trait 5 方法
                   crates/arf-engine/src/builder.rs:33-36   接收 Arc<dyn SessionStore>
```

### 单元 2：Engine + InMemoryStore 端到端

```
单元              : custom_session_store × §2.8（Engine 集成）
能力等级           : E (Extensible，PASS)
判定依据          : EngineBuilder::with_session_store(Arc<InMemoryStore> as Arc<dyn SessionStore>)
                   + 1 round → InMemoryStore 的 snapshot() 被调多次
                   load() 返回 messages=2, last_checkpoint=RoundEnd ✓
                   list() 1 session with sid engine/scripted ✓
file:line         : crates/arf-engine/src/builder.rs:33-36   with_session_store
                   crates/arf-engine/src/engine.rs:155-162   install_session_store
                   crates/arf-engine/src/engine.rs:190-206   snapshot_if_configured
```

### 单元 3：RecordingStore 记录 snapshot 调次数与位置

```
单元              : custom_session_store × §2.8（trait 行为 contract）
能力等级           : E (Extensible，PASS)
判定依据          : 自定义 RecordingStore (atomic counter + Vec<Checkpoint>)
                   + Engine 1 round no tool → 期望 snapshot 调 3 次
                   实测: 3 calls, kinds = [BMC, AMC, RE] ✓
                   验证 trait contract：snapshot() 必被 engine 在 5 位置调用，
                   且 checkpoint 字段（snapshot.checkpoint）反映触发位置。
file:line         : crates/arf-engine/src/engine.rs:330    snapshot_if_configured
                   crates/arf-engine/src/engine.rs:194    snapshot.checkpoint = trigger
```

---

## §C 探查产出汇总

| 单元 | 等级 | 判定依据 |
|---|---|---|
| `custom_session_store × §2.8`（trait 5 方法） | **E** | 自定义 InMemoryStore 5 方法端到端 OK |
| `custom_session_store × §2.8`（Engine 集成） | **E** | Engine + 自定义 store 端到端 OK |
| `custom_session_store × §2.8`（trait contract） | **E** | snapshot 调用次数 + checkpoint 字段反映触发位置 |

> **能力等级总结**：`custom_session_store` = **E (Extensible)**——framework 已声明
> `SessionStore` trait（5 async 方法），app 自行 `impl` 即可接入，framework 无需新 primitive。
> 这与 §1.2 spec 表中 `custom_session_store` 对应 **E 等级** 期望一致。

---

## §D 病灶登记

### **F-012** — `SessionStore::snapshot()` trait 文档不暴露"同时更新 sessions.state_json"副作用

```
病灶 ID       : F-012
信条           : A3 数据唯一 / A1 原子化
Signal         : A1-S2 (doc comment 同时描述 ≥ 2 个不相关领域) / A3-S1 (行为契约散落)
触发情景       : §2.8（长会话持久化）
file:line      : crates/arf-session/src/lib.rs:171-177   trait snapshot doc:
                                                            "Append a checkpoint snapshot to a session's
                                                            most-recent checkpoint. (Engine calls this
                                                            at each of the 5 Checkpoint positions.)"
                                                            **未提及** state 同步更新。
                 crates/arf-session/src/lib.rs:388-416   SqliteSessionStore::snapshot impl:
                                                            INSERT checkpoints + UPDATE sessions.state_json
                                                            + UPDATE sessions.updated_at + status = 'interrupted'
                                                            (4 个副作用)
                 crates/arf-engine/src/engine.rs:190-206  Engine::snapshot_if_configured
                                                            调 store.snapshot(sid, &state_clone, &snap)
                                                            — 显式传 state 入参
首次登记       : audit-probe-9.10.5.md §D（本 task）
状态           : OPEN
命中形态       : SessionStore::snapshot(&self, session_id, state, snapshot) 的 trait
                doc 仅说 "Append a checkpoint snapshot"，**未说** "also update
                session's state_json + updated_at + status"。但 SqliteSessionStore
                impl 实际做了 4 件事（写 checkpoints / UPDATE state_json /
                UPDATE updated_at / 强制 status='interrupted'）。app 自定义
                impl 若不读 SqliteSessionStore source 不知道有 state 同步——
                本 task test 2 第一次跑：InMemoryStore impl 忽略 state 参数
                → load() 返回 state.messages.len() == 0（不是 2）→ fail。
                修复方向：trait doc 显式列 snapshot() 副作用清单：
                (a) 写 checkpoint record
                (b) 更新 session state 为传入的 state
                (c) 更新 session updated_at = now
                (d) status 默认改 'interrupted'（可由 flag 控制）
                或者拆为 2 方法：snapshot_checkpoint() + commit_state()。
影响面         : 任何自定义 impl 都得读 source 才能写对。本 task 实证：先写
                "snapshot 只写 checkpoints" → fail → 改 "也更新 state" → pass。
                production 风险：第三方作者写不完整的 impl → load 后 state 滞后
                真实 Engine 状态。
复现命令       : 见 session_custom_store.rs test 2 注释 + test 2 commit 前的 fail log
```

### 注意事项（潜在 issue，非 lesion）

1. **`SessionStore` trait 5 方法都是 `async`**（session/lib.rs:154-177）—— 同步 store impl 也得返回 `async`（用 `async_trait::async_trait`）。**合理**（统一接口），但**sync-only** 场景（如自写 in-memory）需要 `tokio::task::spawn_blocking` 包装。**建议**：trait 加 sync 方法 + 提供 wrapper，或 doc 提示。

2. **`list_checkpoints(session_id)` 仍缺失**（同 9.10.3 §D）—— trait 5 方法只覆盖 session 级 CRUD，**不**覆盖 checkpoint 级 query。app 想 audit "某 session 跑了 N 个 snapshot" 须自己 query。

3. **trait 5 方法无 version / migration 字段**——若 SessionData schema 升级（如 SessionMeta 加字段），旧 store 的 load 能否兼容？当前 SessionMeta 8 字段有 `serde` default，但 `State.over_view.runtime: Duration` 等嵌套类型须 serde compatible。**建议**：trait 加 `version: u32` 字段供 versioned save/load。

---

## §E 探查回归

- 9.10.1-9.10.4 既有 14 test pass
- 9.10.4 新增 3 test pass
- **F-012 是新发现**：trait doc 不暴露 snapshot() 副作用——所有自定义 impl 都得 source-dive
- 与 F-010 / F-011 病灶**有强相关**：
  - F-010（app 预 save）：本 task test 也预 save（InMemoryStore.snapshot() 要求 session 存在）
  - F-011（save 不持久化 last_checkpoint）：本 task InMemoryStore.save() 不写 last_checkpoint —— **与 F-011 一致**（trait contract 不要求 save 持久化 last_checkpoint）
- 与 9.4.x pool 病灶**无关**——本 task 探查 session 扩展性

---

## §F 与父 task / spec 的关系

| 父 task 期望 | 实证结果 |
|---|---|
| 自定义 impl SessionStore 端到端 work | ✓ test 1 pass（5 方法都 OK） |
| Engine + 自定义 store 端到端 | ✓ test 2 pass（load OK） |
| snapshot 调用次数记录 | ✓ test 3 pass（3 fires / BMC+AMC+RE） |
| 能力等级 E (Extensible) | ✓ E 端到端确认 |
| 预期 0 新 F-lesion | ✗ **1 新 F-lesion F-012**（trait doc 不暴露 snapshot 副作用） |

> 结论：9.10.5 探查显示 framework **SessionStore trait 扩展性**端到端 work（3/3 pass，E 等级）——
> app 可用 `impl SessionStore for CustomStore` 写自己的 store。但暴露 **F-012**——trait doc
> 未暴露 `snapshot()` 的 4 个副作用（写 checkpoint / 同步 state / 更新 updated_at / 强制
> status='interrupted'），所有自定义 impl 都得 source-dive。这是 phase 9 持久化类别
> **第 3 个 F-lesion**（F-010 / F-011 / F-012）。

---

## §G 提交状态

- 工作目录：`crates/arf-e2e/tests/session_custom_store.rs`（~260 行，3 test cases）
- task doc：`docs/v1.x/phase9/task-9.10.5.md`（新增）
- audit probe：本 doc
- lesion-registry：**未变**（按 task spec 不修改；F-012 登记在 §D）
- 待 commit
