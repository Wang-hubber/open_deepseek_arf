# audit-probe-9.9.6：3+ agent + peer 全连通端到端探查

> Task 9.9.6 探查产出 — **Framework 是否支持 3 engine 同 bus 上的全连通 peer 拓扑（A↔B, A↔C, B↔C 共 6 条边）？**
> 父 task doc：`docs/v1.x/phase9/task-9.9.6.md`
> 父 spec：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.9.2（双 agent peer）
> **本 task 探查：3 engine 全连通 peer 拓扑 + 6 条 peer 边 + 6 个 peer_reply 端到端**

---

## §A 探查环境

- working tree：HEAD `094ef32`（task 9.9.5）+ uncommitted `crates/arf-e2e/tests/multi_agent_peer_fully_connected.rs`
- 测试文件：`crates/arf-e2e/tests/multi_agent_peer_fully_connected.rs`（2 test cases，~350 行）
- 驱动：`SimpleMock`（3 个 provider "na6b/nb6b/nc6b"）+ `PeerEchoHandler`（3 engine 各注册一份，注入自己的 online agent_id）
- 测试命令：
  ```bash
  cargo test -p arf-e2e --test multi_agent_peer_fully_connected -- --nocapture --test-threads=1
  ```
- 结果：**`2 passed; 0 failed; 0.61s`**
- 关键运行输出：
  ```
  test1: 3 engines online, all 3 filters 含 peer_reply, 6 bus.send 全部 matching_nodes=1
  test2: 6 peer_reply 收到，6 个 cid 全配对，每个 engine handler 被调 2 次
  ```

### 凭据安全（self-check 已通过）

```bash
$ git grep -n 'sk-' -- crates/arf-e2e/tests/multi_agent_peer_fully_connected.rs   # 无输出
```

---

## §B (capability, 情景) 单元判定

### 单元 1：3 engine 全连通 peer 拓扑能搭

```
单元              : three_engines_fully_connected_peers × §2.6
能力等级           : D（PASS）
判定依据          : 3 个 EngineBuilder.build() 启动 3 engine (provider na6/nb6/nc6)
                   bus graph 在线 engine 数 = 3
                   3 engine filter 都含 peer_reply（routes 含 peer_message）
                   6 条 peer 边 (A↔B, A↔C, B↔C) 全部 bus.send OK
file:line         : crates/arf-e2e/tests/multi_agent_peer_fully_connected.rs:233-290
                   crates/arf-engine/src/engine.rs:726-744 (engine_response_types)
                   ✓ 3 engine 同 bus 共存，6 条 peer 边可定向
```

### 单元 2：6 条 peer 边全部能 peer_reply

```
单元              : three_engines_bidirectional_peer_reply × §2.6
能力等级           : C（Composable，需 app 端 shim × 6）
判定依据          : 6 条 peer 边，每条 1 个 PeerMessage + 1 个 listener + 1 个 dispatch_incoming
                   每条边的 target engine 注册 PeerEchoHandler 收后回 PeerReply
                   6 个 PeerReply 全部回到 source engine，6 个 cid 端到端匹配
                   每个 engine handler 被调 2 次（A→B + C→B = engine_b 收 2 次）
file:line         : crates/arf-e2e/tests/multi_agent_peer_fully_connected.rs:91-128 (PeerEchoHandler)
                   crates/arf-e2e/tests/multi_agent_peer_fully_connected.rs:295-420 (test2)
                   ✓ 6 条 peer 边 + 6 个 reply 端到端 OK
                   ✗ F-012 沿用：PeerEchoHandler 需注入 my_engine_id（online NodeId）
                   ✗ F-011 沿用：6 处 dispatch_incoming 手动调
```

---

## §C 探查产出汇总

| 单元 | 等级 | 判定依据 |
|---|---|---|
| `three_engines_fully_connected_peers × §2.6` | **D** | 3 engine + 6 peer 边端到端 OK |
| `three_engines_bidirectional_peer_reply × §2.6` | **C** | 6 条边 + 6 个 reply work，需 6 处 glue + 3 处 NodeId 注入 |

---

## §D 病灶登记

### 本 task 无新增 F-lesion（沿用 F-010 + F-011 + F-012）

**F-010（9.9.1 触发）** 在本 task 命中：3 engine 需用 3 个不同 provider 名（"na6"/"nb6"/"nc6"）才能在同 bus 共存。**根因依旧**：`Engine::node_id = "engine/{provider}"`（engine.rs:59）。

**F-011（9.9.2 触发）** 在本 task 严重放大：6 条 peer 边 = **6 次 bus.subscribe + 6 次 dispatch_incoming**（每条边 1 listener + 1 dispatch）+ 3 个 PeerEchoHandler 手动注册 = **15 处 app glue code**。**N engine 全连通 = N(N-1) 条边 = N(N-1) 处 glue**。3 engine = 6 条，4 engine = 12 条，5 engine = 20 条——**生产多 agent 网格不可 scale**。

**F-012（9.9.4 触发）** 在本 task 继续命中：PeerEchoHandler 必须注入 `my_engine_id`（online NodeId）。3 engine = **3 处 handler 注入**，**每个 handler 需知道自己的 online agent_id**——peer 协议本身是去中心化的（A2A），但 F-012 强制要求 handler 单边绑定。

### 注意事项（潜在 issue，非 lesion）

1. **3 engine 全连通 6 条边全 work**（✓）——framework 协议层支持全连通拓扑。
2. **每条边 1 个 cid，6 个 cid 端到端唯一**（✓）——correlation_id 路由正交。
3. **每个 engine handler 被调 2 次**（✓）——3 engine 全连通下每个 engine 是其他 2 个 engine 的目标，handler count = N-1 = 2。
4. **F-011 在 N engine 全连通下 O(N²) 增长**——3 engine 15 处 glue，10 engine ≈ 90 处 glue。**framework 必须提供** `Engine::auto_subscribe(peer_message)` 自动 dispatch。
5. **F-012 与 peer 协议去中心化语义冲突**——A2A 协议假设 handler 不知道自己的 id（msg.from 自动），F-012 强制 handler 注入 my_engine_id 破坏对称性。
6. **3 engine filter 都含 peer_reply**（✓）——routes 配置无歧义，peer_message ↔ peer_reply 映射统一。
7. **6 条 peer 边的 dispatch 顺序**与发送顺序一致（test2 输出可见 A→B, A→C, B→A, B→C, C→A, C→B）——broadcast 顺序由 bus cmd channel FIFO 保证。

---

## §E 探查回归

- 9.9.1 4 test + 9.9.2 3 test + 9.9.3 3 test + 9.9.4 2 test + 9.9.5 2 test + 9.9.6 2 test = **16 新 test**，**3 F-lesion**（F-010 + F-011 + F-012）
- 既有 9.4-9.5 测试未触及 engine_id / ActionMessage 派发，未污染

---

## §F 与父 task / spec 的关系

| 父 task 期望 | 实证结果 |
|---|---|
| 3 engine 全连通（6 条 peer 边）能搭 | ✓ test1 实证（6 条 bus.send 全部 matching_nodes=1） |
| 每条 peer 边能 peer_reply | ✓ test2 实证（6 个 reply 全收到，6 个 cid 配对） |
| 6 个 reply 回到 source engine | ✓ test2 实证（reply.to 包含 source engine_id） |
| correlation_id 路由正交 | ✓ test2 实证（6 个 cid 互不相同且都收到） |
| F-010 + F-011 + F-012 沿用 | ✓ 实证（3 provider 名 + 15 处 glue + 3 处 NodeId 注入） |

> 结论：9.9.6 探查显示 framework **支持 N engine 全连通 peer 拓扑 + 端到端 correlation_id 路由**，但 **F-011 病灶在 N engine 全连通下 O(N²) 放大**——N=3 时 15 处 glue，N=10 时 90 处 glue，**生产多 agent mesh 不可用**。**F-012 与 A2A 协议去中心化语义冲突**——peer 协议假设 handler 对称，但 framework 强制 handler 单边绑定。**F-010 限制 1 engine = 1 provider = 1 role**——生产中无法用 1 engine 跑 2 个 peer role。

---

## §G 提交状态

- 工作目录：`crates/arf-e2e/tests/multi_agent_peer_fully_connected.rs`（~350 行，2 test cases）
- task doc：`docs/v1.x/phase9/task-9.9.6.md`（已建）
- audit probe：本 doc
- lesion-registry：**未变**（F-010 + F-011 + F-012 沿用，无新 F-lesion）
- 待 commit
