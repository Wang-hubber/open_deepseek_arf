# 任务 6.10：Python API 绑定

> Phase 6 — Engine 核心实现（§9.B）第十项任务
> 父文档：`docs/v1.x/phase6/phase6-engine-design.md` §11
> 前置：`task-6.5/6.6/6.7/6.8/6.9` ✅

## 设计思路

py-arf 已绑定 Bus、ModelAdapter、McpNode。6.10 添加 arf-engine 绑定（Engine、AgentConfig、EngineBuilder、WaitStrategy、State），让 Python 侧可独立装配 Engine 实例（无须 arf-agent crate）。

### 关键设计决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 模块位置 | `py-arf/src/engine.rs`（新文件） | 与 mcp.rs 同级，结构清晰 |
| 一次性 take 模式 | `EngineBuilder.build()` / `Engine.run()` 都 take 内部值 | 配合 PyO3 GIL：take 后再 await，避免 MutexGuard 跨 .await 持有 |
| Mutex 选型 | `std::sync::Mutex`（不是 tokio） | .lock() 同步，take 后立即 drop；guard 不跨 .await |
| State.take 模式 | `engine.run()` 每次 take 内部 State，run 完 restore | Python 侧 State 对象跨多次 run 保留内部状态 |
| 公开 API | `PyAgentConfig` / `PyEngineBuilder` / `PyEngine` / `PyState` / `PyWaitStrategy` / `PyModelCall` | 覆盖 Engine API surface |
| CheckpointRule binding | 暂不暴露 | 闭包 dyn-trait 不易绑定；6.10 简化，App 通过 Rust 侧 AgentConfig 配置 |
| `from_py_object` 限制 | WaitStrategy 用 `from_py_object` 支持 Python 子类化 | 与 MessageFilter 等已有模式一致 |
| 错误转换 | `BuildError` / `RunError` 转为 `PyException`（带 to_string） | PyO3 简单错误模型；详细信息在异常 message |

### 不在 6.10 范围

- CheckpointRule closures 的 Python 暴露（需要 pyfunction 转换，复杂）
- on_member_failed 闭包的 Python 暴露
- 真实 ModelAdapterNode + McpNode + Engine 端到端 Python 集成测试（依赖 Engine ↔ ModelAdapterNode 格式修复）
- Python `Agent` 高层 wrapper（6.10 暴露底层；App 自己组装）

### 关键既有材料

- `py-arf/src/lib.rs`（已有 Bus、ModelAdapter、McpNode 绑定）
- `EngineBuilder::new(buses).build(cfg).await`（6.3 实现）
- `Engine::run(state, user_input, cancel).await`（6.4 实现）

## 代码实现

### `py-arf/Cargo.toml` 改动

```toml
[dependencies]
pyo3 = { version = "0.29", features = ["extension-module"] }
arf-core = { path = "../crates/arf-core" }
arf-bus = { path = "../crates/arf-bus" }
arf-engine = { path = "../crates/arf-engine" }   # 新增
arf-model-adapter = { path = "../crates/arf-model-adapter" }
arf-mcp = { path = "../crates/arf-mcp" }
tokio = { version = "1", features = ["rt-multi-thread", "macros", "time"] }
tokio-util = "0.7"   # 新增（CancellationToken）
pyo3-async-runtimes = { version = "0.29", features = ["attributes", "tokio-runtime"] }
serde_json = "1"
```

### `py-arf/src/lib.rs` 改动

```rust
pub mod engine;

// In #[pymodule]:
m.add_class::<engine::PyAgentConfig>()?;
m.add_class::<engine::PyEngineBuilder>()?;
m.add_class::<engine::PyEngine>()?;
m.add_class::<engine::PyState>()?;
m.add_class::<engine::PyWaitStrategyInner>()?;
m.add_class::<engine::PyModelCall>()?;
```

### `py-arf/src/engine.rs`（新建）

6 个 pyclass：`PyAgentConfig`、`PyEngineBuilder`、`PyEngine`、`PyState`、`PyWaitStrategyInner`、`PyModelCall`。

关键模式：所有 async 方法都用 `take()` 在同步 mutex 内取内部值，drop guard 后再 `await`。

## 测试

Python 端测试需要 maturin + pytest 设置。本次 task 在 Rust 端做 cargo check 验证；端到端 Python 测试留 6.x。

```bash
# 验证编译
. "$HOME/.cargo/env" && cargo check -p py-arf

# 端到端（需要 maturin develop）
. "$HOME/.cargo/env" && cd py-arf && maturin develop
python3 -c "from _arf import AgentConfig, EngineBuilder, Engine, EngineState, WaitStrategy; print('imports ok')"
```

## 验证命令

```bash
. "$HOME/.cargo/env" && cargo test --workspace -- --test-threads=4
```

---

## 实现后实际发现

### 与初稿的差异

1. **`std::sync::Mutex` 跨 .await 编译失败**：初稿想直接在 async block 内 lock + await，但 std::sync::MutexGuard 不 Send。修复：在 async 之前 take，await 之后 restore。`mutex.lock().take().drop(guard)` 三步法。
2. **EngineBuilder 非 Clone**：初稿想 clone，EngineBuilder 不 impl Clone。修复：用 `Arc<Mutex<Option<EngineBuilder>>>` 包装；build 时 take 内部值。
3. **Engine 不可重复 run**：每次 run 都要 `&mut self`，但 PyO3 pyclass 默认不能 mut。修复：用 `Arc<Mutex<Option<Engine>>>`，run 时 take，run 完 restore。
4. **`PyWaitStrategy` 命名冲突**：用 `PyWaitStrategyInner` 内部名 + `#[pyclass(name = "WaitStrategy")]` 暴露 Python 名为 `WaitStrategy`。
5. **`fn Any() / fn Count()` 方法名警告**：Python 暴露 All/Any/Count 是类属性（classattr），Rust 侧方法名按 snake_case。warning 不影响功能。

### 实现期间 bug

1. **`pub use` 缺失**：engine module 的 pyclass 必须是 `pub struct` 才能在 lib.rs 的 `m.add_class` 中引用。修复：所有 6 个 pyclass 加 `pub`。
2. **`ModelCall.correlation_id()` 找不到**：需要 `use arf_core::ActionMessage;` 让 trait 方法可见。
3. **future_into_py 期望 `Result<_, PyErr>`**：原 async block 返回 `(Result, State)` 元组。修复：run 完后再 restore state，return 单纯 result。

### 实际测试结果

```
cargo test --workspace -- --test-threads=4
合计 717 passed; 0 failed (engine 56 + integration 5 + 其他 656)
cargo check -p py-arf → ok
```

### 6.10 输出

- `py-arf/Cargo.toml`：新增 arf-engine + tokio-util 依赖
- `py-arf/src/lib.rs`：新增 `pub mod engine;` + 6 个 `m.add_class`
- `py-arf/src/engine.rs`（新建）：
  - `PyAgentConfig` — 配置 holder，支持 take-and-consume 模式
  - `PyEngineBuilder` — `new(buses)` / `build(config)`
  - `PyEngine` — `agent_id` / `system_prompt` / `run(state, user_input)`
  - `PyState` — `round_count` / `turn_count` / `context_tokens`
  - `PyWaitStrategyInner` — `All` / `Any` / `Count(n)`
  - `PyModelCall` — read-only accessor

### 下一步：6.x

Phase 6 Engine 核心任务已全部完成（6.1-6.10）。后续：

- **6.11 MCP facade 示例**（`examples/domain_controller/`）
- **6.12 App-level Recovery 示例**（`examples/recovery/`）
- **6.13-6.19 Pool 实现**（`crates/arf-pool/`）