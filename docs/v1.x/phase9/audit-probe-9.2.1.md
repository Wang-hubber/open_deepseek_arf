# audit-probe-9.2.1：Engine + 单 ModelAdapter 探查

> Task 9.2.1 探查产出 — **首个引入 Engine 的 task**（9.2 B 单 agent 骨架起步）
> 父 task doc：`docs/v1.x/phase9/task-9.2.1.md`（commit `f3b2519`）
> 父 spec：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.1 大类收尾，2 病灶 A4-001 / A3-001 已登记
> **本 task 不产生新病灶，而是实证并精确化 A4-001 / A3-001 在 Engine 层的蔓延**

---

## §A 探查环境

- working tree：HEAD `f3b2519`
- 测试命令：`cargo test -p arf-e2e --test engine_single_model -- --nocapture`
- 结果：`1 passed; 0 failed`
- engine_single_model.rs 行数：47（复用 E2EHarness，scripted mock）
- 真实运行输出：
  ```
  agent_id=engine/scripted session_id=engine/scripted
  online node_types=["model", "engine"]
  final_output="hello from model" messages=2
  ```

---

## §B (capability, 情景) 单元判定

### 单元 1：chat × §2.1

```
单元              : chat × §2.1（单 agent 无 tool）
能力等级           : D
判分依据           : Engine + 单 ModelAdapter 端到端 chat 由 framework 供。
                    真实断言（engine_single_model.rs:21-24）：run_react("hi") → "hello from
                    model"；state.messages==2（user + assistant）；assistant.tool_calls 空。
                    组装拓扑（:31-38）：primary_bus().graph() 含 2 node（node_types=[model,
                    engine]）——EngineBuilder 自动 wire engine node + ModelAdapterNode。
                    framework 接触点：
                    - E2EHarness::new (harness.rs:158) → EngineBuilder::build (harness.rs:125)
                    - Engine::run 主循环 (engine.rs:226)
                    - ModelAdapterNode 收 model_call 发 model_response (model-adapter/node.rs:72/124)
framework 行为   : app 仅 new+run 两步即得端到端 chat；engine/model node 自动组装并在线；
                    单 round（无 tool）一次 model_call→model_response 即 final。零非业务 glue。
信号命中         : A4-001 蔓延 + A3-001 蔓延（见 §C，均为已登记病灶，不新登记）
信号是否构成病灶   : 已登记（A4-001 / A3-001），本 task 更新其影响面
影响面           : 见 §C / §D
```

### 单元 2 / 3 / 4：不适用（本 task 范围）

| 单元 | 等级 | 备注 |
|---|---|---|
| `model_switch × §2.1` | 不适用 | 单 model 无切换，留 9.2.5 |
| `streaming_response × §2.1` | 不适用 | 留 9.3 J 流式大类 |
| `thinking_visible × §2.1` | 不适用 | scripted mock 无 reasoning chunk |

> Engine 内部抽象（AgentConfig / ModelDecl / ResourceSpec / dispatcher）的完整 §4 审查随
> 9.2.2（ReAct 主循环）+ 深入；本 task 聚焦 chat 能力 + 已登记病灶的 Engine 层蔓延实证。

---

## §C §4 find signals 探查 — 病灶蔓延实证

本 task 首次让 A4-001 / A3-001 在 Engine 主循环（model_call/model_response 请求-响应对）受检。

### A4-001 蔓延实证（correlation_id）— 附关键新发现

**蔓延确认**：engine.rs 的响应匹配路径确用 correlation_id 关联 model_call↔model_response。

**关键新发现（改变 A4-001 性质）**：framework **已存在 typed 访问器**——
`arf-core/src/message.rs:28` 定义 trait 方法 `fn correlation_id(&self) -> Uuid`，**11 处 impl**
（message.rs:83/141/202/255/302/342/409/455/497/531/591）。但采用**不一致**：

| 位置 | 访问方式 | 形态 |
|---|---|---|
| engine.rs:375 | `msg.correlation_id()` | ✓ typed 访问器 |
| engine.rs:460/559/677 | `model_call.correlation_id` / `tool_exec.correlation_id` | ✓ typed 字段 |
| **engine.rs:689** | `payload.get("correlation_id").and_then(as_str).and_then(Uuid::parse_str)` | ✗ **stringly-typed 手挖（wait_for 响应匹配循环）** |
| connection.rs:105/330 / lib.rs:303 | `json!({"correlation_id": …})` | ✗ **塞入侧手写** |

**结论**：A4-001 的准确性质**不是**"无统一接缝"，而是"**typed 接缝（Message::correlation_id trait）已存在，却未被一致采用**"——挖出侧 engine.rs:689 的 wait_for 匹配绕过 typed 访问器手挖 JSON payload；塞入侧仍全手写 json!。这**精确化并加剧** A4-001：修复不需引入访问器（已有），而需**统一采用 + 补对称的塞入侧 `with_correlation_id`**，消灭 engine.rs:689 类手挖回退。

### A3-001 蔓延实证（消息类型标识）— 加剧

**蔓延确认 + 加剧**：核心协议 `model_call` / `model_response`（每次 chat 必用）散落两 crate 裸字面量：

| 位置 | 形态 |
|---|---|
| engine.rs:19-20 | `const MODEL_RESPONSE: &str = "model_response"` / `const TOOL_RESULT`（**局部常量化尝试**） |
| **engine.rs:749** | `"model_call" => Some("model_response".into())`（**同文件内不用自己的 const，裸字面量**） |
| model-adapter/node.rs:54/72/124/161/185/217/328/338 | `"model_call"` / `"model_response"` 裸字面量（8 处） |
| model-adapter/pool_node.rs:60/73/98/127 | `"model_call"` / `"model_response"` 裸字面量（4 处） |

**结论**：A3-001 加剧——(a) 局部 const（engine.rs:19）存在但**连定义文件内都不一致使用**（:749 裸字面量）；(b) 核心协议 model_call/model_response 散落 arf-engine + arf-model-adapter 两 crate 12+ 裸字面量点，无跨 crate 共享常量。比 9.1.5 的 lifecycle 消息更严重（chat 高频协议）。

### 其他信条

| Signal | 结果 | 命中 |
|---|---|---|
| A1 / A2 | Engine 组装（new+run）职责清晰；node 自动 wire 无跨 module 强耦合暴露 | 未命中 |
| A4-S1（filter 散落） | 未新增 | 未命中 |
| A3-S3（同名 struct 跨 crate） | 未发现 | 未命中 |

**§4 新病灶：0**（无新登记）。**已登记病灶蔓延实证：2（A4-001 精确化 / A3-001 加剧）**。

---

## §D lesion-registry 更新（非新增，更新影响面）

按 spec §4.4 探查回归——本 task 不新登记，更新 A4-001 / A3-001 的影响面：

- **A4-001**：追加 Engine 层证据 + **关键修正**——typed 访问器 `Message::correlation_id`（message.rs:28，11 impl）已存在但未一致采用；engine.rs:689 wait_for 匹配仍手挖。修复方向由"引入访问器"修正为"统一采用已有访问器 + 补对称塞入侧"。
- **A3-001**：追加 Engine 层证据——engine.rs:19 局部 const 形同摆设（:749 自身裸字面量）；核心协议 model_call/model_response 散落 arf-engine + arf-model-adapter 12+ 点。

（对应更新已写入 `lesion-registry.md`。）

---

## §E 观察记录（非病灶）

### 观察 N — Engine app 面极简（正向）

**触发位置**：`engine_single_model.rs:16-22`（new + run 两步）
**观察现象**：app 层 `E2EHarness::new(mock)` + `run_react(input)` 两步即得端到端 chat，engine/model node 自动组装并在线，零非业务 glue。
**判断**：A1 正向——Engine 对 app 暴露的 chat 接口原子、职责单一。符合"framework 供 D 级能力"。
**是否构成病灶**：N
**影响面**：正向——单 agent chat 落地成本极低。

---

## §F 综合判定

- **chat 能力**：D（Engine + 单 ModelAdapter 端到端，零 glue）。
- **新病灶**：0。
- **已登记病灶蔓延实证**：2 —— A4-001 **精确化**（typed 访问器已存在却未一致采用，engine.rs:689 手挖）+ A3-001 **加剧**（局部 const 摆设，核心协议裸字面量散落两 crate）。
- **9.2.1 价值**：不止确认蔓延，更**精确化修复方向**——A4-001 从"缺访问器"修正为"访问器未统一采用"，直接降低后续 fix 成本（复用已有 trait，非新建抽象）。
- **结论**：Engine chat 骨架功能达标（D）；两病灶如预判贯穿至 Engine 核心协议，且 9.2.1 挖出可复用的现成 typed 接缝。进 9.2.2（ReAct 主循环）。

---

## §G 验证命令

```bash
cargo test -p arf-e2e --test engine_single_model -- --nocapture

# A4-001 蔓延 + typed 访问器已存在
grep -rn 'fn correlation_id' crates/arf-core/src/message.rs
grep -n 'correlation_id' crates/arf-engine/src/engine.rs | grep -v test   # :375 typed vs :689 手挖

# A3-001 蔓延 + 局部 const 摆设
sed -n '19,20p' crates/arf-engine/src/engine.rs                            # 局部 const
sed -n '748,752p' crates/arf-engine/src/engine.rs                          # 同文件裸字面量
grep -rn '"model_call"\|"model_response"' crates/arf-model-adapter/src/ | grep -v test
```

---

## §H 下一步

- commit engine_single_model.rs
- commit audit-probe-9.2.1.md + lesion-registry.md 更新（A4-001/A3-001 影响面）
- push 双 remote
- 进 9.2.2（Engine + ReAct 主循环 chat）
