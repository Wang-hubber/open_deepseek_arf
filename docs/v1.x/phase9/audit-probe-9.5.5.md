# audit-probe-9.5.5：McpNode + RemoteRuntime（HTTP 工具执行）端到端探查

> Task 9.5.5 探查产出 — **Framework 的 RemoteRuntime（默认 RuntimeModule）端到端 work？HTTP 工具的 DAG 调度正确？**
> 父 task doc：`docs/v1.x/phase9/task-9.5.5.md`
> 父 spec：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.5.1（McpNode + FsDiscovery OK）+ 9.5.2（HttpDiscovery OK）
> **本 task 探查：RemoteRuntime::capabilities + execute 端到端 + HTTP layer-parallel 并发**

---

## §A 探查环境

- working tree：HEAD `0fd5b5f`（task 9.5.4）+ uncommitted `crates/arf-e2e/tests/mcp_remote_runtime.rs`
- 测试文件：`crates/arf-e2e/tests/mcp_remote_runtime.rs`（4 test cases）
- 驱动：mock HTTP server（tokio TcpListener + initialize / tools/list / tools/call responder）+ HttpDiscovery
- 测试命令：
  ```bash
  cargo test -p arf-e2e --test mcp_remote_runtime -- --nocapture --test-threads=1
  ```
- 结果：**`4 passed; 0 failed; 0.32s`**
- 关键运行输出：
  ```
  test remote_runtime_capabilities_metadata ... [test1] RemoteRuntime::capabilities() = {"runtime":"remote"}
  ok
  test remote_runtime_cascade_cancel_via_http_failure ... 
  [test4]   - call_id=call_0 status=success
  [test4]   - call_id=call_1 status=success
  [test4] 实证发现：HttpProxyTool 当前不根据 isError 标 status=error
  ok
  test remote_runtime_default_executor_with_http_tools ... ok
  test remote_runtime_layer_parallel_http_calls ... [test3] parallel remote execute elapsed = 202.989508ms
  [test3] call_count = 2
  ok
  ```

### 凭据安全（self-check 已通过）

```bash
$ git grep -n 'sk-' -- crates/arf-e2e/tests/mcp_remote_runtime.rs   # 无输出
```

---

## §B (capability, 情景) 单元判定

### 单元 1：`RemoteRuntime::capabilities` 元数据

```
单元              : runtime_capabilities_remote × §2.3
能力等级           : D（PASS）
判定依据          : RemoteRuntime::capabilities() = {"runtime":"remote"}
                   与 LocalRuntime 的 {"runtime":"local","concurrency":"layer-parallel"} 区分清晰
file:line         : crates/arf-mcp/src/runtime.rs:71-75
                   ✓ 单一 JSON object，与 LocalRuntime 形态一致
```

### 单元 2：`RemoteRuntime::execute` 默认 delegate executor + HttpProxyTool

```
单元              : remote_runtime_default_executor × §2.3
能力等级           : D（PASS）
判定依据          : RemoteRuntime 没 override execute()，走默认 executor::execute
                   executor 调 tool.execute() → HttpProxyTool::execute → POST tools/call JSON-RPC
                   端到端 run remote_echo → success + 返回 Value::String("remote-result")
file:line         : crates/arf-mcp/src/runtime.rs:30-36 default impl
                   crates/arf-mcp/src/remote.rs:64-117 HttpProxyTool::execute
                   ✓ RemoteRuntime 通过 trait 默认委托 + DAG executor 调度
                   ✓ HttpProxyTool 解析 CallToolResult.content.text → Value::String
```

### 单元 3：DAG layer-parallel HTTP 并发调用

```
单元              : dag_layer_parallel_http × §2.3
能力等级           : D（PASS）
判定依据          : 2 个远端 tool (slow_a + slow_b)，各 delay 200ms
                   实测 elapsed = 202.99ms（≈ 单 call 200ms，< 串行 400ms）
                   call_count = 2（mock server 实收 2 个 tools/call）
file:line         : crates/arf-mcp/src/executor.rs:54-71 layer by layer
                   crates/arf-mcp/src/executor.rs:65-68 tokio::spawn per call
                   ✓ HTTP 调用通过 tokio::spawn 真并发（reqwest connection pool 自动利用）
                   ✓ HTTP 不串行（验 elapsed vs 串行总和）
```

### 单元 4：Cascade cancel via HTTP failure（**实证 finding**）

```
单元              : remote_runtime_cascade_cancel × §2.3
能力等级           : E（**部分** PASS — 链路 work，但 cascade 不自动触发）
判定依据          : mock server 返回 isError:true，HttpProxyTool::execute 当前**不**根据 isError 标 status=error
                   call_0.status="success" + call_1.status="success" → cascade cancel 不触发
file:line         : crates/arf-mcp/src/remote.rs:78-117 HttpProxyTool::execute
                   ✗ remote.rs:105-115: result 始终 Ok(Value::String(text))，无 isError 检查
                   → Tool::execute() 返回 Ok → executor 标 status="success" → cascade 不触发
                   → 后果：远端 tool 的逻辑失败（如 4xx/5xx）**不**自动级联取消下游
                   影响面：远程 MCP server 返回 content 但 isError:true → app 收 success，无错误信号
                   修复建议：HttpProxyTool 应检查 result.isError / content.isError，标 Err
```

---

## §C 探查产出汇总

| 单元 | 等级 | 判定依据 |
|---|---|---|
| `runtime_capabilities_remote × §2.3` | **D** | RemoteRuntime capabilities 端到端 |
| `remote_runtime_default_executor × §2.3` | **D** | execute + HttpProxyTool 端到端 |
| `dag_layer_parallel_http × §2.3` | **D** | 2 远端并发 203ms ≈ 单 call |
| `remote_runtime_cascade_cancel × §2.3` | **E** | 链路 OK；isError 不透传，cascade 不自动触发 |

---

## §D 病灶登记

### F-011：HttpProxyTool 不识别 `isError` → 远端逻辑失败无错误信号

```
病灶 ID       : F-011（新增）
信条           : A4 处理集中
Signal         : A4-S1（filter 动词散落）/ A4-S3（permission 决策散落）
触发情景       : §2.3（远端 MCP tool 集成）
file:line      : crates/arf-mcp/src/remote.rs:78-117 HttpProxyTool::execute
                crates/arf-mcp/src/remote.rs:25-36 CallToolResult / ToolContent schema
命中形态       : MCP 协议 CallToolResult.content[].isError=true 应视为 tool error
                HttpProxyTool 当前只取 content[].text → Value::String
                完全忽略 content.isError 标记
                → executor 收到 Ok → status="success"
                → cascade cancel 不会触发
                → app 拿到"成功"但实际是逻辑失败
影响面         : 任何通过 HTTP MCP 远端调用返回 isError:true 的场景
                → DAG 下游的 tool 不会被 cascade cancel（继续执行）
                → app 错误处理代码不会收到 Err 信号
                → 与本地 ScriptTool（exit code != 0 → Err）行为不一致
                修复建议：HttpProxyTool::execute 应检查 content[].isError，
                若 true 则返回 Err(ToolError::from("mcp isError: ..."))
                schema 已在 remote.rs:25-36 定义，缺字段 → 需扩展 + 检查
```

### 框架实际行为（按 spec §3.3 输出）

- `RemoteRuntime::capabilities() = {"runtime":"remote"}` —— **D**
- `RemoteRuntime::execute()` 默认 delegate `executor::execute()` —— **D**
- DAG layer-parallel HTTP 真并发（实测 203ms ≈ 单 call）—— **D**
- `HttpProxyTool::execute` 不识别 `isError` → cascade 不触发 —— **F-011**

### 注意事项（潜在 issue，非 lesion）

1. **`RemoteRuntime` 与 `LocalRuntime` 形态不一致**（runtime.rs:57-75）—— LocalRuntime 有 `concurrency` metadata，RemoteRuntime 没。**建议**：统一加 concurrency 字段或删 LocalRuntime 的。
2. **HTTP connection pool 共享**（remote.rs:194-213 build_http_client）—— 每个 HttpDiscovery 一个 reqwest::Client，不在 McpNode 层共享。多个 McpNode 用同一 URL 会建多 client。**不**算 lesion（隔离边界清晰）。
3. **`RemoteConfig.timeout_secs` 用作 HTTP timeout**（remote.rs:93-95）—— 但 Tool::execute 默认 impl 无 timeout（executor.rs:118-199 只用 call_set.timeout_ms）。**建议**：HttpProxyTool 应内部用 config.timeout 而非依赖 call_set.timeout_ms。

---

## §E 探查回归

- 9.5.1 既有 4 test pass（FsDiscovery）
- 9.5.2 既有 4 test pass（HttpDiscovery）
- 9.5.3 既有 4 test pass（custom backend）+ F-010
- 9.5.4 既有 4 test pass（LocalRuntime DAG）
- 9.5.5 新增 4 test pass（RemoteRuntime DAG）+ F-011（isError 不透传）
- 综合：9.5.1-9.5.5 = 20 test，**全 pass**，1 新 F-lesion（F-011）
- F-011 与 F-010（custom discovery ctor）属于不同模块，独立病灶

---

## §F 与父 task / spec 的关系

| 父 task 期望 | 实证结果 |
|---|---|
| 1 个 RemoteRuntime::capabilities test | ✓ test1 pass |
| 1 个 RemoteRuntime::execute + HttpProxyTool test | ✓ test2 pass |
| 1 个 layer-parallel HTTP test | ✓ test3 pass（203ms ≈ 单 call） |
| 1 个 cascade cancel via HTTP test | △ test4 pass（链路 OK，但暴露 F-011） |
| 预期可能 0 新 F-lesion | ✗ **1 新 F-lesion（F-011）** |

> 结论：9.5.5 探查显示 framework **RemoteRuntime + HttpProxyTool** 端到端 work —— capabilities metadata、default executor delegate、layer-parallel HTTP 并发全部行为正确。但**暴露 F-011**：HttpProxyTool 不识别 MCP `isError` 标记 → 远端逻辑失败无错误信号，cascade cancel 不会自动触发。建议后续 phase 修 HttpProxyTool 解析 isError。

---

## §G 提交状态

- 工作目录：`crates/arf-e2e/tests/mcp_remote_runtime.rs`（~210 行，4 test cases）
- task doc：`docs/v1.x/phase9/task-9.5.5.md`（新增）
- audit probe：本 doc
- lesion-registry：**1 新 F-lesion（F-011）**，按 prompt 要求**不修改** lesion-registry.md，仅在本 §D 登记
- 待 commit + push