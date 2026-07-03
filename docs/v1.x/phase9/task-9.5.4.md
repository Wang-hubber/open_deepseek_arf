# 任务 9.5.4：McpNode + LocalRuntime（默认 DAG executor）

> Phase 9 — 9.5 C 工具集成 / McpNode 大类 · 第 4 task（依赖 9.5.1）
> 父文档：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.5.1（McpNode + FsDiscovery OK）
> 输出物：`docs/v1.x/phase9/audit-probe-9.5.4.md`
> 探查结论：**不预设**——本 doc 不写任何预期结果

---

## 设计思路

9.5.1/9.5.2/9.5.3 探查了 discovery 端（FsDiscovery / HttpDiscovery / custom backend）。本 task (9.5.4) 探查 **execution 端** —— `LocalRuntime` 是否能让 McpNode 处理 tool_call_set 走 DAG executor（layer-parallel + cascade cancel + timeout）？

**Framework 现状**（待探查确认）：
- `crate::runtime::RuntimeModule` trait —— `execute(call_set, tools)` 默认调 `executor::execute()`
- `LocalRuntime` —— `capabilities() = {"runtime": "local", "concurrency": "layer-parallel"}`
- `McpNode::local(ns, root)` 默认注入 `LocalRuntime`（node.rs:35）
- `McpNode::dispatch` 收到 `tool_call_set` → `runtime.execute(&call_set, discovery.tool_map())`（node.rs:140）

**关键探查问题**（不预设答案）：
1. `LocalRuntime` 默认 `execute()` 是否真走 `executor::execute()`？
2. McpNode 收到 `tool_call_set` 消息后，是否真经由 `LocalRuntime` 跑 DAG？
3. 多 tool（无依赖）是否真并发执行？layer-parallel 行为？
4. 有依赖（blocked_by）的 tool 是否按拓扑排序？cascade cancel 是否 work？

按父 spec §3 探查 4 步流程 + §4 find signals 跑。

---

## 与现有测试的边界

- `crates/arf-mcp/src/tests/executor_tests.rs`：executor DAG 单元测试（cycle / parallel / cascade）
- **本 task 聚焦**：端到端——McpNode 收 tool_call_set → LocalRuntime → executor → tool execute → result_set 回

---

## 探查步骤（按父 spec §3.1）

### Step 1 — 写最小代码路径（app 层 ≤ 200 行）

`mcp_local_runtime.rs`，4 test cases：

| # | test | 探查 |
|---|---|---|
| 1 | `local_runtime_capabilities_metadata` | `LocalRuntime::capabilities()` 返回 `{"runtime":"local","concurrency":"layer-parallel"}` |
| 2 | `local_runtime_dispatches_tool_call_set_via_bus` | McpNode::local + connect(bus) → 发 tool_call_set 消息 → McpNode 回 tool_result_set |
| 3 | `local_runtime_layer_parallel_concurrent_execution` | 2 个无依赖 tool_call_item → 同时 spawn → 都 success |
| 4 | `local_runtime_cascade_cancel_on_failure` | 2 个 call（A fail 后 B 取消）→ cascade cancel behavior |

### Step 2 — framework 接触点 file:line

```bash
grep -n "pub struct LocalRuntime\|impl RuntimeModule for LocalRuntime" crates/arf-mcp/src/runtime.rs
grep -n "tool_call_set\|runtime.execute" crates/arf-mcp/src/node.rs
grep -n "pub async fn execute" crates/arf-mcp/src/executor.rs
```

逐行解释：
- `LocalRuntime` 4 行 impl（runtime.rs:57-62）
- `McpNode::dispatch` 收到 `tool_call_set` 走 `runtime.execute`（node.rs:140）
- `executor::execute` DAG 调度（executor.rs:15-114）

### Step 3 — framework 真实行为

```bash
cargo test -p arf-e2e --test mcp_local_runtime -- --nocapture --test-threads=1 2>&1 | tee /tmp/mcp_local_runtime_run.log
```

逐行解释：
- 4 test 应全过（mock tool + bus 直接发消息）
- 任何 F-lesion 在 audit-probe §D 记录

**Read `/tmp/mcp_local_runtime_run.log` 后填 Step 4 `framework 行为`**。

### Step 4 — 判定 + 记录

按 §3.3 输出 schema 填 4 个单元的判定。

按 §4 跑 signals：
- A1：`LocalRuntime::execute` 与默认 executor 是否边界清晰？
- A2：`RuntimeModule::capabilities` 是否只在 metadata 层？
- A3：runtime 决策 vs executor 决策是否分开？
- A4：tool_call_set 路径在 McpNode 与 McpPoolNode 是否一致？

**C. 输出**：`audit-probe-9.5.4.md`。