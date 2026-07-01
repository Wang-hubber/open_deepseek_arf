# 任务 6.17-6.19：Pool Resources + Integration

> Phase 6 — Pool 实现（§9.D）第五至第七项
> 前置：`task-6.13-6.16` ✅

## 设计思路

6.17-6.18 把具体 Resource 类型接入 `Resource` trait；6.19 用集成测试验证 Engine + Pool + PoolNode 完整跑通。

## 6.17 ModelAdapterResource

`crates/arf-model-adapter/src/pool_resource.rs`

- 包装 `Arc<dyn Provider>` + per-resource AtomicU64 call_count + last_used_ms
- `kind() = "model_adapter"`
- `try_acquire()` reset counter，记录 last_used_ms
- `release()` no-op（Provider 无状态）

## 6.18 McpResource

`crates/arf-mcp/src/pool_resource.rs`

- 包装 `Arc<McpNode>` + 同上原子
- `kind() = "mcp"`
- `release()` no-op（McpNode 由 Arc 共享）

## 6.19 Pool 集成测试

`crates/arf-pool/tests/integration.rs` — 2 个测试：

1. `pool_with_model_adapter_resource` — 2 resources + Reject overflow 验证
2. `pool_node_with_engine_react_loop` — 端到端：top Bus（Engine）+ sub Bus（PoolNode + Pool<ModelAdapterResource>）；Engine 发 model_call → PoolNode 桥接 → sub Bus → model_response → Engine 收到 → 终止

## 实现后实际发现

### 实现期间 bug

1. **`supported_models` 返回 `&[String]`**：原 stub 返回 `&["stub-v1"]` 编译失败（expected String, found &str）。改用 `static LazyLock<Vec<String>>` 模式。
2. **Pool `release()` 是 private**：集成测试调用失败。改 `pub fn release`。
3. **`provider` 模块需 pub**：pool_resource 集成测试需要 `use arf_model_adapter::provider::Provider`，但 `provider` 之前是 `mod provider`。改 `pub mod provider`。
4. **磁盘满**：编译 arf-pool 集成测试时 OOM（`No space left on device`）。清理 `target/debug/{incremental,deps}` 后解决。

### 实际测试结果

```
cargo test -p arf-pool
test result: ok. 5 passed; 0 failed  (lib)

cargo test -p arf-pool --test integration
test result: ok. 2 passed; 0 failed
  - pool_with_model_adapter_resource
  - pool_node_with_engine_react_loop

cargo test --workspace -- --test-threads=4
合计 727 passed; 0 failed
```

### 6.17-6.19 输出

- `crates/arf-model-adapter/src/pool_resource.rs`（新建，2 测试）
- `crates/arf-model-adapter/src/lib.rs`（re-export + 公开 `provider`/`types`）
- `crates/arf-model-adapter/Cargo.toml`（添加 arf-pool 依赖）
- `crates/arf-mcp/src/pool_resource.rs`（新建，1 测试）
- `crates/arf-mcp/src/lib.rs`（re-export + 公开 `pool_resource`）
- `crates/arf-mcp/Cargo.toml`（添加 arf-pool 依赖）
- `crates/arf-pool/tests/integration.rs`（新建，2 测试）
- `crates/arf-pool/Cargo.toml`（添加 arf-engine 依赖，dev-deps 加 arf-model-adapter）
- `crates/arf-pool/src/lib.rs`（release 改 pub）

## Phase 6 全部完成度

**§9.B Engine 核心 (6.1-6.10)** ✅ + **§9.C 域控制器示例 (6.11-6.12)** ✅ + **§9.D Pool 实现 (6.13-6.19)** ✅

总计：
- 7 task docs in `docs/v1.x/phase6/`
- 2 example crates in `examples/`
- 1 new lib crate `arf-pool`
- 727 tests pass across 11 crates
- 全部 commit + push 到 gitee arfv1
