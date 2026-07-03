# audit-probe-9.11.2：`when_context_over` CheckpointRule 触发探查

> Task 9.11.2 探查产出 — **`CheckpointRule::when_context_over` + `arf_compactor::when_context_over` factory 端到端 work？**
> 父 task doc：`docs/v1.x/phase9/task-9.11.2.md`
> 父 spec：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.11.1（Compactor + 默认 Summarizer 端到端）
> **本 task 探查：rule predicate + build_msg + 边界 + 集成**

---

## §A 探查环境

- working tree：HEAD `c741535`（task 9.11.1）+ uncommitted `crates/arf-e2e/tests/compact_checkpoint_rule.rs`
- 测试文件：`crates/arf-e2e/tests/compact_checkpoint_rule.rs`（4 test cases）
- 驱动：直接 rule.fires() / rule.build_msg() API，无 Engine
- 测试命令：
  ```bash
  cargo test -p arf-e2e --test compact_checkpoint_rule -- --nocapture --test-threads=1
  ```
- 结果：**`4 passed; 0 failed; 0.00s`**
- 关键运行输出：
  ```
  test when_context_over_rule_fires_at_high_utilization ... [cpr/rule] high util: fires=true trigger=BeforeModelCall name=when_context_over
  ok
  test when_context_over_rule_does_not_fire_at_low_utilization ... [cpr/rule] low util: fires=false ✓
  ok
  test when_context_over_builds_compact_request_with_correct_fields ...
  [cpr/rule] build msg: type=compact_request payload={"keep_tail":8,"threshold":0.85}
  [cpr/rule] build msg: threshold=0.85 keep_tail=8 ✓
  ok
  test when_context_over_fires_at_exact_ratio ... [cpr/rule] exact ratio: fires=true ✓
  ok
  ```

### 凭据安全（self-check 已通过）

```bash
$ git grep -n 'sk-' -- crates/arf-e2e/tests/compact_checkpoint_rule.rs   # 无输出
```

---

## §B (capability, 情景) 单元判定

### 单元 1：rule.fires() at high utilization

```
单元              : context_compact × §2.9（when_context_over 触发）
能力等级           : D（PASS）
判定依据          : state.context_tokens=80, model_context_window=100
                   → context_utilization() = 0.8
                   rule.fires(&state) == true ✓
                   rule.trigger == BeforeModelCall ✓
                   rule.name == "when_context_over" ✓
file:line         : crates/arf-core/src/checkpoint.rs:77-92  CheckpointRule::when_context_over
                   crates/arf-core/src/checkpoint.rs:89       when = utilization >= ratio
                   crates/arf-compactor/src/lib.rs:157-169   arf_compactor::when_context_over
```

### 单元 2：rule.fires() at low utilization (no fire)

```
单元              : context_compact × §2.9（边界）
能力等级           : D（PASS）
判定依据          : state.context_tokens=30, model_context_window=100
                   → context_utilization() = 0.3
                   rule.fires(&state) == false ✓
file:line         : crates/arf-core/src/checkpoint.rs:89       >= ratio (3 < 7 → false)
                   crates/arf-core/src/state.rs:31-37          context_utilization
```

### 单元 3：rule.build_msg() 返回 CompactRequest 含正确字段

```
单元              : context_compact × §2.9（build CompactRequest）
能力等级           : D（PASS）
判定依据          : rule.build_msg(&state) → Box<dyn ActionMessage>
                   msg_type = "compact_request" ✓
                   intent = MessageIntent::Command ✓
                   payload["threshold"] = 0.85 ✓
                   payload["keep_tail"] = 8 ✓
file:line         : crates/arf-compactor/src/lib.rs:181-194  impl ActionMessage for CompactRequest
                   crates/arf-compactor/src/lib.rs:163-168  build closure returns CompactRequest
```

### 单元 4：边界 (exact ratio)

```
单元              : context_compact × §2.9（边界 = ratio）
能力等级           : D（PASS）
判定依据          : state.utilization = 0.5 == ratio = 0.5
                   rule.fires(&state) == true（>= 关系）✓
file:line         : crates/arf-core/src/checkpoint.rs:89       >= ratio（0.5 >= 0.5 = true）
```

---

## §C 探查产出汇总

| 单元 | 等级 | 判定依据 |
|---|---|---|
| `context_compact × §2.9`（rule fires high） | **D** | util=0.8 ≥ 0.7 → fires |
| `context_compact × §2.9`（rule fires low） | **D** | util=0.3 < 0.7 → no fire |
| `context_compact × §2.9`（build CompactRequest） | **D** | msg_type + payload 正确 |
| `context_compact × §2.9`（边界 exact ratio） | **D** | util == ratio → fires (>=) |

---

## §D 病灶登记

**本 task 无新增 F-lesion**。

### 框架实际行为（按 spec §3.3 输出）

- `CheckpointRule::when_context_over(name, trigger, ratio, build)`：**D**（core/checkpoint.rs:77-92）
- `arf_compactor::when_context_over(ratio, keep_tail)` factory：**D**（compactor/lib.rs:157-169）
- `CompactRequest` ActionMessage impl（msg_type="compact_request", intent=Command）：**D**
- 边界 predicate `utilization >= ratio`（含 ==）：**D**
- 工厂 build closure 包装 threshold + keep_tail 到 payload：**D**

### 注意事项（潜在 issue，非 lesion）

1. **App 必须 register route for "compact_request"**——若 app 用 `arf_compactor::when_context_over()` 但没在 `AgentConfig.engine.routes` 注册 `"compact_request"` 路由，Engine 会返回 `RunError::UndeclaredMsgType`（engine/checkpoint.rs:140-144）。本 task 未实测（无 Engine），但**与 checkpoint_rules 9.2.3 test 6 同形态**：engine/checkpoint.rs:140-144 严格。
2. **`when_context_over` factory 硬编码 trigger = BeforeModelCall**（compactor/lib.rs:160）—— app 想在 AfterModelCall 触发须直接用 `core::CheckpointRule::when_context_over(name, trigger, ratio, build)`。**合理**（compaction 应该在 model_call 之前发生，否则 model 已 tokenized 完才发现超 context）。
3. **`build` closure 忽略 state 入参**（compactor/lib.rs:162-167）—— `move |_state| Box::new(CompactRequest { threshold, keep_tail })` 不用 state 计算任何东西。**合理**（threshold/keep_tail 是 factory 参数，与 state 无关），但**注意** 显式忽略（`_state`）而非 shadow。
4. **`CompactRequest.payload` 是 `serde_json::to_value(self).unwrap_or_default()`**（compactor/lib.rs:189）—— `unwrap_or_default` 静默失败（理论不会 fail，因为 struct derive Serialize）。**建议**：改成 `.expect("CompactRequest always serializable")` 让 panic 暴露。
5. **factory 名字固定 "when_context_over"**（compactor/lib.rs:159）—— EngineBuilder 不允许重名（builder.rs:86-93 DuplicateRuleName check），所以同一 engine 不能用两次该 factory。**合理**（如要多个 threshold 可手动 `core::CheckpointRule::when_context_over(name="custom1", ...)`）。

---

## §E 探查回归

- 9.11.1 既有 3 test pass
- 9.11.2 新增 4 test pass
- 综合：9.11 = 7 test（3+4），**全 pass**
- 与 F-010 / F-011 / F-012 病灶**无关**——本 task 探查 CheckpointRule，与 SessionStore 不直接相关
- 与 9.4.x pool 病灶**无关**

---

## §F 与父 task / spec 的关系

| 父 task 期望 | 实证结果 |
|---|---|
| rule.fires() at high util | ✓ test 1 pass |
| rule.fires() at low util | ✓ test 2 pass |
| build_msg returns CompactRequest with correct fields | ✓ test 3 pass |
| 边界 = ratio | ✓ test 4 pass (bonus) |
| 预期 0 新 F-lesion | ✓ 0 新 F-lesion |

> 结论：9.11.2 探查显示 framework **`when_context_over` CheckpointRule factory** 端到端
> work（4/4 pass）—— app 可注册 rule，state.util >= ratio 时 fires，build 返回
> `CompactRequest { threshold, keep_tail }`。这是 phase 9 压缩类别**连续 2 个 0 新 F-lesion**
> task（继 9.11.1 后）。

---

## §G 提交状态

- 工作目录：`crates/arf-e2e/tests/compact_checkpoint_rule.rs`（~110 行，4 test cases）
- task doc：`docs/v1.x/phase9/task-9.11.2.md`（新增）
- audit probe：本 doc
- lesion-registry：**未变**
- 待 commit
