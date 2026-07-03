# audit-probe-9.12.4：自定义 CheckpointRule（every_n_rounds / when_context_over）端到端探查

> Task 9.12.4 探查产出 — **Framework CheckpointRule factory 端到端 work？**
> 父 task doc：`docs/v1.x/phase9/task-9.12.4.md`
> 父 spec：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.2.3（Engine + 5 Checkpoint + 自定义 Rule）
> **本 task 探查：app 用 `every_n_rounds` / `when_context_over` factory + build 闭包端到端**

---

## §A 探查环境

- working tree：HEAD `16e9288`（task 9.12.3）+ uncommitted `crates/arf-e2e/tests/custom_checkpoint_factory.rs`
- 测试文件：`crates/arf-e2e/tests/custom_checkpoint_factory.rs`（4 test cases）
- 驱动：framework-supplied `CheckpointRule::every_n_rounds` / `::when_context_over` + app-defined `CustomFactoryMsg` (ActionMessage)
- 测试命令：
  ```bash
  cargo test -p arf-e2e --test custom_checkpoint_factory -- --nocapture --test-threads=1
  ```
- 结果：**`4 passed; 0 failed; 0.16s`**
- 关键运行输出：
  ```
  test every_n_rounds_boundary_1_2_3 ... [test1] every_n=1 fires: 3 (3 round 全 fire)
  [test1] every_n=2 fires: 1 (round 2 fire)
  [test1] every_n=3 fires: 1 (round 3 fire)
  test when_context_over_boundary_ratio_0_1 ... [test2] ratio=0.0 util=0.0 fire
  [test2] ratio=0.5 util=0.6 fire, util=0.4 skip
  [test2] ratio=1.0 util=1.0 fire, util=0.6 skip
  test factory_x_5_checkpoint_matrix ... [test3] 5 trigger (BeforeModelCall/AfterModelCall/BeforeToolExec/AfterToolExec/RoundEnd) 全 fire
  test factory_build_returns_custom_message ... [test4] CustomFactoryMsg build 端到端 OK
  ```

### 凭据安全（self-check 已通过）

```bash
$ git grep -n 'sk-' -- crates/arf-e2e/tests/custom_checkpoint_factory.rs   # 无输出
```

---

## §B (capability, 情景) 单元判定

### 单元 1：`every_n_rounds` factory 边界

```
单元              : checkpoint_factory_every_n × §2.0
能力等级           : D（PASS）
判定依据          : every_n=1/2/3 三个边界，run 3 次：
                   N=1: 3 fires (round 1/2/3 都 %1==0)
                   N=2: 1 fire (round 2 %2==0)
                   N=3: 1 fire (round 3 %3==0)
file:line         : crates/arf-core/src/checkpoint.rs:58-73 every_n_rounds
                   ✓ predicate: round_count > 0 && round_count % N == 0
```

### 单元 2：`when_context_over` factory 边界

```
单元              : checkpoint_factory_context_over × §2.0
能力等级           : D（PASS）
判定依据          : ratio=0.0/0.5/1.0 三个边界：
                   ratio=0.0: util=0.0 fire (>=0 包含)
                   ratio=0.5: util=0.6 fire, util=0.4 skip
                   ratio=1.0: util=1.0 fire (>= 包含 1.0), util=0.6 skip
file:line         : crates/arf-core/src/checkpoint.rs:77-92 when_context_over
                   ✓ predicate: s.over_view.context_utilization() >= ratio
```

### 单元 3：factory × 5 Checkpoint trigger 矩阵

```
单元              : factory_x_5_checkpoint × §2.0
能力等级           : D（PASS）
判定依据          : every_n=1 工厂注册在 5 个 trigger 各一个：
                   BeforeModelCall / AfterModelCall (各 2 fires — 1 round 2 model calls)
                   BeforeToolExec / AfterToolExec / RoundEnd (各 1 fire)
                   全部 trigger 都能 fire
file:line         : crates/arf-core/src/checkpoint.rs:9-20 Checkpoint enum (5 变体)
                   crates/arf-engine/src/engine.rs:331-340 rules dispatch
                   ✓ factory 与 5 trigger 组合 端到端 work
```

### 单元 4：factory + build 自定义 ActionMessage

```
单元              : factory_build_custom_message × §2.0
能力等级           : D（PASS）
判定依据          : CheckpointRule::every_n_rounds(1) + build 返回 CustomFactoryMsg
                   (msg_type="factory/audit")，注册 route("factory/audit", Strict(model/e2e))
                   → run_react 1 次 → fire 1 次 (build 闭包被调)
file:line         : crates/arf-core/src/checkpoint.rs:58-73 every_n_rounds
                   crates/arf-engine/src/engine.rs:331-340 fire 决策 + build
                   crates/arf-engine/src/engine.rs:222 RunError::UndeclaredMsgType 路径
                   ✓ 自定义 ActionMessage 端到端 work
```

---

## §C 探查产出汇总

| 单元 | 等级 | 判定依据 |
|---|---|---|
| `checkpoint_factory_every_n` | **D** | N=1/2/3 边界全 OK |
| `checkpoint_factory_context_over` | **D** | ratio=0.0/0.5/1.0 边界全 OK |
| `factory_x_5_checkpoint` | **D** | factory + 5 trigger 矩阵全 OK |
| `factory_build_custom_message` | **D** | 自定义 ActionMessage 端到端 OK |

---

## §D 病灶登记

**本 task 无新增 F-lesion**。

### 框架实际行为（按 spec §3.3 输出）

- `CheckpointRule::every_n_rounds(name, trigger, N, build)` —— **D 端到端**
  - predicate: `round_count > 0 && round_count % N == 0`（checkpoint.rs:69-71）
  - 边界：N=1 永远 fire（除 round 0）；N>1 round_count 为 N 倍数时 fire
- `CheckpointRule::when_context_over(name, trigger, ratio, build)` —— **D 端到端**
  - predicate: `context_utilization() >= ratio`（checkpoint.rs:89）
  - 边界：ratio=0.0 永远 fire（util >= 0）；ratio=1.0 仅 util=1.0 时 fire
- `Checkpoint` enum 5 变体（BeforeModelCall / AfterModelCall / BeforeToolExec / AfterToolExec / RoundEnd）—— **D 全部可达**
- factory + build 自定义 ActionMessage —— **D 端到端**

### 注意事项（潜在 issue，非 lesion）

1. **`every_n_rounds` 在 round_count=0 时**不 fire（predicate `> 0`）—— 这是设计意图（避免 round 0 触发），但文档未明确说明。**建议** doc 加 "round 0 skipped to avoid triggering on first chat"（其实 checkpoint.rs:56-57 已写）。
2. **`when_context_over` 的 `context_utilization()` 计算**依赖 `model_context_window` 字段（state.over_view）。**如果 `model_context_window == 0` → 除零 panic**？需 framework fix phase 验证。**不**是本 task 探查范畴（无 F-lesion 触发）。
3. **`CheckpointRule` factory 集中在 `crates/arf-core/src/checkpoint.rs`** —— 3 个工厂方法（`new` / `every_n_rounds` / `when_context_over`）在单一文件，符合 A4 处理集中信条。
4. **Capability-matrix L8 `custom_checkpoint_rule` 标记为 D 等级** —— factory + new() 双入口均 work。

---

## §E 探查回归

- 9.2.3（checkpoint_rules）6 test pass，未受本 task 影响
- 9.12.4 新增 4 test pass
- 综合：9.12.4 = 4 test，**4 pass, 0 新 F-lesion**
- F-001~F-010 与本 task 无关

---

## §F 与父 task / spec 的关系

| 父 task 期望 | 实证结果 |
|---|---|
| 1 个 every_n_rounds 边界 test | ✓ test1 pass (N=1/2/3) |
| 1 个 when_context_over 边界 test | ✓ test2 pass (ratio=0.0/0.5/1.0) |
| 1 个 factory × 5 trigger 矩阵 test | ✓ test3 pass |
| 1 个 factory + build 自定义 ActionMessage test | ✓ test4 pass |
| 预期 0 新 F-lesion | ✓ 0 新 F-lesion |

> 结论：9.12.4 探查显示 framework `CheckpointRule` 两个 built-in factory 端到端 work。app 可用 factory 构造 fire 规则 + build 自定义 ActionMessage。capability-matrix L8 `custom_checkpoint_rule` 标记为 D 等级。

---

## §G 提交状态

- 工作目录：`crates/arf-e2e/tests/custom_checkpoint_factory.rs`（~270 行，4 test cases）
- task doc：`docs/v1.x/phase9/task-9.12.4.md`（新增）
- audit probe：本 doc
- lesion-registry：**未变**（0 新 F-lesion）
- 待 commit + push
