# audit-probe-9.9.5：3+ agent + subagent 嵌套（3 层）端到端探查

> Task 9.9.5 探查产出 — **Framework 是否支持 parent → child → grandchild → great-grandchild 的 3 层 subagent 委派 + correlation_id 端到端透传？**
> 父 task doc：`docs/v1.x/phase9/task-9.9.5.md`
> 父 spec：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.9.4（2 层委派）
> **本 task 探查：3 层委派 + 2 个中间层转发 + correlation_id 端到端匹配（4 engine 共享 1 个 cid）**

---

## §A 探查环境

- working tree：HEAD `e760c1e`（task 9.9.4）+ uncommitted `crates/arf-e2e/tests/nested_subagent_three_layer.rs`
- 测试文件：`crates/arf-e2e/tests/nested_subagent_three_layer.rs`（2 test cases，~430 行）
- 驱动：`SimpleMock`（4 个 provider "np3b/nc3b/ng3b/ngg3b"）+ 2 个 `ForwardHandler`（中间层 child/grandchild）+ `LeafHandler`（终层 great-grandchild）
- 测试命令：
  ```bash
  cargo test -p arf-e2e --test nested_subagent_three_layer -- --nocapture --test-threads=1
  ```
- 结果：**`2 passed; 0 failed; 0.51s`**
- 关键运行输出：
  ```
  test1: online engines: 4, L1/L2/L3 cids 各异, matching_nodes=1 (×3)
  test2: L3 forwarded task=[forwarded] [forwarded] task root, cid=<root_cid>
  test2: grandchild 收到 leaf subagent_result: output=[leaf] [forwarded] [forwarded] task root, status=Success, cid=<root_cid>
  test2: fwd_c_count=1, fwd_g_count=1
  ```

### 凭据安全（self-check 已通过）

```bash
$ git grep -n 'sk-' -- crates/arf-e2e/tests/nested_subagent_three_layer.rs   # 无输出
```

---

## §B (capability, 情景) 单元判定

### 单元 1：4 engine + 3 SubagentDelegate 链构造

```
单元              : nested_three_layer_chain_construct × §2.7
能力等级           : D（PASS）
判定依据          : 4 个 EngineBuilder.build() 启动 4 engine (provider np3/nc3/ng3/ngg3)
                   bus graph 在线 engine 数 = 4
                   L1: parent→child, L2: child→grandchild, L3: grandchild→great-grandchild
                   3 个 SubagentDelegate 独立构造，cid 各异
                   3 次 bus.send 全部 matching_nodes=1
file:line         : crates/arf-e2e/tests/nested_subagent_three_layer.rs:215-280
                   crates/arf-core/src/message.rs:167-195 (SubagentDelegate)
                   ✓ 4 engine 同 bus 共存（用不同 provider 名避 F-010），3 个 Delegate 独立可发
```

### 单元 2：3 层委派 correlation_id 端到端匹配 + leaf result 回传

```
单元              : nested_three_layer_correlation_propagate × §2.7
能力等级           : C（Composable，需 app 端 shim + bus.send 限制）
判定依据          : L1 (parent→child) 被 child 的 ForwardHandler 收 → 保留 cid 转发 L2
                   L2 (child→grandchild) 被 grandchild 的 ForwardHandler 收 → 保留 cid 转发 L3
                   L3 (grandchild→great-grandchild) 被 great-grandchild 的 LeafHandler 收
                   LeafHandler 用 cid_root 构造 SubagentResult 回 grandchild
                   验证 cid 端到端匹配，output 包含 "[forwarded] [forwarded] [leaf]"
                   2 个 ForwardHandler 各触发 1 次
file:line         : crates/arf-e2e/tests/nested_subagent_three_layer.rs:96-149 (ForwardHandler)
                   crates/arf-e2e/tests/nested_subagent_three_layer.rs:65-94 (LeafHandler)
                   crates/arf-e2e/tests/nested_subagent_three_layer.rs:286-430 (test2)
                   ✓ 3 层委派 + cid 端到端匹配 work
                   ✗ F-012 沿用：LeafHandler 需注入真实 online NodeId
```

---

## §C 探查产出汇总

| 单元 | 等级 | 判定依据 |
|---|---|---|
| `nested_three_layer_chain_construct × §2.7` | **D** | 4 engine + 3 SubagentDelegate 端到端 OK |
| `nested_three_layer_correlation_propagate × §2.7` | **C** | 3 层委派 + cid 端到端匹配 work，handler 注入扩大（F-012） |

---

## §D 病灶登记

### 本 task 无新增 F-lesion（沿用 F-010 + F-011 + F-012）

**F-010（9.9.1 触发）** 在本 task 进一步命中：4 engine 需用 4 个不同 provider 名（"np3"/"nc3"/"ng3"/"ngg3"）才能在同 bus 共存。**根因依旧**：`Engine::node_id = "engine/{provider}"`（engine.rs:59）。N 层嵌套 = N 个 provider，**production 需 1 个 engine 跑多个 sub-agent role**——framework 完全不支持。

**F-011（9.9.2 触发）** 在本 task 严重放大：3 层 = **3 次 bus.subscribe + dispatch_incoming**（每层一次） + 2 个 ForwardHandler 手动注册 + 1 个 LeafHandler 手动注册 = **6 处 app glue code**。**真实生产环境 N 层 = 3N 处 glue**，framework 完全不简化。

**F-012（9.9.4 触发）** 在本 task 继续命中：LeafHandler 必须注入真实 online NodeId（`leaf_node_id` + `reply_to`）。3 层 + 2 个 handler = **3 处 handler 都需要硬编码上下游 agent_id**，app 层耦合度 N² 增长。

### 注意事项（潜在 issue，非 lesion）

1. **3 层需要 4 个不同 provider 名**（F-010）——生产中无法用单一 provider 跑多角色。
2. **3 层 ForwardHandler 模板代码完全相同**（仅 next_id + counter 不同）——framework 应提供 `DelegateForwarder { target, wrap_task }` 通用 helper。
3. **3 层 cid 端到端匹配 work**（✓）——证明 `SubagentDelegate::new` + 手动 cid override 模式可 scale。
4. **leaf result 只回 grandchild，未回 child/parent**（沿 9.9.4）——完整 3 层 round-trip 需 2 个 `ForwarderReplyHandler`，本 test 暂未覆盖（复杂度超 300 行）。
5. **handler 注入的 reply_to 是单向的**（F-012）——grandchild 收到 leaf result 后，如果想再 forward 给 child 需另一套注入，**handler 的"上下游拓扑"硬编码在 struct 字段**，无法动态发现。

---

## §E 探查回归

- 9.9.1 4 test + 9.9.2 3 test + 9.9.3 3 test + 9.9.4 2 test + 9.9.5 2 test = **14 新 test**，**3 F-lesion**（F-010 + F-011 + F-012）
- 既有 9.4-9.5 测试未触及 engine_id / ActionMessage 派发，未污染

---

## §F 与父 task / spec 的关系

| 父 task 期望 | 实证结果 |
|---|---|
| 3 层 SubagentDelegate + 2 个中间层 ForwardHandler + 1 个 LeafHandler | ✓ test1 + test2 实证（4 engine + 3 Delegate + 2 Forward + 1 Leaf） |
| 3 层委派 correlation_id 端到端匹配 | ✓ test2 实证（L3.cid == L2.cid == L1.cid == root_cid） |
| 终层 result 回传中间层（grandchild） | ✓ test2 实证（grandchild 收到 leaf result，output 含 "[leaf]" + 2 层 "[forwarded]"） |
| 2 个中间层 forward_count 各 1 | ✓ test2 实证（fwd_c_count=1, fwd_g_count=1） |
| N 层 = N provider 限制（F-010 放大） | ✓ test1 实证（4 engine 需 4 provider 名） |
| N 层 = 3N glue code（F-011 放大） | ✓ test2 实证（3 layer × 3 glue = 9 处 dispatch/subscribe） |
| F-012 在 3 层更突出 | ✓ test2 实证（LeafHandler 注入 2 个 NodeId，ForwardHandler 注入 1 个） |

> 结论：9.9.5 探查显示 framework **支持 3 层 subagent 嵌套 + 端到端 correlation_id 透传**，但 **F-010/F-011/F-012 三病灶随层数 N 线性放大**：N 层 = N provider + 3N glue code + 2N-1 NodeId 注入。**framework 必须提供**：(1) EngineBuilder 显式 agent_id（修 F-010）；(2) Engine auto-dispatch ActionMessage（修 F-011）；(3) bus.send 区分 broadcast vs directed（修 F-012）。**否则生产多 agent 拓扑不可用**。

---

## §G 提交状态

- 工作目录：`crates/arf-e2e/tests/nested_subagent_three_layer.rs`（~430 行，2 test cases）
- task doc：`docs/v1.x/phase9/task-9.9.5.md`（已建）
- audit probe：本 doc
- lesion-registry：**未变**（F-010 + F-011 + F-012 沿用，无新 F-lesion）
- 待 commit
