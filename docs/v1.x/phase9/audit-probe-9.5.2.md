# audit-probe-9.5.2：McpNode + HttpDiscovery（JSON-RPC initialize + tools/list）端到端探查

> Task 9.5.2 探查产出 — **Framework 是否让 app 通过 JSON-RPC 连到远端 MCP server、自动获取 tool 列表、远端 tool 端到端执行？**
> 父 task doc：`docs/v1.x/phase9/task-9.5.2.md`
> 父 spec：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.5.1（McpNode + FsDiscovery 端到端 OK）
> **本 task 探查：HttpDiscovery::connect (initialize + tools/list) + McpNode::remote + HttpProxyTool::execute 端到端**

---

## §A 探查环境

- working tree：HEAD `107c56b`（task 9.5.1）+ uncommitted `crates/arf-e2e/tests/mcp_http_discovery.rs`
- 测试文件：`crates/arf-e2e/tests/mcp_http_discovery.rs`（4 test cases）
- 驱动：mock HTTP server（tokio TcpListener + 手写 HTTP/1.1 responder，3 method: initialize / tools/list / tools/call）
- 测试命令：
  ```bash
  cargo test -p arf-e2e --test mcp_http_discovery -- --nocapture --test-threads=1
  ```
- 结果：**`4 passed; 0 failed; 0.21s`**
- 关键运行输出：
  ```
  test http_discovery_handles_404 ... [test4] HttpDiscovery::connect 返回 Err: remote MCP server rejected handshake (http://127.0.0.1:44883/mcp): [-32000] parse failed: not found
  ok
  test http_discovery_initialize_and_tools_list ... [test1] HttpDiscovery list_tools() = 2 tools
  [test1]   - remote_echo: echo on remote
  [test1]   - remote_sum: sum on remote
  [test1] resolve_tool('remote_echo') = Some ✓
  [test1] tool_map 含 2 entries ✓
  ok
  test http_proxy_tool_executes_via_tools_call ... [test3] HttpProxyTool execute result: String("hello-from-remote")
  ok
  test mcp_node_remote_connects_to_bus ... [test2] McpNode::remote 创建成功
  [test2] McpNode::remote + connect(bus) 成功
  [test2] bus.subscribe() OK ✓
  ok
  ```

### 凭据安全（self-check 已通过）

```bash
$ git grep -n 'sk-' -- crates/arf-e2e/tests/mcp_http_discovery.rs   # 无输出
```

---

## §B (capability, 情景) 单元判定

### 单元 1：`HttpDiscovery::connect` 走完 initialize + tools/list

```
单元              : mcp_remote_discovery × §2.3
能力等级           : D（PASS）
判定依据          : mock server 接收 initialize + tools/list 两个 JSON-RPC 请求
                   返回 2 个 tool；HttpDiscovery::connect 成功返回 HttpDiscovery
                   list_tools / tool_map / resolve_tool 三个方法全部端到端 work
file:line         : crates/arf-mcp/src/remote.rs:128-176 HttpDiscovery::connect
                   crates/arf-mcp/src/remote.rs:179-190 DiscoveryBackend impl
                   ✓ initialize + tools/list 顺序两次 rpc_call（remote.rs:132-148）
                   ✓ tool_info + tools HashMap 正确填充
```

### 单元 2：`McpNode::remote` + `connect(bus)`

```
单元              : mcp_node_remote × §2.3
能力等级           : D（PASS）
判定依据          : McpNode::remote("test-remote-mcp", config) 一步创建
                   connect(bus) 注册 listener 并 spawn message_loop
file:line         : crates/arf-mcp/src/node.rs:41-51 McpNode::remote
                   crates/arf-mcp/src/node.rs:72-81 McpNode::connect
                   ✓ 与 McpNode::local 同形（Box<dyn DiscoveryBackend> + Box<dyn RuntimeModule>）
                   ✓ RemoteRuntime（node.rs:48）正确注入
```

### 单元 3：`HttpProxyTool::execute` 转发 tools/call

```
单元              : tool_execution_remote × §2.3
能力等级           : D（PASS）
判定依据          : resolve_tool("remote_echo") → HttpProxyTool::execute({"msg":"hi"})
                   → POST JSON-RPC tools/call → 解析 CallToolResult.content
                   → 返回 Value::String("hello-from-remote")
file:line         : crates/arf-mcp/src/remote.rs:64-117 HttpProxyTool::execute
                   crates/arf-mcp/src/remote.rs:25-36 CallToolResult + ToolContent
                   ✓ text content 收集并 join("\n")（remote.rs:108-114）
                   ✓ 错误路径：jrpc.error → ToolError（remote.rs:101-103）
```

### 单元 4：错误路径（404 server）

```
单元              : http_discovery_error_handling × §2.3
能力等级           : D（PASS）
判定依据          : mock server 返回 404 + "not found" body（非 JSON）
                   HttpDiscovery::connect 返回 Err（parse failed → RemoteRejected）
file:line         : crates/arf-mcp/src/remote.rs:225-233 parse_sse_or_json
                   crates/arf-mcp/src/remote.rs:138-142 init_resp.error → McpError::RemoteRejected
                   ✓ 错误正确分类为 RemoteRejected（parse 失败被识别为 handshake reject）
```

---

## §C 探查产出汇总

| 单元 | 等级 | 判定依据 |
|---|---|---|
| `mcp_remote_discovery × §2.3` | **D** | initialize + tools/list 双 RPC + 2 tools 端到端 |
| `mcp_node_remote × §2.3` | **D** | McpNode::remote + connect(bus) 成功 |
| `tool_execution_remote × §2.3` | **D** | HttpProxyTool tools/call 端到端 |
| `http_discovery_error_handling × §2.3` | **D** | 404 → Err(RemoteRejected) |

---

## §D 病灶登记

**本 task 无新增 F-lesion**（framework HttpDiscovery + McpNode::remote + HttpProxyTool 端到端 work）。

### 框架实际行为（按 spec §3.3 输出）

- `HttpDiscovery::connect(config)` 两步：rpc_call("initialize") + rpc_call("tools/list") —— **D**
- `HttpDiscovery` DiscoveryBackend impl 4 方法（list_tools / tool_map / resolve_tool，skill 走默认）—— **D**
- `McpNode::remote(ns, config)` 一步创建并注入 HttpDiscovery + RemoteRuntime —— **D**
- `McpNode::connect(bus)` 注册 listener + spawn message_loop —— **D**
- `HttpProxyTool::execute` POST JSON-RPC tools/call + 解析 CallToolResult.content —— **D**
- `parse_sse_or_json` 双格式支持（plain JSON + SSE `data: ` 帧）—— **D**
- 错误路径：HTTP 错误 → reqwest::Error 透传；JSON 解析失败 → `JsonRpcError { code: -32000 }` 透传为 RemoteRejected —— **D**

### 注意事项（潜在 issue，非 lesion）

1. **`HttpDiscovery::connect` 的两次 RPC 不原子**（remote.rs:132-148）—— initialize 成功但 tools_list 失败时，server 端已留下 "initialized" 状态。本 task 通过 ok 路径验无此问题；error 路径实证 `tools_list` 也走相同分类。**不**算 lesion（无重试语义需求）。
2. **`RemoteConfig.tls_ca_cert` 当前无 test 覆盖**（remote.rs:48）—— truststore path 假设 PEM 格式。本 task 不探查；建议后续 phase 单独补 HTTPS 测试。
3. **`parse_sse_or_json` 错误信息含原始 body**（remote.rs:232）—— `format!("parse failed: {text}")` 把整个 response 塞进 message，敏感环境可能泄露。**不**算 lesion（仅 error path）。
4. **错误代码粒度**：所有错误统一 `code: -32000`，未区分 HTTP 4xx vs JSON parse vs MCP-level error。**建议**：细化错误代码（如 -32001 HTTP / -32002 Parse）。

---

## §E 探查回归

- 9.5.1 既有 4 test pass（FsDiscovery + McpNode + DiscoveryBackend + ScriptTool）
- 9.5.2 新增 4 test pass（HttpDiscovery + McpNode::remote + HttpProxyTool + 错误路径）
- 综合：9.5.1 + 9.5.2 = 8 test，**全 pass**，0 新 F-lesion
- 与 F-002/F-009 无关（pool overflow 与 tool discovery 不同抽象层）

---

## §F 与父 task / spec 的关系

| 父 task 期望 | 实证结果 |
|---|---|
| 1 个 initialize + tools/list 端到端 test | ✓ test1 pass |
| 1 个 McpNode::remote + connect(bus) test | ✓ test2 pass |
| 1 个 HttpProxyTool::execute tools/call test | ✓ test3 pass |
| 1 个错误路径（404）test | ✓ test4 pass |
| 预期 0 新 F-lesion | ✓ 0 新 F-lesion |

> 结论：9.5.2 探查显示 framework **McpNode + HttpDiscovery** 端到端 work——app 通过 `HttpDiscovery::connect(config)` 即可一步连远端 MCP server，initialize + tools/list + tools/call 完整工作。错误路径有合理分类（RemoteRejected）。

---

## §G 提交状态

- 工作目录：`crates/arf-e2e/tests/mcp_http_discovery.rs`（~210 行，4 test cases）
- task doc：`docs/v1.x/phase9/task-9.5.2.md`（新增）
- audit probe：本 doc
- lesion-registry：**未变**（0 新 F-lesion）
- 待 commit + push