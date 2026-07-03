# audit-probe-9.1.2：Bus + 多 Node 异构 baseline+1 探查

> Task 9.1.2 探查产出
> 父 task doc：`docs/v1.x/phase9/task-9.1.2.md`（commit `78f523b`）
> 父 spec：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`（commit `8ad5247`）
> 前置：task 9.1.1 baseline 已 stable（audit-probe-9.1.1.md）

---

## §A 探查环境

- working tree：HEAD `b16e146`
- 测试命令：`cargo test -p arf-e2e --test multi_node_heterogeneous -- --nocapture`
- 结果：`1 passed; 0 failed; finished in 1.00s`
- multi_node_heterogeneous.rs 行数：110（含注释 + drain helper + 断言）
- 真实运行输出：
  ```
  engine_got=["model_call"]
  mcp_got=["node_online", "model_call", "model_response"]
  model_got=["model_response"]
  ```

**拓扑**：同一 `Bus` 接 3 个异构 Node
- `engine/main`（node_type=`engine`, filter types=`[model_call, tool_exec]`, ToMatch::All）
- `mcp/fs`（node_type=`mcp`, filter types=`None`, ToMatch::All）
- `model/primary`（node_type=`model`, filter types=`[model_response, model_response_chunk]`, ToMatch::All）

---

## §B (capability, 情景) 单元判定

按父 spec §3.3 schema 填。

### 单元 1：bus_health_observe × §2.0（多 Node）

```
单元              : bus_health_observe × §2.0（多 Node）
能力等级           : D
判分依据           : `Bus::graph()`（crates/arf-bus/src/graph.rs:12）在 3 Node 接入后
                    返回 3 个 NodeInfo。断言 g.nodes.len()==3 且 node_type 集合
                    =={engine,mcp,model} 通过。capabilities 字段各 Node 独立
                    （engine=sessions / mcp=tools / model=models）。
                    framework 接触点：
                    - Bus::connect (connection.rs:411) ×3
                    - Bus::graph (graph.rs:12)
                    - BusGraph.nodes (arf-core/src/lib.rs:165)
                    - test 断言 (multi_node_heterogeneous.rs:71-80)
framework 行为   : graph() 聚合全部 3 个 online Node，无丢失 / 无重复；
                    各 NodeInfo.node_type 与 capabilities 保持 connect 时原值，互不串扰。
信号命中         : 无
信号是否构成病灶   : N/A
影响面           : N/A
```

### 单元 2：node_online_announcement × §2.0（多 Node）

```
单元              : node_online_announcement × §2.0（多 Node）
能力等级           : D
判分依据           : connect 时广播 node_online（connection.rs:407 doc + :510 测试证）。
                    多 Node 下真实观察：mcp（types=None）收到 1 条 node_online，
                    engine / model（type 白名单）挡掉 node_online。
framework 行为   : mcp_got 含 "node_online"（来自 model/primary connect——mcp 在 model
                    之前 connect 且 subscribe 在先，故收到 model 的 online 广播）；
                    engine（先于 mcp connect）看不到 mcp / model 的 online，因其 filter
                    types 白名单不含 node_online；self 不见自身 online（subscribe after
                    registration，connection.rs:186 / :407）。
信号命中         : 无
信号是否构成病灶   : N/A
影响面           : N/A
```

### 单元 3：route_resolution（type filter 隔离）× §2.0

> 情景表 §2 未单列此 capability（属 §2.2+ 的 route_resolution delta 前身），
> 但多 Node broadcast 天然触发 filter 路由，故本 task 顺带探查并记录。

```
单元              : route_resolution（type filter 隔离）× §2.0
能力等级           : D
判分依据           : broadcast 消息经 per-subscription forward task 的 filter.matches
                    过滤（connection.rs:386），type 白名单精确隔离。
                    真实观察：engine.send(model_call) + model.send(model_response) 后——
                    · engine 收 [model_call]（白名单含），不收 model_response
                    · mcp（None）收 [node_online, model_call, model_response] 全量
                    · model 收 [model_response]，不收 model_call
                    framework 接触点：
                    - MessageFilter::matches (arf-core/src/lib.rs:214)
                    - forward task 投递过滤 (connection.rs:386)
                    - Bus dispatch 计数（非投递）(lib.rs:434)
framework 行为   : type filter 逐 Node 独立生效，白名单外类型精确挡掉；无跨 Node 串漏。
信号命中         : 见 §C（A4-S1 边缘观察，未命中）
信号是否构成病灶   : N
影响面           : N/A
```

### 单元 4 / 5 / 6：不适用（本 task 范围内）

| 单元 | 等级 | 备注 |
|---|---|---|
| `multi_bus_attach × §2.0` | 不适用 | 单 bus；attach_to 留给 9.1.3 |
| `barrier_sync × §2.0` | 不适用 | 未用 barrier |
| `checkpoint_rules × §2.0` | 不适用 | 无 Engine |

---

## §C §4 find signals 探查

按父 spec §4.2 各信条信号跑。重点：baseline（9.1.1）未触雷的 signals 在多 Node 累加后是否仍干净。

### A1 原子化

| Signal | 探查结果 | 命中 |
|---|---|---|
| A1-S1（方法名多职责） | NodeHandle 方法（send / send_via / attach_to / recv / try_recv / disconnect / barrier_ack）均单动词单职责，无 `and/or/with_xxx_and_yyy` | **未命中** |
| A1-S2（doc 多领域） | MessageFilter / NodeHandle doc 均单一领域 | **未命中** |
| A1-S3（trait ≥5 方法分多阶段） | 本 task 不新增 trait；Node trait 仍 4 方法（见 9.1.1 观察 B） | **未命中** |

### A2 正交性

| Signal | 探查结果 | 命中 |
|---|---|---|
| A2-S1（cross-module 强依赖） | connection.rs / lib.rs / graph.rs 均 `use crate::…`；crate 内 use 不算跨 module 强依赖 | **未命中** |
| A2-S2（字段引用他 crate 具体类型） | NodeHandle 字段引用 arf-core 的 NodeInfo / MessageFilter——但这是**抽象数据契约**（core 定义、bus 消费），非跨 crate 具体实现耦合 | **未命中** |

### A3 数据唯一

| Signal | 探查结果 | 命中 |
|---|---|---|
| A3-S1（同名字段跨 crate） | 多 Node 下 NodeInfo 字段（node_id / node_type / capabilities / online_since）仅在 arf-core:149 定义 | **未命中** |
| A3-S2（serde alias） | 探查范围内未发现 `#[serde(alias=…)]` | **未命中** |
| A3-S3（同名 struct 跨 crate） | `NodeHandle` / `Subscription` 仅 arf-bus（connection.rs:65/37）；`NodeInfo` / `BusGraph` / `NodeId` 仅 arf-core（lib.rs:149/165/40）。**无同名 struct 跨 ≥2 crate** | **未命中** |
| A3-S4（同义不同形） | 未发现 | **未命中** |

### A4 处理集中

| Signal | 探查结果 | 命中 |
|---|---|---|
| A4-S1（filter 散落） | `MessageFilter::matches` 逻辑**唯一定义** arf-core/src/lib.rs:214。调用两处：(a) lib.rs:434 = broadcast 的 `matching_nodes` **计数**（非投递）；(b) connection.rs:386 = forward task **投递过滤**。用途不同、逻辑单一——是"处理集中"的**正面证据**，非散落 | **未命中**（边缘观察见 §D 观察 D） |
| A4-S2（validate 散落） | 探查范围内无 `fn validate` | **未命中** |
| A4-S3（permission 散落） | 无 tool / ToolPermission | **未命中** |
| A4-S4（convert 散落） | 未发现 `impl From` / `convert.rs` | **未命中** |

---

## §D 观察记录（非病灶的 framework 行为）

### 观察 C — self-delivery：Node 收到自己发的 broadcast

**触发位置**：`crates/arf-bus/src/connection.rs:363-397`（spawn_forward_task）
**观察现象**：forward task 只挡 `heartbeat_request`（:378）与 filter 不匹配项（:386），**不排除 sender 自身**。故 engine 收到自己发的 `model_call`、model 收到自己发的 `model_response`（因各自 filter 白名单含之）。
**判断**：framework 行为——broadcast 是全总线扇出，sender 自身也在 subscriber 集合内；是否消费自己的消息完全由该 Node 的 filter 决定，而非 sender 身份。语义一致（CAN-bus 广播模型）。
**是否构成病灶**：N
**影响面**：若上层（Engine）不希望消费自身广播，须靠 filter 的 `ToMatch` / type 白名单排除，或在 handler 内按 `msg.from == self.node_id` 自过滤——framework 不代劳。当前无 Engine 场景，无实际影响。

### 观察 D — `matches` 谓词双用途（计数 vs 投递）

**触发位置**：`crates/arf-bus/src/lib.rs:434`（计数）与 `crates/arf-bus/src/connection.rs:386`（投递）
**观察现象**：同一 `MessageFilter::matches`（arf-core:214）被两处调用：Bus 中央 dispatch 用它统计 `SendReceipt.matching_nodes`（不影响实际投递，广播 :445 无条件扇出）；per-subscription forward task 用它做真实投递决策。
**判断**：逻辑定义单一（A4 正面），两处是合法复用同一谓词服务两个目的。非双重过滤、非散落。
**是否构成病灶**：N
**影响面**：`matching_nodes` 计数与实际投递用同一 filter 判据，保证 receipt 与投递语义一致（不会出现"报告匹配 N 个但实际投递 M 个"的偏差）。属正向一致性。

### 观察 E — 隐式 node_online 对 types=None Node 可见

**触发位置**：`crates/arf-bus/src/connection.rs:407`（connect 广播 online）
**观察现象**：mcp（types=None 全收）收到 model/primary connect 时的 `node_online`；engine / model（type 白名单）挡掉。可见性取决于 (a) filter 白名单是否含 node_online、(b) connect / subscribe 时序（先 subscribe 者才见后来者的 online）。
**判断**：framework 行为——延续 9.1.1 观察语义（隐式 lifecycle 消息随 broadcast 扇出，由各 Node filter 决定可见性）。
**是否构成病灶**：N
**影响面**：诊断类 Node（如 trace，types=None）能观察全部 lifecycle 事件；业务 Node 用 type 白名单自然屏蔽 lifecycle 噪声。设计自洽。

### 观察 F — message_count 语义在多 Node 下不变

**触发位置**：`crates/arf-bus/src/lib.rs:446`
**观察现象**：延续 9.1.1 观察 A——`message_count.fetch_add` 仍仅在 `BusCommand::Send` 分支；多 Node 的 connect / node_online / heartbeat 均不计数。
**判断**：跨复杂度累加，message_count 语义保持"显式 send 计数"稳定。
**是否构成病灶**：N
**影响面**：与 9.1.1 观察 A 同。

---

## §E baseline+1 综合判定

- **多 Node 行为闭合**：3 异构 Node 同 Bus，graph 聚合正确，type filter 逐 Node 精确隔离。
- **信号命中**：0 项（多 Node 范围内）。
- **观察记录**：4 项（C self-delivery / D matches 双用途 / E 隐式 online 可见性 / F message_count 语义不变）。
- **framework 信号总体干净**：baseline+1 累加复杂度后，§4 全部 signals 仍无一超阈进入病灶。
- **结论**：多 Node 异构不引入抽象边界不洁；9.1.3（multi-bus attach_to）可在此基础继续累加。

---

## §F 验证命令

```bash
# 复现探查
git log --oneline -n 3
cargo test -p arf-e2e --test multi_node_heterogeneous -- --nocapture

# 复现 §C 信号 grep
grep -rn 'pub struct NodeHandle\|pub struct Subscription' crates/arf-bus/src/
grep -rn 'pub struct NodeInfo\|pub struct BusGraph\|pub struct NodeId' crates/arf-core/src/
grep -rn 'fn matches\|filter.matches' crates/arf-bus/src/ crates/arf-core/src/lib.rs
grep -n 'message_count.fetch_add' crates/arf-bus/src/lib.rs

# 复现观察 C（forward task 不排除 sender）
sed -n '363,397p' crates/arf-bus/src/connection.rs
```

每条在 HEAD 上可直接复现。

---

## §G 下一步

- commit multi_node_heterogeneous.rs（新 e2e test 文件）
- commit audit-probe-9.1.2.md
- push 到双 remote
- 进 task 9.1.3（Bus + multi-bus 拓扑 / NodeHandle.attach_to）
