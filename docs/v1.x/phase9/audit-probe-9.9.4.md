# audit-probe-9.9.4：双 agent + subagent 嵌套（2 层）端到端探查

> Task 9.9.4 探查产出 — **Framework 是否支持 parent → child → grandchild 的 2 层 subagent 委派 + correlation_id 端到端透传？**
> 父 task doc：`docs/v1.x/phase9/task-9.9.4.md`
> 父 spec：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.9.3（1 层委派）
> **本 task 探查：2 层委派 + 中间层转发 + correlation_id 端到端匹配 + leaf result 回传中间层**

---

## §A 探查环境

- working tree：HEAD `7e278b1`（task 9.9.2）+ uncommitted `crates/arf-e2e/tests/nested_subagent_two_layer.rs`
- 测试文件：`crates/arf-e2e/tests/nested_subagent_two_layer.rs`（2 test cases，~390 行）
- 驱动：`SimpleMock`（3 个 provider "np2/nc2/ng2"）+ `LeafHandler`（终层，回 subagent_result）+ `ForwardHandler`（中间层转发）
- 测试命令：
  ```bash
  cargo test -p arf-e2e --test nested_subagent_two_layer -- --nocapture --test-threads=1
  ```
- 结果：**`2 passed; 0 failed; 0.31s`**
- 关键运行输出：
  ```
  test1: online engines: 3, L1 cid=…, L2 cid=…, matching_nodes=1 (×2)
  test2: L2 forwarded task=[forwarded] task root, cid=<root_cid>
  test2: child 收到 leaf subagent_result: output=[leaf] [forwarded] task root, status=Success, cid=<root_cid>
  ```

### 凭据安全（self-check 已通过）

```bash
$ git grep -n 'sk-' -- crates/arf-e2e/tests/nested_subagent_two_layer.rs   # 无输出
```

---

## §B (capability, 情景) 单元判定

### 单元 1：3 engine + 2 SubagentDelegate 链构造

```
单元              : nested_two_layer_chain_construct × §2.7
能力等级           : D（PASS）
判定依据          : 3 个 EngineBuilder.build() 启动 3 engine (provider np/nc/ng)
                   bus graph 在线 engine 数 = 3
                   L1: parent → child, L2: child → grandchild
                   两个 SubagentDelegate 独立构造，cid 各异
file:line         : crates/arf-e2e/tests/nested_subagent_two_layer.rs:215-278
                   crates/arf-core/src/message.rs:167-195 (SubagentDelegate)
                   ✓ 3 engine 同 bus 共存（用不同 provider 名避 F-010），2 个 Delegate 独立可发
```

### 单元 2：2 层委派 correlation_id 端到端匹配 + leaf result 回传

```
单元              : nested_two_layer_correlation_propagate × §2.7
能力等级           : C（Composable，需 app 端 shim + bus.send 限制）
判定依据          : L1 (parent→child) 被 child 的 ForwardHandler 收
                   ForwardHandler 构造新 SubagentDelegate 但保留原 cid
                   L2 (child→grandchild) 被 grandchild 的 LeafHandler 收
                   LeafHandler 用 cid_root 构造 SubagentResult 回 child
                   验证 cid 端到端匹配，output 包含 "[forwarded]" + "[leaf]"
file:line         : crates/arf-e2e/tests/nested_subagent_two_layer.rs:99-149 (ForwardHandler)
                   crates/arf-e2e/tests/nested_subagent_two_layer.rs:65-96 (LeafHandler)
                   crates/arf-e2e/tests/nested_subagent_two_layer.rs:284-389 (test2)
                   ✓ correlation_id 端到端匹配，leaf result 回中间层 OK
                   ✗ LeafHandler 必须注入真实 online NodeId（不能 msg.from）
```

---

## §C 探查产出汇总

| 单元 | 等级 | 判定依据 |
|---|---|---|
| `nested_two_layer_chain_construct × §2.7` | **D** | 3 engine + 2 SubagentDelegate 端到端 OK |
| `nested_two_layer_correlation_propagate × §2.7` | **C** | 2 层委派 + cid 端到端匹配 work，但 LeafHandler 需注入真实 online NodeId（不能用 msg.from） |

---

## §D 病灶登记

### 本 task 无新增 F-lesion（沿用 F-010 + F-011，**+1 个新 F-lesion：F-012**——同 9.9.3 模式，仅 audit probe 记录，未入 lesion-registry）

**F-010（9.9.1 触发）** 在本 task 再次命中：3 engine 需用不同 provider 名（"np"/"nc"/"ng"）才能在同 bus 共存。**根因依旧**：`Engine::node_id = "engine/{provider}"`（engine.rs:59），framework 不支持自定义 agent_id。

**F-011（9.9.2 触发）** 在本 task 同样命中：3 处 bus.subscribe + dispatch_incoming（每层一次）。**subagent 与 peer 共用同一 F-011 病灶**。

### 新增 F-lesion：F-012（9.9.4 触发）— bus.send 强校验 to 节点必须 online

**根因**：`bus.send(msg)` 在 `crates/arf-bus/src/lib.rs:421` 验证 `to` 列表所有 NodeId 必须 online（否则返回 `SendError::NodeOffline`）。**这与 broadcast 语义冲突**——broadcast 应该是无目标的 push-payload，subscribers 各自决定收不收。

**触发路径**：ForwardHandler 转发 L2 时设置 `from = NodeId::new("engine/child-stub")`（stub id，因为 handler 不知道自己是哪个 engine）。LeafHandler 收到 L2 时尝试 `bus.send(reply, to=[msg.from])` 失败，因为 `engine/child-stub` 不在线。

**临时规避**（本 test 2 用的）：LeafHandler 注入真实 online NodeId（`leaf_node_id` + `reply_to`），不依赖 `msg.from`。但这意味着 **handler 必须知道上下游的 agent_id**，破坏 handler 通用性。

**修复需 framework**：
- 选项 A：bus.send 对 stub/未注册 to 节点回退为 broadcast（不报错）
- 选项 B：Message 加 `broadcast: bool` 字段，标记为 broadcast 的 send 跳过 online 校验
- 选项 C：handler 提供 `peer_id` 注入，framework 自动转换 stub → real id

### 注意事项（潜在 issue，非 lesion）

1. **handler.reply_to 必须 app 端硬编码**（同 F-012）——本 test 2 在 LeafHandler struct 里存 `reply_to: NodeId`，但实际业务中 handler 不知道下游是谁。**framework 应提供 `HandlerContext::peer_registry` 查表**。
2. **2 层委派需要 3 次 dispatch_incoming 调用**（每层一次）——app 端 glue code 增长 3×。**framework 应提供 `Engine::subscribe_incoming(msg_type)` 自动 dispatch**。
3. **LeafHandler 的 `from` 字段是 stub（"engine/leaf-stub"）**——bus.subscribe 收到 subagent_result 后，`from` 不指向任何真实 engine，**trace 完整性受影响**。9.9.3 也同样问题。
4. **correlation_id 透传依赖 app 端手动赋值**（`fwd.correlation_id = sd.correlation_id;`）——`SubagentDelegate::new` 每次都生成新 UUID，**framework 应提供 `SubagentDelegate::forward()` 保留原 cid 的 API**。
5. **SubagentResult 缺 child_session_id 字段**（沿 9.9.3）——parent 拿到 leaf result 后无法知道 grandchild 的 session_id，**无法续聊**。
6. **test 2 只验证了 leaf result 回中间层（child），未验证中间层转发 result 回 parent**——完整 2 层 round-trip 需要再写一个 handler 处理 subagent_result 然后回 parent，本 test 暂未覆盖（复杂度超 200 行上限）。

---

## §E 探查回归

- 9.9.1 4 test + 9.9.2 3 test + 9.9.3 3 test + 9.9.4 2 test = **12 新 test**，**3 F-lesion**（F-010 + F-011 + **F-012**）
- 既有 9.4-9.5 测试未触及 engine_id / ActionMessage 派发，未污染

---

## §F 与父 task / spec 的关系

| 父 task 期望 | 实证结果 |
|---|---|
| 2 层 SubagentDelegate + 2 个 SubagentResult 链能搭 | ✓ test1 实证（3 engine + 2 Delegate） |
| 2 层委派 correlation_id 端到端匹配 | ✓ test2 实证（L2.cid == L1.cid == root_cid） |
| 中间层转发是关键 | ✓ test2 实证（ForwardHandler 保留 cid，task 包装 "[forwarded]"） |
| grandchild 的 result 回传中间层 | ✓ test2 实证（child 收到 leaf result，output 含 "[leaf]"） |
| grandchild 的 result 再回 parent | △ 暂未测（需 4 次 dispatch，需另写 handler） |

> 结论：9.9.4 探查显示 framework **提供 2 层 subagent 嵌套的协议 + 端到端 correlation_id 透传**，但 **app 端需 ~100 行 glue code 桥接 3 处 handler 派发**（沿 F-011），**且 handler 需硬编码上下游 online NodeId**（新 F-012）。F-010 病灶在 3 engine 同 bus 时更突出。**F-012 是 9.9.4 的核心新发现**——framework bus.send 不区分 broadcast 与定向，对未注册 NodeId 报错与 broadcast 语义冲突。

---

## §G 提交状态

- 工作目录：`crates/arf-e2e/tests/nested_subagent_two_layer.rs`（~390 行，2 test cases）
- task doc：`docs/v1.x/phase9/task-9.9.4.md`（已建）
- audit probe：本 doc
- lesion-registry：**未变**（F-010 + F-011 + F-012 待登记，**F-012 不在本 task 修改范围**）
- 待 commit
