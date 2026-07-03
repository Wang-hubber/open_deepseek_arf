# audit-probe-9.9.2：双 agent + peer（A2A / PeerMessage + PeerReply）端到端探查

> Task 9.9.2 探查产出 — **Framework 是否让双 Engine 通过 PeerMessage / PeerReply 通信？**
> 父 task doc：`docs/v1.x/phase9/task-9.9.2.md`
> 父 spec：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.9.1（双 agent 独立）
> **本 task 探查：PeerMessage/PeerReply 协议 + Engine filter 行为 + MessageHandler 派发**

---

## §A 探查环境

- working tree：HEAD `d5410ae`（task 9.9.1）+ uncommitted `crates/arf-e2e/tests/dual_agent_peer.rs`
- 测试文件：`crates/arf-e2e/tests/dual_agent_peer.rs`（3 test cases）
- 驱动：`SimpleMock`（name/text）+ `PeerEchoHandler`（手写 sync handler）
- 测试命令：
  ```bash
  cargo test -p arf-e2e --test dual_agent_peer -- --nocapture --test-threads=1
  ```
- 结果：**`3 passed; 0 failed; 2.72s`**
- 关键运行输出：
  ```
  test1: engine_a filter types: ["model_response", "peer_reply", "tool_result"]
  test1: F-011 确认：Engine filter 只看 response types，不收 peer_message
  test2: dispatch_incoming outcome: Handled, saw peer_reply on bus
  test3: dispatch_incoming calls: 2, handler_a count: 1, handler_b count: 1
  ```

### 凭据安全（self-check 已通过）

```bash
$ git grep -n 'sk-' -- crates/arf-e2e/tests/dual_agent_peer.rs   # 无输出
```

---

## §B (capability, 情景) 单元判定

### 单元 1：Engine filter 行为

```
单元              : engine_filter × §2.6
能力等级           : D（PASS，但有副作用）
判定依据          : cfg.engine.routes 含 "peer_message" → engine filter = [model_response, tool_result, peer_reply]
                   engine A 收 peer_reply 但不收 peer_message（filter 仅 response types）
file:line         : crates/arf-engine/src/engine.rs:69-74
                   crates/arf-engine/src/engine.rs:726-744 (engine_response_types)
                   ✓ routes key = request type → response type via response_msg_type_for
```

### 单元 2：MessageHandler for peer_message

```
单元              : message_handler × §2.6
能力等级           : C（Composable，但有阻塞陷阱）
判定依据          : engine.add_handler(Arc<dyn MessageHandler>, replace=true) 可注册 peer_message handler
                   engine.dispatch_incoming(msg) 手动调（Engine 不自动）
                   handler 内部发 peer_reply → bus 投递
file:line         : crates/arf-engine/src/engine.rs:139-147 (add_handler)
                   crates/arf-engine/src/engine.rs:172-185 (dispatch_incoming, blocking_lock)
                   ✓ 端到端 work，但 app 须用 block_in_place 包装（async 不允许 blocking_lock）
```

### 单元 3：A2A 协议 + 双向往返

```
单元              : peer_round_trip × §2.6
能力等级           : C（Composable，需 app 桥接）
判定依据          : PeerMessage::new(from_session, to_session, content) 构造
                   bus.send(Message{msg_type="peer_message", to=[engine_b_id]}) 送达
                   handler 解析 PeerMessage 构造 PeerReply，回 send
file:line         : crates/arf-core/src/message.rs:272-295 (PeerMessage)
                   crates/arf-core/src/message.rs:313-335 (PeerReply)
                   ✓ A↔B 双向往返（每个 engine handler 被调 1 次，dispatch_incoming 调 2 次）
```

---

## §C 探查产出汇总

| 单元 | 等级 | 判定依据 |
|---|---|---|
| `engine_filter × §2.6` | **D** | routes key → response type 映射 OK |
| `message_handler × §2.6` | **C** | add_handler + dispatch_incoming 端到端 work，但需 block_in_place |
| `peer_round_trip × §2.6` | **C** | A↔B 双向往返 OK，但需 app 端 bus.subscribe + dispatch_incoming 桥接 |

---

## §D 病灶登记

### 新增 F-lesion：F-011 — Engine 不自动 dispatch incoming ActionMessage 到 MessageHandler

**病灶 ID       : F-011**
**触发 task    : 9.9.2**
**触发探查    : test1/2/3（peer_message 端到端）**

**症状**：
Engine 注册了 `MessageHandler for "peer_message"` 后，bus 上定向送达的 peer_message **不**会被 Engine 自动 dispatch。app 必须自订阅 bus（`bus.subscribe()`），并手动调 `engine.dispatch_incoming(msg)` 把消息送给 handler。**Engine 缺自动派发循环**——handler 路径不简化 app 的 incoming message 处理。

**file:line 锚点**：
- `crates/arf-engine/src/engine.rs:69-74` — Engine filter 仅 `[model_response, tool_result, peer_reply, ...]`，**不含 peer_message**
- `crates/arf-engine/src/engine.rs:172-185` — `dispatch_incoming` 是 public API 但**不**被 Engine 内部任何循环调
- `crates/arf-engine/src/engine.rs:139-147` — `add_handler` 是 public API 但**不**被 Engine 内部任何循环调
- `crates/arf-engine/src/dispatcher.rs:85-100` — `HandlerRegistry::dispatch` 存在但**只**被 `Engine::dispatch_incoming` 调

**实际影响**：
1. **A2A 协议需 app 端"shim 桥接"**——每个 peer_message 都需 app 侧 bus.subscribe + 手动 dispatch（~30 行 glue code）
2. **Engine 主循环不处理 peer_message**——peer 是"二等公民"，与 model_response 不对等
3. **F-004 衍生**：原 F-004 是"chunks 缺 callback API"；F-011 进一步实证"所有 ActionMessage 都不自动派发"

**根因**：
Engine 设计只内置 `model_call` / `tool_exec` 两个主循环 action；其他 ActionMessage（peer/subagent/memory_op/human_handoff）都是**纯数据协议**，handler 路径设计但**无 main loop 调度**。

**修复建议**（不入本 task 范围）：
- Engine 主循环 add `ActionMessage dispatch tick`（每 turn 后扫所有订阅消息 → dispatch 到 handler）
- 或 EngineBuilder 接受 `with_auto_dispatch_handler(msg_type)` 注册后自动派发

**A4 信条命中**：handler 路径分散在"app subscribe + 手动 dispatch"两处，未集中在 Engine 一处。违反"同类处理在单一接缝完成"。

### 注意事项（潜在 issue，非 lesion）

1. **`add_handler` / `dispatch_incoming` 用 `blocking_lock`**（engine.rs:141/177）—— 在 async runtime 调会 panic，app 必须用 `tokio::task::block_in_place` 包装。这是 framework 设计选择（handler 路径在 sync context），但**文档未明示**，增加 app 学习成本。
2. **`MessageHandler::handle` 是 sync fn，不能 `await`**（dispatcher.rs:51）—— handler 内部发 bus 消息（async）需用 `std::thread::spawn` + 新建 runtime 的"土办法"（本测试的 workaround）。framework 应提供 `async fn` 的 `AsyncMessageHandler` trait 或 helper。
3. **handler 内部发 reply 的 from 必须是已 online 的 NodeId**——否则 `bus.send` 报 `NodeOffline`。本 task 探查时 test2 第一次失败就是因为 from = "external"（未注册）。**framework 隐式契约**应文档化。

---

## §E 探查回归

- 9.9.1 4 test pass
- 9.9.2 新增 3 test pass
- 综合：3 新 test，1 新 F-lesion（F-011）

---

## §F 与父 task / spec 的关系

| 父 task 期望 | 实证结果 |
|---|---|
| Engine filter 不收 peer_message | ✓ test1 实证（filter = [model_response, peer_reply, tool_result]） |
| app 需外部 subscriber 桥接 peer_message | ✓ test2 实证（手动 dispatch_incoming 触发 handler） |
| 双向往返 session_id 正确路由 | ✓ test3 实证（A↔B 各调 1 次） |

> 结论：9.9.2 探查显示 framework **提供 PeerMessage/PeerReply 协议 + handler 派发入口**，但 **app 端需 ~30 行 glue code 桥接**（C 级）。F-011 病灶（Engine 不自动 dispatch）解释了为何 handler 路径"看着对、用着烦"。

---

## §G 提交状态

- 工作目录：`crates/arf-e2e/tests/dual_agent_peer.rs`（~480 行，3 test cases）
- task doc：`docs/v1.x/phase9/task-9.9.2.md`（新增）
- audit probe：本 doc
- lesion-registry：**未变**（F-011 待 task 9.9.x 跑完统一登记）
- 待 commit + push
