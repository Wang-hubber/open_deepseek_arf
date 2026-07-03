# Phase 9 病灶登记册（Lesion Registry）

> **用途**：统一收集 phase 9 各 task 探查跑出的**待修复病灶**，供后续 fix phase 使用。
> 本文档是 phase 9 病灶的**单一权威汇总**——各 `audit-probe-9.X.Y.md` 的 §D 病灶登记在此**去重汇总**。
>
> **与 spec 的关系**：
> - 病灶判定规则见父 spec `capability-matrix-and-audit-design.md` §4（四信条 + find signals + §4.3 登记 schema）
> - 每个 task 的 `audit-probe-9.X.Y.md` 是病灶的**首次现场登记**；本册是**跨 task 汇总 + 状态跟踪**
>
> **更新约定**：
> - 每当某 task 的 audit-probe 判出"信号构成病灶 Y"，将该病灶**追加**到本册（总表 + 详情）
> - 病灶 ID 按信条分组顺序编号（A1-00N / A2-00N / A3-00N / A4-00N），全 phase 9 唯一、不复用
> - `状态` 字段：`OPEN`（待修复）/ `FIXED`（fix phase 已修，附 commit）/ `WONTFIX`（评估后不修，附理由）
> - 本册**不含时间字段**（遵循 spec §5.2）

---

## §1 病灶总表

| 病灶 ID | 信条 | Signal | 触发 task | 命中摘要 | 状态 | 修复归属 |
|---|---|---|---|---|---|---|
| **A4-001** | A4 处理集中 | A4-S4（convert 散落） | 9.1.4（barrier）；9.2.1 精确化；**9.2.2 真实 payload 复测** | `correlation_id` Uuid↔JSON string 转换散落；**typed 访问器 Message::correlation_id 已存在却未一致采用**（engine.rs:689 仍手挖）；9.2.2 真实 DashScope qwen 端到端下匹配工作（tool loop 5 消息实证），病灶形态未在真实流量下恶化 | **OPEN** | 后续 fix phase |
| **A3-001** | A3 数据唯一 | A3-S1（同名标识跨 crate） | 9.1.5（异常）；9.2.1 加剧；**9.2.2 真实 payload 复测** | lifecycle + model_call/model_response 消息类型名裸字面量散落 arf-bus/core/engine/model-adapter，局部 const 摆设，无跨 crate 声明；9.2.2 真实 payload 下路由工作（tool message 正确归位），病灶形态未在真实流量下恶化 | **OPEN** | 后续 fix phase |

> 统计：OPEN 2 / FIXED 0 / WONTFIX 0（截至 task 9.2.2；两病灶经 9.2.1 在 Engine 层实证蔓延并精确化，9.2.2 在真实 DashScope qwen payload 下复测通过——病灶形态未在真实流量下恶化，无新增病灶）

---

## §2 病灶详情

### A4-001 — correlation_id Uuid↔string 转换散落

```
病灶 ID       : A4-001
信条           : A4 处理集中
Signal         : A4-S4（convert 散落）
触发情景       : §2.0（barrier 协议，但根因贯穿全框架 request-response 协议）
首次登记       : audit-probe-9.1.4.md §D
状态           : OPEN
file:line      : 塞（Uuid→string）: connection.rs:105 / connection.rs:330 / lib.rs:303
                挖（string→Uuid）: lib.rs:333-338
                typed 端点         : lib.rs:56（BarrierReceipt.correlation_id: Uuid）
                                    connection.rs:325（barrier_ack 参数 Uuid）
命中形态       : correlation_id 作为跨协议关联 ID，在 API 边界是 typed Uuid、在 wire payload
                是 JSON string。两形之间的转换（Uuid.to_string 塞入 / payload.get+parse 挖出）
                无统一 envelope，散落在每个协议各自的构造/解析点。塞侧有 send_response
                （connection.rs:96-109）半集中；挖侧完全无 helper。
影响面         : 1) 全框架 request-response 协议（barrier / model_response / tool_result /
                   app_checkpoint_result / compaction 等）各自手写 correlation_id 的 to_string
                   塞与 as_str+parse 挖——correlation_id 出现于 12 个非 test src 文件。
                2) 隐式约定：每个新协议实现者须自知"correlation_id 要 to_string 塞、as_str+
                   parse 挖"，无类型强制；拼错 key / 忘 to_string / parse 失败均静默降级。
                3) app 层外溢：task 9.1.4 participant（barrier_multi.rs:32-37）被迫手挖
                   payload.get("correlation_id").and_then(as_str).and_then(Uuid::parse_str)，
                   framework 未提供 typed 提取入口。
修复方向       : 引入统一 correlation envelope，或在 Message 上提供 typed
                （供参考）      `correlation_id() -> Option<Uuid>` / `with_correlation_id(Uuid)` 接缝，
                将 Uuid↔string 转换集中到单一 convert 点，塞挖双侧对称。
修复方向       : 统一采用**已存在**的 typed 访问器 `Message::correlation_id`（arf-core/
                （供参考）      message.rs:28 trait + 11 impl），消灭挖出侧手挖回退（engine.rs:689
                wait_for 匹配仍 payload.get 手挖）；并补对称的塞入侧 `with_correlation_id(Uuid)`，
                将 Uuid↔string 转换集中到单一 convert 点。
                【9.2.1 修正】原以为"缺访问器"，实证发现访问器已存在却未一致采用——
                修复是"统一采用"而非"新建抽象"，成本更低。
Engine 层蔓延  : （9.2.1 实证）engine.rs:375 用 typed `msg.correlation_id()` ✓，
                但 engine.rs:689 wait_for 响应匹配绕过它、手挖 payload.get("correlation_id") ✗；
                塞入侧 lib.rs:303 / connection.rs:105,330 仍手写 json!。typed 与 stringly 混用。
复现命令       : grep -rln 'correlation_id' crates/*/src/ | grep -v test   # 12 个非 test src 文件
                grep -rn 'fn correlation_id' crates/arf-core/src/message.rs  # typed 访问器已存在
                grep -n 'correlation_id' crates/arf-engine/src/engine.rs | grep -v test  # :375 typed vs :689 手挖
```

### A3-001 — lifecycle 消息类型标识散落（无单一 const）

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
                arf-bus / arf-core / arf-engine 三 crate 生产代码，无单一 const/enum 声明。
                关键：arf-engine（engine.rs:88）消费判断 "node_online"/"node_offline" 用裸
                字面量，与 arf-bus（生产者 lib.rs:528/553）无共享常量——跨 crate 契约各自硬编码。
影响面         : 1) 消息类型名散落 arf-bus + arf-core + arf-engine 3 crate，改名须全仓手动同步，
                   无编译期防护。
                2) 跨 crate 静默失效：arf-bus 改 "node_online" 拼写，engine.rs:88 消费侧不报错、
                   缓存失效逻辑静默失灵。
                3) 拼写错误无防护：msg.msg_type == "node_onlien" 编译通过、运行时静默漏判。
修复方向       : 在 arf-core 定义消息类型常量模块（pub const NODE_ONLINE: &str = "node_online"）
                （供参考）      或 enum MsgType，arf-bus/arf-engine/arf-model-adapter 统一引用，消灭裸字面量。
Engine 层蔓延  : （9.2.1 实证）核心协议 model_call/model_response 散落更严重：engine.rs:19
                有局部 const MODEL_RESPONSE，但同文件 engine.rs:749 自身用裸字面量；
                model-adapter/node.rs（8 处）+ pool_node.rs（4 处）全裸字面量。
                局部 const 形同摆设，无跨 crate 共享。model_call/model_response 是 chat 高频协议，
                比 lifecycle 消息散落更广。
复现命令       : grep -rn '"node_offline"\|"node_online"\|"heartbeat_request"' crates/*/src/ | grep -v test
                grep -rn '"model_call"\|"model_response"' crates/arf-engine/src/ crates/arf-model-adapter/src/ | grep -v test
                grep -rn 'const .*: &str' crates/arf-bus/src/ crates/arf-core/src/   # 无跨 crate 消息类型常量
                sed -n '86,90p' crates/arf-engine/src/engine.rs

区分说明     : A4-001 是 correlation_id 值的 typed↔string 转换散落（convert 轴）；
                A3-001 是消息类型标识符声明不唯一（标识声明轴）。两者不同轴、独立修复。
```

---

## §3 后续 task 追加区

> 9.1.5 及以后的 task 若跑出新病灶，在此追加（先补 §1 总表一行，再在 §2 加详情块）。
> 9.1 A 总线基线大类（9.1.1–9.1.5）收尾：Bus 全能力判定 D；产出 2 病灶（A4-001 / A3-001），
> 均为跨全框架协议契约问题，非 Bus 独有。9.1.1/9.1.2/9.1.3 探查为 0 病灶（结构层洁净），
> 9.1.4（协议层）产出 A4-001，9.1.5（容错层）产出 A3-001。

---

## §4 与 fix phase 的接口契约

- 本册是 fix phase 的**唯一病灶输入源**——fix phase 逐 `OPEN` 病灶处理
- fix 完成后，将对应病灶 `状态` 改 `FIXED` 并附 fix commit hash
- 按 spec §4.4 探查回归：fix 后须重跑触发该病灶的 task audit-probe，确认命中消失
