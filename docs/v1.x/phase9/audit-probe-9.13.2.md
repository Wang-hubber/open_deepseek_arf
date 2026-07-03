# audit-probe-9.13.2：Node 掉线（SwitchTo alternative）端到端探查

> Task 9.13.2 探查产出 — **Framework node_offline 时是否调用 `MemberFailedAction::SwitchTo` handler？**
> 父 task doc：`docs/v1.x/phase9/task-9.13.2.md`
> 父 spec：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.13.1（Node 掉线 FailSession，F-011 已证 handler 不被调）
> **本 task 探查：SwitchTo action 真实调用路径 + alternative 节点切换**

---

## §A 探查环境

- working tree：HEAD `9b39a2b`（task 9.13.1）+ uncommitted `crates/arf-e2e/tests/node_offline_switch_to.rs`
- 测试文件：`crates/arf-e2e/tests/node_offline_switch_to.rs`（4 test cases）
- 驱动：`SwitchToHandler` (SwitchTo{alternative}) / Engine 真实路径
- 测试命令：
  ```bash
  cargo test -p arf-e2e --test node_offline_switch_to -- --nocapture --test-threads=1
  ```
- 结果：**`4 passed; 0 failed; 3.11s`**
- 关键运行输出：
  ```
  test switch_to_handler_returns_alternative_node ... [test1] SwitchTo { alternative: model/alternative } OK ✓
  test switch_to_alternative_nonexistent_node ... [test3] framework 不验证 alternative 存在
  test engine_node_offline_with_switch_to_handler ... [test2] 实证 F-011: handler 未被调
  test switch_to_realistic_alternative_node ... [test4] 期望 handler 至少 1 次 invoke, 实际: 0 次
  ```

### 凭据安全（self-check 已通过）

```bash
$ git grep -n 'sk-' -- crates/arf-e2e/tests/node_offline_switch_to.rs   # 无输出
```

---

## §B (capability, 情景) 单元判定

### 单元 1：SwitchTo handler 直接 invoke (trait 边界)

```
单元              : switch_to_handler_trait × §2.0
能力等级           : D（PASS）
判定依据          : SwitchToHandler::handle() 返回 MemberFailedAction::SwitchTo{alternative}
                   直接 invoke 验 trait 边界 OK，alternative 字段正确
file:line         : crates/arf-engine/src/config.rs:50 SwitchTo 变体
                   ✓ trait 形态端到端 OK
```

### 单元 2：Engine + SwitchTo handler + node_offline

```
单元              : engine_node_offline_switch_to × §2.0
能力等级           : F（FAIL — handler 未被调，SwitchTo 路径同样缺失）
判定依据          : Engine with on_member_failed=Some(handler) + drop model node
                   + heartbeat timeout + node_offline → handler invocations = []
                   沿用 F-011
file:line         : crates/arf-engine/src/engine.rs:81-92 lifecycle listener
                   ✗ on_member_failed.handle() 未被调
```

### 单元 3：SwitchTo alternative 节点不存在（边界）

```
单元              : switch_to_nonexistent × §2.0
能力等级           : C（partial — trait 形态 OK，framework 不验证 alternative）
判定依据          : SwitchTo{alternative: NodeId("nonexistent")} 被 trait 接受，
                   framework 不验证 alternative 是否在 bus 上注册
file:line         : crates/arf-engine/src/config.rs:50 alternative: NodeId
                   ✓ trait 接受任何 NodeId
                   ✗ framework 缺 alternative 存在性验证
```

### 单元 4：SwitchTo 到真实存在的 alternative 节点

```
单元              : switch_to_realistic × §2.0
能力等级           : F（FAIL — handler 未被调）
判定依据          : 2 个 model nodes (primary + alt) + drop primary + node_offline
                   + SwitchTo{alternative: model/alt} → handler invocations = []
                   即使 alternative 存在，handler 仍未被调
file:line         : 同单元 2 (F-011 沿用)
                   ✗ handler 不被调
```

---

## §C 探查产出汇总

| 单元 | 等级 | 判定依据 |
|---|---|---|
| `switch_to_handler_trait` | **D** | SwitchTo 变体 trait 边界 OK |
| `engine_node_offline_switch_to` | **F** | 沿用 F-011: handler 未被调 |
| `switch_to_nonexistent` | **C** | trait OK；framework 不验证 alternative 存在 |
| `switch_to_realistic` | **F** | 沿用 F-011: handler 未被调 |

---

## §D 病灶登记

**本 task 无新增 F-lesion**（沿用 F-011 病灶）。

### 框架实际行为（按 spec §3.3 输出）

- `MemberFailedAction::SwitchTo { alternative: NodeId }` —— **D**（trait 形态 OK）
- trait 直接 invoke 多次 OK —— **D**
- Engine node_offline 真实路径不调 handler —— **F-011 沿用**
- framework 不验证 `alternative` 节点存在性 —— **C**

### 注意事项（潜在 issue，非 lesion）

1. **F-011 沿用**：SwitchTo action 的 framework 真实调用点缺失，handler 在
   node_offline 路径上**未**被调。修复方向同 F-011（lifecycle listener
   增加 handler.handle() 调用 + 根据 action 类型执行 FailSession/Retry/SwitchTo）。
2. **SwitchTo 真实切换逻辑未实现**：即便 handler 被调且返回 SwitchTo，Engine
   怎么用 `alternative` 重新路由后续 model_call？framework 缺切换逻辑：
   - 选项 A：更新 `cfg.model.provider` → 走 resolve_model
   - 选项 B：注册临时 route → 后续 model_call 强制 to=alternative
   - 选项 C：保留 alternative NodeId 作为 cached owner → 走 owner_of_tool
   - **framework 尚未实现任一切换路径**——本 task 仅实证 F-011 沿用。
3. **alternative 不验证存在性**：SwitchTo{alternative: NodeId("nonexistent")}
   trait 接受但 framework 不检查。后果：handler 真被调后切到不存在节点，**后续
   model_call 会 stuck**。建议 framework 在 handle() 后立即验证 alternative 在线。
4. **MemberFailedAction 三 action 在 framework 端均无实现**：FailSession（9.13.1）/
   Retry（待探查）/ SwitchTo（本 task）— 全部 declared but unwired。

---

## §E 探查回归

- 9.13.1（node_offline_fail_session）4 test pass，未受本 task 影响
- 9.13.2 新增 4 test pass
- 综合：9.13.2 = 4 test，**4 pass, 0 新 F-lesion（沿用 F-011）**
- F-001~F-010 + F-011 关联本 task

---

## §F 与父 task / spec 的关系

| 父 task 期望 | 实证结果 |
|---|---|
| 1 个 SwitchTo handler trait test | ✓ test1 pass |
| 1 个 Engine + SwitchTo handler + node_offline test | ✓ test2 pass (F-011 沿用) |
| 1 个 SwitchTo alternative 不存在 test | ✓ test3 pass (framework 不验证) |
| 1 个 SwitchTo 真实 alt + 2 model nodes test | ✓ test4 pass (F-011 沿用) |
| 预期 0 新 F-lesion | ✓ 0 新 F-lesion（F-011 已登记） |

> 结论：9.13.2 探查确认 `MemberFailedAction::SwitchTo` 沿用 F-011：handler 真实调用路径缺失，framework 不调 handler。同时发现 framework 不验证 `alternative` 节点存在性。两个潜在 issue（缺切换逻辑 + 缺 alternative 验证）都依赖 F-011 修复。

---

## §G 提交状态

- 工作目录：`crates/arf-e2e/tests/node_offline_switch_to.rs`（~220 行，4 test cases）
- task doc：`docs/v1.x/phase9/task-9.13.2.md`（新增）
- audit probe：本 doc
- lesion-registry：**未变**（沿用 F-011）
- 待 commit + push
