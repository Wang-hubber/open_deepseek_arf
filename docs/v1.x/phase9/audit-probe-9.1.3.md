# audit-probe-9.1.3：Bus + multi-bus 拓扑（attach_to）探查

> Task 9.1.3 探查产出
> 父 task doc：`docs/v1.x/phase9/task-9.1.3.md`（commit `03dd01b`）
> 父 spec：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`（commit `8ad5247`）
> 前置：task 9.1.1 baseline / 9.1.2 多 Node 异构 均已 stable

---

## §A 探查环境

- working tree：HEAD `03dd01b`
- 测试命令：`cargo test -p arf-e2e --test multi_bus_attach -- --nocapture`
- 结果：`1 passed; 0 failed; finished in 1.21s`
- multi_bus_attach.rs 行数：92（含注释 + 2 helper + 3 探查点断言）
- 真实运行输出：
  ```
  worker_got={"task_b", "task_a"}
  sa_got={} sb_got={"ping_b"}
  ```

**拓扑**：单 worker Node 跨 2 个独立 Bus
- `worker/w1`：`bus_a.connect(filter=[task_a])`（primary）→ `attach_to(bus_b, filter=[task_b])`（attached）
- `sender/a` 接 bus_a（types=None）/ `sender/b` 接 bus_b（types=None）

---

## §B (capability, 情景) 单元判定

按父 spec §3.3 schema 填。

### 单元 1：multi_bus_attach × §2.0

```
单元              : multi_bus_attach × §2.0
能力等级           : D
判分依据           : `NodeHandle::attach_to`（connection.rs:167）公开 API 一次调用即让
                    同一 Node 接第二个 Bus，带独立 filter。app 层无 glue：
                    worker.attach_to(bus_b, filter_b) 返回 bus_b 的 BusId。
                    真实断言通过：
                    · worker.primary_bus_id()==bus_a.id（multi_bus_attach.rs:41）
                    · attach 返回 bid_b==bus_b.id（:42）
                    · recv() 跨两 subscription 汇聚（:67 got=={task_a,task_b}）
                    framework 接触点：
                    - attach_to (connection.rs:167-210)
                    - send_via 定向 (connection.rs:136-161)
                    - recv 跨 subscription 汇聚 (connection.rs:218-)
framework 行为   : 单 Node 跨 2 Bus 端到端由 framework 供。attach 后 subscriptions
                    含 [bus_a, bus_b]；send_via(bid_b) 精确落 bus_b；recv 汇聚两 Bus。
信号命中         : 见 §C（观察 G：connect/attach_to 注册逻辑重复，未匹配任一 signal 形态）
信号是否构成病灶   : N
影响面           : N/A
```

### 单元 2：bus_health_observe × §2.0（双 Bus 独立）

```
单元              : bus_health_observe × §2.0（双 Bus）
能力等级           : D
判分依据           : bus_a.graph() 与 bus_b.graph() 各返回**独立** BusGraph。
                    真实断言：ids_a=={worker/w1, sender/a}（multi_bus_attach.rs:54）
                    ids_b=={worker/w1, sender/b}（:55）——worker 同 NodeId 双 Bus 各占一席。
                    framework 接触点：
                    - Bus::graph (graph.rs:12)
                    - 每 Bus 独立 nodes map（BusCommand::Connect 各注册 lib.rs:456）
framework 行为   : 两 Bus 各维护独立 online-nodes map；同 NodeId 在两 graph 独立出现，
                    互不合并、互不干扰。sender_a 只现 bus_a、sender_b 只现 bus_b。
信号命中         : 无
信号是否构成病灶   : N/A
影响面           : N/A
```

### 单元 3：node_online_announcement × §2.0（跨 Bus）

```
单元              : node_online_announcement × §2.0（跨 Bus）
能力等级           : D
判分依据           : attach_to 内注册即向 bus_b 广播 node_online（connection.rs:177
                    BusCommand::Connect → lib.rs:81 doc 注 node_online 广播）；
                    bus_a 的 online 集合不因 worker attach 到 bus_b 而变化。
framework 行为   : worker attach 到 bus_b 只影响 bus_b 的 nodes map（bus_b graph 现
                    worker）；bus_a graph 保持 {worker, sender_a} 不变。跨 Bus online
                    通告互相隔离。self 不见自身 online（subscribe after registration，
                    connection.rs:186）。
信号命中         : 无
信号是否构成病灶   : N/A
影响面           : N/A
```

### 单元 4 / 5 / 6：不适用（本 task 范围内）

| 单元 | 等级 | 备注 |
|---|---|---|
| `barrier_sync × §2.0` | 不适用 | 未用 barrier（留 9.1.4） |
| `checkpoint_rules × §2.0` | 不适用 | 无 Engine |
| `heartbeat × §2.0` | 延续 9.1.1（D） | 双 Bus 各自 heartbeat，本 task 未专门断言 |

---

## §C §4 find signals 探查

按父 spec §4.2 各信条信号跑。重点：单 Node 跨多 Bus 引入 per-subscription 状态后是否触雷。

### A1 原子化

| Signal | 探查结果 | 命中 |
|---|---|---|
| A1-S1（方法名多职责） | `attach_to` / `send_via` / `subscriptions` 均单动词单职责，无 `and/or/with_xxx_and_yyy` | **未命中** |
| A1-S2（doc 多领域） | attach_to doc（connection.rs:163-166）单一领域（"attach handle to another Bus"） | **未命中** |
| A1-S3（trait ≥5 方法分多阶段） | 本 task 不新增 trait；Node trait 仍 4 方法 | **未命中** |

### A2 正交性

| Signal | 探查结果 | 命中 |
|---|---|---|
| A2-S1（cross-module 强依赖） | attach_to 仅依赖 arf-core 的 MessageFilter / NodeInfo（抽象契约），无跨 module 具体实现联动 | **未命中** |
| A2-S2（字段引用他 crate 具体类型） | Subscription 字段（bus_id / cmd_tx / inbound_rx / filter）为 bus 内部类型 + arf-core MessageFilter（抽象），无他 crate 具体实现耦合 | **未命中** |

### A3 数据唯一

| Signal | 探查结果 | 命中 |
|---|---|---|
| A3-S1（同名字段跨 crate） | per-subscription filter 存于 Subscription.filter（connection.rs 内），无跨 crate 重叠 | **未命中** |
| A3-S2（serde alias） | 未发现 | **未命中** |
| A3-S3（同名 struct 跨 crate） | `Subscription`（connection.rs:37）/ `NodeHandle`（:65）**仅** arf-bus 定义；多 subscription 只是 `Vec<Subscription>`，非同名 struct 跨 crate | **未命中** |
| A3-S4（同义不同形） | 未发现 | **未命中** |

### A4 处理集中

| Signal | 探查结果 | 命中 |
|---|---|---|
| A4-S1（filter 散落） | filter.matches 调用仍两处：lib.rs:434（计数）+ connection.rs:386（投递）——attach 场景**未新增**散落，per-subscription filter 各自复用同一 forward task 逻辑 | **未命中** |
| A4-S2（validate 散落） | 无 `fn validate` | **未命中** |
| A4-S3（permission 散落） | 无 tool / ToolPermission | **未命中** |
| A4-S4（convert 散落） | 无 `impl From` / `convert.rs` | **未命中** |

**§4 signals 总命中：0**。

---

## §D 观察记录（非病灶的 framework 行为）

### 观察 G — connect / attach_to 注册逻辑重复（DRY smell，未匹配 §4 signal 形态）

**触发位置**：`connection.rs:167-210`（attach_to）与 `connection.rs:411-458`（connect）
**观察现象**：两方法的节点注册四步**几乎逐行重复**：
1. register：`BusCommand::Connect { info, filter, respond_to }`（attach_to :177 / connect :421）
2. subscribe-after：`bus.subscribe_internal()` ×2（:187-188 / :432-433）
3. spawn forward：`tokio::spawn(spawn_forward_task(...))`（:193 / :437）
4. push Subscription（attach_to :201 追加 self.subscriptions / connect :445 构造 vec![primary_sub]）

唯一差异：connect 返回**新** `NodeHandle`，attach_to **追加**到既有 handle 并返回 `BusId`。
**判断**：这是 DRY 层面的 code smell，也贴近 A4"同类处理集中"的**精神**（"节点注册"这一处理散在两个接缝）。但 §4.2 的 A4 signals 是 filter / validate / permission / convert **具体动词**的散落——"register / subscribe / spawn"不在已定义 signal 形态内。**严格按 signal 定义：未命中**，故不登记病灶，仅记观察。
**是否构成病灶**：N（不匹配任一已定义 signal 形态）
**影响面**：当前无功能影响（两处行为一致，单测覆盖）。潜在关注：若未来出现第三种接入方式（如 `attach_to_pool`），注册逻辑将三处重复；若父 spec 后续为 A4 增补"lifecycle 注册散落"类 signal，此处会转为命中候选。留待 §4.4 探查回归跟踪。

### 观察 H — send_via 定向精确，不跨 Bus 泄漏

**触发位置**：`connection.rs:136-161`（send_via）
**观察现象**：`send_via` 按 `bus_id` 在 `self.subscriptions` 中 `find`（:143-147），仅经该 subscription 的 cmd_tx 发送。真实证据：worker.send_via(bid_b, "ping_b") 后 sa_got={}（bus_a 无泄漏）、sb_got={ping_b}（bus_b 收到）。
**判断**：framework 行为——多 Bus 下发送目标由 BusId 精确路由，subscription 隔离干净。
**是否构成病灶**：N
**影响面**：正向——单 Node 跨 Bus 时可精确控制每条消息落哪个 Bus，无隐式跨 Bus 广播。

### 观察 I — filter.matches 调用点跨 attach 无新增散落

**触发位置**：`lib.rs:434`（计数）+ `connection.rs:386`（投递）
**观察现象**：延续 9.1.2 观察 D。attach_to 复用同一 `spawn_forward_task`（含 :386 的 filter.matches），未在别处新增 filter 判定点。per-subscription filter 各异，但判定逻辑单一复用。
**判断**：A4 正向证据在 multi-bus 下保持——filter 处理集中未被 attach 打破。
**是否构成病灶**：N
**影响面**：与 9.1.2 观察 D 同。

---

## §E multi-bus 综合判定

- **多 Bus 行为闭合**：单 Node 跨 2 Bus，双 graph 独立、send_via 定向精确、per-subscription filter 各自生效、recv 汇聚。
- **信号命中**：0 项（multi-bus 范围内）。
- **观察记录**：3 项（G connect/attach_to 注册重复 / H send_via 定向精确 / I filter 判定无新增散落）。
- **首个 DRY smell 出现但不构成 signal 病灶**：观察 G 是 phase 9 至今第一个"代码重复"级观察，但诚实按 §4.2 signal 形态判定为未命中——记录留档，不夸大为病灶。
- **结论**：正交维度（多 Bus 单 Node）抽象边界仍洁净；9.1.4（barrier 多参与者）可继续累加。

---

## §F 验证命令

```bash
# 复现探查
git log --oneline -n 3
cargo test -p arf-e2e --test multi_bus_attach -- --nocapture

# 复现 §C 信号 grep
grep -rn 'pub struct Subscription\|pub struct NodeHandle' crates/arf-bus/src/
grep -rn 'filter.matches' crates/arf-bus/src/lib.rs crates/arf-bus/src/connection.rs

# 复现观察 G（connect / attach_to 注册重复）
grep -n 'BusCommand::Connect' crates/arf-bus/src/connection.rs   # :177 attach_to / :421 connect
grep -n 'spawn_forward_task' crates/arf-bus/src/connection.rs    # :193 attach_to / :437 connect
sed -n '167,210p' crates/arf-bus/src/connection.rs
sed -n '411,458p' crates/arf-bus/src/connection.rs
```

每条在 HEAD 上可直接复现。

---

## §G 下一步

- commit multi_bus_attach.rs（新 e2e test 文件）
- commit audit-probe-9.1.3.md
- push 到双 remote
- 进 task 9.1.4（Bus + barrier 多参与者）
