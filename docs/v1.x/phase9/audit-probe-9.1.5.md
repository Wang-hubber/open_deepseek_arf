# audit-probe-9.1.5：Bus + 异常（lagged / 掉线 / 重连）探查

> Task 9.1.5 探查产出 — **收尾 9.1 A 总线基线大类**
> 父 task doc：`docs/v1.x/phase9/task-9.1.5.md`（commit `cf7411e`）
> 父 spec：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.1.1–9.1.4 均已 stable
> **本 task 登记第二个病灶 A3-001（观察 J 升级）**

---

## §A 探查环境

- working tree：HEAD `cf7411e`
- 测试命令：`cargo test -p arf-e2e --test bus_exceptions -- --nocapture`
- 结果：`3 passed; 0 failed; finished in 1.35s`
- bus_exceptions.rs 行数：84（3 子场景各独立 `#[tokio::test]`）
- 真实运行输出：
  ```
  lagged: 50 sends all Ok, online=["slow", "sender"]
  offline: after timeout online=["observer"]
  reconnect: reconnect Ok, dup connect -> AlreadyConnected
  ```

---

## §B (capability, 情景) 单元判定

按父 spec §3.3 schema 填。

### 单元 1：bus_health_observe × §2.0（Lagged 下存活）

```
单元              : bus_health_observe × §2.0（Lagged / 慢消费者）
能力等级           : D
判分依据           : CAN-bus sender-never-blocks 由 framework 供。真实断言
                    （bus_exceptions.rs:38-44）：capacity=8 下 sender 连发 50 条，每条 send
                    均 Ok；bus.graph() 仍含 sender + slow（存活）。
                    framework 接触点：
                    - broadcast_tx.send 无条件广播 (lib.rs:445)
                    - drain_rx 保活 receiver_count>=1 (lib.rs 注 74-98)
                    - forward task Lagged=>continue (connection.rs:393)
framework 行为   : 慢消费者（slow 不 recv）触发 broadcast ring 溢出，tokio 内建 Lagged
                    机制丢弃旧消息、慢 consumer 得 Lagged(n)；sender 侧 send 全部 Ok 返回
                    （永不 block / 永不 err）；bus 存活，两 Node 仍在线。
信号命中         : 无（Lagged 处理集中，见 §D 观察 L）
信号是否构成病灶   : N
影响面           : N/A
```

### 单元 2：heartbeat × §2.0（掉线检测）

```
单元              : heartbeat × §2.0（掉线 / timeout 剔除）
能力等级           : D
判分依据           : drop handle → forward task 停 ack → heartbeat tick timeout 剔除。
                    真实断言（bus_exceptions.rs:61-62）：ghost drop 后经 timeout(500ms)，
                    graph 不再含 ghost；observer 持续 ack 保持在线。
                    framework 接触点：
                    - forward task is_closed=>break 停 ack (connection.rs:372-374)
                    - heartbeat tick filter last_ack>timeout (heartbeat.rs:42)
                    - remove + broadcast node_offline (heartbeat.rs:47-55)
framework 行为   : ghost handle drop 后其 forward task 下轮检测 inbound_tx.is_closed()→break，
                    停止 heartbeat ack；heartbeat tick 检测 last_ack 超 timeout → 从 nodes map
                    remove 并广播 node_offline；graph 剔除生效。observer 持续 ack 未被剔除。
信号命中         : A3-S1 × heartbeat.rs:55 等 × lifecycle 消息类型字符串散落（见 §C / A3-001）
信号是否构成病灶   : Y（见 A3-001）
影响面           : 见病灶 A3-001
```

### 单元 3：node_online_announcement × §2.0（重连）

```
单元              : node_online_announcement × §2.0（disconnect/reconnect）
能力等级           : D
判分依据           : 同 NodeId 生命周期由 framework 供。真实断言（bus_exceptions.rs:76-80）：
                    disconnect 后同 NodeId reconnect Ok；未 disconnect 重复 connect →
                    Err(AlreadyConnected)。（bus_exceptions.rs:76-82）
                    framework 接触点：
                    - disconnect (connection.rs:298-)
                    - connect 注册查重 → AlreadyConnected (lib.rs:509)
                    - ConnectError::AlreadyConnected(NodeId) (lib.rs:21-23)
framework 行为   : disconnect 从 nodes map 移除后同 NodeId 可复用；未移除时重复 connect
                    返回 AlreadyConnected(NodeId)，携带冲突 NodeId。生命周期语义清晰。
信号命中         : 无
信号是否构成病灶   : N/A
影响面           : N/A
```

---

## §C §4 find signals 探查

按父 spec §4.2 各信条信号跑。本 task 触及全部 lifecycle 消息类型，是**观察 J 升级判定**的关键节点。

### A1 原子化 / A2 正交性

| Signal | 探查结果 | 命中 |
|---|---|---|
| A1-S1/S2/S3 | disconnect / connect / heartbeat tick 均单职责；不新增 trait | **未命中** |
| A2-S1/S2 | 容错逻辑自包含于 arf-bus，无跨 module 具体实现联动 | **未命中** |

### A3 数据唯一

| Signal | 探查结果 | 命中 |
|---|---|---|
| **A3-S1（同名字段/标识跨 crate 重叠）** | **lifecycle 消息类型字符串字面量跨 crate 散落、无单一 const 声明**。生产代码散落点（排除 #[cfg(test)]）：`"node_offline"` @lib.rs:553 + heartbeat.rs:55（arf-bus 生产）+ **engine.rs:88（arf-engine 消费判断）**；`"node_online"` @lib.rs:528 + **engine.rs:88**；`"heartbeat_request"` @heartbeat.rs:30 + connection.rs:378；`"barrier_request/ack"` @lib.rs:299/329 + connection.rs:327。grep 全仓 **无 `const … : &str` 消息类型定义**。同一协议标识（消息类型名）作为跨 crate 契约以裸字面量重复声明 | **命中 → 病灶 A3-001** |
| A3-S2/S3/S4 | 无 serde alias / BarrierReceipt 等结构唯一 / 无同义不同形 | **未命中** |

### A4 处理集中

| Signal | 探查结果 | 命中 |
|---|---|---|
| A4-S1（filter 散落） | filter.matches 仍 lib.rs:434 + connection.rs:386 两处 | **未命中** |
| A4-S4（convert 散落） | 异常路径未新增 correlation_id 转换（heartbeat.rs 无 correlation_id，见 grep）；A4-001 未蔓延至容错层 | **未命中（A4-001 未扩散）** |
| A4-S2/S3 | 无 validate / permission | **未命中** |

**§4 signals 总命中：1（A3-S1，构成病灶 A3-001）**。

---

## §D 病灶登记（按父 spec §4.3 schema）

### 病灶 A3-001

```
病灶 ID       : A3-001
信条           : A3 数据唯一
Signal         : A3-S1（同名字段/标识跨 crate 重叠）
触发情景       : §2.0（容错/异常路径，根因贯穿全框架 lifecycle 协议）
首次登记       : audit-probe-9.1.5.md（前身为 9.1.4 观察 J，本 task 升级）
状态           : OPEN
file:line      : "node_offline": lib.rs:553 / heartbeat.rs:55 / engine.rs:88
                "node_online" : lib.rs:528 / engine.rs:88
                "heartbeat_request": heartbeat.rs:30 / connection.rs:378
                "barrier_request": lib.rs:299   "barrier_ack": lib.rs:329 / connection.rs:327
                常量定义       : 无（grep 'const … : &str' 消息类型 = 空）
命中形态       : lifecycle 协议消息类型名（node_online / node_offline / heartbeat_request /
                barrier_request / barrier_ack）作为跨模块契约，以裸字符串字面量散落声明于
                arf-bus / arf-core / arf-engine 三 crate 的生产代码，无单一 const/enum 声明。
                尤为关键：arf-engine（engine.rs:88）**消费**判断 "node_online"/"node_offline"
                用裸字面量，与 arf-bus（生产者 lib.rs:528/553）无共享常量——跨 crate 协议契约
                各自硬编码。
影响面         : 1) 消息类型名散落 arf-bus + arf-core + arf-engine 3 crate 生产代码，改名/加
                   类型须全仓手动 grep 同步，无编译期防护。
                2) 跨 crate 静默失效风险：arf-bus 改 "node_online" 拼写，engine.rs:88 消费侧
                   不报错、缓存失效逻辑静默失灵（cache 不再 invalidate）。
                3) 拼写错误无防护：消费侧 msg.msg_type == "node_onlien" 编译通过、运行时静默漏判。
修复方向       : 在 arf-core 定义消息类型常量模块（`pub const NODE_ONLINE: &str = "node_online"`
                （供参考）      等）或 `enum MsgType`，arf-bus/arf-engine 统一引用，消灭裸字面量。
复现命令       : grep -rn '"node_offline"\|"node_online"\|"heartbeat_request"' crates/*/src/ | grep -v test
                grep -rn 'const .*: &str' crates/arf-bus/src/ crates/arf-core/src/   # 应无消息类型常量
                sed -n '86,90p' crates/arf-engine/src/engine.rs   # 跨 crate 裸字面量消费
```

> 与 A4-001 的区分：A4-001 是 correlation_id **值的 typed↔string 转换**散落（convert）；A3-001 是消息类型**标识符声明**不唯一（同名标识跨 crate 重复字面量）。两者不同轴。

---

## §E 观察记录（非病灶）

### 观察 L — Lagged 被 framework 静默吸收（CAN-bus 正向）

**触发位置**：`connection.rs:361-362`（doc）+ `:393`（Lagged=>continue）+ `lib.rs:74-98`（drain_rx 保活）
**观察现象**：慢消费者触发 broadcast ring 溢出，tokio 内建 Lagged 丢旧消息；forward task 遇 Lagged 单点 `continue`（connection.rs:393），不向 app 暴露（handle.recv() 不透出 Lagged）；drain_rx 保 receiver_count>=1，sender 的 broadcast_tx.send 永远 Ok。
**判断**：Lagged 处理**集中**（forward task 一处 + tokio 内建），是 A4"处理集中"正向证据。CAN-bus 语义完整——慢消费者不拖垮总线、不背压 sender。
**是否构成病灶**：N
**影响面**：正向——单个慢/卡死 Node 无法阻塞全总线或拖慢其他 Node。

### 观察 M — NodeHandle 未实现 Debug（探查副发现）

**触发位置**：`connection.rs:65`（NodeHandle struct，无 `#[derive(Debug)]`）
**观察现象**：编写 bus_exceptions.rs 时，`assert!(matches!(dup, Err(AlreadyConnected(_))), "{dup:?}")` 编译失败——`Result<NodeHandle, ConnectError>` 无法 `{:?}`，因 NodeHandle 未实现 Debug。测试改为先 match 再断言绕过。
**判断**：非 §4 signal。但影响 app 层错误处理的可诊断性——`connect()` 返回 `Result<NodeHandle, _>`，app 无法直接 `.expect()`/`{:?}` 打印含 NodeHandle 的 Result。
**是否构成病灶**：N（不匹配已定义 signal）
**影响面**：app 侧对 connect 结果的调试打印受限，须手动解构。轻微人机工程问题。

### 观察 J 结案 — 升级为 A3-001

9.1.4 观察 J（"barrier 消息类型字符串未常量化"，当时 2-3 处判 N）在本 task 扩范围后确认：lifecycle 消息类型散落跨 3 crate、含 arf-engine 跨 crate 消费点，影响面显著 → 正式升级为病灶 **A3-001**。观察 J 就此结案，后续以 A3-001 跟踪。

---

## §F 9.1.5 综合判定 + 9.1 大类收尾

- **容错三路径闭合**：Lagged sender-never-blocks / heartbeat timeout 掉线剔除 / disconnect-reconnect 生命周期，全部由 framework 正确供（3 单元 D）。
- **信号命中**：1 项（A3-S1）→ **第二个病灶 A3-001**（lifecycle 消息类型标识散落）。
- **观察记录**：2 项（L Lagged 静默吸收正向 / M NodeHandle 无 Debug）+ 观察 J 结案升级。
- **9.1 A 总线基线大类收尾统计**：
  | task | 单元判定 | 病灶 | 观察 |
  |---|---|---|---|
  | 9.1.1 baseline | 全 D | 0 | 2（A/B 边缘） |
  | 9.1.2 多 Node | 全 D | 0 | 4 |
  | 9.1.3 multi-bus | 全 D | 0 | 3（含 G DRY） |
  | 9.1.4 barrier | 全 D | **A4-001** | 2（含 J） |
  | 9.1.5 异常 | 全 D | **A3-001** | 2（+J 结案） |
  - **总计**：Bus 全能力 D；2 病灶（A4-001 convert 散落 / A3-001 标识散落）；均 OPEN，进 lesion-registry。
  - **结论**：Bus 功能层完全达标（全 D）；抽象层暴露 2 个跨全框架的协议契约病灶——均非 Bus 独有，根因在"协议契约的值转换与标识声明未集中"。留后续 fix phase。
- **下一步**：进 9.2 B 单 agent 骨架（9.2.1 Engine + 单 ModelAdapter）。

---

## §G 验证命令

```bash
# 复现探查
git log --oneline -n 3
cargo test -p arf-e2e --test bus_exceptions -- --nocapture

# 复现病灶 A3-001（lifecycle 消息类型标识散落）
grep -rn '"node_offline"\|"node_online"\|"heartbeat_request"' crates/*/src/ | grep -v test
grep -rn 'const .*: &str' crates/arf-bus/src/ crates/arf-core/src/   # 应无消息类型常量
sed -n '86,90p' crates/arf-engine/src/engine.rs                      # engine 跨 crate 裸字面量消费

# 复现观察 L（Lagged 处理集中）
sed -n '391,394p' crates/arf-bus/src/connection.rs
```

每条在 HEAD 上可直接复现。

---

## §H 下一步

- commit bus_exceptions.rs（新 e2e test 文件）
- commit audit-probe-9.1.5.md + lesion-registry.md 更新（A3-001）
- push 到双 remote
- **9.1 A 总线基线大类收尾** → 进 9.2.1（Engine + 单 ModelAdapter）
