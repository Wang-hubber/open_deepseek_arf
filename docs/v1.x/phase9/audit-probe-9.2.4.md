# audit-probe-9.2.4：Engine + CancellationToken interrupt 协同探查

> Task 9.2.4 探查产出 — **Engine cancel / interrupt 集成 + replay from session**
> （9.2 B 单 agent 骨架第 4 步）
> 父 task doc：`docs/v1.x/phase9/task-9.2.4.md`（commit `9bfe5e9`）
> 父 spec：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.2.1（Engine + 单 ModelAdapter mock chat）/ 9.2.2（真实 DashScope qwen ReAct loop）/ 9.2.3（5 Checkpoint + 自定义 Rule）
> **本 task 探查 Engine 的 7 cancel 检查点 + cancel 不依赖 provider 响应 + replay 闭环**

---

## §A 探查环境

- working tree：HEAD `9bfe5e9`
- 测试文件：`crates/arf-e2e/tests/interrupt.rs`（6 test cases）
- 探查基础设施：harness 加 `with_session_store(Arc<dyn SessionStore>)` 方法（per commit
  `9bfe5e9`+1，harness 扩展，**不**是 framework 改动）+ Cargo.toml 加 `arf-session` + `chrono` dev-deps
- 驱动：scripted mock provider + `SlowMockProvider`（tokio::select 竞速 cancel vs sleep）
  + `SqliteSessionStore::in_memory()`（隔离）—— **不**依赖任何 LLM
- 测试命令：
  ```bash
  cargo test -p arf-e2e --test interrupt -- --nocapture --test-threads=1
  ```
- 结果：`6 passed; 0 failed; 0.70s`
- 关键真实运行输出：
  ```
  [interrupt] model_wait:  result=Err(Stopped),  elapsed=101.6ms  (cancel 100ms vs provider 500ms)
  [interrupt] tool_wait:   result=Some(Stopped), elapsed=31.7ms,   messages=4
  [interrupt] multi_round: result=Some(Stopped), elapsed=51.8ms,   messages=6
  [interrupt] state consistency: cancel-before-run  → rc=1 tc=0
                              cancel-during-run   → rc=1 tc=1
  [interrupt] replay: loaded session meta.title=replay-test, state.messages=1, status=Interrupted
  ```

### 探查执行中遇到的 1 个 framework 设计点（记入 §E 观察）

**`SessionStore::snapshot` 要求 session 已 `save()`**：engine 在每个 Checkpoint 位置调
`store.snapshot(session_id, state, snap)`（engine.rs:191），但 `SqliteSessionStore::snapshot`
要求 sessions 表中已有该 session_id（lib.rs:382-391 `if !exists: NotFound`）。**Engine
不会自动 save**——app 必须先 `save(&SessionData{...})` 注册 session。这是合理的关注点
分离（engine 不知道完整 SessionData / meta.title / created_at），但**未文档化在
`snapshot_if_configured` 注释**。初版测试触发 `[arf-engine] snapshot error: session
not found: engine/slow`，修法：test 预 `save` 一个 minimal SessionData。

---

## §B (capability, 情景) 单元判定

### 单元 1：cancel in-flight × §2.10

```
单元              : interrupt_in_flight × §2.10
能力等级           : D
判分依据           : engine 7 个 cancel 检查点（engine.rs:235/259/288/325/372/522/613）
                    + `wait_for_strategy` 的 `tokio::select!` 竞速 cancel.cancelled()
                    与 handle.recv()（engine.rs:665-672）。
                    真实断言（interrupt.rs:127-165）：
                    SlowMockProvider delay=500ms；外部 task 100ms 后 cancel；
                    engine 在 101.6ms 返回 Stopped（远小于 500ms provider delay）——
                    证明 cancel 集成**不依赖** provider 响应。
framework 行为   : cancel 触发 → wait_for 立即返 Err(Stopped)；
                    engine.run 终止；state 保留 inc_round 状态；
                    framework 端到端供 in-flight interrupt 能力。
信号命中         : 无新病灶
```

### 单元 2：cancel during tool exec × §2.10

```
单元              : interrupt_tool_exec × §2.10
能力等级           : D
判分依据           : McpNode + harness tool_exec_responder；外部 cancel 30ms。
                    真实断言（interrupt.rs:289-330）：cancel 31.7ms 后返 Stopped，
                    state.messages=4（user + assistant(t1) + tool(t1) + assistant(t2)）。
                    关键：cancel 在 tool batch 中途触发，state 部分保留（partial progress）。
framework 行为   : 框架在 tool batch 末尾（AfterToolExec 后）也检查 cancel（engine.rs:296
                    后调 do_tool_turns_concurrent，:303 后判 cancel），cancel 传播到
                    engine 内部即可生效；tool_exec_responder 本身**不**响应 cancel，
                    但 engine 取消优先。
信号命中         : 无新病灶
```

### 单元 3：cancel mid multi-round × §2.10

```
单元              : interrupt_multi_round × §2.10
能力等级           : D
判分依据           : scripted 5 tool_calls + 1 text；外部 cancel 50ms。
                    真实断言（interrupt.rs:171-219）：51.8ms 返 Stopped，
                    state.messages=6（user + 3 assistant + 2 tool 完整 + 1 partial）
                    round_count=1（prepare_round inc）。
framework 行为   : cancel 在多 round 中间某 turn 触发；engine 立即终止；
                    state 部分保留反映已 inc 次数；multi-round 中途 cancel 不 hang。
信号命中         : 无新病灶
```

### 单元 4：state consistency × §2.10

```
单元              : state_consistency × §2.10
能力等级           : D
判分依据           : 两次 cancel 触发点对比：
                    (a) cancel-before-run：rc=1, tc=0（prepare_round inc 了 round，turn 未发生）
                    (b) cancel-during-run：rc=1, tc=1（round inc，1 个 model_call turn 完成）
                    真实断言（interrupt.rs:335-378）：两次 inc_* 调用一致反映在 state.over_view。
framework 行为   : prepare_round 的 inc_round 在 cancel 之前调用（engine.rs:232 + 428），
                    不论 cancel 多快，round_count 必为 1。turn_count 取决于 cancel
                    时机——此观察验证 state 内部一致性。
信号命中         : 无新病灶
```

### 单元 5：replay from session store × §2.10（核心）

```
单元              : replay_from_session × §2.10
能力等级           : D
判分依据           : `SqliteSessionStore::in_memory()` + 预 `save` SessionData +
                    engine 5 Checkpoint 位置 snapshot（engine.rs:191）+
                    `load(session_id)` 还原 state。
                    真实断言（interrupt.rs:225-303）：
                    预 save → engine run partial → cancel → load 还原 → 
                    state.messages=1（user message 已持久化），
                    status=SessionStatus::Interrupted（snapshot impl:393 显式设）。
framework 行为   : snapshot 是 best-effort tokio::spawn（engine.rs:201-206），
                    错误仅 eprintln 不传播；status 自动从 Active 变 Interrupted；
                    load 还原完整 state + last_checkpoint + config_snapshot。
                    端到端供 replay 能力，app 自行负责 save 初始 session + 处理 load 后 resume。
信号命中         : 无新病灶
```

### 单元 6：cancel before run × §2.10（baseline）

```
单元              : interrupt_before_run × §2.10
能力等级           : D
判分依据           : cancel 在 run 之前 fired → Stopped。
                    真实断言（interrupt.rs:104-122）：与 react_loop.rs:5 baseline 一致。
framework 行为   : run 入口检查 cancel.is_cancelled()（engine.rs:235）→ 立即返 Stopped。
信号命中         : 无新病灶
```

---

## §C §4 find signals 探查

### A3 数据唯一 — cancel / session 路径是否引入新散落

**结论：未引入新 msg_type 字面量散落**。

| 检查项 | 结果 |
|---|---|
| cancel 路径 msg_type 字面量 | **0 处**（cancel 不发消息，靠 cancellation token 状态传播） |
| session 路径 msg_type 字面量 | **0 处**（session 是状态持久化层，不走 bus 协议） |
| session 路径新增字符串字面量 | 1 处 `"interrupted"`（SessionStatus enum discriminant，lib.rs:41/49）—— **非 msg_type**，是 state 状态字段，不构成 A3 病灶 |
| correlation_id 散落 | **0 处**（session 不涉及 correlation_id） |

**Checkpoint 路径**（9.2.3）+ **cancel / session 路径**（9.2.4）**共同实证**：framework 的 5 Checkpoint 位置 + cancel 集成 + session 持久化路径**完全不引入新 msg_type 字面量**。这与 Engine 内部协议（model_call / model_response / tool_exec / tool_result 散落 12+ 处）形成对比——framework 越靠近"新机制"边界（Checkpoint / cancel / session）越**洁净**，越靠近"既有核心协议"越**散落**。这是 framework 演化的健康信号（新抽象比老抽象更严格）。

### A4 处理集中 — correlation_id 在 cancel / session 路径

**结论：不涉及**。

cancel 路径不发消息，无 correlation_id。
session 路径不与 bus 协议交叉，state 序列化走 serde，**无 correlation_id 手动序列化**（`grep correlation_id crates/arf-session/src/lib.rs` = 0 命中）。

**Checkpoint 路径** + **cancel / session 路径**均**不加剧** A4-001。A4-001 局限于 engine.rs:375/460/559 typed + engine.rs:689 手挖 + connection.rs:105/330 / lib.rs:303 塞入侧手挖——**核心请求-响应协议层**问题，不在 Checkpoint / cancel / session 边界扩散。

### A1 / A2 — cancel / session 抽象

- **A1 原子化**：`CancellationToken` 来自 tokio_util，专用于取消传播，职责单一。`SessionStore` trait 5 个方法各司其职（list / load / save / delete / snapshot），无 `and / or` 多职责模式。`EngineBuilder::with_session_store` 1 个参数 1 个职责。
- **A2 正交性**：`SessionStore` 不依赖具体 transport（trait 抽象），`SqliteSessionStore` 是单一实现。cancel 与 checkpoint / state / bus 完全正交（cancel 是 token 状态，不与 message protocol 耦合）。

**§4 新病灶：0**。**已登记病灶 cancel / session 路径实证：0 加剧**（A3-001 缓解 / A4-001 不涉及）。

---

## §D lesion-registry 更新（无需新增，仅追加观察）

本 task 不新登记。A3-001 / A4-001 在 cancel / session 路径均**未加剧**。

§1 总表无需追加新行；§2 详情无需修改。

---

## §E 观察记录（非病灶）

### 观察 I1 — `SessionStore::snapshot` 要求 session 已 `save()`

**触发位置**：`arf-session/src/lib.rs:382-391`（snapshot 内部 `if !exists: NotFound`）+ `engine.rs:191-209`（snapshot_if_configured 调 snapshot）
**观察现象**：`SqliteSessionStore::snapshot(session_id, ...)` 在 sessions 表中找不到 session_id 时直接返 `SessionError::NotFound`（lib.rs:393），engine 把错误 eprintln 不传播（engine.rs:205）。**Engine 不会自动 save**——app 必须先 `save(&SessionData{...})` 注册 session。
**判断**：**不构成病灶**——engine 不应替 app 决定 SessionData 完整内容（meta.title / created_at / config_snapshot）。关注点分离正确。但**应文档化**在 `snapshot_if_configured` 注释中（目前无）。
**修复方向**（供参考，留后续 fix phase）：
- 选项 A：在 `snapshot_if_configured` doc 注释补"require session already saved"
- 选项 B：`SqliteSessionStore::snapshot` 自动 `INSERT OR IGNORE` 空 session 后再追加 snapshot（更宽容，但弱化 save / snapshot 边界）
**影响面**：所有用 session store + snapshot 的 app 都需在 run 前先 save。

### 观察 I2 — `SessionStatus::Interrupted` 仅由 snapshot 路径设置

**触发位置**：`arf-session/src/lib.rs:393`（snapshot impl 显式 `status = 'interrupted'`）
**观察现象**：session 的 `status` 字段有 4 个 variant（Active / Interrupted / Completed / Failed），但实际**仅** snapshot 路径在 session 已被 cancel 时 set Interrupted。Active/Completed/Failed 状态由谁设置未明确（grep 整个 workspace 0 命中除 snapshot impl）。
**判断**：**不构成病灶**——当前只暴露 Interrupted 是因为 cancel + snapshot 是目前唯一的"非正常结束"路径。但 **status 字段的完整生命周期未文档化**。
**影响面**：app 依赖 status 字段判断 session 状态时，可能对 Active/Completed/Failed 语义有歧义。

### 观察 I3 — `snapshot_if_configured` 是 best-effort `tokio::spawn`（engine.rs:201-206）

**触发位置**：`engine.rs:201-206` `tokio::spawn(async move { store.snapshot(...).await })`
**观察现象**：snapshot 调用 spawn 到独立 task，**不等**其完成就返回 engine 主循环。test 4 必须 `tokio::time::sleep(300ms)` 等异步 snapshot 完成才能 load 看到数据。
**判断**：**设计正确**——snapshot 不能阻塞主循环（持久化是 I/O，5 Checkpoint 位置每 turn 都 fire），但**测试需考虑异步性**。app 需在 cancel 后 sleep 足够时间才能 load 完整快照。
**影响面**：app 持久化语义——cancel 后立即 load 可能看到旧 state；需 sleep 或 await 持久化完成信号。

---

## §F 综合判定

- **cancel in-flight**：D（7 cancel 检查点 + wait_for select 竞速，**不**依赖 provider 响应，101.6ms 响应 100ms cancel）。
- **cancel during tool exec**：D（31.7ms 响应，state 部分保留）。
- **cancel mid multi-round**：D（51.8ms 响应，state 部分保留）。
- **state consistency**：D（cancel 前后 round_count / turn_count 反映 inc_* 一致）。
- **replay from session store**：D（snapshot 5 位置 fire + load 还原完整 state，status 自动 Interrupted）。
- **cancel before run**：D（baseline 一致）。
- **新病灶**：0。
- **已登记病灶 cancel / session 路径实证**：A3-001 **缓解**（cancel/session 路径**不引入**新 msg_type 字面量）/ A4-001 **不涉及**。
- **9.2.4 价值**：**首次实证 Engine cancel 集成在 framework 各阻塞点**（model wait / tool wait / multi-round loop / checkpoint dispatch）**都正确响应**——不依赖 provider 响应、不依赖 tool 响应；并验证 session snapshot 闭环（save + run + snapshot + load）。3 个新观察（I1/I2/I3）全部**非病灶**，仅文档化建议。
- **结论**：Engine cancel / interrupt + session 机制在 mock + in-memory store 端到端下功能达标（D × 6）；无新病灶。进 9.2.5（多 model）。

---

## §G 验证命令

```bash
# 跑通（mock 驱动，无 key 依赖）
cargo test -p arf-e2e --test interrupt -- --nocapture --test-threads=1

# 7 cancel 检查点 cross-check
grep -n 'cancel.is_cancelled' crates/arf-engine/src/engine.rs | grep -v test

# wait_for 与 cancel select 集成
sed -n '665,675p' crates/arf-engine/src/engine.rs

# snapshot 5 位置 fire
grep -n 'snapshot_if_configured' crates/arf-engine/src/engine.rs

# SessionStore trait
grep -n 'trait SessionStore\|pub.*fn.*snapshot\|pub.*fn.*load' crates/arf-session/src/lib.rs

# §4 信号 cross-check（cancel/session 路径零新散落）
grep -rn '"session\|"snapshot\|"interrupt\|"cancel' crates/arf-engine/src/ crates/arf-session/src/ | grep -v test
grep -n 'correlation_id' crates/arf-session/src/lib.rs

# 凭据安全自查
git grep -n 'sk-' -- crates/ docs/
```

---

## §H 下一步

1. self-review（凭据 / 一致性 / scope / granular）— commit 前必跑
2. **granular commit**（per CLAUDE.md workflow）：
   - commit Cargo.toml（加 arf-session + chrono dev-deps）
   - commit `harness.rs` 改动（with_session_store builder 方法）
   - commit `interrupt.rs`（probe）
   - commit `audit-probe-9.2.4.md`（结果）
3. push 双 remote（github + gitee）
4. 进 9.2.5（多 model）
