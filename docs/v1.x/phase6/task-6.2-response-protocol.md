# 任务 6.2：Response 协议

> Phase 6 — Engine 核心实现（§9.B）第二项任务
> 父文档：`docs/v1.x/phase6/phase6-engine-design.md` §1.2 / §2.P5
> 前置：`task-6.1-core-types` ✅

## 设计思路

定义 Engine 处理 Bus 响应的协议层抽象。本任务**只做类型定义**——不实现 Engine run 循环（6.3 / 6.4）或响应 dispatch 表（6.5）。

按设计文档 §1.2 + §2.P5：

- **`Response::Done(Value)`**：Engine 唯一关心的响应形态。`Value` 是 JSON，Engine 不解析内部字段（语义归上层）
- **无 `Failed` / `Wait` / `Err` variant**：错误走 `node_offline` lifecycle signal + `OnMemberFailedHandler`（§2.P8）
- **`ResponseProcessor` trait**：把 raw `Message` 转 `Response`。App 注册 processor 给非内置 msg_type；内置 msg_type 隐式 dispatch（§1.1 白名单）

### 关键设计决议（来自 §2.P5）

> 决定（2026-06-30）：
> 1. **Engine 不解析 `Receiver` 内部错误**——Receiver 内部错误处理是 App 开发者在执行节点（ModelAdapter / McpNode）中的职责
> 2. Receiver 崩溃表现为两种 Engine 可观察的信号：
>    - `node_offline` lifecycle signal → `OnMemberFailedHandler`
>    - 超时（hang 但未下线）→ `OnMemberFailedHandler`
> 3. **业务错误**（如 "permission denied"、"model refused"）作为 `Value.content` 正常返回，由 processor 解释

## 代码实现

### `crates/arf-core/src/response.rs`（新建）

```rust
//! Response — single-variant protocol for Bus receivers.
//!
//! Phase 6 task 6.2: Engine protocol semantics.
//!
//! **Invariants** (Phase 6 §1.2 / §2.P5):
//! - Only `Done(Value)`: Engine doesn't dispatch error variants
//! - Errors flow through `node_offline` lifecycle + `OnMemberFailedHandler`
//! - Business errors (e.g. permission denied) ride inside `Value` and are
//!   interpreted by App's `ResponseProcessor`
//! - Engine has no `Wait` variant: slow consumer ≠ engine-pause

use serde::{Deserialize, Serialize};
use serde_json::Value;

/// Engine-facing response type.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub enum Response {
    /// Final value from receiver. `Value`'s internal schema is up to the
    /// msg_type (e.g. `model_response` has `{content, tool_calls, usage}`).
    Done(Value),
}
```

### `crates/arf-core/src/processor.rs`（新建）

```rust
//! ResponseProcessor — turn raw Bus `Message` into typed `Response`.
//!
//! App code registers processors for custom msg_types in `AgentConfig.processors`.
//! Built-in msg_types (`model_response`, `tool_result`) have implicit dispatch
//! in Engine (no processor needed).
//!
//! Phase 6 task 6.2: type definition only. Engine-side dispatch wired in 6.5.

use crate::{Message, Response};

/// Convert a raw `Message` (typically a response with `correlation_id`
/// matching a pending WaitEvent) into a typed `Response`.
///
/// App implements this for each custom msg_type they expect.
pub trait ResponseProcessor: Send + Sync {
    /// Whether this processor handles the given msg_type.
    /// Allows AgentConfig to dispatch by msg_type without dynamic dispatch.
    fn handles(&self, msg_type: &str) -> bool;

    /// Process the message. Returns `Err(msg)` if processor can't handle it
    /// (lets caller fall through to next processor).
    fn process(&self, msg: &Message) -> Result<Response, String>;
}
```

注：实际 dispatch 表可能用 `HashMap<String, Arc<dyn ResponseProcessor>>` 或 enum dispatch。本任务先定义 trait。

### `crates/arf-core/src/lib.rs` 模块声明

```rust
pub mod response;
pub mod processor;
pub use response::Response;
pub use processor::ResponseProcessor;
```

## 测试

### `crates/arf-core/src/lib.rs` 加 4 个测试

```rust
// [构造] Response::Done(Value) 接受任意 JSON
#[test]
fn response_done_accepts_any_value() {
    let r = Response::Done(json!({"content": "hi", "tool_calls": []}));
    match r {
        Response::Done(v) => assert_eq!(v["content"], "hi"),
    }
}

// [序列化] Response serde 往返
#[test]
fn response_serde_roundtrip() {
    let r = Response::Done(json!({"x": 42}));
    let s = serde_json::to_string(&r).unwrap();
    let back: Response = serde_json::from_str(&s).unwrap();
    assert_eq!(r, back);
}

// [trait] Mock ResponseProcessor 的 handles() 返回注册类型
#[test]
fn mock_processor_handles_returns_true_for_registered_type() {
    struct MockProc;
    impl ResponseProcessor for MockProc {
        fn handles(&self, msg_type: &str) -> bool { msg_type == "custom_thing" }
        fn process(&self, _msg: &Message) -> Result<Response, String> {
            Ok(Response::Done(json!(null)))
        }
    }
    let p = MockProc;
    assert!(p.handles("custom_thing"));
    assert!(!p.handles("model_response"));
}

// [trait] ResponseProcessor::process 转换有效消息
#[test]
fn mock_processor_processes_valid_message() {
    struct P;
    impl ResponseProcessor for P {
        fn handles(&self, msg_type: &str) -> bool { msg_type == "ok" }
        fn process(&self, msg: &Message) -> Result<Response, String> {
            Ok(Response::Done(msg.payload.clone()))
        }
    }
    let p = P;
    let msg = Message::new("ok", NodeId::new("test"), vec![], json!({"result": 7}));
    let r = p.process(&msg).unwrap();
    match r {
        Response::Done(v) => assert_eq!(v["result"], 7),
    }
}
```

## 验证命令

```bash
. "$HOME/.cargo/env" && cargo test -p arf-core
```

## 测试覆盖摘要

| 模块 | 测试数 | 覆盖角度 |
|------|--------|---------|
| `Response` | 2 | `[构造][序列化]` |
| `ResponseProcessor` | 2 | `[trait][方法]` |
| **合计新增** | **4** |

---

## 实现后实际发现

### 与初稿的差异

无——`Response::Done(Value)` + `ResponseProcessor` trait 都按设计落地。

`Response` 仅 1 个 variant，符合 Phase 6 §2.P5 "only `Done` variant" 决议；错误全部走 `node_offline` lifecycle signal + `OnMemberFailedHandler`（§2.P8，6.8 / 6.6 阶段实现）。

### 实际测试结果

```
cargo test -p arf-core
test result: ok. 158 passed  (154 + 4 new)

cargo test --workspace
... 158 + 91 + 14 + ... 全部 OK
0 FAILED
```

4 个新测试：
- `response_done_accepts_any_value`：Done 接受任意 JSON
- `response_serde_roundtrip`：Response 序列化往返
- `mock_processor_handles_registered_type`：handles() 路由
- `mock_processor_processes_valid_message`：process() 转换

### 不在范围内（明确延后）

| 项 | 何时做 |
|----|--------|
| AgentConfig.processors 注册表 | 6.8 EngineBuilder API |
| OnMemberFailedHandler trait（§2.P8）| 6.6 / 6.8 |
| Engine park 逻辑（Query 等/Command 不入队）| 6.3 / 6.4 Engine 主体 |
| SessionSnapshot / Engine.snapshot() / Engine.restore() | 6.6 / 6.8 |

### 进入 6.3

Engine 核心抽象已就位（Node / State / ActionMessage / CheckpointRule / Route / Response）。**6.3 Engine 骨架** 是 §9.B 第一个"非平凡"任务——引入 AgentConfig + EngineBuilder + Engine.run() 的最小可用版。

但不建议一次写完 6.3 + 6.4——粒度过大会让 review 复杂化。**先做 6.3 骨架**（Engine 接收 State + user_input，调一次 model_call，回 output），**然后 6.4 完整 ReAct 循环**（多 turn、park/resume、CheckpointRule 评估）。