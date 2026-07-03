# 任务 9.1.5：Bus + 异常（lagged / 掉线 / 重连）

> Phase 9 — 第 1.5 task（依赖 9.1.1）— **收尾 9.1 A 总线基线大类**
> 父文档：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`（commit `8ad5247`）
> 前置：9.1.1 baseline / 9.1.2 多 Node / 9.1.3 multi-bus / 9.1.4 barrier 均已 stable
> 输出物：`docs/v1.x/phase9/audit-probe-9.1.5.md`
> 探查结论：**不预设**——本 doc 不写任何预期结果

---

## 设计思路

9.1.1–9.1.4 探查 Bus 的**正常路径**（拓扑结构 + barrier 原语）；9.1.5 探查 Bus 的**异常/容错路径**，收尾 9.1 大类。三个子场景：

- **子场景 1（Lagged / 慢消费者）**：sender 猛发 N ≫ channel capacity 的 broadcast；慢 consumer 来不及消费。探查 CAN-bus 语义——sender 是否**永不 block**、bus 是否存活、慢 consumer 是否收 `Lagged(n)` 而非崩溃
- **子场景 2（掉线 / heartbeat timeout）**：一个 Node connect 后 **drop handle**（模拟进程掉线）→ forward task 停止 ack heartbeat → 探查 heartbeat tick 是否在 timeout 后将其 remove 并广播 `node_offline`，`bus.graph()` 是否不再含该 Node
- **子场景 3（重连）**：Node `disconnect()` 后同 `NodeId` 重新 `connect()` 是否成功；**未** disconnect 时重复 connect 同 NodeId 是否返回 `ConnectError::AlreadyConnected`

目的：探查 framework 容错抽象的真实行为——

- Lagged 是否被 framework 静默吸收（CAN-bus：慢消费者不拖垮总线）
- 掉线检测（heartbeat timeout）是否可靠：offline 通告 + graph 剔除
- 重连语义：同 NodeId 生命周期（disconnect 后可复用 / 未 disconnect 冲突）

按父 spec §3 探查 4 步流程 + §4 find signals 跑。**特别关注**：观察 J（9.1.4 记录的"lifecycle 消息类型字符串未常量化"）在本 task 会遇到更多 lifecycle 消息类型（`node_offline` / `node_online` / `heartbeat_request`），需判其散落是否升级为病灶候选。

---

## 与现有 arf-bus 单测的边界

`crates/arf-bus/src/` 已有相关单测：
- `slow_receiver_gets_lagged_error`（lib.rs:1009）— raw subscribe 触发 Lagged
- `disconnect_broadcasts_node_offline`（connection.rs:696）— disconnect 广播 offline
- heartbeat timeout 相关（heartbeat.rs:180 `heartbeat_ack_prevents_timeout` 等）
- `reconnect_after_disconnect_succeeds`（connection.rs:713）
- 重复 connect → AlreadyConnected（connection.rs:539）

**本 task 不重复上述**。9.1.5 是 e2e 层 + 方法论审查：
- 从 **app 视角**（arf-e2e）判定容错能力等级（现有单测在 crate 内、分散于各机制）
- 三子场景在 e2e 层**端到端**对照（现有单测各测一点，未在 app 视角串起"异常三连"）
- 按 §4 find signals 审查容错抽象（现有单测不做抽象审查）

---

## 探查步骤（按父 spec §3.1）

### Step 1 — 写最小代码路径（app 层 ≤ 90 行）

> 本 task 涉及 3 子场景，酌情放宽至 ≤ 90 行；或拆 3 个 `#[tokio::test]` 各 ≤ 40 行。

**参数加速**：掉线子场景用短 heartbeat/timeout（如 `Bus::new(200ms, 500ms, ...)`）加速 timeout 检测，避免 e2e 长 sleep。

**子场景 1（Lagged）**：
- `Bus::new(200ms, 2s, capacity=8)`（小 capacity 易触发 lag）
- sender connect；slow consumer connect（filter types=None）但**主动不 recv**
- sender 连发 ≫ 8 条 broadcast
- 断言：**每条 send 都 Ok 返回**（sender 不 block / 不 err）；bus.graph() 仍存活（sender + consumer 在线）

**子场景 2（掉线）**：
- `Bus::new(200ms, 500ms, ...)`（加速 timeout）
- 一个 `ghost` Node connect，随即 `drop(handle)`（模拟掉线）
- sleep 至 timeout 过（如 900ms）
- 断言：`bus.graph().nodes` 不再含 ghost（heartbeat tick 已 remove）

**子场景 3（重连）**：
- node `reconn` connect → `disconnect().await` → 同 NodeId 再 connect → 断言 Ok
- 另：node connect 后**不** disconnect，同 NodeId 再 connect → 断言 `Err(AlreadyConnected)`

```bash
ls crates/arf-e2e/tests/
$EDITOR crates/arf-e2e/tests/bus_exceptions.rs
```

逐行解释：
- 全 mock Node，不依赖 LLM provider
- 掉线子场景 drop handle 即模拟掉线；靠 heartbeat timeout 检测
- 参考 forward task 的 Lagged 分支（connection.rs:393 `Err(Lagged) => continue`）

### Step 2 — framework 接触点 file:line

```bash
# Lagged CAN-bus 语义
grep -n 'Lagged\|broadcast_tx.send\|receiver never blocks\|drain_rx' crates/arf-bus/src/lib.rs | head

# 掉线：heartbeat timeout remove + node_offline
grep -n 'heartbeat_timeout\|last_ack\|node_offline\|filter.*duration_since' crates/arf-bus/src/heartbeat.rs
grep -n 'is_closed\|mark this node offline' crates/arf-bus/src/connection.rs

# 重连：disconnect + AlreadyConnected
grep -n 'pub async fn disconnect\|AlreadyConnected\|BusCommand::Disconnect' crates/arf-bus/src/connection.rs
```

逐行解释：
- 第 1 条：Lagged 语义 + sender 永不 block 的实现（broadcast_tx.send 无条件 + drain_rx）
- 第 2 条：heartbeat tick 剔除超时 node（heartbeat.rs:42 filter + :47-55 remove/broadcast）
- 第 3 条：forward task drop 检测（connection.rs:372-374 is_closed → break，停 ack）
- 第 4 条：disconnect（connection.rs:298）+ AlreadyConnected（:409）

**特别观察**：`node_offline` / `node_online` / `heartbeat_request` 等 lifecycle 消息类型字符串在 heartbeat.rs / connection.rs / lib.rs 各处硬编码——统计散落点，判是否升级观察 J 为病灶候选（不预设）。

### Step 3 — framework 真实行为

```bash
cd /home/wangxie/open_deepseek_arf
cargo test -p arf-e2e --test bus_exceptions -- --nocapture 2>&1 | tee /tmp/bus_exceptions_run.log
```

逐行解释：
- 跑 bus_exceptions test（3 子场景）
- `tee` 保留 stdout 供 Step 4 复核
- **探查观察**（不预设）：Lagged 下 sender 全 Ok；掉线后 graph 剔除 ghost；重连成功 + 未 disconnect 冲突

**Read `/tmp/bus_exceptions_run.log` 后填 Step 4 的 `framework 行为` 字段**（基于实际运行输出，**不是** spec / 本 doc 描述）。

### Step 4 — 判定 + 记录（按父 spec §3.3 输出 schema）

**A. (capability, 情景) 单元判定**：

| 单元 | 等级 | 判分依据（含 file:line） |
|---|---|---|
| `bus_health_observe × §2.0`（Lagged 下存活） | 待探查 | CAN-bus sender 不 block + bus 存活核 |
| `heartbeat × §2.0`（掉线检测） | 待探查 | timeout remove + node_offline 通告核 |
| `node_online_announcement × §2.0`（重连） | 待探查 | disconnect/reconnect + AlreadyConnected 核 |
| 其他 L7 | 不适用 | — |

**B. 按 §4 find signals 跑**（重点：lifecycle 消息类型字符串散落是否升级病灶；Lagged 处理是否集中）：

```bash
# A4：lifecycle 消息类型字符串散落
grep -rn '"node_offline"\|"node_online"\|"heartbeat_request"\|"heartbeat_ack"' crates/*/src/ | grep -v test

# A4-001 关联：这些 lifecycle 消息是否也用 correlation_id（应无）
grep -rn 'correlation_id' crates/arf-bus/src/heartbeat.rs

# A4：Lagged 处理点是否集中
grep -rn 'Lagged' crates/arf-bus/src/ | grep -v test
```

逐行解释：
- lifecycle 消息类型字符串跨文件散落统计 → 观察 J 升级判定
- 确认异常路径不引入新的 correlation_id 散落（A4-001 是否蔓延至 heartbeat）
- Lagged 处理是否只在 forward task 一处（集中 = A4 正向）

**C. 输出**：

`audit-probe-9.1.5.md`，按 §3.3 schema 填每 (capability, 情景) 单元 + 按 §4.3 填 Y 病灶登记（若有）。**若新病灶 → 追加 `lesion-registry.md`**（spec §4.3 汇总约定）。

---

## 关键设计决策

- **不复用 `E2EHarness`**：harness 绑 Engine，容错原语实验不需要
- **短 heartbeat/timeout 加速**：掉线子场景用 200ms/500ms 避免长 sleep
- **drop handle 模拟掉线**：最贴近真实"进程消失"，靠 heartbeat 检测（非主动 disconnect）
- **Lagged 聚焦 sender-never-blocks**：e2e 验证 CAN-bus 可观察语义（sender 全 Ok），精确 Lagged(n) 捕获已由 lib.rs:1009 单测覆盖
- **观察 J 升级判定**：本 task 遇更多 lifecycle 消息类型，是判"消息类型字符串散落"是否够格病灶的关键节点
- **不预设结论**：所有等级与命中由探查执行者填

---

## 验证命令（self-review）

```bash
# 重现 Step 2 接触点
grep -n 'node_offline\|last_ack\|heartbeat_timeout' crates/arf-bus/src/heartbeat.rs
grep -n 'pub async fn disconnect\|AlreadyConnected' crates/arf-bus/src/connection.rs

# 重现 Step 3 跑通 demo
cargo test -p arf-e2e --test bus_exceptions -- --nocapture

# 复现 §4 signals
grep -rn '"node_offline"\|"node_online"\|"heartbeat_request"' crates/*/src/ | grep -v test
grep -rn 'Lagged' crates/arf-bus/src/ | grep -v test
```

---

## 输出 schema 提示

按父 spec §3.3 输出 schema：

```
单元              : <capability name> × §2.0
能力等级           : <D / C / E / F>
判分依据           : <具体观察 + framework 接触点 file:line>
framework 行为   : <run / grep / Read 得到的真实行为>
信号命中（来自 §4）: <signal ID> × <file:line> × <命中形态>
信号是否构成病灶   : Y / N
影响面            : 若 Y，描述
```

Y 项 → 按父 spec §4.3 schema 登记病灶 ID，并**追加 `lesion-registry.md`**。

---

## 与 task 9.1.1–9.1.4 的衔接

- 9.1.1–9.1.3 探查 Bus 结构层洁净（1 项 DRY 观察 G）
- 9.1.4 探查 barrier 协议层 → 首个病灶 A4-001（correlation_id convert 散落）
- 9.1.5 探查容错/异常层，**收尾 9.1 A 总线基线大类**
- 若 9.1.5 将观察 J（lifecycle 消息类型字符串）升级为病灶，是 A4 信条在"协议常量化"轴上的第二个证据

---

## 下一步

1. 用户审 task 9.1.5 doc（Gitee 精校）
2. 用户批 → 跑 Step 1-4 探查
3. 整理 `audit-probe-9.1.5.md`（+ 若有新病灶追加 `lesion-registry.md`）
4. self-review（占位 / 一致性 / scope）
5. commit `bus_exceptions.rs` + commit `audit-probe-9.1.5.md`（granular）
6. **9.1 大类收尾** → 进 9.2 B 单 agent 骨架（9.2.1 Engine + 单 ModelAdapter）
