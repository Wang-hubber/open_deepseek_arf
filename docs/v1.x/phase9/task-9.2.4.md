# 任务 9.2.4：Engine + CancellationToken interrupt 协同

> Phase 9 — 9.2 B 单 agent 骨架 · 第 4 task（依赖 9.2.1, 9.2.2, 9.2.3）
> 父文档：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`（commit `8ad5247`）
> 前置：9.2.1（Engine + 单 ModelAdapter mock chat）/ 9.2.2（真实 DashScope qwen ReAct loop）/ 9.2.3（5 Checkpoint + 自定义 Rule）
> 输出物：`docs/v1.x/phase9/audit-probe-9.2.4.md`
> 探查结论：**不预设**——本 doc 不写任何预期结果

---

## 设计思路

9.2.1-9.2.3 探查了 Engine 的 chat / ReAct / Checkpoint 主路径；本 task (9.2.4) 探查 Engine 的 **interrupt / cancel 协同** + **replay from session**：

- **7 个 cancel 检查点**（engine.rs:235/259/288/325/372/522/613）：loop 顶 / model turn 后 / tool batch 前 / wait_for 内 / do_tool_turn 内 / wait_for_strategy 内
- **`wait_for_strategy` 与 cancel 的 `tokio::select!` 集成**（engine.rs:668-672）—— cancel.cancelled() 与 handle.recv() 竞速，cancel 立即触发 `Err(RunError::Stopped)`
- **Checkpoint 触发的 snapshot**（engine.rs:191 `snapshot_if_configured`）—— 每个 Checkpoint 位置自动写 SessionStore（best-effort，错误不传播）
- **replay 路径**：`SessionStore::load` 恢复 `SessionData` + `CheckpointSnapshot` + state + config → 新 Engine + load 后的 state → 续 run

按父 spec §3 探查 4 步流程 + §4 find signals 跑。

---

## 与现有测试的边界

- `react_loop.rs::react_cancel_yields_stopped`（test 5）：cancel **before** run → Stopped（已覆盖，但本 task 重新验证 baseline）
- `recovery.rs::cancellation_during_run_yields_stopped`：cancel **during checkpoint** → Stopped
- **本 task 不重复** baseline 场景；聚焦：
  - **cancel during long model response wait**（核心：cancel 在 framework 真正阻塞等待处生效）
  - **cancel during tool exec wait**（次要：tool 路径 cancel）
  - **cancel mid-multi-round**（多 round 中途 cancel，state 部分保留）
  - **replay from session store**（核心：checkpoint snapshot 持久化 + load + resume）
  - **multi-round cancel**（不同 cancel 时机的 state 一致性）

---

## 探查步骤（按父 spec §3.1）

### Step 1 — 写最小代码路径（app 层 ≤ 90 行）

`interrupt.rs`，6 test cases，覆盖 cancel + replay：

```rust
// ── Slow mock provider（cancel 期间返回）────────────────────────
struct SlowMockProvider {
    cancel: CancellationToken,
    response: ModelResponsePayload,
    delay: Duration,
}

#[async_trait]
impl Provider for SlowMockProvider {
    async fn chat(&self, _model: &str, _msgs: Vec<ModelMessage>,
                  _tools: Vec<ToolDef>, _params: ModelParams)
        -> Result<ModelResponsePayload, ProviderError>
    {
        tokio::select! {
            biased;
            _ = self.cancel.cancelled() => {
                // cancel 触发：返回 transport error（engine 不会重启，
                // 但 wait_for 也会 cancel，二者竞速）
                Err(ProviderError::Transport("cancelled".into()))
            }
            _ = tokio::time::sleep(self.delay) => Ok(self.response.clone()),
        }
    }
    // name / supported_models 同 scripted
}
```

6 test cases：

| # | test | 探查 |
|---|---|---|
| 1 | `cancel_before_run_yields_stopped` | baseline 重验证：cancel 在 run_react 之前 fired → Stopped（与 react_loop.rs:5 一致） |
| 2 | `cancel_during_model_response_wait` | Slow provider delay=500ms，run_react 启动 100ms 后 cancel → 期望 engine 内部 wait_for 触发 Stopped（不依赖 provider 返回） |
| 3 | `cancel_during_tool_exec_wait` | McpNode 阻塞 tool_exec，run_react 启动 100ms 后 cancel → 期望 Stopped |
| 4 | `cancel_mid_multi_round` | scripted provider 持续调 tool 3 次，run_react 启动后 200ms cancel → 期望 Stopped 且 state.messages 部分保留（< expected N tool messages） |
| 5 | `replay_from_session_store` | 装 `SqliteSessionStore::in_memory()`；round 1 partial run，cancel，load 状态，新 Engine + state → 续 run 成功（验证 snapshot+load+resume 闭环） |
| 6 | `multi_round_state_consistency` | 两次 cancel 触发点不同时：cancel 在 round 1 中途 vs round 2 中途 → state.over_view.round_count 反映已 inc_round 次数（一致） |

**关键探查价值**：
- cancel during model wait = §3.3 capability 单元 1（interrupt in-flight 能力）
- cancel during tool wait = 单元 2
- multi-round cancel + state consistency = 单元 3（partial state 保留）
- replay from session = 单元 4（checkpoint snapshot 持久化能力）
- L6 interrupt capability = D（trait SessionStore 已声明，SqliteSessionStore 已实现，framework 端到端供）

### Step 2 — framework 接触点 file:line

```bash
grep -n 'cancel.is_cancelled\|cancel\.clone\|CancellationToken' crates/arf-engine/src/engine.rs | grep -v test | head -20
grep -n 'snapshot_if_configured\|session_store' crates/arf-engine/src/engine.rs | grep -v test
grep -n 'trait SessionStore\|pub fn snapshot\|pub fn load\|pub fn save' crates/arf-session/src/lib.rs
sed -n '650,700p' crates/arf-engine/src/engine.rs   # wait_for_strategy + cancel select
```

逐行解释：
- engine.rs:668-672 `wait_for_strategy` 用 `tokio::select!` 竞速 cancel.cancelled() 与 handle.recv()——**cancel 立即返回，不依赖 provider 反应**
- engine.rs:191-209 `snapshot_if_configured` 在 5 Checkpoint 位置 fire（每个位置单独调用 snapshot）
- arf-session/src/lib.rs:154 `trait SessionStore` 声明 list/load/save/delete/snapshot 5 个方法
- arf-session/src/lib.rs:188 `SqliteSessionStore::in_memory()` 给 test 用

### Step 3 — framework 真实行为

```bash
cd /home/wangxie/open_deepseek_arf
cargo test -p arf-e2e --test interrupt -- --nocapture --test-threads=1 2>&1 | tee /tmp/interrupt_run.log
```

逐行解释：
- 6 test cases 各跑独立 Engine + State
- 关键观察：`run_react` 触发 cancel 后，engine 内 wait_for 立即返 Stopped（< 100ms）
- replay test：snapshot 在每个 Checkpoint 触发，load 还原完整 state

**Read `/tmp/interrupt_run.log` 后填 Step 4 `framework 行为`**（真实行为，非 mock 假设）。

### Step 4 — 判定 + 记录（按父 spec §3.3 输出 schema）

| 单元 | 等级 | 判分依据（含 file:line） |
|---|---|---|
| `interrupt_in_flight × §2.10` | 待探查 | `wait_for_strategy` select 竞速（engine.rs:668-672）+ 6 cancel 检查点（:235/259/288/325/372/522） |
| `replay_from_session × §2.10` | 待探查 | `snapshot_if_configured` 5 位置 fire（:191）+ `SessionStore::load` trait（arf-session:154） |
| `partial_state_preservation × §2.10` | 待探查 | engine.run 中途返回 Stopped 时 state 是否保持（state 序列化是 phase 6 的事） |
| `multi_round_cancel_consistency × §2.10` | 待探查 | 不同 cancel 位置 round_count / turn_count 反映一致 |

按 §4 跑 signals（**重点：cancel / session 路径是否引入新病灶**，A4-001 / A3-001 在 cancel 路径是否加剧）：

```bash
# A3-001 在 cancel / session 路径：检查新 msg_type 字面量
grep -rn '"session\|"snapshot\|"interrupt\|"cancel' crates/arf-engine/src/ crates/arf-session/src/ | grep -v test
# A4-001 在 cancel / session 路径：检查 correlation_id 是否在 snapshot 中
grep -n 'correlation_id' crates/arf-session/src/lib.rs | head -5
# SessionData 序列化路径（cancel 后 load 是否完整）
grep -n 'serialize\|deserialize' crates/arf-session/src/lib.rs | head -10
```

**C. 输出**：`audit-probe-9.2.4.md`。cancel / session 路径在 framework 不同模块（engine + session），若引入新病灶应在新位置（engine run 路径 + session 模块）。

---

## 关键设计决策

- **SlowMockProvider 用 `tokio::select!` 竞速 cancel**：让 provider 自身也能立即响应 cancel，与 engine 内部 wait_for 的 cancel 集成**双层防护**——即使 provider 不响应，engine wait_for 也会 cancel。验证 framework 的 cancel 集成**不依赖**第三方响应。
- **replay test 用 `SqliteSessionStore::in_memory()`**：避免落盘副作用，in-memory 隔离。
- **不预设 6 cancel 检查点都生效**：framework 注释（engine.rs:212-227）列出终止条件，但实际每个 check point 是否触达需实证。
- **probe 不重写 react_loop.rs 的 cancel test**：本 task 是它的扩展而非替代。
- **不测 long-lived session 恢复**：9.10+ task 会覆盖长会话持久化（情景 §2.8）。本 task 聚焦单 session 内 cancel + replay。

---

## 验证命令（self-review）

```bash
# 跑通
cargo test -p arf-e2e --test interrupt -- --nocapture --test-threads=1

# 6 cancel 检查点 cross-check
grep -n 'cancel.is_cancelled' crates/arf-engine/src/engine.rs | grep -v test

# wait_for 与 cancel select 集成
sed -n '665,675p' crates/arf-engine/src/engine.rs

# snapshot 5 位置 fire
grep -n 'snapshot_if_configured' crates/arf-engine/src/engine.rs

# SessionStore trait
grep -n 'trait SessionStore\|pub.*fn.*snapshot\|pub.*fn.*load' crates/arf-session/src/lib.rs

# 凭据安全自查（本 task 无 LLM 调用，但仍必跑）
git grep -n 'sk-' -- crates/ docs/
```

---

## 与前序 task 的衔接

- 9.2.1 mock chat 骨架 + A4-001/A3-001 Engine 蔓延
- 9.2.2 真实 LLM 端到端 chat + 工具 loop + 真实 payload 复测病灶
- 9.2.3 Engine Checkpoint 5 位置 + 自定义 Rule（无新病灶）
- **9.2.4** Engine cancel / interrupt + replay from session
- 后续 9.2.5（多 model）在此之上

---

## 下一步

1. 用户审 task 9.2.4 doc（Gitee 精校）
2. 用户批 → 跑 Step 1-4 探查（mock + in-memory SqliteSessionStore）
3. 整理 `audit-probe-9.2.4.md`
4. self-review（凭据 / 一致性 / scope）
5. commit `interrupt.rs` + commit `audit-probe-9.2.4.md`（granular）
6. 进 9.2.5（多 model）
