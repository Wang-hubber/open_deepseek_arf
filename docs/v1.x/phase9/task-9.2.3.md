# 任务 9.2.3：Engine + 5 Checkpoint + 自定义 Rule

> Phase 9 — 9.2 B 单 agent 骨架 · 第 3 task（依赖 9.2.1, 9.2.2）
> 父文档：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`（commit `8ad5247`）
> 前置：9.2.1（Engine + 单 ModelAdapter，mock chat）/ 9.2.2（真实 DashScope qwen 端到端 chat + 工具 loop）
> 输出物：`docs/v1.x/phase9/audit-probe-9.2.3.md`
> 探查结论：**不预设**——本 doc 不写任何预期结果

---

## 设计思路

9.2.1 验证 Engine + ModelAdapter chat 骨架（D）+ 实证 A4-001/A3-001 蔓延；9.2.2 用真实 qwen 复测 ReAct 主循环；本 task (9.2.3) 探查 Engine 的 **Checkpoint 注入点机制**：

- **5 Checkpoint 位置**各调一次 `evaluate_and_dispatch`（父 spec §2.3 + §3.3）
- **2 个 built-in rule factory**：`CheckpointRule::every_n_rounds` / `CheckpointRule::when_context_over`（arf-core/src/checkpoint.rs:58/77）
- **app-level factory**：`arf_compactor::when_context_over`（crates/arf-compactor/src/lib.rs:157）
- **自定义 CheckpointRule**：`CheckpointRule::new(name, trigger, when, build)` + 自由定义 when / build 闭包
- **route → recipient 解析**（`engine.routes` + `Route::Strict` / `Route::Discovery`）
- **undeclared msg_type → RunError::UndeclaredMsgType**（error path，engine.rs:227 注释 "programming bug"）

按父 spec §3 探查 4 步流程 + §4 find signals 跑。

---

## 与现有测试的边界

- `react_loop.rs` 探查 ReAct 主循环 chat/tool 行为（mock 5 test）
- `react_live_qwen.rs` 9.2.2 探查真实 LLM chat + 工具 loop
- **本 task 不重复** ReAct 行为；聚焦 Checkpoint 注入点本身：
  - 5 个位置**都**能 fire rule
  - 2 个 built-in factory + 1 app-level factory + 1 自定义 Rule
  - routes 解析（Strict / Discovery）
  - 错误路径（undeclared msg_type）

---

## 探查步骤（按父 spec §3.1）

### Step 1 — 写最小代码路径（app 层 ≤ 90 行）

`checkpoint_rules.rs`，mock 驱动，6 test cases：

```rust
fn trace_rule(fires: Arc<Mutex<Vec<&'static str>>>, tag: &'static str, trigger: Checkpoint)
  -> CheckpointRule
{
    let f = fires.clone();
    CheckpointRule::new(
        format!("ckpt/{tag}"),
        trigger,
        move |_s| { f.lock().unwrap().push(tag); true },
        move |_s| Box::new(MarkerMsg::new("ckpt/audit")),  // 单一 msg_type，单一 route
    )
}

#[tokio::test]
async fn ckpt_all_positions_single_round_no_tool() {
    let fires = Arc::new(Mutex::new(Vec::<&'static str>::new()));
    // 注册 5 条 rule：每条 trigger 不同，build 都返回 "ckpt/audit" message
    let mut h = E2EHarness::builder(ProviderKind::Mock(simple_mock("ok")))
        .with_checkpoint_rules(vec![
            trace_rule(fires.clone(), "before_model_call", Checkpoint::BeforeModelCall),
            trace_rule(fires.clone(), "after_model_call",  Checkpoint::AfterModelCall),
            trace_rule(fires.clone(), "before_tool_exec",  Checkpoint::BeforeToolExec),
            trace_rule(fires.clone(), "after_tool_exec",   Checkpoint::AfterToolExec),
            trace_rule(fires.clone(), "round_end",         Checkpoint::RoundEnd),
        ])
        .route("ckpt/audit", Route::Strict(vec![NodeId::new("audit/sink")]))
        .build().await.unwrap();
    let out = h.run_react("hi").await.expect("run");
    let observed = fires.lock().unwrap().clone();
    // 单 round 无 tool：BeforeModelCall + AfterModelCall + RoundEnd 各 1 次
    // 另 2 个 tool_exec 位置不 fire（无 tool）
}
```

6 test cases 覆盖：

| # | test | 探查 |
|---|---|---|
| 1 | `ckpt_all_positions_single_round_no_tool` | 5 位置都注册 + 1 round 无 tool → 3 fires (Before/AfterModelCall, RoundEnd) |
| 2 | `ckpt_all_positions_single_round_one_tool` | 同上 + 1 tool call → 5 fires 全到位 |
| 3 | `ckpt_every_n_rounds_builtin` | built-in `every_n_rounds(2)` factory，3 round 后期望 fire 1 次（round 2） |
| 4 | `ckpt_when_context_over_builtin` | built-in `when_context_over(0.5)` factory，set state.context_utilization=0.6 期望 fire |
| 5 | `ckpt_custom_rule_via_new` | `CheckpointRule::new` with 自定义 when (round>0) + 自定义 build (return marker msg) |
| 6 | `ckpt_undeclared_msgtype_errors` | rule build 返回未注册 msg_type → `RunError::UndeclaredMsgType` |

**关键探查价值**：
- 5 位置都 fire = 父 spec §2.3 capability 矩阵单元 1
- 2 built-in factory = §2.3 capability 单元 2 + 3
- 自定义 rule = §2.3 capability 单元 4（E - Extensible，验证 trait 边界可用）
- route 解析（Strict） + error path = §2.3 capability 单元 5/6

### Step 2 — framework 接触点 file:line

```bash
grep -n 'pub enum Checkpoint\|pub struct CheckpointRule\|fn every_n_rounds\|fn when_context_over' \
  crates/arf-core/src/checkpoint.rs
grep -n 'evaluate_and_dispatch\|BeforeModelCall\|AfterModelCall\|BeforeToolExec\|AfterToolExec\|RoundEnd' \
  crates/arf-engine/src/engine.rs | grep -v test
grep -n 'pub.*checkpoint_rules\|with_checkpoint_rules' crates/arf-engine/src/builder.rs crates/arf-engine/src/config.rs
grep -n 'Route::Strict\|Route::Discovery\|resolve_route' crates/arf-core/src/route.rs crates/arf-engine/src/checkpoint.rs
```

逐行解释（按 file:line 锚定 framework 接触点）：
- `Checkpoint` 5 个 variant（arf-core/src/checkpoint.rs:9-20）
- `CheckpointRule` 4-tuple (name, trigger, when, build)（:31-38）+ `new`（:43）+ `every_n_rounds`（:58）+ `when_context_over`（:77）
- Engine 5 个 fire 点（engine.rs:247/264/277/291/296）
- Engine config `checkpoint_rules` 字段（config.rs:19）
- evaluate 纯函数（engine/checkpoint.rs:120-153）：rules 过滤 → fires 评估 → build 调 → route 解析

### Step 3 — framework 真实行为（mock 驱动）

```bash
cd /home/wangxie/open_deepseek_arf
cargo test -p arf-e2e --test checkpoint_rules -- --nocapture 2>&1 | tee /tmp/ckpt_rules_run.log
```

逐行解释：
- mock provider scripted，不依赖网络
- 6 test case 各跑独立 Engine + State 实例
- 观察 `fires` Vec 顺序 vs framework 注释（engine.rs:212-219 注释列出 5 步骤）

**Read `/tmp/ckpt_rules_run.log` 后填 Step 4 `framework 行为`**。

### Step 4 — 判定 + 记录（按父 spec §3.3 输出 schema）

| 单元 | 等级 | 判分依据（含 file:line） |
|---|---|---|
| `checkpoint_rules × §2.3` (5 位置全 fire) | 待探查 | 5 Checkpoint variant 各调 evaluate_and_dispatch（engine.rs:247/264/291/296/277） |
| `CheckpointRule::every_n_rounds × §2.3` (built-in) | 待探查 | 工厂返回 typed rule（checkpoint.rs:58-73） |
| `CheckpointRule::when_context_over × §2.3` (built-in) | 待探查 | 工厂返回 typed rule（checkpoint.rs:77-92） |
| `custom_checkpoint_rule × §2.3` (E - Extensible) | 待探查 | `CheckpointRule::new` HRTB closure（checkpoint.rs:43-54） |
| `route_resolution` | 待探查 | `Route::Strict` 直返 ids（engine/checkpoint.rs:93）/ `Route::Discovery` 走 `DiscoveryCache`（:94） |
| `undeclared_msgtype_error × §2.3` (error) | 待探查 | `RunError::UndeclaredMsgType` 抛出（engine/checkpoint.rs:140-144） |

按 §4 跑 signals（**重点：5 Checkpoint 路径是否引入新病灶**，A4-001/A3-001 是否在 Checkpoint 路径加剧）：

```bash
# A3-001 在 Checkpoint 路径：检查 msg_type 字面量是否新引入散落
grep -rn '"ckpt\|"checkpoint\|"audit"' crates/arf-engine/src/ crates/arf-core/src/ | grep -v test
# A4-001 在 Checkpoint 路径：检查 correlation_id 是否被 Checkpoint msg 用上
grep -n 'correlation_id' crates/arf-engine/src/checkpoint.rs crates/arf-core/src/checkpoint.rs
# Checkpoint rule 4-tuple closure 一致性
grep -n 'Box<dyn' crates/arf-core/src/checkpoint.rs
```

**C. 输出**：`audit-probe-9.2.3.md`。Checkpoint 是 Engine 内置协议，若引入新病灶应在新位置（engine/checkpoint.rs 或 arf-core/checkpoint.rs）。

---

## 关键设计决策

- **5 位置 fire 验证用 side-effect Vec**：build closure 同步 side-effect to `Arc<Mutex<Vec<&'static str>>>`，run_react 完成后 inspect。比订阅节点更轻（避免 5 个 Node 样板）。
- **共用 msg_type + 单一 route**：所有 rule build 返回 `ckpt/audit` message，单一 Route::Strict 解析到 dummy node，简化测试基础设施。
- **不预设 5 位置 fire 顺序**：依赖 framework 注释（engine.rs:212-219）做预期，但不写死；framework 实际行为若与注释不符，可能暴露 framework 文档漂移。
- **app-level CheckpointRule factory（`CheckpointRule::new`）= E 级**（Extensible）：验证 trait 边界可由 app 实现，不需 framework 改造。
- **error path 必须测**：undeclared msg_type 是 programming bug 守门员（engine.rs:225 注释），验 framework 不静默失效。

---

## 验证命令（self-review）

```bash
# 跑通
cargo test -p arf-e2e --test checkpoint_rules -- --nocapture

# 5 位置 fire 顺序 cross-check（应与 framework 注释 engine.rs:212-219 一致）
grep -A 8 'pub async fn run' crates/arf-engine/src/engine.rs | head -10

# 凭据安全自查（本 task 无 LLM 调用，但 commit 仍必跑）
git grep -n 'sk-' -- crates/ docs/
```

---

## 与前序 task 的衔接

- 9.2.1 mock chat 骨架（D）+ 实证 A4-001/A3-001 蔓延至 Engine
- 9.2.2 真实 LLM（qwen）端到端 chat + 工具 loop + 真实 payload 复测 A4-001/A3-001
- **9.2.3** Engine Checkpoint 5 位置注入点（不依赖 LLM，mock 驱动）
- 后续 9.2.4（interrupt）/ 9.2.5（多 model）在此之上

---

## 下一步

1. 用户审 task 9.2.3 doc（Gitee 精校）
2. 用户批 → 跑 Step 1-4 探查（mock 驱动）
3. 整理 `audit-probe-9.2.3.md`
4. self-review（凭据 / 一致性 / scope）
5. commit `checkpoint_rules.rs` + commit `audit-probe-9.2.3.md`（granular）
6. 进 9.2.4（Engine + interrupt / cancel）
