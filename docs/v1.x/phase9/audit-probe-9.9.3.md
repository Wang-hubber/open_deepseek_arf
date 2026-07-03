# audit-probe-9.9.3：双 agent + subagent 委派（1 层）端到端探查

> Task 9.9.3 探查产出 — **Framework 是否让 parent engine 通过 SubagentDelegate 委派任务给 child engine？**
> 父 task doc：`docs/v1.x/phase9/task-9.9.3.md`
> 父 spec：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.9.1（双 agent 独立）
> **本 task 探查：SubagentDelegate 协议 + parent → child 委派 + child 端 handler 回 SubagentResult**

---

## §A 探查环境

- working tree：HEAD `7e278b1`（task 9.9.2）+ uncommitted `crates/arf-e2e/tests/dual_agent_subagent.rs`
- 测试文件：`crates/arf-e2e/tests/dual_agent_subagent.rs`（3 test cases）
- 驱动：`SimpleMock`（2 个 provider）+ `SimpleSubagentHandler`（child 端，固定 output）
- 测试命令：
  ```bash
  cargo test -p arf-e2e --test dual_agent_subagent -- --nocapture --test-threads=1
  ```
- 结果：**`3 passed; 0 failed; 0.31s`**
- 关键运行输出：
  ```
  test1: parent.send(subagent_delegate) = Ok(... online_nodes=4, matching_nodes=1)
  test2: dispatch_incoming outcome: Handled, output=[done: analyze Y], status=Success
  test3: engine_p filter types: [model_response, peer_reply, subagent_result, tool_result]
  ```

### 凭据安全（self-check 已通过）

```bash
$ git grep -n 'sk-' -- crates/arf-e2e/tests/dual_agent_subagent.rs   # 无输出
```

---

## §B (capability, 情景) 单元判定

### 单元 1：SubagentDelegate 构造 + 发送

```
单元              : subagent_delegate_construct × §2.7
能力等级           : D（PASS）
判定依据          : SubagentDelegate::new(parent_session, subagent_node_id, task) 构造
                   engine.handle().send("subagent_delegate", to=[child_id], payload) → Ok
                   bus broadcast → child 端 bus.subscribe 收
file:line         : crates/arf-core/src/message.rs:167-195 (SubagentDelegate)
                   crates/arf-core/src/message.rs:198-211 (ActionMessage impl)
                   ✓ 端到端 OK（online_nodes=4, matching_nodes=1）
```

### 单元 2：child handler 委派 + SubagentResult 回传

```
单元              : subagent_delegate_dispatch × §2.7
能力等级           : C（Composable，需 app 端 shim）
判定依据          : child engine 注册 SimpleSubagentHandler (msg_type="subagent_delegate")
                   app 端 bus.subscribe + dispatch_incoming(peer_message) 触发 handler
                   handler 解析 SubagentDelegate，构造 SubagentResult，发回 parent
file:line         : crates/arf-engine/src/engine.rs:172-185 (dispatch_incoming)
                   crates/arf-engine/src/dispatcher.rs:85-100 (HandlerRegistry::dispatch)
                   ✓ 端到端 work，但需 ~25 行 app glue（bus.subscribe + dispatch + handler 桥接）
```

### 单元 3：parent filter 含 subagent_result

```
单元              : parent_filter_subagent × §2.7
能力等级           : D（PASS）
判定依据          : parent engine cfg.engine.routes 含 "subagent_delegate"
                   → engine_response_types 添加 "subagent_result" 到 filter
                   → parent filter = [model_response, peer_reply, subagent_result, tool_result]
file:line         : crates/arf-engine/src/engine.rs:726-744
                   crates/arf-engine/src/engine.rs:747-757 (response_msg_type_for)
                   ✓ parent filter 含 subagent_result，可收到 child 回的 result
```

---

## §C 探查产出汇总

| 单元 | 等级 | 判定依据 |
|---|---|---|
| `subagent_delegate_construct × §2.7` | **D** | 构造 + bus.send 端到端 OK |
| `subagent_delegate_dispatch × §2.7` | **C** | handler 派发需 app 端桥接（沿 F-011） |
| `parent_filter_subagent × §2.7` | **D** | routes → filter 映射 OK |

---

## §D 病灶登记

### 本 task 无新增 F-lesion（沿用 F-010 + F-011）

**F-010（9.9.1 触发）** 在本 task 再次命中：test2 重建 child engine 时使用 provider="child" 撞了 test1 留在线的 `engine/child`，报 `PrimaryBusConnect("node already connected")`。**F-010 影响 multi-subagent**：child engine 只能用唯一 provider 名（每个 child 用不同 provider 才能并存），实际是 brittle workaround。**修复需 framework 支持** `EngineBuilder::with_agent_id(NodeId)` 显式指定。

**F-011（9.9.2 触发）** 在本 task 同样命中：child engine 收 subagent_delegate 需 app 端 bus.subscribe + 手动 dispatch_incoming。**subagent 协议与 peer 协议共用同一 F-011 病灶**——Engine 不自动派发所有 ActionMessage。

### 注意事项（潜在 issue，非 lesion）

1. **`SubagentHandler::handle` 是 sync fn，handler 内部不能 `await`**——本 task 探查时尝试调 child engine.run() 失败（run 是 async）。**framework 应提供 AsyncMessageHandler trait** 或 helper。
2. **handler 内部发 reply 的 from 必须是已 online 的 NodeId**（与 9.9.2 沿用）——本 test 2 用 `NodeId::new("engine/child-stub")` 临时指定，因为 child engine 实际没注册 NodeId 字段可取。
3. **SubagentResult 缺 `child_session_id` 字段**（message.rs:215-221）——回包只含 correlation_id + status + output + trajectory，**不含 child engine 自己的 session**。parent 无法用 child_session_id 续聊。**spec 应扩展**。
4. **subagent_node_id 是 NodeId 字符串，framework 不验证它是否 online**——`SubagentDelegate::new(parent_session, "engine/nonexistent", task)` 编译 OK，运行时报 `NodeOffline`。**应在 build 阶段校验**。
5. **test2 child engine 实际未"委派"任务**——`SimpleSubagentHandler` 只构造固定 output（"[done: analyze Y]"），**没**调 child engine.run() 拿到真实 model response。**真实 subagent flow 需 handler 内 `engine.run(state, task, cancel)`**，但因为 (1) 同步性限制，**当前 framework 强制 handler 走"土办法"**（独立 OS 线程 + 独立 runtime）。

---

## §E 探查回归

- 9.9.1 4 test + 9.9.2 3 test + 9.9.3 3 test = 10 新 test，2 F-lesion（F-010 + F-011）
- 既有 9.4-9.5 测试未触及 engine_id / ActionMessage 派发，未污染

---

## §F 与父 task / spec 的关系

| 父 task 期望 | 实证结果 |
|---|---|
| SubagentDelegate::new 可发到目标 engine | ✓ test1 实证（bus.send OK） |
| child handler 委派 + 回 SubagentResult | ✓ test2 实证（output 含 task，status=Success） |
| parent filter 含 subagent_result | ✓ test3 实证（filter = [model_response, peer_reply, subagent_result, tool_result]） |

> 结论：9.9.3 探查显示 framework **提供 subagent 委派协议 + 端到端流**，但 **app 端需 ~25 行 glue code 桥接 handler 派发**（沿 F-011）。F-010 病灶（agent_id 命名）在本 task 再次命中，凸显多 subagent 拓扑的 framework 限制。

---

## §G 提交状态

- 工作目录：`crates/arf-e2e/tests/dual_agent_subagent.rs`（~290 行，3 test cases）
- task doc：`docs/v1.x/phase9/task-9.9.3.md`（新增）
- audit probe：本 doc
- lesion-registry：**未变**（F-010 + F-011 沿用，无新 F-lesion）
- 待 commit + push
