# audit-probe-9.1.4：Bus + barrier 多参与者探查

> Task 9.1.4 探查产出
> 父 task doc：`docs/v1.x/phase9/task-9.1.4.md`（commit `02a7ae7`）
> 父 spec：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`（commit `8ad5247`）
> 前置：9.1.1 baseline / 9.1.2 多 Node / 9.1.3 multi-bus 均已 stable
> **本 task 首次登记病灶（A4-001）**

---

## §A 探查环境

- working tree：HEAD `02a7ae7`
- 测试命令：`cargo test -p arf-e2e --test barrier_multi -- --nocapture`
- 结果：`1 passed; 0 failed; finished in 0.91s`
- barrier_multi.rs 行数：95（含注释 + acker helper + 2 场景断言）
- 真实运行输出：
  ```
  A: acked=[NodeId("p/1"), NodeId("p/3"), NodeId("p/2")] missing=[] timed_out=false
  B: acked=[NodeId("q/2"), NodeId("q/1")] missing=[NodeId("q/3")] timed_out=true
  ```

**拓扑**：单 Bus，多 participant
- 场景 A：`p/1` `p/2` `p/3` 全 spawn acker → `barrier([p1,p2,p3], 1s)`
- 场景 B：`q/1` `q/2` spawn acker，`q/3` 静默 hold → `barrier([q1,q2,q3], 300ms)`

---

## §B (capability, 情景) 单元判定

按父 spec §3.3 schema 填。

### 单元 1：barrier_sync × §2.0（全 ack 路径）

```
单元              : barrier_sync × §2.0（全 ack）
能力等级           : D
判分依据           : `Bus::barrier`（lib.rs:285）公开 API 一次调用即 broadcast barrier_request
                    并收集 ack。真实断言（barrier_multi.rs:61-63）：3 participant 并发 ack →
                    acked.len()==3 / missing 空 / !timed_out。
                    framework 接触点：
                    - Bus::barrier (lib.rs:285-359)
                    - barrier subscribe-before-broadcast (lib.rs:294-296)
                    - BarrierReceipt (lib.rs:55)
                    - NodeHandle::barrier_ack (connection.rs:325)
framework 行为   : barrier 广播（非 p2p）到全 participant；correlation_id + participants_set
                    双重过滤（lib.rs:333-343），并发 ack 全部准确汇聚进 acked。
信号命中         : A4-S4 × lib.rs:303/333-338 + connection.rs:105/330 × correlation_id
                    Uuid↔JSON string 转换散落（详见 §C / §D 病灶 A4-001）
信号是否构成病灶   : Y（见 A4-001）
影响面           : 见病灶 A4-001
```

### 单元 2：barrier_sync × §2.0（部分 ack / best-effort）

```
单元              : barrier_sync × §2.0（部分 ack）
能力等级           : D
判分依据           : best-effort 语义由 framework 供。真实断言（barrier_multi.rs:77-79）：
                    q/3 静默 → acked.len()==2 / missing==[q/3] / timed_out==true。
                    framework 接触点：
                    - deadline 循环 (lib.rs:319-349)
                    - missing = participants_set.difference(acked) (lib.rs:351)
                    - timed_out = !missing.is_empty() (lib.rs:352)
framework 行为   : 超时未 ack 者准确进 missing；timed_out 正确置位。best-effort 协议
                    （app 决定 retry/fail/accept）——framework 不代决策。
信号命中         : 同单元 1（A4-001，correlation_id 转换散落）
信号是否构成病灶   : Y（见 A4-001）
影响面           : 见病灶 A4-001
```

### 单元 3 / 4：不适用（本 task 范围内）

| 单元 | 等级 | 备注 |
|---|---|---|
| `heartbeat × §2.0` | 延续 9.1.1（D） | 本 task 未专门断言 |
| `checkpoint_rules × §2.0` | 不适用 | 无 Engine |

---

## §C §4 find signals 探查

按父 spec §4.2 各信条信号跑。barrier 是首个深入**协议层**（非结构层）的探查。

### A1 原子化

| Signal | 探查结果 | 命中 |
|---|---|---|
| A1-S1（方法名多职责） | `barrier` / `barrier_ack` 单动词单职责 | **未命中** |
| A1-S2（doc 多领域） | barrier doc（lib.rs:273-284）单一领域 | **未命中** |
| A1-S3（trait ≥5 方法分多阶段） | 不新增 trait | **未命中** |

### A2 正交性

| Signal | 探查结果 | 命中 |
|---|---|---|
| A2-S1（cross-module 强依赖） | barrier 逻辑自包含于 arf-bus，仅依赖 arf-core Message/NodeId 抽象 | **未命中** |
| A2-S2（字段引用他 crate 具体类型） | BarrierReceipt 字段（Uuid / Vec<NodeId> / bool）无他 crate 具体实现耦合 | **未命中** |

### A3 数据唯一

| Signal | 探查结果 | 命中 |
|---|---|---|
| A3-S1（同名字段跨 crate） | `correlation_id` 作为 payload key 出现在 arf-bus / arf-core / arf-engine / arf-mcp / arf-model-adapter / arf-compactor **12 个非 test src 文件**（grep 全仓）。但多为 JSON payload key 而非 struct 字段；根因归 A4-S4（转换散落），此处记边缘 | **边缘**（根因见 A4-S4） |
| A3-S2（serde alias） | 未发现 | **未命中** |
| A3-S3（同名 struct 跨 crate） | `BarrierReceipt` 仅 lib.rs:55 单一定义 | **未命中** |
| A3-S4（同义不同形） | `correlation_id` 概念两种形状：typed（BarrierReceipt.correlation_id: Uuid @lib.rs:56、barrier_ack 参数 Uuid @connection.rs:325）vs stringly（payload["correlation_id"] JSON string @lib.rs:303/335、connection.rs:105/330）。同义不同形成立，但**根因是转换未集中**，归并入 A4-S4 主命中 | **命中（并入 A4-S4）** |

### A4 处理集中

| Signal | 探查结果 | 命中 |
|---|---|---|
| A4-S1（filter 散落） | filter.matches 仍 lib.rs:434 + connection.rs:386 两处，barrier 未新增 | **未命中** |
| A4-S2（validate 散落） | 无 `fn validate` | **未命中** |
| A4-S3（permission 散落） | 无 tool | **未命中** |
| **A4-S4（convert 散落）** | **`correlation_id` 的 Uuid↔JSON string 转换无单一接缝，散落多处**：塞（Uuid→string）在 connection.rs:105（`cid.to_string()`）、connection.rs:330、lib.rs:303；挖（string→Uuid）在 lib.rs:333-338（`get→as_str→parse_str`）。塞侧有 `send_response`（connection.rs:96-109）半集中，**挖侧无任何 helper**，每个协议各自手写 payload.get + parse。app 层（barrier_multi.rs:32-37）亦被迫手挖 | **命中 → 病灶 A4-001** |

**§4 signals 总命中：1（A4-S4，构成病灶 A4-001）**。

---

## §D 病灶登记（按父 spec §4.3 schema）

### 病灶 A4-001

```
病灶 ID       : A4-001
信条           : A4 处理集中
Signal         : A4-S4（convert 散落）
触发情景       : §2.0（barrier 协议，但根因贯穿全框架 request-response 协议）
file:line      : 塞（Uuid→string）: connection.rs:105 / connection.rs:330 / lib.rs:303
                挖（string→Uuid）: lib.rs:333-338
                typed 端点         : lib.rs:56（BarrierReceipt.correlation_id: Uuid）/ connection.rs:325（barrier_ack 参数）
命中形态       : correlation_id 作为跨协议关联 ID，在 API 边界是 typed Uuid、在 wire payload
                是 JSON string。两形之间的转换（Uuid.to_string 塞入 / payload.get+parse 挖出）
                无统一 envelope，散落在每个协议各自的构造/解析点。塞侧有 send_response
                （connection.rs:96-109）半集中；挖侧完全无 helper。
影响面         : 1) 全框架 request-response 协议（barrier / model_response / tool_result /
                   app_checkpoint_result / compaction 等）各自手写 correlation_id 的 to_string
                   塞与 as_str+parse 挖——correlation_id 出现于 12 个非 test src 文件。
                2) 隐式约定：每个新协议实现者须自知"correlation_id 要 to_string 塞、as_str+
                   parse 挖"，无类型强制；拼错 key / 忘 to_string / parse 失败均静默降级。
                3) app 层外溢：本 task participant（barrier_multi.rs:32-37）被迫手挖
                   payload.get("correlation_id").and_then(as_str).and_then(Uuid::parse_str)，
                   framework 未提供 typed 提取入口。
修复方向（供后续 fix phase 参考，非本 task 职责）:
                引入统一 correlation envelope 或在 Message 上提供 typed
                `correlation_id() -> Option<Uuid>` / `with_correlation_id(Uuid)` 接缝，
                将 Uuid↔string 转换集中到单一 convert 点，塞挖双侧对称。
```

---

## §E 观察记录（非病灶）

### 观察 J — barrier 协议消息类型字符串未常量化

**触发位置**：`"barrier_request"`（lib.rs:299 生产）/ `"barrier_ack"`（lib.rs:329 比对 + connection.rs:327 构造）
**观察现象**：barrier 协议的两个消息类型为**裸字符串字面量**，`"barrier_ack"` 硬编码在 2 处（Bus 收比对 + participant 发构造）。无 `const BARRIER_ACK: &str` 常量。
**判断**：属"魔法字符串"code smell，但 §4.2 A4 signals（filter/validate/permission/convert 动词）不含"消息类型常量化"形态；与 A4-001（convert 散落）根因相关但不同轴。记观察不并入病灶。
**是否构成病灶**：N（不匹配已定义 signal 形态）
**影响面**：拼写错误无编译期防护；barrier_request/barrier_ack 字符串若改需多点同步。当前 2-3 处，影响有限。

### 观察 K — barrier best-effort 语义清晰（正向）

**触发位置**：`lib.rs:281-284`（doc）+ `:351-352`（missing/timed_out 计算）
**观察现象**：barrier 明确声明 best-effort（doc :281），未 ack 者进 missing、timed_out 置位，app 自决 retry/fail/accept。framework 不隐式重试、不 panic。
**判断**：A1 正向证据——barrier 只做"广播+收集+超时报告"单一职责，决策权留给 app。
**是否构成病灶**：N
**影响面**：正向——同步原语边界清晰，app 完全掌控部分失败处理策略。

---

## §F barrier 综合判定

- **barrier 行为闭合**：多参与者并发 ack 全汇聚；部分 ack best-effort 语义正确。
- **信号命中**：1 项（A4-S4）→ **首个病灶 A4-001**（correlation_id Uuid↔string 转换散落）。
- **观察记录**：2 项（J 消息类型字符串未常量化 / K best-effort 语义清晰）。
- **首个病灶意义**：9.1.1–9.1.3 探查 Bus **结构层**均洁净；9.1.4 深入**协议层**后，跑出 correlation_id 契约的转换散落——印证 spec"病灶是探查跑出来的，不是预设的"。A4-001 根因贯穿全框架 request-response，非 barrier 独有，留作后续 fix phase 入口。
- **结论**：barrier 功能正确；抽象层暴露 1 个真实病灶（A4-001）。9.1.5（异常路径）继续收尾 9.1 大类。

---

## §G 验证命令

```bash
# 复现探查
git log --oneline -n 3
cargo test -p arf-e2e --test barrier_multi -- --nocapture

# 复现病灶 A4-001（correlation_id 转换散落）
grep -rn 'correlation_id' crates/arf-bus/src/lib.rs crates/arf-bus/src/connection.rs | grep -v test
grep -rln 'correlation_id' crates/*/src/ | grep -v test   # 12 个非 test src 文件
sed -n '325,332p' crates/arf-bus/src/connection.rs         # barrier_ack 塞
sed -n '333,338p' crates/arf-bus/src/lib.rs                # barrier 挖

# 复现观察 J（消息类型字符串）
grep -rn '"barrier_request"\|"barrier_ack"' crates/*/src/ | grep -v test
```

每条在 HEAD 上可直接复现。

---

## §H 下一步

- commit barrier_multi.rs（新 e2e test 文件）
- commit audit-probe-9.1.4.md（含首个病灶 A4-001）
- push 到双 remote
- 进 task 9.1.5（Bus + 异常：lagged / 掉线 / 重连）— 收尾 9.1 大类
