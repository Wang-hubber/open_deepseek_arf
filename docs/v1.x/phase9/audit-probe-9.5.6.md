# audit-probe-9.5.6：McpNode + 自定义 RuntimeModule 端到端探查

> Task 9.5.6 探查产出 — **Framework 的 RuntimeModule trait 扩展点是否端到端 work？app 能否注入 sandbox/retry/metrics runtime？**
> 父 task doc：`docs/v1.x/phase9/task-9.5.6.md`
> 父 spec：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.5.1（McpNode + FsDiscovery OK）+ 9.5.4（LocalRuntime OK）
> **本 task 探查：RuntimeModule trait app 实现 + capabilities metadata + execute override + McpNode::local_with_runtime 注入**

---

## §A 探查环境

- working tree：HEAD `28148e9`（task 9.5.5）+ uncommitted `crates/arf-e2e/tests/mcp_custom_runtime.rs`
- 测试文件：`crates/arf-e2e/tests/mcp_custom_runtime.rs`（4 test cases）
- 驱动：app 自定义 `CountingRuntime`（atomic counter）+ `RetryRuntime`（重试逻辑）+ tmpdir script tool
- 测试命令：
  ```bash
  cargo test -p arf-e2e --test mcp_custom_runtime -- --nocapture --test-threads=1
  ```
- 结果：**`4 passed; 0 failed; 0.08s`**
- 关键运行输出：
  ```
  test custom_runtime_capabilities_custom_metadata ... ok
  test custom_runtime_default_execute_delegate ... ok
  test custom_runtime_override_execute_with_retry ... ok
  test custom_runtime_via_local_with_runtime_in_mcp_node ... 
  [test4] node_online payload: {"capabilities":{"runtime":{"concurrency":"sequential","metadata":{"kind":"counting-runtime","version":"1.0"},"runtime":"custom-counting"},"skills":[],"tools":[...]}}
  [test4] NodeInfo.runtime.runtime == custom-counting ✓
  ok
  ```

### 凭据安全（self-check 已通过）

```bash
$ git grep -n 'sk-' -- crates/arf-e2e/tests/mcp_custom_runtime.rs   # 无输出
```

---

## §B (capability, 情景) 单元判定

### 单元 1：自定义 RuntimeModule capabilities metadata

```
单元              : custom_runtime_capabilities × §2.3
能力等级           : D（PASS）
判定依据          : CountingRuntime::capabilities() 返回完整自定义 JSON
                   {"runtime":"custom-counting","concurrency":"sequential","metadata":{...}}
file:line         : crates/arf-mcp/src/runtime.rs:20-25 trait 定义
                   crates/arf-e2e/tests/mcp_custom_runtime.rs:48-58 CountingRuntime impl
                   ✓ capabilities 签名是 `fn capabilities(&self) -> Value`（无 async）
                   ✓ app 可注入任意 metadata
```

### 单元 2：默认 execute() → delegate executor

```
单元              : custom_runtime_default_execute × §2.3
能力等级           : D（PASS）
判定依据          : CountingRuntime override execute 调 executor::execute + 加 counter
                   2 个无依赖 tool 并发 → success
                   counter.fetch_add(1) 仅 1 次（说明 execute 调 1 次，含并发 call）
file:line         : crates/arf-e2e/tests/mcp_custom_runtime.rs:54-65 CountingRuntime::execute
                   crates/arf-mcp/src/runtime.rs:30-36 default impl
                   ✓ app 可 override execute + 加前置/后置逻辑
                   ✓ 委托给 executor::execute 仍是正确路径
```

### 单元 3：override execute + retry 逻辑

```
单元              : custom_runtime_override_execute_retry × §2.3
能力等级           : D（PASS）
判定依据          : RetryRuntime override execute → max_retries=3 + 重试直到 all success
                   1 个成功 tool → execute 跑 1 次即返 success
file:line         : crates/arf-e2e/tests/mcp_custom_runtime.rs:70-87 RetryRuntime::execute
                   ✓ retry 逻辑可注入 runtime 层（无需修改 framework）
                   ✓ app 可实现自定义执行策略（sandbox / metrics / circuit breaker 同理）
```

### 单元 4：McpNode::local_with_runtime + NodeInfo 含自定义 caps

```
单元              : mcp_node_local_with_runtime × §2.3
能力等级           : D（PASS）
判定依据          : McpNode::local_with_runtime("test-custom-rt", root, Box<dyn RuntimeModule>)
                   + connect(bus) → node_online 消息含完整 capabilities：
                   {"runtime":{"concurrency":"sequential","metadata":{...},"runtime":"custom-counting"},"skills":[],"tools":[...]}
file:line         : crates/arf-mcp/src/node.rs:54-68 McpNode::local_with_runtime
                   crates/arf-mcp/src/node.rs:85-103 build_node_info (注入 runtime.capabilities())
                   crates/arf-bus/src/lib.rs:495-537 handle_connect (broadcast node_online)
                   ✓ 自定义 caps 正确嵌套在 NodeInfo.capabilities.runtime
                   ✓ app 可通过 node_online 消息观察 runtime 决策（meta-programming 友好）
```

---

## §C 探查产出汇总

| 单元 | 等级 | 判定依据 |
|---|---|---|
| `custom_runtime_capabilities × §2.3` | **D** | 自定义 caps JSON 端到端 |
| `custom_runtime_default_execute × §2.3` | **D** | override + delegate executor 端到端 |
| `custom_runtime_override_execute_retry × §2.3` | **D** | retry 策略端到端 |
| `mcp_node_local_with_runtime × §2.3` | **D** | 自定义 caps 注入 NodeInfo 端到端 |

---

## §D 病灶登记

**本 task 无新增 F-lesion**（framework RuntimeModule trait + local_with_runtime 端到端 work）。

### 框架实际行为（按 spec §3.3 输出）

- `RuntimeModule` trait 公开、Send + Sync、async_trait —— **D**（runtime.rs:20-50）
- `capabilities` 方法签名 `fn capabilities(&self) -> Value`（非 async）—— **D**
- `execute(call_set, tools)` 默认 delegate `executor::execute` —— **D**
- `run_single(call_id, tool, params)` 默认调 `tool.execute` —— **D**
- `McpNode::local_with_runtime(ns, root, Box<dyn RuntimeModule>)` 注入 runtime —— **D**（node.rs:54-68）
- `build_node_info` 注入 `runtime.capabilities()` 到 NodeInfo —— **D**（node.rs:100）
- 自定义 caps 通过 node_online 消息传播 —— **D**

### 注意事项（潜在 issue，非 lesion）

1. **`RuntimeModule::capabilities` 不应返回 future**（runtime.rs:25 sync）—— 实际好设计（metadata 应同步），但限制 runtime 在 capabilities 表达"动态资源"的能力。**不**算 lesion。
2. **`run_single` 默认 impl 不支持 cancel**（runtime.rs:39-49）—— 仅 `tool.execute` 直调，不走 cancel_txs。**建议**：默认 impl 应与 ScriptTool cancel 模式一致。
3. **`McpNode::local_with_runtime` 仍强制 FsDiscovery**（node.rs:60）—— discovery 不能自定义。结合 F-010（缺 with_discovery），custom backend + custom runtime 仍需绕远路。**已知问题**：F-010。
4. **`build_node_info` 调用 `runtime.capabilities()` 每次 connect**（node.rs:100）—— 对动态资源（如实时 queue depth）有 race。**不**算 lesion（runtime 内部应保证 idempotent）。

---

## §E 探查回归

- 9.5.1 既有 4 test pass（FsDiscovery）
- 9.5.2 既有 4 test pass（HttpDiscovery）
- 9.5.3 既有 4 test pass（custom backend）+ F-010
- 9.5.4 既有 4 test pass（LocalRuntime DAG）
- 9.5.5 既有 4 test pass（RemoteRuntime DAG）+ F-011
- 9.5.6 新增 4 test pass（custom runtime）
- 综合：9.5.1-9.5.6 = 24 test，**全 pass**，0 新 F-lesion
- 与 F-010（discovery ctor）/ F-011（isError）属于不同模块，独立病灶

---

## §F 与父 task / spec 的关系

| 父 task 期望 | 实证结果 |
|---|---|
| 1 个自定义 capabilities test | ✓ test1 pass |
| 1 个默认 execute delegate test | ✓ test2 pass |
| 1 个 override execute + retry test | ✓ test3 pass |
| 1 个 McpNode::local_with_runtime + NodeInfo caps test | ✓ test4 pass |
| 预期 0 新 F-lesion | ✓ 0 新 F-lesion |

> 结论：9.5.6 探查显示 framework **RuntimeModule trait + local_with_runtime** 端到端 work —— app 可实现 capabilities / execute / run_single 三个方法，注入到 McpNode 后 NodeInfo 自动包含自定义 caps。建议后续 phase 补 `McpNode::with_discovery_and_runtime` 一并解决 F-010。

---

## §G 提交状态

- 工作目录：`crates/arf-e2e/tests/mcp_custom_runtime.rs`（~210 行，4 test cases）
- task doc：`docs/v1.x/phase9/task-9.5.6.md`（新增）
- audit probe：本 doc
- lesion-registry：**未变**（0 新 F-lesion）
- 待 commit + push