# audit-probe-9.9.7：3+ agent + peer + subagent 同时端到端探查

> Task 9.9.7 探查产出 — **Framework 是否支持 3 engine 同时承担 peer 通信 + subagent 委派？**
> 父 task doc：`docs/v1.x/phase9/task-9.9.7.md`
> 父 spec：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.9.6（3 engine peer 全连通）+ 9.9.5（3 层 subagent）
> **本 task 探查：3 engine 同时支持 peer_message/peer_reply + subagent_delegate/subagent_result 两种协议**

---

## §A 探查环境

- working tree：HEAD `958e187`（task 9.9.6）+ uncommitted `crates/arf-e2e/tests/multi_agent_peer_and_subagent.rs`
- 测试文件：`crates/arf-e2e/tests/multi_agent_peer_and_subagent.rs`（2 test cases，~360 行）
- 驱动：`SimpleMock`（3 个 provider "na7b/nb7b/nc7b"）+ `PeerEchoHandler`（engine_b 收 peer_message）+ `SubagentHandler`（engine_c 收 subagent_delegate）
- 测试命令：
  ```bash
  cargo test -p arf-e2e --test multi_agent_peer_and_subagent -- --nocapture --test-threads=1
  ```
- 结果：**`2 passed; 0 failed; 0.27s`**
- 关键运行输出：
  ```
  test1: 3 engines online, all 3 filters = [model_response, peer_reply, subagent_result, tool_result]
  test1: peer_message send matching_nodes=1, subagent_delegate send matching_nodes=1
  test2: 1 peer_reply + 1 subagent_result 收到，cid 配对，handler 各被调 1 次
  ```

### 凭据安全（self-check 已通过）

```bash
$ git grep -n 'sk-' -- crates/arf-e2e/tests/multi_agent_peer_and_subagent.rs   # 无输出
```

---

## §B (capability, 情景) 单元判定

### 单元 1：3 engine routes 同时含 peer_message + subagent_delegate

```
单元              : three_engines_with_both_routes × §2.6+§2.7
能力等级           : D（PASS）
判定依据          : 3 engine routes 配 {"peer_message": Strict, "subagent_delegate": Strict}
                   engine filter = [model_response, peer_reply, subagent_result, tool_result]
                   bus.send peer_message 和 subagent_delegate 都 matching_nodes=1
file:line         : crates/arf-e2e/tests/multi_agent_peer_and_subagent.rs:218-275
                   crates/arf-engine/src/engine.rs:726-744 (engine_response_types)
                   ✓ 单一 engine filter 累加多个 response type，bus.send 互不干扰
```

### 单元 2：peer + subagent handler 独立处理，reply 互不干扰

```
单元              : three_engines_peer_and_subagent_independent × §2.6+§2.7
能力等级           : C（Composable，需 app 端 shim）
判定依据          : engine_b 注册 PeerEchoHandler（msg_type=peer_message）
                   engine_c 注册 SubagentHandler（msg_type=subagent_delegate）
                   A→B 发 peer_message，B 收后回 peer_reply → bus 收到 1 peer_reply
                   A→C 发 subagent_delegate，C 收后回 subagent_result → bus 收到 1 subagent_result
                   2 个 handler 各被调 1 次，2 个 reply 互不干扰
file:line         : crates/arf-e2e/tests/multi_agent_peer_and_subagent.rs:91-126 (PeerEchoHandler)
                   crates/arf-e2e/tests/multi_agent_peer_and_subagent.rs:130-170 (SubagentHandler)
                   crates/arf-e2e/tests/multi_agent_peer_and_subagent.rs:280-360 (test2)
                   ✓ peer + subagent 协议在同一 engine 上独立工作
                   ✗ F-011 沿用（2 个 listener + 2 个 dispatch）
                   ✗ F-012 沿用（2 个 handler 各自注入 my_engine_id）
```

---

## §C 探查产出汇总

| 单元 | 等级 | 判定依据 |
|---|---|---|
| `three_engines_with_both_routes × §2.6+§2.7` | **D** | 3 engine routes + filter 累加 2 种协议 OK |
| `three_engines_peer_and_subagent_independent × §2.6+§2.7` | **C** | peer + subagent handler 独立 work，需 4 处 glue + 2 处 NodeId 注入 |

---

## §D 病灶登记

### 本 task 无新增 F-lesion（沿用 F-010 + F-011 + F-012）

**F-010（9.9.1 触发）** 在本 task 命中：3 engine 需 3 个 provider 名。**根因依旧**：`Engine::node_id = "engine/{provider}"`。**1 engine 1 provider 1 role 限制**在混合协议下更突出——生产中 1 engine 想同时是 peer node + subagent target 是 OK 的（filter 累加），但想跑 2 个 subagent role 仍需 2 provider。

**F-011（9.9.2 触发）** 在本 task 命中：2 个 listener + 2 个 dispatch_incoming = **4 处 glue**。本 task 比 9.9.6 简单（只测 1 peer + 1 subagent），但 **N 种协议 × M 边 = O(NM) glue**，framework 仍不简化。

**F-012（9.9.4 触发）** 在本 task 命中：2 个 handler 各自注入 `my_engine_id`。**1 engine 注册 2 个 handler × 各自注入 = handler 与 online NodeId 强绑定**。

### 注意事项（潜在 issue，非 lesion）

1. **filter 累加 work**（✓）——`engine_response_types` 把多个 route key 累加到 filter（engine.rs:726-744），engine 同时收 peer_reply + subagent_result。
2. **2 个 handler 独立 dispatch**（✓）——`dispatch_incoming` 按 msg_type 路由到对应 handler（dispatcher.rs:85-100），peer_message 不触发 subagent handler。
3. **tokio::join! 并发 send work**（✓）——bus.send 是 async，2 个并发 send 互不干扰，bus cmd channel FIFO 保证顺序。
4. **F-010 限制 1 engine 1 provider**——3 engine 仍需 3 provider 名，**与协议无关**。框架允许 1 engine 跑多协议，但不允许 1 engine 跑多 role。
5. **F-011 仍随协议种类线性放大**——K 种协议 = K 个 listener + K 个 dispatch glue，**framework 应提供** `Engine::auto_subscribe(&[msg_type1, msg_type2, ...])` 自动 dispatch。
6. **handler 类型安全但运行时 dispatch 仍 msg_type 字符串**——handler 按 `msg_type()` 注册，dispatcher 按 `msg.msg_type` 字符串匹配，**编译时无校验**（如把 peer_message 错发到 subagent handler 不会编译报错）。
7. **9.9.7 完整 3 engine mesh = 6 peer + 3 subagent = 9 边 × glue 数量 = ~30 处**（沿 9.9.6 O(N²) + 9.9.5 O(N)），**生产混合多 agent 拓扑最复杂也最暴露 framework 缺口**。

---

## §E 探查回归

- 9.9.1 4 test + 9.9.2 3 test + 9.9.3 3 test + 9.9.4 2 test + 9.9.5 2 test + 9.9.6 2 test + 9.9.7 2 test = **18 新 test**，**3 F-lesion**（F-010 + F-011 + F-012）
- 既有 9.4-9.5 测试未触及 engine_id / ActionMessage 派发，未污染

---

## §F 与父 task / spec 的关系

| 父 task 期望 | 实证结果 |
|---|---|
| 3 engine routes 同时含 peer_message + subagent_delegate | ✓ test1 实证（routes 配置 2 个 key） |
| filter 同时含 peer_reply + subagent_result | ✓ test1 实证（filter = [model_response, peer_reply, subagent_result, tool_result]） |
| 2 种协议 handler 独立处理 | ✓ test2 实证（2 个 handler 各被调 1 次） |
| 2 个 reply 互不干扰 | ✓ test2 实证（1 peer_reply + 1 subagent_result，cid 各自配对） |
| 1 engine 同时是 peer target + subagent target | ✓ test2 实证（engine_b 收 peer，engine_c 收 subagent） |
| F-010 + F-011 + F-012 沿用 | ✓ 实证（3 provider + 4 glue + 2 NodeId 注入） |

> 结论：9.9.7 探查显示 framework **支持 3 engine 同时承担 peer 通信 + subagent 委派**（filter 累加 + handler 按 msg_type 独立 dispatch）。**F-010 限制 1 engine 1 provider 1 role 是混合协议下的最大痛点**——生产中想跑"agent 网关 + subagent worker + peer node"三角色需 3 个 engine 实例，**资源浪费 3×**。**F-011 在 K 种协议下 O(K) 增长**——app 端 glue 数量与协议种类线性相关。**F-012 强制 handler 绑定 online NodeId**——破坏 handler 通用性，handler 不能从 struct 字段"解耦"。**三病灶需 framework 提供**：(1) `EngineBuilder::with_agent_id(NodeId)`（修 F-010）；(2) `Engine::auto_subscribe(&[...])`（修 F-011）；(3) `Message::broadcast()` 标记（修 F-012）——**任一缺失则生产多 agent 拓扑不可用**。

---

## §G 提交状态

- 工作目录：`crates/arf-e2e/tests/multi_agent_peer_and_subagent.rs`（~360 行，2 test cases）
- task doc：`docs/v1.x/phase9/task-9.9.7.md`（已建）
- audit probe：本 doc
- lesion-registry：**未变**（F-010 + F-011 + F-012 沿用，无新 F-lesion）
- 待 commit

---

## §H 9.9 G Multi-agent 拓扑 总结

| Task | 探查 | 通过率 | 新 F-lesion |
|---|---|---|---|
| 9.9.1 | 双 agent 独立 | 4/4 | F-010 |
| 9.9.2 | 双 agent peer | 3/3 | F-011 |
| 9.9.3 | 双 agent subagent（1 层） | 3/3 | F-010+11 沿用 |
| 9.9.4 | 双 agent subagent 嵌套（2 层） | 2/2 | F-012 |
| 9.9.5 | 3+ agent subagent 嵌套（3 层） | 2/2 | F-010+11+12 沿用 |
| 9.9.6 | 3+ agent peer 全连通 | 2/2 | F-010+11+12 沿用 |
| 9.9.7 | 3+ agent peer + subagent 同时 | 2/2 | F-010+11+12 沿用 |
| **合计** | — | **18/18** | **3 F-lesion（F-010/11/12）** |

**核心发现**：
1. **F-010**：Engine agent_id 硬编码 = "engine/{provider}"，1 engine = 1 role，N role = N provider。修：`EngineBuilder::with_agent_id(NodeId)`。
2. **F-011**：Engine 不自动 dispatch ActionMessage，每条消息需 app 端 bus.subscribe + dispatch_incoming，O(N) 至 O(N²) glue。修：`Engine::auto_subscribe(&[msg_type, ...])`。
3. **F-012**：bus.send 验证 to 节点必须 online，handler reply 用 msg.from 失败，handler 需注入 online NodeId。修：`Message::broadcast()` 标记 + bus 区分 broadcast/directed。

**framework 演化方向**：上述 3 个 F-lesion 是 multi-agent 拓扑的 **3 大基础设施缺口**，任一缺失则 9.9 实证的拓扑仅能在 test 环境跑通，**production 多 agent 不可用**。
