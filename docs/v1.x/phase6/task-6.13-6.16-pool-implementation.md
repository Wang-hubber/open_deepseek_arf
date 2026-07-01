# 任务 6.13-6.16：arf-pool 实现

> Phase 6 — Pool 实现（§9.D）第一至第四项
> 父文档：`docs/v1.x/phase6/phase6-engine-design.md` §2.P10
> 前置：`task-6.0.5` ✅

## 设计思路

新建 `crates/arf-pool/` crate，实现 bounded Resource pool 抽象：
- `Resource` trait — Send+Sync, try_acquire/release 钩子
- `PoolConfig` — max_size, overflow, idle_timeout
- `Pool<R>` — Arc<PoolInner>，含 Mutex<PoolState> + Semaphore + Notify
- `Lease<R>` — RAII handle，含 OwnedSemaphorePermit（关键：确保 permit 与 resource 生命周期绑定）
- `ResourceManager` — 状态机文档（Nil/Idle/Busy/Draining）+ 转换规则
- `Overflow` enum — Queue(n) / Reject / Block(timeout)
- `PoolNode<R>` — Bus bridge：top Bus ↔ sub Bus 转发

## 代码结构

`crates/arf-pool/src/`
- `lib.rs` — Pool, Resource, Lease, PoolError, PoolConfig + 5 单元测试
- `manager.rs` — ResourceManager, ResourceState + 2 单元测试
- `overflow.rs` — Overflow enum
- `node.rs` — PoolNode (Bus bridge)

## 关键设计

| 决策 | 选择 | 理由 |
|------|------|------|
| semaphore 绑在 Lease | `Lease._permit: OwnedSemaphorePermit` | permit 在 Lease drop 时释放，否则 acquire 完立即释放，max_size 无效 |
| release 异步 | `tokio::spawn` 异步把 resource 放回 idle | 避免 sync drop 持 MutexGuard |
| Drop 实现 | spawn 异步 release | Lease 可在 sync 上下文 drop |
| PoolNode.connect 返回 `()` | NodeHandle !Clone，hand给spawned task | connect() 不返回 handle，由 PoolNode 持有 |

## 实现后实际发现

### 实现期间 bug

1. **Lease 没存 permit → semaphore 立即释放**：初版 Lease 只含 `resource` + `pool`，permit 在 acquire 函数返回时随 OwnedSemaphorePermit drop。导致第二个 acquire 看到 max_size permits 仍然有 → Reject 路径走不到。修复：Lease 加 `_permit: OwnedSemaphorePermit` 字段，绑定生命周期。
2. **PoolError 需要 Debug derive**：测试用 `assert!(matches!(res, Err(PoolError::Full)))` 配合 panic message 需要 Debug。PoolError 已有 `#[derive(Debug, Error)]`，但 Lease 不能 derive Debug（因为 PoolInner 没 Debug）。修复：测试用 err_variant match string。
3. **`NodeHandle` !Clone**：PoolNode.connect 不能既返回 handle 又 spawn 用 handle。修复：connect 返回 `()`，handle move 进 spawned task。
4. **`tokio::sync::Semaphore` 1.52 API**：`try_acquire_owned()` 返回 `Result<OwnedSemaphorePermit, TryAcquireError>`，不是 `Result<_, AcquireError>`。

### 实际测试结果

```
cargo test -p arf-pool
test result: ok. 5 passed; 0 failed  (lib + manager 子测试)

cargo test --workspace -- --test-threads=4
合计 722 passed; 0 failed
```

### 6.13-6.16 输出

- `crates/arf-pool/Cargo.toml`（新建）
- `crates/arf-pool/src/lib.rs`（新建）
- `crates/arf-pool/src/manager.rs`（新建）
- `crates/arf-pool/src/overflow.rs`（新建）
- `crates/arf-pool/src/node.rs`（新建）
- 加入 workspace `members`

## 6.14-6.16 完成度

- 6.14 ResourceManager — `ResourceState` enum + `transition()` 验证（2 测试）
- 6.15 Overflow — `Queue/Reject/Block` 三策略，集成在 acquire 路径
- 6.16 PoolNode — Bus bridge connect 逻辑（结构已就绪，端到端路由 6.19 验证）

### 下一步：6.17-6.19

**6.17 ModelAdapterResource** — `crates/arf-model-adapter/src/pool_resource.rs`（用 ModelAdapter 包装 Resource trait）
**6.18 McpResource** — `crates/arf-mcp/src/pool_resource.rs`
**6.19 Pool 集成测试** — Engine + PoolNode 跑通 ReAct