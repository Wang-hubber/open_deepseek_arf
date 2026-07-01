# 任务 6.11：MCP Facade 示例

> Phase 6 — 域控制器示例（§9.C）第一项
> 父文档：`docs/v1.x/phase6/phase6-engine-design.md` §2.P7
> 前置：`task-6.9-integration-tests` ✅

## 设计思路

演示多 Bus 拓扑下的 facade pattern：top Bus（Engine + facade）与 sub Bus（McpNode）通过 facade 节点桥接。facade 订阅 top Bus 的 `tool_exec`，转发到 sub Bus，sub Bus 的 `tool_result` 再回传到 top Bus 给 Engine。

## 代码结构

`examples/domain_controller/`

- `Cargo.toml`：独立可执行 crate，加入 workspace
- `src/main.rs`：
  - `DomainController` struct — 持有 top_bus / sub_bus Arc
  - `connect()` — 双向 connect + spawn run_loop
  - `run_loop()` — 接收 tool_exec → 转发到 sub_bus → 等 tool_result → 回传 top_bus
  - `main()` — 装配：建临时 echo tool、top/sub bus、McpNode、DomainController、mock model responder、Engine；运行 1 round

## 验证

```bash
. "$HOME/.cargo/env" && cargo run -p domain_controller
```

期望输出：
```
Engine output: ok from mock
State messages: 3
State over_view: OverView { round_count: 1, turn_count: 1, ... }
```

## 实现后实际发现

### 与初稿的差异

1. **`examples/` 目录**需要加入 workspace `members` 才被 cargo 识别。
2. **示例 Cargo.toml 不能有 `[workspace]` 段**：否则变独立 workspace，继承不到父 edition。
3. **`"tool_result".into()` 编译歧义**：reqwest/bytes 等 crate 提供多个 `From<&str>` impl。改用 `String::from(...)` 显式类型。
4. **Strict route 需要 NodeId 在线**：`EngineBuilder.build` 校验时若 route 的 NodeId 不在 BusGraph 即报 `MissingNodes`。示例用 dummy node 仅满足 build 校验，实际响应靠 mock responder 跑。
5. **mock responder 不算 Node**：仅 bus.subscribe() 不算 connect，BusGraph 不包含。所以示例必须 connect 一个 dummy `model/mock` node 才能 build 成功。

### 实现期间 bug

1. **重复的 tempfile 模块**：原本想 `mod tempfile { pub use ::tempfile::TempDir; }` 避免加 dep 两次，实际反而冲突。修复：直接 `tempfile::tempdir()`。
2. **`[workspace]` 段被识别为独立 workspace**：删除即可。

### 实际测试结果

```
cargo run -p domain_controller
Engine output: ok from mock
State messages: 3
State over_view: OverView { round_count: 1, turn_count: 1, context_tokens: 0, model_context_window: 0, runtime: 0ns, last_user_message: "test the facade" }
```

### 6.11 输出

- `examples/domain_controller/Cargo.toml`（新建）
- `examples/domain_controller/src/main.rs`（新建）

### 下一步：6.12

**6.12 App-level Recovery 示例**：在 6.11 基础上加 AppCheckpoint Node + Bus::barrier + 文件持久化。