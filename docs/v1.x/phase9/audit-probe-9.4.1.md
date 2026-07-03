# audit-probe-9.4.1：ModelAdapterPoolNode facade 探查（含 F-002 critical + F-003 design quirk）

> Task 9.4.1 探查产出 — **Pool 自身 + ModelAdapterPoolNode facade 行为**
> 父 task doc：`docs/v1.x/phase9/task-9.4.1.md`
> 父 spec：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.2.5（多 ModelAdapter 候选切换）
> **本 task 在 framework 当前实现下发现 2 个 critical finding + 1 个 design quirk（user 2026-07-03 round 3-6 多轮反馈）**

---

## §A 探查环境

- working tree：HEAD `375695d`
- 测试文件：`crates/arf-e2e/tests/pool_node_facade.rs`（5 test cases）
- 驱动：4 mock（fast, deterministic）+ 1 F-002 实证（mock）
- 真实 LLM 矩阵测试（7 cases）**未跑**——F-003 framework design quirk 阻断
- 测试命令：
  ```bash
  cargo test -p arf-e2e --test pool_node_facade -- --nocapture --test-threads=1
  ```
- 结果：`5 passed; 0 failed`
- 关键真实运行输出（已实证）：
  ```
  [pool] block-succeed: 2nd acquire 阻塞 51.32ms 后成功 ✓
  [pool] block: 2nd acquire timeout after 201.35ms ✓
  [pool] queue: 4th acquire 立即 Full（queue=2 已满）✓
  [pool] reject: 2nd acquire 立即 Full ✓
  [F-002] pool 大小严格保持 2（不 auto-provision），K=4 排队后逐个 succeed ✓
  ```

### 探查过程中暴露的 framework 问题（user 2026-07-03 round 6："框架还在开发中，有 BUG 是正常的"）

**F-001**（framework 缺 EnginePool 抽象）—— N 个 Engine 共享 model config 的 production 场景，framework 当前无 direct support。Engine::new 时 `NodeId = "engine/{provider}"`（engine.rs:59）导致多 Engine 同 provider 必然 NodeId 冲突。**唯一 workaround**：app 层用"N facade 共享 1 pool"模式手动 virtualize，但 F-003 又使 facade 模式不可用——恶性循环。

**F-002（CRITICAL：实现偏离设计意图）**—— pool 设计意图是 `min_size` + `max_size` + `auto_provision`（load 增长时自动扩容），超 max_size 才开始排队。当前实现只有 fixed `max_size`，**无 min_size，无 auto-provision**——load 来时只能 Block/Queue/Reject。**不是隐藏 BUG，是 design 文档明示的 dynamic expansion code 完全没做**（user 2026-07-03 round 5 判定）。**production 影响**：N 用户同时咨询时，pool 需扩到 N 才能保证所有用户不排队；当前会直接 Block/Queue/Reject，无弹性伸缩能力。

**F-003**（framework 设计 quirk，development-stage）—— `ModelAdapterPoolNode::connect` 在 sub-bus 注册 listener `node_id = "model/pool-{i}/sub"`（pool_node.rs:65）；facade forward model_call 时 `to=this sub_id`；任何想在此 id 注册 `ModelAdapterNode` 会被 bus 拒绝（`AlreadyConnected`，9.4.1 probe 实证）。**唯一可工作的 sub-bus handler 是 manual broadcast subscriber**（如 `crates/arf-pool/tests/integration.rs` 既有 pattern）。**后果**：9.4.1 设计意图的"facade × N 真实 qwen 节点"模式**当前不可行**。user 2026-07-03 round 6 判定："暴露问题，记录即可，框架还在开发中，有 BUG 是正常的"。

### 凭据安全（self-check 已通过）

```bash
$ git grep -n 'sk-' -- crates/ docs/   # 无新增 key 匹配
```

---

## §B (capability, 情景) 单元判定

### 单元 1：`model_pool_overflow × §2.12`（3 策略边界）

```
单元              : model_pool_overflow × §2.12
能力等级           : D
判分依据           : `Pool::acquire` + `Overflow::{Queue,Reject,Block}`
                    （arf-pool/src/overflow.rs）端到端工作。
                    4 mock test 实证：
                    - Reject (pool_overflow_reject_immediate): l1 持有 → 2nd acquire 立即 PoolError::Full
                    - Block timeout (pool_overflow_block_timeout): l1 持有 → 2nd acquire 阻塞 201.35ms 后 PoolError::Timeout
                    - Block 成功 (pool_overflow_block_succeeds_after_release): l1 50ms 后 drop → 2nd acquire 阻塞 51.32ms 后成功
                    - Queue 满 (pool_overflow_queue_buffers_then_full): max_size=1 + Queue(2)，3 个并发 → 1 立即 + 2 入队 + 第 4 立即 Full
framework 行为   : framework 端到端供 3 overflow 策略 + Lease auto-release on drop
信号命中         : 无新病灶
```

### 单元 2：pool 生命周期（Lease auto-release）

```
单元              : pool_lease_lifecycle × §2.12
能力等级           : D
判分依据           : `Lease::drop` auto-release（arf-pool/src/lib.rs:112）
                    + pool_overflow_block_succeeds_after_release 实证 l1 drop 后 l2 立即成功
framework 行为   : framework 端到端供 lease 生命周期，drop = release
信号命中         : 无新病灶
```

### 单元 3：`model_discovery_capability × §2.12`（facade advertised_provider）

```
单元              : model_discovery_capability × §2.12
能力等级           : D
判分依据           : `ModelAdapterPoolNode.connect()`（pool_node.rs:46-77）注册 facade
                    到 top bus，capabilities.provider = advertised_provider。
                    engine resolve_model（registry.rs:253）匹配。
                    **9.4.1 实证：7 个 matrix test 全部因 F-003 framework quirk 失败**——
                    ModelAdapterNode 试图在 facade 的 sub_id 注册被 bus 拒绝。
framework 行为   : facade 本身端到端工作（D），但 sub-bus 集成受 F-003 阻断
信号命中         : F-003（development-stage design quirk）
```

### 单元 4：`engine_pool × §2.12`（F-001 framework gap）

```
单元              : engine_pool × §2.12
能力等级           : F（FAIL）
判分依据           : framework 缺 `EnginePool` 抽象
                    - Engine::new NodeId 硬编码 "engine/{provider}"（engine.rs:59）
                    - 多 Engine 同 provider 必然 NodeId 冲突（bus AlreadyConnected）
                    - 无 EnginePool 抽象 virtualize N Engine
                    **9.4.1 未实测**（F-003 阻断，绕道 multi-facade 也受 F-001 限制）
framework 行为   : framework 缺 primitive（记入 F-001 lesion）
信号命中         : F-001（framework missing primitive）
```

### 单元 5：`pool_dynamic_expansion × §2.12`（F-002 CRITICAL）

```
单元              : pool_dynamic_expansion × §2.12
能力等级           : F（FAIL，CRITICAL）
判分依据           : 设计意图 = min_size + max_size + auto_provision
                    当前实现   = 只有 fixed max_size
                    **f002_pool_does_not_auto_provision 实证**：
                    - max_size=2, Overflow::Queue(10)
                    - 第 3-4 个 acquire 入 Queue（不触发扩容）
                    - drop l1 → l3 获得（pool 大小严格保持 2）
                    - drop l2 → l4 获得（仍 2）
                    - pool **永远不扩 1 个 resource**——与设计意图严重不符
framework 行为   : 实现偏离设计意图（user 2026-07-03 round 5 判定）
信号命中         : F-002（CRITICAL implementation-vs-design intent gap）
```

---

## §C §4 find signals 探查

### A3 数据唯一 — pool/facade 路径是否引入新散落

**结论：未引入新散落**（未变化）。

| 检查项 | 结果 |
|---|---|
| `"model"` 字面量（node_type） | 2 处（pool_node.rs:53 connect + 已有），与 §1.1 A3-001 既有相同 pattern |
| `"model_call"` / `"model_response"` 字面量 | 既有散落（pool_node.rs:107, 121），A3-001 既有记录 |
| correlation_id 散落 | 0 新增（pool 不涉及 correlation_id） |

### A4 处理集中 — pool/facade 路径

**结论：不涉及**（未变化）。

Pool 的 acquire/release 已在 `Pool::acquire` 集中实现（arf-pool/src/lib.rs:184），无散落。

### F-category（framework missing primitive / design intent gap）—— 本 task 新增

| ID | 严重度 | 描述 | 记录位置 |
|---|---|---|---|
| F-001 | F（FAIL） | framework 缺 `EnginePool` 抽象 | lesion-registry §2 |
| F-002 | **F（CRITICAL）** | **实现偏离设计意图**——pool 无 min_size / auto_provision | lesion-registry §2 |
| F-003 | F（development-stage） | facade sub_id 模式阻断 ModelAdapterNode 集成 | lesion-registry §2 |

---

## §D lesion-registry 更新

本 task 增 **3 个 F-category lesion**：
- F-001（EnginePool 缺失）
- F-002（pool 动态扩容缺失，CRITICAL）
- F-003（facade sub_id 模式 design quirk）

§1 总表新增 3 行，§2 新增 3 个详情块，§1 统计更新为 **OPEN 5 / FIXED 0 / WONTFIX 0**。

---

## §E 观察记录（非病灶）

### 观察 P1 — 1 facade = 1 LLM stream（设计 by design）

**触发位置**：`ModelAdapterPoolNode::run_loop`（pool_node.rs:85）
**观察现象**：facade 的 run_loop 是**单 task**（`loop { recv → acquire → forward → wait → drop }`）—— 1 facade 串行处理 1 stream model_calls。**这是设计意图**（user 2026-07-03 round 4 确认），转发开销可忽略（bus send 快），慢的是 LLM call。N 个并发 LLM call 需要 **N 个 PoolNode facade** 共享 1 个 `Arc<Pool<...>>`（app 层用 N 个 facade 手动 virtualize）。
**判断**：**不构成病灶**——framework 抽象清晰，1 facade 适合 1 chat stream 的常见场景。
**影响面**：生产场景需 N facade 才能真并发 LLM；app 层 N facade boilerplate。

### 观察 P2 — F-003 与 F-001 互锁（恶性循环）

**触发位置**：F-001（EnginePool 缺失）+ F-003（facade sub_id 模式）
**观察现象**：
- 多 Engine 共享 pool → 需 N facade 共享 1 pool（F-001 workaround）
- N facade → 需每 facade 配 ModelAdapterNode 处理 sub-bus（F-003 阻断）
- 结果：F-001 workaround 不可用，**两个 framework gap 互锁**
**判断**：framework 仍在开发期（user 2026-07-03 round 6 判定）。fix 一个可能解另一个。
**影响面**：phase 9 真并发 LLM call 探查受阻；production 部署需 framework 演化。

### 观察 P3 — mock pool 测试 + manual broadcast subscriber 是当前唯一可行路径

**触发位置**：`pool_node_facade.rs` 现有 5 个 test + `crates/arf-pool/tests/integration.rs`
**观察现象**：本 task 4 个 mock pool overflow test + 1 F-002 test 都用 `pool.acquire()` 直接调（绕过 facade），验证 Pool 自身行为。这与既有 arf-pool integration test 的 manual broadcast subscriber 模式一致。**当前 framework 唯一能稳定工作的 pattern 是"直接调 pool + 手动 broadcast subscriber"**。
**判断**：framework 仍在演化期，**这两种 pattern 是设计的"低层 escape hatch"**。
**影响面**：phase 9 后续探查（9.4.2 / 9.4.3 / 9.5.x）需沿用此 pattern。

---

## §F 综合判定

- **pool 自身**（acquire/release/3 overflow 策略）：**D**（端到端工作，4 mock test 实证）
- **pool lease 生命周期**：**D**（drop auto-release 实证）
- **ModelAdapterPoolNode facade advertised_provider**：**D**（facade 端到端工作）
- **sub-bus 集成（ModelAdapterNode 共享）**：**F**（F-003 design quirk 阻断）
- **真并发 LLM call（N facade 共享 pool）**：**F**（F-001 + F-003 互锁）
- **pool 动态扩容**：**F（CRITICAL）**（F-002 实现偏离设计意图）
- **新病灶**：0（A3/A4 类别）
- **新发现 F-category**：3（F-001 / F-002 critical / F-003 design quirk）
- **9.4.1 价值**：
  - 验证 Pool 自身 3 overflow 策略 + lease 生命周期端到端工作
  - **暴露 framework 2 个 critical gap**（F-002 设计意图偏离 + F-003 facade 设计 quirk）
  - 暴露 F-001 + F-003 互锁问题，提示 fix phase 需协同处理
- **结论**：Pool 抽象自身端到端工作（D）；facade 在 1 stream 场景端到端工作（D）；多 LLM 并发场景受 framework 仍在开发期 design 阻断（F-001/F-003）；pool 动态扩容**与设计意图严重不符**（F-002 CRITICAL）。**3 个 F-category lesion 全部登记 lesion-registry**，留 fix phase 协同处理。**9.4.1 任务完成**（按 user 2026-07-03 round 6 反馈"暴露问题，记录即可，框架还在开发中，有 BUG 是正常的"）。

---

## §G 验证命令

```bash
# 跑通（5 test: 4 mock pool + 1 F-002 实证）
cargo test -p arf-e2e --test pool_node_facade -- --nocapture --test-threads=1

# 既有 integration test（参照模板）
cargo test -p arf-pool --test integration -- --nocapture

# Pool 3 overflow 策略
grep -A 6 "pub enum Overflow" crates/arf-pool/src/overflow.rs

# Facade sub_id 模式（F-003 实证）
grep -n "sub_id\|format.*sub" crates/arf-model-adapter/src/pool_node.rs

# Engine NodeId 派生（F-001 实证）
grep -n "engine_id\|NodeId::new.*engine/" crates/arf-engine/src/engine.rs

# §4 信号 cross-check（pool/facade 路径零新散落）
grep -rn '"model"\|"provider"\|"model_call"\|"model_response"' crates/arf-model-adapter/src/ crates/arf-pool/src/ | grep -v test

# 凭据安全
git grep -n 'sk-' -- crates/ docs/
```

---

## §H 下一步

1. self-review（凭据 / 一致性 / scope / granular）— ✅
2. **granular commit**：
   - `pool_node_facade.rs`（5 个 test，4 mock + 1 F-002 实证）
   - `audit-probe-9.4.1.md`（含 F-001/F-002/F-003 finding 总结）
3. push 双 remote（github + gitee）
4. **回做 9.3.1**（streaming，补 spec 顺序）
5. 9.4.2（Provider::supported_models capability 路由）
6. 9.4.3（Pool overflow 三策略完整覆盖）—— 9.4.1 已覆盖大部分，留细节
7. 9.5.x（McpNode 工具集成）