# audit-probe-9.1.1：Bus + 单一 Node + heartbeat baseline 探查

> Task 9.1.1 探查产出
> 父 task doc：`docs/v1.x/phase9/task-9.1.1.md`（commit `299b23b`）
> 跑通 commit：见 git log（baseline_bus.rs 新增 commit）

---

## §A 探查环境

- working tree：HEAD `299b23b`
- 测试命令：`cargo test -p arf-e2e --test baseline_bus -- --nocapture`
- 结果：`1 passed; 0 failed; finished in 0.80s`
- baseline_bus.rs 行数：58（含 8 行注释 + 14 行断言）

---

## §B (capability, 情景) 单元判定

按父 spec §3.3 schema 填。

### 单元 1：bus_health_observe × §2.0

```
单元              : bus_health_observe × §2.0
能力等级           : D
判分依据           : `Bus::graph()` 在 `crates/arf-bus/src/graph.rs:12` 公开方法返回 `BusGraph`
                    ；baseline 跑后调用获得 1 个 Node（self）+ message_count=0 + uptime_ms≥500。
                    framework 接触点：
                    - Bus::graph (graph.rs:12)
                    - BusGraph 结构 (arf-core/src/lib.rs:165-169): nodes + message_count + uptime_ms
                    - baseline_test 调用 bus.graph() (baseline_bus.rs:40)
framework 行为   : 1 个 Node 在线 / 0 message / uptime 800ms 后 ≥500ms。
                    语义正常：self-node 在线，message_count 反映 Bus::SendCommand 次数（baseline 无 send）。
信号命中         : 无
信号是否构成病灶   : N/A
影响面           : N/A
```

### 单元 2：heartbeat × §2.0

```
单元              : heartbeat × §2.0
能力等级           : D
判分依据           : heartbeat 协议由 `crates/arf-bus/src/heartbeat.rs` 实现；
                    `handle_heartbeat_tick` 周期性 broadcast `heartbeat_request`。
                    Node 接入后 forwarding task 自动 ack（NodeHandle.rs 转发逻辑）。
framework 行为   : 跑通后 800ms 内无 panic / lag，self-node 仍在线（说明 heartbeat 未超时剔除）。
                    探查附带发现：**heartbeat tick 不增加 message_count**（详见 §D 观察 A）
信号命中         : 无
信号是否构成病灶   : N/A
影响面           : N/A
```

### 单元 3：node_online_announcement × §2.0

```
单元              : node_online_announcement × §2.0
能力等级           : D
判分依据           : `Bus::connect` (connection.rs:411) 注册时广播 `node_online` 消息；
                    自连接 Node 看不到自己的 online（forwarding 任务 subscribe 时机保证）。
framework 行为   : bus.graph().nodes 包含 self-node（1 个节点）。online_since 字段本次未严格断言
                    （构造时 0，但 Bus 在 Connect 时如何覆盖 online_since 字段需核 Step 2 文件）。
信号命中         : 无
信号是否构成病灶   : N/A
影响面           : N/A
```

### 单元 4 / 5 / 6：不适用（baseline 范围内）

| 单元 | 等级 | 备注 |
|---|---|---|
| `multi_bus_attach × §2.0` | 不适用 | 本 task 单 bus；不调用 NodeHandle.attach_to |
| `barrier_sync × §2.0` | 不适用 | barrier 需 ≥ 2 参与者 |
| `checkpoint_rules × §2.0` | 不适用 | 无 Engine，无 CheckpointRule |

---

## §C §4 find signals 探查

按父 spec §4.2 各信条信号跑（即使 baseline 也全跑）。

### A1 原子化

| Signal | 探查结果 | 命中 |
|---|---|---|
| A1-S1（方法名暗示多职责） | 扫 `crate::Node` trait 各方法名（id / snapshot / restore / on_message），无 `and / or / with_xxx_and_yyy` 模式 | **未命中** |
| A1-S2（doc comment 多领域） | Node trait doc 与 Bus struct doc 均单一领域 | **未命中** |
| A1-S3（trait ≥ 5 方法分多阶段） | Node trait **只有 4 方法**（id / snapshot / restore / on_message），跨 4 阶段（run / persist / start / run）；方法数 < 5，未达阈值 | **未命中**（边缘观察：4 个方法分 4 阶段，结构清晰，反而是 A1 正面证据） |

### A2 正交性

| Signal | 探查结果 | 命中 |
|---|---|---|
| A2-S1（cross-module 强依赖） | baseline 范围内 `heartbeat.rs` / `graph.rs` / `connection.rs` 都 `use crate::{Bus, ...}`；crate 内 use 不算跨 module 强依赖 | **未命中** |
| A2-S2（字段交叉引用其他 crate 具体类型） | baseline 范围内未发现该模式 | **未命中** |

### A3 数据唯一

| Signal | 探查结果 | 命中 |
|---|---|---|
| A3-S1（同名字段跨 crate 重叠） | baseline 范围内：`Message.msg_type` / `from` / `to` 仅在 arf-core（line 71/73/75）；arf-bus 范围内未重复 | **未命中** |
| A3-S2（serde alias） | baseline 探查范围内未发现 `#[serde(alias=...)]` | **未命中** |
| A3-S3（同名 struct 跨 crate） | baseline 范围：`pub struct Bus` 在 arf-bus（line 102）；`pub struct BusGraph` / `NodeInfo` 仅在 arf-core；无同名 struct 在 ≥ 2 crate | **未命中** |
| A3-S4（同义不同形） | baseline 范围内未发现该模式 | **未命中** |

### A4 处理集中

| Signal | 探查结果 | 命中 |
|---|---|---|
| A4-S1（filter 散落） | `MessageFilter::matches` 在 `arf-core/src/lib.rs:214` 一处；`arf-bus/src/filter.rs:3` 注释确认（"is defined in arf-core"），filter.rs 是测试 | **未命中**（集中） |
| A4-S2（validate 散落） | baseline 范围内未发现 `fn validate` | **未命中** |
| A4-S3（permission 散落） | baseline 范围内无 tool / ToolPermission | **未命中** |
| A4-S4（convert 散落） | baseline 范围内未发现 `impl From` / `convert.rs` | **未命中** |

---

## §D 观察记录（信号命中 N 项 + 非信号探查的 framework 行为）

### 观察 A — heartbeat 不增加 message_count

**触发位置**：`crates/arf-bus/src/lib.rs:446`
**观察现象**：`message_count.fetch_add(1)` 仅在 `BusCommand::Send` 分支；heartbeat / Connect / Disconnect 都不增。
**判断**：framework 行为 — message_count 语义是"实际通过 BusCommand::Send 广播的消息数"，heartbeat / Connect 是隐式 lifecycle 消息，不计数。
**是否构成病灶**：N
**影响面**：对用户的影响 — 任何探查 "总消息流量" 的诊断工具会把 heartbeat 等隐式消息算入；但 BusGraph.message_count 命名清楚，是"显式 send 计数"。

### 观察 B — Node trait 4 方法分 4 阶段（edge case）

**触发位置**：`crates/arf-core/src/node.rs:231-267`
**观察现象**：Node trait 4 个方法（id / snapshot / restore / on_message）分布在 init（id）/ persist（snapshot/restore）/ run（on_message）4 阶段。
**判断**：当前方法数 = 4 < A1-S3 阈值（≥ 5）；未达 signal 阈值。是 A1-S3 边缘观察。
**是否构成病灶**：N（信号未达阈值）
**影响面**：未来若 Node trait 增加方法（如 metrics / name / is_alive），方法数破 5 时 A1-S3 命中 — 关注。

---

## §E baseline 综合判定

- **bus 行为闭合**：baseline 跑通，self-node 在线，heartbeat 不 panic。
- **信号命中**：0 项（baseline 范围内）。
- **观察记录**：2 项（A 隐式消息不计数 / B Node trait 边缘）。
- **framework 信号总体干净**：无任何信号超过阈值进入病灶。
- **结论**：baseline 稳，phase 9 后续 54 个 task 可在此基础上累加复杂度。

---

## §F 验证命令

```bash
# 复现探查
git log --oneline -n 3
cargo test -p arf-e2e --test baseline_bus

# 复现 §C 信号 grep
grep -n 'fn on_message\|fn snapshot\|fn restore\|fn id' crates/arf-core/src/node.rs
grep -rn 'pub struct Bus\b' crates/arf-bus/src/lib.rs
grep -rn 'pub struct BusGraph\|pub struct NodeInfo' crates/arf-core/src/lib.rs
grep -n 'MessageFilter::matches' crates/arf-core/src/lib.rs
grep -n 'message_count.fetch_add' crates/arf-bus/src/lib.rs
```

每条在 HEAD 上可直接复现。

---

## §G 探查用时

- 4 步流程：约 1.5h
- 信号 grep / 真实行为 trace：约 0.5h
- 文档：约 0.5h
- **总**：~2.5h（实操低于 task doc 估时 0.5d = 4h）

---

## §H 下一步

- commit audit-probe-9.1.1.md + baseline_bus.rs（新 e2e test 文件）
- push 到双 remote
- 进 task 9.1.2（Bus + 多 Node 异构 node_type）
