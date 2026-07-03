# 任务 9.5.2：McpNode + HttpDiscovery（JSON-RPC initialize + tools/list）

> Phase 9 — 9.5 C 工具集成 / McpNode 大类 · 第 2 task（依赖 9.5.1）
> 父文档：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.5.1（McpNode + FsDiscovery 端到端 OK）
> 输出物：`docs/v1.x/phase9/audit-probe-9.5.2.md`
> 探查结论：**不预设**——本 doc 不写任何预期结果

---

## 设计思路

9.5.1 探查了 `FsDiscovery`（filesystem scan）。本 task (9.5.2) 探查 **`HttpDiscovery`**——framework 是否能让 app 通过 JSON-RPC（HTTP POST）连到远端 MCP server，自动获取 tool 列表？

**Framework 现状**（待探查确认）：
- `arf-mcp::remote::HttpDiscovery::connect(config)` —— 发起 `initialize` + `tools/list` JSON-RPC
- `arf-mcp::config::RemoteConfig` —— url / transport / timeout / headers / tls
- `McpNode::remote(ns, config)` —— 构造远端节点（async）
- `HttpProxyTool` —— 远端 tool 的本地代理（`tools/call` JSON-RPC）

**关键探查问题**（不预设答案）：
1. `HttpDiscovery::connect` 是否真发 `initialize` + `tools/list`？返回的 tool list 完整？
2. `McpNode::remote` 是否组合 `HttpDiscovery + RemoteRuntime`，与 `McpNode::local` 同形？
3. `HttpProxyTool::execute` 转发 `tools/call` 是否端到端 work？
4. 错误路径（404 / 500 / 非法 JSON）framework 是否优雅处理？

按父 spec §3 探查 4 步流程 + §4 find signals 跑。

---

## 与现有测试的边界

- `crates/arf-mcp/src/tests/discovery_tests.rs`：单元 trait 测试（与本 task 不重叠）
- **本 task 聚焦**：端到端——`HttpDiscovery::connect` + `McpNode::remote` + `HttpProxyTool::execute`
- **本 task 需 mock HTTP server**：tokio TcpListener 写最简 HTTP/1.1 responder（无 axum/hyper 依赖）

---

## 探查步骤（按父 spec §3.1）

### Step 1 — 写最小代码路径（app 层 ≤ 200 行）

`mcp_http_discovery.rs`，4 test cases：

| # | test | 探查 |
|---|---|---|
| 1 | `http_discovery_initialize_and_tools_list` | 启动 mock server（initialize + tools/list 双 endpoint）+ `HttpDiscovery::connect` → 列出 tool |
| 2 | `mcp_node_remote_connects_to_bus` | mock server + `McpNode::remote` + `connect(bus)` —— bus 端可见 |
| 3 | `http_proxy_tool_executes_via_tools_call` | mock server 响应 `tools/call` + `HttpProxyTool::execute` → 端到端 work |
| 4 | `http_discovery_handles_404` | mock server 返回 404 → `HttpDiscovery::connect` 返回 Err | 

### Step 2 — framework 接触点 file:line

```bash
grep -n "pub async fn connect\|pub async fn rpc_call" crates/arf-mcp/src/remote.rs
grep -n "pub async fn remote" crates/arf-mcp/src/node.rs
```

逐行解释：
- `HttpDiscovery::connect` 两步：rpc_call("initialize") + rpc_call("tools/list")
- `rpc_call` 用 reqwest POST application/json
- `HttpProxyTool::execute` 用 rpc_call("tools/call")

### Step 3 — framework 真实行为

```bash
cargo test -p arf-e2e --test mcp_http_discovery -- --nocapture --test-threads=1 2>&1 | tee /tmp/mcp_http_discovery_run.log
```

逐行解释：
- 4 test 应全过（mock server 启 127.0.0.1 + port）
- 任何 F-lesion 在 audit-probe §D 记录

**Read `/tmp/mcp_http_discovery_run.log` 后填 Step 4 `framework 行为`**。

### Step 4 — 判定 + 记录

按 §3.3 输出 schema 填 4 个单元的判定。

按 §4 跑 signals：
- A1：`HttpDiscovery::connect` 一个职责（HTTP connect），还是含 connect + cache 两件事？
- A2：`HttpProxyTool::execute` 与 `RpcCall` helper 是否边界清晰？
- A3：`RemoteConfig` 字段（url / timeout / headers / tls）跨 crate 是否唯一？
- A4：JSON-RPC envelope 构造（jsonrpc+id+method+params）是否集中？

**C. 输出**：`audit-probe-9.5.2.md`。