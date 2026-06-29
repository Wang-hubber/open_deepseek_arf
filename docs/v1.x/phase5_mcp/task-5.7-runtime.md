# 任务 5.7：RuntimeModule

> **订正**：此文档中的类型名已被 [McpNode 统一重构](./mcp-node-unified-design.md) 更新。当前实现以代码为准。
> Phase 5 — MCP 第七项任务
> 父文档：`docs/v1.x/phase5_mcp/phase5-mcp-design.md`
> 依赖：Task 5.4 (DAG 执行器)

## 设计思路

`RuntimeModule` 是执行后端抽象——trait 定义 `capabilities()` + `execute()` + `run_single()`。框架默认提供 `LocalRuntime`（宿主机直接 spawn），用户可实现 `SandboxRuntime`（转发到 Bus sandbox 节点）。trait 对象在 `LocalMcpNode` 构造时绑定，执行方式在定义阶段固定。

| 文件 | 操作 | 内容 |
|------|------|------|
| `runtime.rs` | 新建 | `RuntimeModule` trait + `LocalRuntime` 实现 |
| `lib.rs` | 更新 | `pub mod runtime;` |

---

## 代码实现

### `crates/arf-mcp/src/runtime.rs` — 新建

```rust
use std::collections::HashMap;
use std::sync::Arc;

use serde_json::Value;

use crate::executor;
use crate::tool::Tool;
use crate::types::{ToolCallSet, ToolResultSet};

/// Execution backend for tool calls — bound at LocalMcpNode construction.
///
/// The RuntimeModule trait decouples DAG scheduling from subprocess execution.
/// The executor handles topology, concurrency, and cascade cancel.
///
/// Implementations:
/// - `LocalRuntime` (framework default): spawn subprocesses on the host
/// - `SandboxRuntime` (user-defined, future): forward to a sandbox Bus node
#[async_trait::async_trait]
pub trait RuntimeModule: Send + Sync {
    /// Self-describing capabilities → injected into node_online.
    fn capabilities(&self) -> Value;

    /// Execute a full tool_call_set via the DAG executor.
    /// Default delegates to `executor::execute()`.
    async fn execute(
        &self,
        call_set: &ToolCallSet,
        tools: &HashMap<String, Arc<dyn Tool>>,
    ) -> ToolResultSet {
        executor::execute(call_set, tools).await
    }

    /// Execute a single tool call. Used by tests and simple execution paths.
    async fn run_single(
        &self,
        _call_id: &str,
        tool: &dyn Tool,
        params: Value,
    ) -> (String, Value, Option<String>) {
        match tool.execute(params).await {
            Ok(val) => ("success".into(), val, None),
            Err(e) => ("error".into(), Value::Null, Some(e.message)),
        }
    }
}

/// Default RuntimeModule — spawns subprocesses directly on the host.
pub struct LocalRuntime;

#[async_trait::async_trait]
impl RuntimeModule for LocalRuntime {
    fn capabilities(&self) -> Value {
        serde_json::json!({"runtime": "local", "concurrency": "layer-parallel"})
    }
}
```

逐行解释：
- `capabilities()` — 每个实现返回自己的执行环境描述。`LocalRuntime` 返回 `{"runtime": "local"}`，未来 `SandboxRuntime` 返回 `{"runtime": "sandbox", "engine": "docker", "image": "..."}`
- `execute()` — 默认实现委托 `executor::execute()`（Task 5.4）。trait 有默认实现，用户不需要覆盖（除非想完全替换执行模型）
- `run_single()` — 单 tool 执行，带默认实现。用于测试和不需要 DAG 的场景（如 `run_skill_script`）
- `execute()` 和 `run_single()` 都有默认实现 → 实现 `RuntimeModule` trait 时最小只需提供 `capabilities()`

---

## 测试

### `crates/arf-mcp/src/tests/runtime_tests.rs` — 新建

5 个测试：

| 测试 | 覆盖角度 |
|------|---------|
| `local_runtime_capabilities` | [方法] 返回 `{"runtime": "local", "concurrency": "layer-parallel"}` |
| `run_single_success` | [方法] 工具执行成功 → `("success", result, None)` |
| `run_single_error` | [方法] 工具返回 Err → `("error", null, Some(msg))` |
| `execute_via_dag_executor` | [方法] `execute()` 通过 DAG executor 运行单 tool，验证 result name 回填 |
| `runtime_module_is_object_safe` | [类型] `Box<dyn RuntimeModule>` 可用 |

---

## 验证命令

```bash
. "$HOME/.cargo/env" && cargo test -p arf-mcp
. "$HOME/.cargo/env" && cargo test --workspace
```

---

## 测试覆盖摘要

| 文件 | 新增测试 | 覆盖角度 |
|------|---------|---------|
| `runtime_tests.rs` | 5 | `[方法][类型]` — capabilities(1)、run_single success/error(2)、execute DAG(1)、object safe(1) |
| **合计** | **5** | 累计 arf-mcp: 161 + 5 = **166 tests** |
