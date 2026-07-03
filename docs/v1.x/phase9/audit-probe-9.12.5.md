# audit-probe-9.12.5：自定义 OnMemberFailedHandler（FailSession / SwitchTo）端到端探查

> Task 9.12.5 探查产出 — **Framework 是否让 app 端实现自定义 `OnMemberFailedHandler` trait？**
> 父 task doc：`docs/v1.x/phase9/task-9.12.5.md`
> 父 spec：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.13.1（Node 掉线 FailSession 实证）
> **本 task 探查：app 自定义 `OnMemberFailedHandler`（闭包 / struct）+ 3 actions + Engine build**

---

## §A 探查环境

- working tree：HEAD `92f8ca7`（task 9.12.4）+ uncommitted `crates/arf-e2e/tests/custom_member_failed_handler.rs`
- 测试文件：`crates/arf-e2e/tests/custom_member_failed_handler.rs`（4 test cases）
- 驱动：闭包 handler（blanket impl）/ `ThreeActionHandler` (struct impl) / `CountingHandler` (struct impl)
- 测试命令：
  ```bash
  cargo test -p arf-e2e --test custom_member_failed_handler -- --nocapture --test-threads=1
  ```
- 结果：**`4 passed; 0 failed; 0.01s`**
- 关键运行输出：
  ```
  test handler_closure_fail_session ... [test1] 闭包 handler 返回 FailSession 端到端 OK ✓
  test handler_struct_three_actions ... [test2] mcp/retry → Retry { delay_ms: 500 } ✓
  [test2] mcp/switch → SwitchTo { mcp/alternative } ✓
  [test2] mcp/other → FailSession ✓
  test handler_default_none_is_fail_session ... [test3] MemberFailedAction::default() == FailSession ✓
  test handler_invoke_directly_returns_action ... [test4] Engine build with custom handler 端到端 OK ✓
  ```

### 凭据安全（self-check 已通过）

```bash
$ git grep -n 'sk-' -- crates/arf-e2e/tests/custom_member_failed_handler.rs   # 无输出
```

---

## §B (capability, 情景) 单元判定

### 单元 1：闭包 handler + FailSession

```
单元              : handler_closure_fail_session × §2.0
能力等级           : D（PASS）
判定依据          : `|_a, _m, _r| MemberFailedAction::FailSession` 闭包
                   通过 blanket impl 自动 OnMemberFailedHandler。
                   handle() 调通返回 FailSession。
file:line         : crates/arf-engine/src/config.rs:61-68 blanket impl
                   crates/arf-engine/src/config.rs:57-59 trait
                   ✓ 闭包可作 handler
```

### 单元 2：struct impl OnMemberFailedHandler + 3 actions

```
单元              : handler_struct_three_actions × §2.0
能力等级           : D（PASS）
判定依据          : ThreeActionHandler::handle() 根据 member 节点名返回
                   Retry{delay_ms:500} / SwitchTo{alternative} / FailSession。
                   3 次调用全部返回预期 action。
file:line         : crates/arf-engine/src/config.rs:57-59 trait
                   crates/arf-engine/src/config.rs:47-51 MemberFailedAction enum
                   ✓ struct impl + 3 actions 端到端 work
```

### 单元 3：handler default (None) + MemberFailedAction::default()

```
单元              : handler_default_none × §2.0
能力等级           : D（PASS）
判定依据          : E2EHarness default build (None) OK + run round 端到端 OK。
                   MemberFailedAction::default() == FailSession（config.rs:54）。
file:line         : crates/arf-engine/src/config.rs:53-55 default impl
                   ✓ None 与 default() 行为一致
```

### 单元 4：自定义 handler + Engine build 端到端

```
单元              : handler_engine_build × §2.0
能力等级           : D（PASS）
判定依据          : CountingHandler (struct impl) + on_member_failed=Some(handler)
                   + EngineBuilder.build() → Result<Engine, _> = Ok
file:line         : crates/arf-engine/src/config.rs:25 on_member_failed field
                   crates/arf-engine/src/builder.rs EngineBuilder
                   ✓ Engine build 接受自定义 handler 端到端 OK
```

---

## §C 探查产出汇总

| 单元 | 等级 | 判定依据 |
|---|---|---|
| `handler_closure_fail_session` | **D** | 闭包 + blanket impl 端到端 OK |
| `handler_struct_three_actions` | **D** | struct impl + 3 actions (FailSession/Retry/SwitchTo) OK |
| `handler_default_none` | **D** | default None 端到端 OK |
| `handler_engine_build` | **D** | Engine build 接受自定义 handler OK |

---

## §D 病灶登记

**本 task 无新增 F-lesion**。

### 框架实际行为（按 spec §3.3 输出）

- `MemberFailedAction` 3 变体（FailSession / Retry{delay_ms} / SwitchTo{alternative}）—— **D**
- `OnMemberFailedHandler` trait + blanket impl 闭包 —— **D**（config.rs:57-68）
- `EngineConfig.on_member_failed: Option<Arc<dyn OnMemberFailedHandler>>` —— **D**
- `MemberFailedAction::default() == FailSession` —— **D**（config.rs:54）
- `EngineBuilder.build()` 接受 `on_member_failed = Some(handler)` —— **D**

### 注意事项（潜在 issue，非 lesion）

1. **handler 实际调用点（node_offline 触发）** 未在本 task 探查范畴。9.13.1 任务专门探查 `OnMemberFailedAction::FailSession` 在 node_offline 真实路径下的调用。**本 task 仅验证 trait 边界 + 3 actions 可返回 + Engine build 接受 handler**。完整链路验证在 9.13.1 / 9.13.2。
2. **`MemberFailedAction::SwitchTo { alternative }` 的 `alternative` 是 `NodeId`**（config.rs:50）—— 切换目标必须事先是已注册 NodeId；framework 不验证其存在性，**不**是 lesion 但调用者责任。
3. **`Retry { delay_ms }` 是否实际等待延迟**未探查（9.13.2 切换路径时再查）。

### 与 9.13.1 / 9.13.2 的接口

本 task 验证了 **handler trait 边界 + 3 actions 形态 + Engine 接受 handler 配置**。9.13.1 / 9.13.2 进一步探查：
- 9.13.1: Node 掉线时 framework 真实调用 handler 的路径（FailSession 端到端）
- 9.13.2: SwitchTo action 在替代节点上 work

---

## §E 探查回归

- 9.2.3（checkpoint_rules）6 test pass，未受本 task 影响
- 9.12.1-9.12.4 各 4 test pass
- 9.12.5 新增 4 test pass
- 综合：9.12.5 = 4 test，**4 pass, 0 新 F-lesion**
- F-001~F-010 与本 task 无关

---

## §F 与父 task / spec 的关系

| 父 task 期望 | 实证结果 |
|---|---|
| 1 个闭包 handler FailSession test | ✓ test1 pass |
| 1 个 struct 3 actions test | ✓ test2 pass |
| 1 个 default None test | ✓ test3 pass |
| 1 个 handler + Engine build test | ✓ test4 pass |
| 预期 0 新 F-lesion | ✓ 0 新 F-lesion |

> 结论：9.12.5 探查显示 framework `OnMemberFailedHandler` trait + blanket impl 闭包 + 3 actions 端到端 work。**handler 真实调用路径（node_offline 触发）由 9.13.1 / 9.13.2 探查**。capability-matrix L8 `custom_member_failed_handler` 标记为 D 等级。

---

## §G 提交状态

- 工作目录：`crates/arf-e2e/tests/custom_member_failed_handler.rs`（~190 行，4 test cases）
- task doc：`docs/v1.x/phase9/task-9.12.5.md`（新增）
- audit probe：本 doc
- lesion-registry：**未变**（0 新 F-lesion）
- 待 commit + push
