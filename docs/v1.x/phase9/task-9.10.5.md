# 任务 9.10.5：自定义 SessionStore impl trait

> Phase 9 — 9.10 H 持久化大类 · 第 5 task（依赖 9.10.1）
> 父文档：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.10.1（EngineBuilder + SqliteSessionStore 端到端）
> 输出物：`docs/v1.x/phase9/audit-probe-9.10.5.md`
> 探查结论：**不预设**——本 doc 不写任何预期结果

---

## 设计思路

9.10.1-9.10.4 都用 `SqliteSessionStore`（framework 默认实现）。**本 task (9.10.5) 探查 trait 扩展性——**
app 自己 `impl SessionStore for CustomStore`（in-memory HashMap / 内存 fake / 文件 / Redis stub / ...）
能否被 `EngineBuilder::with_session_store(Arc<dyn SessionStore>)` 接受？

**Framework 现状**（待探查确认）：
- `SessionStore` trait（session/lib.rs:154-177）：5 异步方法（list / load / save / delete / snapshot）
- `EngineBuilder::with_session_store(Arc<dyn SessionStore>)`（builder.rs:33-36）：只接收 trait object
- `Engine::install_session_store(Arc<dyn SessionStore>, String)`（engine.rs:155-162）：同上

**关键探查问题**（不预设答案）：
1. 自定义 `impl SessionStore for InMemoryStore`（用 `Mutex<HashMap>`）端到端 work？5 方法都能实现？
2. Engine + 自定义 store + 1 round → 5 位置 snapshot 全部调用自定义 snapshot()？
3. trait 5 方法签名是否清晰，app 实现无歧义？
4. 自定义 snapshot() 实现可选择性忽略某些 state？验证 trait 是 minimal commitment（只承诺 5 方法签名，不约束内部行为）？

按父 spec §3 探查 4 步流程 + §4 find signals 跑。

---

## 与现有测试的边界

- `crates/arf-session/src/lib.rs` 单测已测 `SessionStore` trait 5 方法的 `SqliteSessionStore` 实现
- **本 task 不重复**：现有 SQLite 实现的行为
- **本 task 聚焦**：trait 扩展性——验证 E (Extensible) 等级，让 app 能用 trait 写自己的 store

---

## 探查步骤（按父 spec §3.1）

### Step 1 — 写最小代码路径（app 层 ≤ 80 行）

`session_custom_store.rs`，3 test cases：

| # | test | 探查 |
|---|---|---|
| 1 | `in_memory_store_impl_trait` | 自定义 InMemoryStore（`Mutex<HashMap<String, SessionData>>` + `Mutex<Vec<CheckpointSnapshot>>`）impl SessionStore 5 方法 → 5 方法独立工作 |
| 2 | `custom_store_with_engine_round_trip` | Engine + InMemoryStore + 1 round → load 完整 state；snapshot 5 位置都调用自定义 snapshot() |
| 3 | `custom_store_snapshot_drops_pending_messages` | 自定义 RecordingStore 记录 snapshot 调用次数 + state 字段；验证 snapshot 被调 3 次（no_tool） |

### Step 2 — framework 接触点 file:line

```bash
grep -n "pub trait SessionStore\|pub async fn.*self.*\&" crates/arf-session/src/lib.rs
grep -n "with_session_store" crates/arf-engine/src/builder.rs
```

逐行解释：
- trait 5 async 方法签名（list / load / save / delete / snapshot）
- builder 接收 `Arc<dyn SessionStore>` trait object
- engine `install_session_store` 同上

### Step 3 — framework 真实行为

```bash
cd /home/wangxie/open_deepseek_arf/.worktrees/group5-persist
cargo test -p arf-e2e --test session_custom_store -- --nocapture --test-threads=1 2>&1 | tee /tmp/session_custom_store_run.log
```

逐行解释：
- 3 test 应全过（mock + 自定义 store impl + Engine 端到端）
- 任何 F-lesion 在 audit-probe §D 记录

**Read `/tmp/session_custom_store_run.log` 后填 Step 4 `framework 行为`**。

### Step 4 — 判定 + 记录

按 §3.3 输出 schema 填 3 个单元的判定。

按 §4 跑 signals：
- A1：trait 5 方法是否 atomic 化（每个方法单一职责）？
- A2：自定义 impl 与 SqliteSessionStore impl 是否正交（互不耦合）？
- A3：trait 5 方法签名无字段重复声明？
- A4：trait 5 方法实现分布在自定义 store 一处？

**C. 输出**：`audit-probe-9.10.5.md`。
