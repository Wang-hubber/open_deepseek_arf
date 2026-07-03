# 任务 9.10.1：EngineBuilder + SqliteSessionStore 端到端

> Phase 9 — 9.10 H 持久化大类 · 第 1 task（依赖 9.2.x）
> 父文档：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.2.x（Engine + ModelAdapter + ReAct 主循环 + Checkpoint）
> 输出物：`docs/v1.x/phase9/audit-probe-9.10.1.md`
> 探查结论：**不预设**——本 doc 不写任何预期结果

---

## 设计思路

9.2.x 探查了 Engine 的 ReAct 主循环。本 task (9.10.1) 探查 **session_persist**——framework 是否能让 app 通过 `EngineBuilder::with_session_store` 安装 `SqliteSessionStore`，让 Engine 在 5 Checkpoint 位置自动写 snapshot？

**Framework 现状**（待探查确认）：
- `arf_session::SqliteSessionStore::in_memory()` —— 内存版
- `arf_session::SqliteSessionStore::new(path)` —— 文件版
- `EngineBuilder::with_session_store(Arc<dyn SessionStore>)` —— 安装 store
- `EngineBuilder::with_session_id(String)` —— 自定义 session_id（默认 = engine.agent_id）
- `Engine::snapshot_if_configured()` —— 在 5 Checkpoint 位置自动 spawn 异步写

**关键探查问题**（不预设答案）：
1. `EngineBuilder::with_session_store(Arc<SqliteSessionStore>)` 端到端 work？需不需要 `Arc<dyn SessionStore>` 包装？
2. 1 round 跑完后 store 中是否能 load 出来？state.messages / meta / last_checkpoint 是否完整？
3. session_id 默认是 `engine.agent_id`？可自定义？
4. snapshot 是 best-effort 异步 spawn，**不**保证 run 返前回写完——store.list() / load() 时机如何确认？

按父 spec §3 探查 4 步流程 + §4 find signals 跑。

---

## 与现有测试的边界

- `crates/arf-session/src/lib.rs` 单测（17 tests）—— 已覆盖 SessionStore trait 单元
- `crates/arf-e2e/tests/interrupt.rs` 已用 `with_session_store` —— 但仅做 interrupt 场景
- **本 task 不重复**：单元 trait 测试
- **本 task 聚焦**：端到端 probe——`EngineBuilder::with_session_store` + run_react + load round-trip

---

## 探查步骤（按父 spec §3.1）

### Step 1 — 写最小代码路径（app 层 ≤ 80 行）

`session_persist.rs`，3 test cases：

| # | test | 探查 |
|---|---|---|
| 1 | `engine_builder_installs_session_store_and_saves` | EngineBuilder + SqliteSessionStore(in_memory) + 1 round + load → meta + state + checkpoint 完整 |
| 2 | `session_id_defaults_to_agent_id` | 不设 with_session_id → load(session_id) 仍能找到（session_id == engine.agent_id）|
| 3 | `session_id_override_via_builder` | with_session_id("my-custom-id") → load("my-custom-id") 找到；load(default) 找不到 |

### Step 2 — framework 接触点 file:line

```bash
grep -n "pub fn with_session_store\|pub fn with_session_id" crates/arf-engine/src/builder.rs
grep -n "pub async fn snapshot\|pub fn install_session_store" crates/arf-engine/src/engine.rs
grep -n "pub async fn save\|pub async fn load\|pub async fn snapshot" crates/arf-session/src/lib.rs
```

逐行解释：
- `EngineBuilder::with_session_store` 存 `Arc<dyn SessionStore>`
- `Engine::install_session_store` 私有 — 只 builder 可调
- `snapshot_if_configured` 在 `evaluate_and_dispatch` 顶部调用，`tokio::spawn` 异步写 store

### Step 3 — framework 真实行为

```bash
cd /home/wangxie/open_deepseek_arf/.worktrees/group5-persist
cargo test -p arf-e2e --test session_persist -- --nocapture --test-threads=1 2>&1 | tee /tmp/session_persist_run.log
```

逐行解释：
- 3 test 应全过（mock + in-memory sqlite + run_react + load round-trip）
- snapshot 是 `tokio::spawn` 异步 —— `load()` 前需 `tokio::time::sleep` 或 store.list 等待
- 任何 F-lesion 在 audit-probe §D 记录

**Read `/tmp/session_persist_run.log` 后填 Step 4 `framework 行为`**。

### Step 4 — 判定 + 记录

按 §3.3 输出 schema 填 3 个单元的判定。

按 §4 跑 signals：
- A1：snapshot 是 best-effort spawn，semantics 是否清晰？
- A2：EngineBuilder 与 Engine 在 session_store 装/卸 上是 single-source？
- A3：session_id 默认值（agent_id）与 override（with_session_id）单点声明？
- A4：snapshot write 集中？（同一接缝 spawn）

**C. 输出**：`audit-probe-9.10.1.md`。
