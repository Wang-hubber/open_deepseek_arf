# audit-probe-9.10.1：EngineBuilder + SqliteSessionStore 端到端探查

> Task 9.10.1 探查产出 — **Framework 是否让 app 通过 EngineBuilder.with_session_store + SqliteSessionStore 端到端持久化 session？**
> 父 task doc：`docs/v1.x/phase9/task-9.10.1.md`
> 父 spec：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.2.x（Engine + ModelAdapter + ReAct 主循环 + Checkpoint）
> **本 task 探查：EngineBuilder.with_session_store + Engine.run + SqliteSessionStore.save/load/snapshot 端到端**

---

## §A 探查环境

- working tree：HEAD `107c56b`（group5-persist base）+ uncommitted `crates/arf-e2e/tests/session_persist.rs`
- 测试文件：`crates/arf-e2e/tests/session_persist.rs`（4 test cases）
- 驱动：mock provider + `SqliteSessionStore::in_memory()`（test 1/2/4 用 E2EHarness + harness.with_session_store；test 3 直接 EngineBuilder 以验证 with_session_id）
- 测试命令：
  ```bash
  cargo test -p arf-e2e --test session_persist -- --nocapture --test-threads=1
  ```
- 结果：**`4 passed; 0 failed; 1.22s`**
- 关键运行输出：
  ```
  test engine_builder_installs_session_store_and_saves ...
  [persist] session_id=engine/scripted title=session_persist test status=Interrupted round_count=0 turn_count=0
  [persist] last_checkpoint = RoundEnd turn_index=1 captured_at=2026-07-03 08:08:35.554719330 UTC
  ok
  test session_data_load_returns_all_4_fields ...
  [persist] fields: meta=engine/scripted state.messages=2 last_checkpoint=true config_snapshot={"k":"v","model":"test-model"}
  ok
  test session_id_defaults_to_agent_id ...
  [persist] default session_id = engine/scripted
  ok
  test session_id_override_via_builder ...
  [persist] custom session_id=my-custom-session-001 agent_id=engine/scripted
  ok
  ```

### 凭据安全（self-check 已通过）

```bash
$ git grep -n 'sk-' -- crates/arf-e2e/tests/session_persist.rs   # 无输出
```

---

## §B (capability, 情景) 单元判定

### 单元 1：`EngineBuilder::with_session_store` + run + load round-trip

```
单元              : session_persist × §2.8
能力等级           : D（PASS）
判定依据          : E2EHarness + Arc<dyn SessionStore> = SqliteSessionStore(in_memory)
                   + 预 save 初始 session（因为 snapshot 假设 session 存在——见 §D F-010）
                   + run_react 1 round
                   → store.list() 1 session ✓
                   → store.load() 完整 state（user + assistant 2 messages）✓
                   → last_checkpoint = RoundEnd（engine 在 RoundEnd 触发 snapshot_if_configured）✓
                   → meta.status = Interrupted（snapshot() 路径 session/lib.rs:412 强制 interrupted）✓
file:line         : crates/arf-engine/src/builder.rs:33-36  with_session_store
                   crates/arf-engine/src/engine.rs:190-206  snapshot_if_configured
                   crates/arf-session/src/lib.rs:382-416    snapshot impl
                   crates/arf-session/src/lib.rs:283-335    load impl（读 state_json + last checkpoint）
```

### 单元 2：session_id 默认 = engine.agent_id

```
单元              : session_persist × §2.8（默认 session_id 行为）
能力等级           : D（PASS）
判定依据          : 不调 with_session_id → Engine::new（engine.rs:99）设
                   session_id = info.node_id = `engine/{provider}` = `engine/scripted`
                   → load(`engine/scripted`) Some ✓
file:line         : crates/arf-engine/src/engine.rs:99    session_id = info.node_id
                   crates/arf-engine/src/builder.rs:97-101 with_session_store fallback
```

### 单元 3：with_session_id("custom") 覆盖

```
单元              : session_persist × §2.8（custom session_id 行为）
能力等级           : D（PASS）
判定依据          : EngineBuilder::with_session_id("my-custom-session-001")
                   → install_session_store(store, "my-custom-session-001")（engine.rs:155-162）
                   → engine.session_id() == "my-custom-session-001" ✓
                   → load("my-custom-session-001") Some ✓
                   → load(agent_id = "engine/scripted") None ✓
file:line         : crates/arf-engine/src/builder.rs:40-43  with_session_id
                   crates/arf-engine/src/engine.rs:155-162    install_session_store
```

### 单元 4：SessionData 4 字段全持久化

```
单元              : session_persist × §2.8（SessionData 完整序列化）
能力等级           : D（PASS）
判定依据          : SessionData { meta, state, last_checkpoint, config_snapshot }
                   预 save 时 config_snapshot = {"model":"test-model","k":"v"}
                   → load 后 config_snapshot 仍为该值（snapshot() 路径 session/lib.rs:409
                   只重写 state_json，不动 config_json）✓
file:line         : crates/arf-session/src/lib.rs:337-370   save 写 4 字段
                   crates/arf-session/src/lib.rs:283-335    load 读 4 字段
```

---

## §C 探查产出汇总

| 单元 | 等级 | 判定依据 |
|---|---|---|
| `session_persist × §2.8`（with_session_store 端到端） | **D** | Engine + SqliteSessionStore + 1 round + load 完整 round-trip OK |
| `session_persist × §2.8`（默认 session_id） | **D** | agent_id 派生 session_id，load 找得到 |
| `session_persist × §2.8`（custom session_id） | **D** | with_session_id 覆盖，load 找得到 custom / 找不到 default |
| `session_persist × §2.8`（SessionData 4 字段） | **D** | meta/state/last_checkpoint/config_snapshot 全持久化 |

---

## §D 病灶登记

### **F-010** — Engine 不自动 save 初始 session，app 必须预 save

```
病灶 ID       : F-010
信条           : (F-category) 设计 quirk
Signal         : 缺 primitive
触发情景       : §2.8（长会话持久化）
file:line      : crates/arf-engine/src/engine.rs:190-206   snapshot_if_configured
                 crates/arf-session/src/lib.rs:382-416     snapshot impl：先 query existence，
                                                            NotFound 直接 return Err
首次登记       : audit-probe-9.10.1.md §D（本 task）
状态           : OPEN
命中形态       : snapshot_if_configured 在 evaluate_and_dispatch 顶部调用（engine.rs:330），
                若 session 还没在 store 里（即 app 还未 save ），snapshot() 内部
                query existence 返回 false → return Err(NotFound) →
                eprintln!("[arf-engine] snapshot error: session not found: {sid}")
                → snapshot 静默失败（engine 不 abort run，但 state 永远不会被持久化）。
                app 必须先手 save 一份 SessionData（interrupt.rs:255-270 pattern），
                engine 才会成功写 snapshot。
                后果：app 调 `EngineBuilder::with_session_store(store).build()` 后
                `engine.run()` —— 期望"开启持久化"，实际"静默丢弃所有 snapshot"。
                修复方向：EngineBuilder 在 build 阶段（或 Engine::new 阶段）若装了
                session_store 且 store 中无 session，自动 save 初始 SessionData
                （meta.session_id = engine.agent_id or custom；title = "..."；state = State::new()）。
影响面         : 任何想用 session_persist 的 app 都得知道这个 quirk，否则 session 永远写不进去。
                实证：本 task 第一个 test 版本未预 save → 4/4 全 fail + console 报
                "snapshot error: session not found: engine/scripted" 3 次（1 round 3 Checkpoint）。
                修复后预 save 4/4 pass。interrupt.rs:225-228 已有相同注释警示。
复现命令       : 见 session_persist.rs test 1 注释 + interrupt.rs:252 "预 save session"
```

### 注意事项（潜在 issue，非 lesion）

1. **`Engine::install_session_store` 私有**（engine.rs:155）—— 只能 builder 调，app 无法动态装/卸。**合理**（build-time fail-fast），无 lesion。
2. **`snapshot()` 静默吞错**（engine.rs:202 `eprintln!`）—— app 端无法察觉 store 写失败。**建议**：暴露 `Engine::snapshot_errors()` 收集最近 N 次错误，app 可诊断。
3. **snapshot 是 `tokio::spawn` 异步**（engine.rs:200）—— run() 返回时 snapshot 不一定写完。`load()` 前需 `tokio::time::sleep`（test 用了 200~300ms）。**建议**：EngineBuilder 加 `await_initial_snapshot: bool` 选项，或 Engine.run() 返 `Result<RunOutput, RunError>` 包含 "all snapshots persisted" 标志。
4. **Harness 缺 `with_session_id` 方法**（harness.rs builder）—— 测 custom session_id 须直接 EngineBuilder（test 3 模式）。**建议**：harness builder 加 `.with_session_id(String)`。

---

## §E 探查回归

- 9.2.x 既有 test pass（Engine + ModelAdapter + ReAct 主循环 + Checkpoint）
- 9.10.1 新增 4 test pass
- 与既有 9.4.1-9.4.3 pool 病灶**无关**——本 task 探查 session persistence
- 与既有 9.3.x streaming/thinking 病灶**无关**——本 task 不触达 model_response_chunk
- **F-010 是新发现**：framework session_persist API 有 "app 必须预 save" 的隐藏契约，doc 没说

---

## §F 与父 task / spec 的关系

| 父 task 期望 | 实证结果 |
|---|---|
| EngineBuilder + SqliteSessionStore 端到端 | ✓ test 1 pass（list/load 完整） |
| session_id 默认 = engine.agent_id | ✓ test 2 pass |
| session_id override via builder | ✓ test 3 pass（with_session_id 覆盖） |
| 4 test cases pass | ✓ 4/4 pass（多 1 bonus test 4：4 字段全可见） |
| 预期 0 新 F-lesion | ✗ **1 新 F-lesion F-010**（app 必须预 save）—— 病灶影响所有 session_persist 用户 |

> 结论：9.10.1 探查显示 framework **EngineBuilder + SqliteSessionStore** 端到端 work（4/4 pass），
> 但暴露 **F-010** 隐藏契约（app 必须预 save initial session）—— 这是 phase 9 持久化类别
> 首次探查，新增 1 个 F-lesion。

---

## §G 提交状态

- 工作目录：`crates/arf-e2e/tests/session_persist.rs`（~230 行，4 test cases）
- task doc：`docs/v1.x/phase9/task-9.10.1.md`（新增）
- audit probe：本 doc
- lesion-registry：**未变**（按 task spec 不修改，本 task 不要求；F-010 登记在 §D）
- 待 commit
