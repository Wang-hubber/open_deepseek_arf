# audit-probe-9.2.3：Engine + 5 Checkpoint + 自定义 Rule 探查

> Task 9.2.3 探查产出 — **Engine Checkpoint 注入点机制**（9.2 B 单 agent 骨架第 3 步）
> 父 task doc：`docs/v1.x/phase9/task-9.2.3.md`（commit `46b6de0`）
> 父 spec：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.2.1（Engine + 单 ModelAdapter mock chat）/ 9.2.2（真实 DashScope qwen ReAct loop）
> **本 task 探查 Engine 内置 5 Checkpoint 位置 + 2 built-in rule factory + 自定义 Rule + error path**

---

## §A 探查环境

- working tree：HEAD `46b6de0`
- 测试文件：`crates/arf-e2e/tests/checkpoint_rules.rs`（6 test cases）
- 探查基础设施：harness 加 `with_checkpoint_rules(Vec<CheckpointRule>)` 方法
  （per commit `8e0b...`——harness builder 扩展，**不**是 framework 改动）
- 驱动：scripted mock provider + scripted McpNode（test 2 tool 路径），**不**依赖任何 LLM
- 测试命令：
  ```bash
  cargo test -p arf-e2e --test checkpoint_rules -- --nocapture --test-threads=1
  ```
- 结果：`6 passed; 0 failed; 0.04s`（mock 驱动，无网络）
- 关键真实运行输出：
  ```
  [ckpt] no_tool fires: ["before_model_call", "after_model_call", "round_end"]
  [ckpt] one_tool fires: ["before_model_call", "after_model_call",
                          "before_tool_exec", "after_tool_exec",
                          "before_model_call", "after_model_call",
                          "round_end"]
  [ckpt] every_2 fires: ["every_2"]
  [ckpt] when_ctx_over fires: ["ctx_over"]
  [ckpt] custom fires: ["custom_when", "custom_build"]
  [ckpt] undeclared error: msg_type=ckpt/never_registered ✓
  ```

### 探查执行中遇到的 2 个 framework 设计点（记入 §E 观察）

1. **Strict route NodeId 须在 BusGraph 中**（builder.rs:73-82 `BuildError::MissingNodes`）——
   探查初版用 `NodeId::new("audit/sink")` 触发 build 失败，改用 `NodeId::new("model/e2e")`
   （harness 必有的 model 节点）作为 Strict route target。ModelAdapterNode 在收到非
   "model_call" msg 时静默丢弃（node.rs:72 if 守卫），不影响测试。**这是 framework 期望
   行为，不是 bug**。

2. **CheckpointRule 自动注册到 engine.routes 的契约不包含 framework 内置 msg_type**——
   `model_call` / `model_response` / `tool_exec` / `tool_result` 不在 `engine.routes` 中
   （仅 `extra_routes` 加上 CheckpointRule 自身 msg_type）。test 3 第一版用 `ModelCall`
   作 build 返回（msg_type="model_call"），触发 `RunError::UndeclaredMsgType`。改用
   `MarkerMsg`（msg_type="ckpt/audit"）走 user-registered route 通过。**这是
   registry/builder 行为预期，不是 bug**——model_call 的 auto-routing 仅在 Engine 内部
   `do_model_turn` 直接发时生效，不经 engine.routes 查表。

---

## §B (capability, 情景) 单元判定

### 单元 1：5 Checkpoint 位置各 fire（情景 §2.3 1）

```
单元              : checkpoint_rules × §2.3 — 5 位置全 fire
能力等级           : D
判分依据           : 5 Checkpoint variant 各在 engine.rs:247/264/277/291/296
                    触发 evaluate_and_dispatch。真实断言
                    （checkpoint_rules.rs:128-156, 169-237）：
                    1 round 无 tool：Vec = [bmc, amc, re]（3 fires）
                    1 round 1 tool：Vec = [bmc, amc, bte, ate, bmc, amc, re]（7 fires）
                    bmc=2, amc=2, bte=1, ate=1, re=1 — 全部 5 位置至少 1 fire。
framework 行为   : app 注册 5 rule + 1 route 即得 5 位置全 dispatch；engine
                    主循环在 turn 边界 fire BeforeModelCall/AfterModelCall，
                    tool 批 fire BeforeToolExec/AfterToolExec（per-tool 不并发
                    fire，per-batch fire 1 次——engine.rs:285-287 注释明示），
                    final tool_calls 空时 fire RoundEnd 后 return。
信号命中         : 无新病灶（详见 §C）
```

### 单元 2：built-in `every_n_rounds` factory（情景 §2.3 2）

```
单元              : CheckpointRule::every_n_rounds × §2.3
能力等级           : D
判分依据           : factory 返回 typed rule（checkpoint.rs:58-73）。真实断言
                    （checkpoint_rules.rs:224-264）：run_react 2 次，第 2 次
                    round_count=2 → 1 fire。round 1 跳过因
                    `round_count > 0 && round_count % every_n == 0`。
framework 行为   : factory 闭包逻辑等价 `|s| s.over_view.round_count > 0
                    && s.over_view.round_count as u32 % every_n == 0`，
                    round 1 (round_count=1) 必跳。app 调 factory 1 行即得
                    "每 N round 触发" 语义。
信号命中         : 无新病灶
```

### 单元 3：built-in `when_context_over` factory（情景 §2.3 3）

```
单元              : CheckpointRule::when_context_over × §2.3
能力等级           : D
判分依据           : factory 返回 typed rule（checkpoint.rs:77-92）。真实断言
                    （checkpoint_rules.rs:269-308）：utilization=0.6 fire 1 次；
                    utilization=0.4 no fire。边界正确。
framework 行为   : factory 闭包逻辑等价
                    `|s| s.over_view.context_utilization() >= ratio`，
                    app 调 factory 1 行即得"上下文 ≥ ratio 触发"语义。
信号命中         : 无新病灶
```

### 单元 4：自定义 CheckpointRule（情景 §2.3 4）

```
单元              : custom_checkpoint_rule × §2.3 — E - Extensible
能力等级           : E
判分依据           : `CheckpointRule::new(name, trigger, when, build)` 接受任意
                    HRTB 闭包（checkpoint.rs:43-54）。真实断言
                    （checkpoint_rules.rs:313-358）：自定义 when=`round_count > 3`、
                    自定义 build=返回 MarkerMsg。round 1-3 不 fire，round 4 fire
                    1 次（when + build 各 1 push = 2 entries）。
framework 行为   : framework 提供 trait 边界 `Box<dyn Fn>` + `Box<dyn ActionMessage>`，
                    app 完全自定义 when/build。零 framework 改动 → E 级。
信号命中         : 无新病灶
```

### 单元 5：route 解析（Strict）（情景 §2.3 5）

```
单元              : route_resolution_strict × §2.3
能力等级           : D
判分依据           : Strict route 直返 ids（engine/checkpoint.rs:93）。真实断言：
                    `Route::Strict(vec![NodeId::new("model/e2e")])` build 通过
                    （NodeId 必须在 BusGraph 中，builder.rs:73-82 验证），
                    消息 dispatch 成功。ModelAdapterNode 收到非 "model_call"
                    消息后静默丢弃（node.rs:72），不影响测试。
framework 行为   : framework 端到端供 Strict route 解析 + 验证。Discovery 路由
                    探查留 9.1.3 已覆盖。
信号命中         : 无新病灶
```

### 单元 6：undeclared msg_type error path（情景 §2.3 6）

```
单元              : undeclared_msgtype_error × §2.3
能力等级           : D
判分依据           : rule build 返回未注册 msg_type → RunError::UndeclaredMsgType
                    （engine/checkpoint.rs:140-144）。真实断言
                    （checkpoint_rules.rs:363-401）：rule build 返回
                    msg_type="ckpt/never_registered"，未注册 route →
                    `Err(RunError::UndeclaredMsgType { msg_type: "ckpt/never_registered" })`。
framework 行为   : framework 不静默失效——rule 写错 msg_type 立即报错（programming bug
                    守门员，engine.rs:227 注释明示）。
信号命中         : 无新病灶
```

---

## §C §4 find signals 探查

### A3 数据唯一 — Checkpoint 路径是否引入新散落

**结论：未引入新散落**。

| 检查项 | 结果 |
|---|---|
| Checkpoint module 内部 msg_type 字面量 | **0 处**（grep '\"ckpt\|\"checkpoint' 在 arf-engine/src + arf-core/src 仅 tests 命中） |
| Checkpoint 路径 correlation_id 散落 | **0 处**（build 闭包返回的 ActionMessage 的 correlation_id 由 app 决定，framework 不集中） |
| Checkpoint 路径硬编码 lifecycle 消息 | **0 处**（framework 不知道也不关心 Checkpoint 派发的具体 msg_type） |

Checkpoint 路径设计上**完全 data-agnostic**——framework 只暴露 `Checkpoint` 5 个 variant + `CheckpointRule` 4-tuple + `evaluate_and_dispatch` 触发点，msg_type 由 `build` 闭包动态决定。这**缓解** A3-001（Checkpoint 路径未加剧散落）。

### A4 处理集中 — correlation_id 在 Checkpoint 路径

**观察（非病灶）**：CheckpointRule 派发的 ActionMessage 的 correlation_id 由 app 的 build 闭包内自决（典型 `Uuid::new_v4()`）。framework 不集中管。`engine/checkpoint.rs:140-144` 的 `UndeclaredMsgType` 错误路径**也不涉及** correlation_id。

**判定**：Checkpoint 路径**不涉及** correlation_id 流程，因此**不加剧** A4-001。

### A1 原子化 — CheckpointRule 4-tuple

`CheckpointRule { name, trigger, when, build }`（checkpoint.rs:31-38）4 个字段各司其职：
- name：rule id（用于日志 / 唯一性校验 builder.rs:87）
- trigger：fire 位置（`Checkpoint` enum）
- when：fire 条件（HRTB 闭包）
- build：fire 时构造的副作用消息

无 `and / or / with_xxx_and_yyy` 多职责模式（`A1-S1`）✓；无 doc comment 描述不相关领域（A1-S2）✓；trait 方法不跨生命周期阶段（A1-S3）✓。

**结论**：A1 洁净。

### A2 正交性 — CheckpointRule 与其他抽象

CheckpointRule 只依赖 `&State`（checkpoint.rs:35/37）；不引用具体 provider / message / transport 类型。无 `cross-import` 强依赖（A2-S1）✓；无字段交叉引用（A2-S2）✓。

**结论**：A2 洁净。

### A3-S2 / A3-S3 / A3-S4 — 跨 crate 同名/同义结构

未发现新增同名/同义结构。

**§4 新病灶：0**。**已登记病灶 Checkpoint 路径实证：0 加剧**（A3-001 缓解，A4-001 不涉及）。

---

## §D lesion-registry 更新（无需新增，仅追加观察）

本 task 不新登记。A3-001 / A4-001 在 Checkpoint 路径均**未加剧**（A3-001 缓解 / A4-001 不涉及）。

§1 总表无需追加新行；§2 详情无需修改。

---

## §E 观察记录（非病灶）

### 观察 P1 — Strict route NodeId 须在 BusGraph 中（builder.rs:73-82）

**触发位置**：`checkpoint_rules.rs:133`（初版用 `NodeId::new("audit/sink")` 触发 build panic）
**观察现象**：Strict route 的 NodeId 在 build 阶段必须已存在 BusGraph，否则 `BuildError::MissingNodes`。初版无对应节点 → build panic。改用 harness 必有的 `NodeId::new("model/e2e")` 通过。
**判断**：framework 期望行为，**不构成病灶**。Strict route 语义 = "必须存在的精确节点"。
**影响面**：仅 test 编写——必须先在 bus 上有节点，再把 NodeId 写进 route。**app 实际用时**：CheckpointRule 派发的消息通常给 app 自定义节点接收，app 在 bus 上注册节点后即可。

### 观察 P2 — Checkpoint 路径不涉及 correlation_id 集中管理

**触发位置**：`CheckpointRule.build` 闭包返回的 `Box<dyn ActionMessage>`（checkpoint.rs:37/100-102）
**观察现象**：build 闭包返回的 ActionMessage 的 `correlation_id()` 由 app 自决（典型 `Uuid::new_v4()`）。framework 不集中管 checkpoint msg 的 correlation_id 来源。
**判断**：**不构成病灶**——framework 不应该为 app 自定义 msg 决定 correlation_id 语义（app 决定 "compaction 关联的旧会话 id" / "checkpoint trace id" 等不同语义）。这与 A4-001（framework 内置 request-response 协议 correlation_id 散落）**是不同问题**：A4-001 是 framework 自身协议挖出侧手挖；P2 是 framework 故意把 checkpoint msg correlation_id 决策权下放给 app。
**影响面**：app 写 CheckpointRule.build 时需自决 correlation_id——可考虑 framework 在 `evaluate` 时统一注入 checkpoint::rule_name + checkpoint::trigger 作为 correlation_id 元数据，但当前**不是** framework 责任范围。

### 观察 P3 — app-level factory `arf_compactor::when_context_over`（crates/arf-compactor/src/lib.rs:157）

**触发位置**：`CheckpointRule::when_context_over`（核心 built-in） + `arf_compactor::when_context_over`（app-level 包装，注入 CompactRequest marker message）
**观察现象**：framework 核心提供**通用** `when_context_over(ratio)`（只产 typed rule，build 闭包由 app 提供）；`arf-compactor` crate 提供**专用** `when_context_over(ratio, keep_tail)`（build 闭包直接返回 CompactRequest）。
**判断**：A1 正向——framework 提供正交 primitive，app crate 在 primitive 上包装成领域专用 factory。两层"通用 + 专用" pattern 是 framework 健康信号。
**是否构成病灶**：N（正向观察）
**影响面**：所有需要 context-over 的 app 都能用 `arf_compactor::when_context_over` 一行接入；framework core 不被 compaction 概念污染。

---

## §F 综合判定

- **5 Checkpoint 位置全 fire**：D（5 variant 各在 engine 主循环触发；mock 实证 7 fires 匹配预期）。
- **built-in `every_n_rounds`**：D（factory 1 行接入；round 1 跳过逻辑正确）。
- **built-in `when_context_over`**：D（factory 1 行接入；utilization 边界正确）。
- **自定义 CheckpointRule**：E（`CheckpointRule::new` HRTB 闭包边界可用，零 framework 改动）。
- **Strict route 解析**：D（build-time 验证 + 运行时 dispatch 双层）。
- **undeclared msg_type error path**：D（framework 不静默失效，programming bug 守门）。
- **新病灶**：0。
- **已登记病灶 Checkpoint 路径实证**：A3-001 **缓解**（Checkpoint 路径不引入 msg_type 字面量散落）/ A4-001 **不涉及**（Checkpoint 路径无 correlation_id 流程）。
- **9.2.3 价值**：**首次实证 Engine Checkpoint 注入点机制完整工作**——5 位置 fire 顺序、2 built-in factory、自定义 Rule、route 解析、error path 全部端到端验证，且**不引入新散落**。Checkpoint 路径是 framework 抽象"data-agnostic"原则的正面案例。
- **结论**：Engine Checkpoint 机制在 mock 端到端下功能达标（D × 5 + E × 1）；无新病灶。进 9.2.4（Engine + interrupt / cancel）。

---

## §G 验证命令

```bash
# 跑通（mock 驱动，无 key 依赖）
cargo test -p arf-e2e --test checkpoint_rules -- --nocapture --test-threads=1

# 5 位置 fire 顺序 cross-check（应与 engine.rs:212-219 注释一致）
grep -A 8 'pub async fn run' crates/arf-engine/src/engine.rs | head -10

# CheckpointRule 4-tuple 字段
sed -n '31,38p' crates/arf-core/src/checkpoint.rs

# Built-in factory 入口
grep -n 'pub fn every_n_rounds\|pub fn when_context_over' crates/arf-core/src/checkpoint.rs

# Per-tool checkpoint 不并发 fire（fire 1 次/batch）的设计注释
sed -n '285,290p' crates/arf-engine/src/engine.rs

# §4 信号 cross-check（应零命中——Checkpoint 路径不引入新散落）
grep -rn '"ckpt\|"checkpoint' crates/arf-engine/src/ crates/arf-core/src/ | grep -v test
grep -n 'correlation_id' crates/arf-engine/src/checkpoint.rs crates/arf-core/src/checkpoint.rs
```

---

## §H 下一步

1. self-review（凭据 / 一致性 / scope / granular）— commit 前必跑
2. **granular commit**（per CLAUDE.md workflow）：
   - commit `harness.rs` 改动（with_checkpoint_rules builder 方法）
   - commit `checkpoint_rules.rs`（probe）
   - commit `audit-probe-9.2.3.md`（结果）
3. push 双 remote（github + gitee）
4. 进 9.2.4（Engine + interrupt / cancel）
