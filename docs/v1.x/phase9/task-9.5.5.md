# 任务 9.5.5：McpNode + RemoteRuntime（HTTP 工具执行）

> Phase 9 — 9.5 C 工具集成 / McpNode 大类 · 第 5 task（依赖 9.5.1）
> 父文档：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.5.1（McpNode + FsDiscovery OK）+ 9.5.2（HttpDiscovery OK）
> 输出物：`docs/v1.x/phase9/audit-probe-9.5.5.md`
> 探查结论：**不预设**——本 doc 不写任何预期结果

---

## 设计思路

9.5.4 探查了 `LocalRuntime`（默认 executor 走 subprocess）。本 task (9.5.5) 探查 **`RemoteRuntime`** —— 当 tool 是 `HttpProxyTool`（远端代理）时，`RemoteRuntime::execute(call_set, tools)` 端到端 work？

**Framework 现状**（待探查确认）：
- `crate::runtime::RemoteRuntime` —— `capabilities() = {"runtime": "remote"}`（runtime.rs:71-75）
- `RemoteRuntime` 没 override `execute()` —— 走默认 `executor::execute()`（runtime.rs:30-36）
- `McpNode::remote(ns, config)` 默认注入 `RemoteRuntime`（node.rs:48）

**关键探查问题**（不预设答案）：
1. `RemoteRuntime::capabilities()` 返回 `{"runtime": "remote"}` —— 与 LocalRuntime 区分？
2. `RemoteRuntime::execute(call_set, tools)` 默认 delegate executor —— 但 tools 是 `HttpProxyTool`，execute 走 JSON-RPC tools/call，端到端 work？
3. McpNode 收到 `tool_call_set` 走 RemoteRuntime 路径，与走 LocalRuntime 的响应格式是否一致？
4. 多个远端 tool 并发调用是否真并发（HTTP connection pool）？

按父 spec §3 探查 4 步流程 + §4 find signals 跑。

---

## 与现有测试的边界

- 9.5.2 已测 HttpDiscovery + McpNode::remote + HttpProxyTool::execute 单 tool 路径
- **本 task 聚焦**：RemoteRuntime DAG executor（多 tool + 依赖 + cascade）通过 HttpProxyTool
- **mock**：与 9.5.2 同款 tokio TcpListener mock HTTP server

---

## 探查步骤（按父 spec §3.1）

### Step 1 — 写最小代码路径（app 层 ≤ 220 行）

`mcp_remote_runtime.rs`，4 test cases：

| # | test | 探查 |
|---|---|---|
| 1 | `remote_runtime_capabilities_metadata` | `RemoteRuntime::capabilities() = {"runtime":"remote"}` |
| 2 | `remote_runtime_default_executor_with_http_tools` | 1 个 tool_call_set → RemoteRuntime → executor → HttpProxyTool → JSON-RPC tools/call → success |
| 3 | `remote_runtime_layer_parallel_http_calls` | 2 个远端 tool 无依赖 → 并发调 mock server → 都 success |
| 4 | `remote_runtime_cascade_cancel_via_http_failure` | 1 个 tool fail + 1 个 downstream → cascade cancel 触发 |

### Step 2 — framework 接触点 file:line

```bash
grep -n "pub struct RemoteRuntime\|impl RuntimeModule for RemoteRuntime" crates/arf-mcp/src/runtime.rs
grep -n "Box::new(crate::runtime::RemoteRuntime)" crates/arf-mcp/src/node.rs
grep -n "impl Tool for HttpProxyTool" crates/arf-mcp/src/remote.rs
```

逐行解释：
- `RemoteRuntime` 4 行 impl（runtime.rs:71-75）
- `McpNode::remote` 注入 `RemoteRuntime`（node.rs:48）
- `HttpProxyTool::execute` POST JSON-RPC tools/call

### Step 3 — framework 真实行为

```bash
cargo test -p arf-e2e --test mcp_remote_runtime -- --nocapture --test-threads=1 2>&1 | tee /tmp/mcp_remote_runtime_run.log
```

逐行解释：
- 4 test 应全过（mock server 启 127.0.0.1 + port）
- 任何 F-lesion 在 audit-probe §D 记录

**Read `/tmp/mcp_remote_runtime_run.log` 后填 Step 4 `framework 行为`**。

### Step 4 — 判定 + 记录

按 §3.3 输出 schema 填 4 个单元的判定。

按 §4 跑 signals：
- A1：`RemoteRuntime` 与 `LocalRuntime` 抽象边界清晰？
- A2：`HttpProxyTool::execute` 与 executor 调度正交？
- A3：tool 结果格式（remote 返回 String vs local 返回 Value）是否一致？
- A4：runtime 决策 vs executor 决策是否分开？

**C. 输出**：`audit-probe-9.5.5.md`。