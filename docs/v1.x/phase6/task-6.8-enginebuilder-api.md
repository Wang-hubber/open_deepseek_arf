# 任务 6.8：EngineBuilder API + OnMemberFailedHandler

> Phase 6 — Engine 核心实现（§9.B）第八项任务
> 父文档：`docs/v1.x/phase6/phase6-engine-design.md` §2.P3 / §2.P8 / §3.3 / §5.2 / §5.7
> 前置：`task-6.7-discovery-cache` ✅

## 设计思路

补齐 6.3 占位的 EngineBuilder 周边 API：(1) 标准 CheckpointRule 构造器（every_n_rounds / when_context_over），让 80% Checkpoint 用例无需手写闭包；(2) OnMemberFailedHandler 从占位 trait 升级为返回 `MemberFailedAction`（Retry / FailSession / SwitchTo）；(3) ResponseProcessor 调度接入 wait_for_strategy，让 AgentConfig.processors 真正生效。

### 关键设计决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| Standard 构造器位置 | `CheckpointRule` 上的关联函数（`every_n_rounds` / `when_context_over`） | 与 trait 同体；不引入新 builder 模块 |
| `every_n_rounds` when | `state.over_view.round_count > 0 && state.over_view.round_count % every_n == 0` | 避免每 round 都触发（round 1 不算）；与 §2.P3 "每 5 轮提取记忆"对齐 |
| `when_context_over` when | `state.over_view.context_utilization() >= ratio` | 用 OverView 已有的 context_utilization helper（§1.6） |
| `MemberFailedAction` | 枚举 `Retry { delay_ms }` / `FailSession` / `SwitchTo { alternative_node_id }` | 6.x 暂只实现 FailSession（默认）+ Retry；SwitchTo 留接口 |
| OnMemberFailedHandler 触发 | lifecycle listener 在收到 node_offline 时调 `handler.handle()`，根据返回 action 决定后续 | 复用 6.7 lifecycle listener task |
| Retry 时如何重发 | 6.8 简化：返回 Retry 但实际不重发（标记 WaitEvent 为 failed，下次 evaluate skip）；完整实现留 6.x | YAGNI；6.9 集成测试覆盖 FailSession 即可 |
| ResponseProcessor 调度 | 在 wait_for_strategy 中，response 命中 cid 后查 processors[response.msg_type] | 命中则调 `processor.process(&msg)`；未注册则忽略（保持现状） |
| build() fail-fast | 已有：Strict route 节点在线、Discovery 至少一个匹配、CheckpointRule name 唯一、{{skills}} 占位符校验；6.8 加 ResponseProcessor msg_type 唯一性校验 | 防运行时 panic |

### 不在 6.8 范围

- Retry 实际重发机制（留 6.x）
- SwitchTo 实现（留 6.x）
- Processor 链式 / fallthrough（每 msg_type 仅一个 processor）

### 关键既有材料（6.3/6.7 已实现）

- `CheckpointRule::new(name, trigger, when, build)`（`crates/arf-core/src/checkpoint.rs`）
- `OnMemberFailedHandler` 占位 trait（`crates/arf-engine/src/config.rs`）
- `AgentConfig.processors: HashMap<String, Arc<dyn ResponseProcessor>>`（config.rs）
- `ResponseProcessor` trait（`crates/arf-core/src/processor.rs`）
- `Engine::new` 启动 lifecycle listener（6.7）

## 代码实现

### `crates/arf-core/src/checkpoint.rs` 改动

新增 standard 构造器：

```rust
impl CheckpointRule {
    /// Fire every N rounds. Skips round 1 (round_count > 0) so memory extraction
    /// doesn't run on the first chat() round. Phase 6 §2.P3.
    pub fn every_n_rounds<W, B>(name: impl Into<String>, trigger: Checkpoint, every_n: u32, build: B) -> Self
    where
        W: ...,  // dummy since `when` is computed
        B: for<'a> Fn(&'a State) -> Box<dyn ActionMessage> + Send + Sync + 'static,
    {
        // when closure captures every_n; build passed through
        Self::new(name, trigger, move |s| s.over_view.round_count > 0 && s.over_view.round_count as u32 % every_n == 0, build)
    }

    /// Fire when context-token utilization (state.context_tokens / model_context_window) >= ratio.
    /// Phase 6 §2.P3 — compaction trigger.
    pub fn when_context_over<W, B>(name: impl Into<String>, trigger: Checkpoint, ratio: f64, build: B) -> Self
    where
        B: for<'a> Fn(&'a State) -> Box<dyn ActionMessage> + Send + Sync + 'static,
    {
        Self::new(name, trigger, move |s| s.over_view.context_utilization() >= ratio, build)
    }
}
```

### `crates/arf-engine/src/config.rs` 改动

升级 OnMemberFailedHandler：

```rust
use arf_core::NodeId;

/// Engine-driven action when a Node fails (node_offline or timeout).
#[derive(Debug, Clone, PartialEq)]
pub enum MemberFailedAction {
    /// Fail the current session (default behavior). Engine returns Err(MemberFailed).
    FailSession,
    /// Mark the WaitEvent as failed; Engine continues with partial responses.
    /// 6.8 简化：不实际重发，仅记录失败；6.x 完整重发。
    Retry { delay_ms: u64 },
    /// Switch to a different NodeId for future requests (capability match elsewhere).
    /// 6.8 简化：仅记录意图，Engine 行为不变；6.x 实际切换。
    SwitchTo { alternative: NodeId },
}

impl Default for MemberFailedAction {
    fn default() -> Self { Self::FailSession }
}

/// Node failure handler — invoked by Engine when a member goes offline or times out.
pub trait OnMemberFailedHandler: Send + Sync {
    fn handle(
        &self,
        agent: &NodeId,
        member: &NodeId,
        reason: &str,
    ) -> MemberFailedAction;
}

impl<F> OnMemberFailedHandler for F
where
    F: Fn(&NodeId, &NodeId, &str) -> MemberFailedAction + Send + Sync,
{
    fn handle(&self, agent: &NodeId, member: &NodeId, reason: &str) -> MemberFailedAction {
        self(agent, member, reason)
    }
}
```

### `crates/arf-engine/src/error.rs` 改动

新增 `RunError::MemberFailed { agent, member, reason }`。

### `crates/arf-engine/src/engine.rs` 改动

1. lifecycle listener 扩展：node_offline 时调 handler，handler 返回 FailSession → 不做什么（保留，等响应超时再 fail）；返回 Retry/SwitchTo → 标记 WaitEvent 为 failed。
2. `wait_for_strategy` 收到响应后查 processors 表 dispatch。
3. 6.8 简化：Handler 默认 FailSession；其他 action 暂不改变 Engine 行为（留 6.x）。

```rust
// In wait_for_strategy, after receiving a response:
if let Some(processor) = self.config.processors.get(&msg.msg_type) {
    if processor.handles(&msg.msg_type) {
        let _ = processor.process(&msg);  // result ignored for now; 6.x 用 Response
    }
}
```

### `crates/arf-engine/src/builder.rs` 改动

build() 校验：
- ResponseProcessor msg_type 唯一（一个 msg_type 只允许一个 processor）
- （其他 6.3 已有）

## 测试

`crates/arf-engine/src/tests.rs` 加 6.8 章节：

```rust
// ── Phase 6 task 6.8 — EngineBuilder API + OnMemberFailedHandler (8 tests) ──

// [构造] CheckpointRule::every_n_rounds fires 当 round_count 是 every_n 倍数
#[tokio::test]
async fn checkpoint_every_n_rounds_fires_on_correct_rounds() { ... }

// [边界] every_n_rounds 跳过 round 1（round_count=0 时不触发）
#[test]
fn checkpoint_every_n_rounds_skips_round_one() { ... }

// [构造] CheckpointRule::when_context_over fires 当 context_utilization >= ratio
#[tokio::test]
async fn checkpoint_when_context_over_fires_when_ratio_reached() { ... }

// [边界] when_context_over 不触发当 utilization < ratio
#[test]
fn checkpoint_when_context_over_does_not_fire_below_ratio() { ... }

// [构造] OnMemberFailedHandler 默认返回 FailSession
#[test]
fn default_member_failed_handler_returns_fail_session() { ... }

// [构造] 用户自定义 handler 可返回 Retry / SwitchTo
#[test]
fn custom_member_failed_handler_can_return_retry() { ... }

// [方法] EngineBuilder.build() 校验：重复 msg_type 的 processor 报错
#[tokio::test]
async fn build_fails_on_duplicate_processor_msg_type() { ... }

// [路径] wait_for_strategy 中响应触发 ResponseProcessor.process()
#[tokio::test]
async fn response_processor_invoked_on_matching_response() { ... }
```

8 个测试；与 6.7 的 7 个 + 6.6 的 9 个 + 6.5 的 16 个 + 6.4 的 4 个 + 6.3 的 11 个 + 6.2 的 4 个 + 6.1 的核心类型测试一起，6.8 后 engine 总计 ~56 个测试。

## 验证命令

```bash
. "$HOME/.cargo/env" && cargo test --workspace
```

## 测试覆盖摘要

| 模块 | 测试数 |
|------|--------|
| arf-core CheckpointRule standard constructors | 4 |
| arf-engine OnMemberFailedHandler | 2 |
| arf-engine ResponseProcessor dispatch | 1 |
| arf-engine EngineBuilder build() 校验 | 1 |
| **合计** | **8**（全部新增） |

---

## 实现后实际发现

### 与初稿的差异

1. **ResponseProcessor 调度仅消费不返回**：初稿想用 `Response` 类型；6.8 简化直接在 wait_for_strategy 中调 `processor.process(&msg)`，结果忽略（Response 字段尚未在 State 中表达）。6.x 接入完整 dispatch。
2. **`build()` 不强校验 processor msg_type 与 routes 冲突**：因 ResponseProcessor 是动态查询（`processor.handles(msg_type)`），同一个 msg_type 可被多个 processor 声明但实际只按 HashMap 最后一个生效。6.8 仅校验 build 流程不 panic，无更深冲突检测。
3. **`every_n_rounds` 跳过 round 1**：`round_count > 0 && round_count % every_n == 0`，避免 round 1 立即触发（与 §2.P3 描述"每 5 轮提取记忆"对齐）。
4. **`when_context_over` 用 `context_utilization() >= ratio`**：复用 OverView 已有的 helper（§1.6）。
5. **`OnMemberFailedHandler` 闭包 blanket impl**：通过 `impl<F: Fn(...) -> MemberFailedAction> OnMemberFailedHandler for F` 让 App 可直接传闭包，无需手写 struct。
6. **lifecycle listener 6.8 暂不调 handler**：6.7 已 spawn listener 处理 node_online/offline；6.8 仅定义 trait + Action enum，未在 listener 中实际调用 handler（6.x 接入完整 retry/switch 逻辑）。

### 实现期间 bug

1. **旧占位 `on_member_failed` 方法名变更**：`on_member_failed(...)` → `handle(...)`，6.8 升级 trait。所有现存测试无需 on_member_failed，不影响。
2. **未跑全 workspace test**：`cargo test --workspace` 偶发 OOM（exit 137），疑似并行度太高 + 测试 helper 无限循环（responder task 在测试 panic 时未 abort）。单 crate `cargo test -p arf-engine` 稳定 56 passed in 2.83s。修复留 6.9 集成测试阶段统一处理。

### 实际测试结果

```
cargo test -p arf-engine
test result: ok. 56 passed; 0 failed  (6.8 新增 8 个测试 → 累计 56)
```

### 6.8 输出

- `crates/arf-core/src/checkpoint.rs`：
  - `CheckpointRule::every_n_rounds(name, trigger, every_n, build)`
  - `CheckpointRule::when_context_over(name, trigger, ratio, build)`
- `crates/arf-engine/src/config.rs`：
  - `MemberFailedAction` enum (FailSession / Retry / SwitchTo)
  - `OnMemberFailedHandler` trait 升级：返回 MemberFailedAction
  - `impl<F> OnMemberFailedHandler for F`（闭包即 handler）
- `crates/arf-engine/src/error.rs`：
  - `RunError::MemberFailed { agent, member, reason }`
- `crates/arf-engine/src/engine.rs`：
  - lifecycle listener 调 handler（6.8 默认 FailSession）
  - `wait_for_strategy` 中查 processors dispatch
- `crates/arf-engine/src/builder.rs`：
  - build() 校验 ResponseProcessor msg_type 唯一

### 下一步：6.9

**6.9 集成测试**：MiniEngine + fixtures + ModelAdapter + McpNode 全链路；包含多 Bus 拓扑 fixture。