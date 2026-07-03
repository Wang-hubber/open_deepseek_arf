# audit-probe-9.12.2：自定义 RuntimeModule（自定义 execute 策略）端到端探查

> Task 9.12.2 探查产出 — **Framework 是否让 app 端实现自定义 `RuntimeModule` trait？**
> 父 task doc：`docs/v1.x/phase9/task-9.12.2.md`
> 父 spec：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.5.6（McpNode + 自定义 RuntimeModule 探查）
> **本 task 探查：app `impl RuntimeModule for MyRuntime`（override execute / run_single / capabilities）+ `McpNode::local_with_runtime` 端到端**

---

## §A 探查环境

- working tree：HEAD `ccd61a5`（task 9.12.1）+ uncommitted `crates/arf-e2e/tests/custom_runtime_module.rs`
- 测试文件：`crates/arf-e2e/tests/custom_runtime_module.rs`（4 test cases）
- 驱动：`CountingRuntime`（override execute）/ `LoggingRuntime`（override run_single）/ `CustomCapRuntime`（override capabilities only）+ 走 `McpNode::local_with_runtime` public API
- 测试命令：
  ```bash
  cargo test -p arf-e2e --test custom_runtime_module -- --nocapture --test-threads=1
  ```
- 结果：**`4 passed; 0 failed; 0.13s`**
- 关键运行输出：
  ```
  test custom_runtime_override_execute_strategy ... [test1] CountingRuntime override execute() 串行端到端 OK ✓
  test custom_runtime_override_run_single ... [logging_runtime] call_id=call-x status=success
  [test2] LoggingRuntime override run_single() pre/post 钩子 OK ✓
  test custom_runtime_capabilities_only ... [test3] CustomCapRuntime override capabilities() OK ✓
  test custom_runtime_in_mcp_node_end_to_end ... [test4] McpNode + bus connect OK ✓
  [test4] mcp node_online payload: {"capabilities":{"runtime":{"mode":"sequential-override","runtime":"counting"},"skills":[],"tools":[...]},...}
  [test4] 端到端 tool_exec → CountingRuntime::execute → ScriptTool OK ✓
  ```

### 凭据安全（self-check 已通过）

```bash
$ git grep -n 'sk-' -- crates/arf-e2e/tests/custom_runtime_module.rs   # 无输出
```

---

## §B (capability, 情景) 单元判定

### 单元 1：自定义 `RuntimeModule` override `execute()` 自定义执行策略

```
单元              : custom_runtime_execute_strategy × §2.12
能力等级           : D（PASS）
判定依据          : CountingRuntime override execute() 完全不调 executor::execute，
                   串行执行 ToolCallSet。结果含 2 个 ToolResultItem，count+1。
file:line         : crates/arf-mcp/src/runtime.rs:19-50 trait
                   crates/arf-mcp/src/runtime.rs:30-36 execute() default impl
                   crates/arf-mcp/src/node.rs:54-67 local_with_runtime
                   ✓ override execute() 端到端 work
```

### 单元 2：自定义 `RuntimeModule` override `run_single()`

```
单元              : custom_runtime_run_single × §2.12
能力等级           : D（PASS）
判定依据          : LoggingRuntime override run_single() 加 pre/post 钩子 + 调
                   tool.execute()。结果 status=success, pre_count+1, post_count+1。
file:line         : crates/arf-mcp/src/runtime.rs:39-49 run_single() default impl
                   ✓ override run_single() 端到端 work
```

### 单元 3：自定义 `RuntimeModule` 只 override `capabilities()`

```
单元              : custom_runtime_capabilities_only × §2.12
能力等级           : D（PASS）
判定依据          : CustomCapRuntime override capabilities() 返回自定义 JSON，
                   execute() 走 default impl 调 executor::execute，结果 success。
file:line         : crates/arf-mcp/src/runtime.rs:25-26 capabilities() 抽象方法
                   crates/arf-mcp/src/runtime.rs:30-36 execute() default impl
                   ✓ 只 override capabilities 端到端 work
```

### 单元 4：`McpNode::local_with_runtime` 注入自定义 Runtime 端到端

```
单元              : mcp_node_inject_custom_runtime × §2.12
能力等级           : D（PASS）
判定依据          : McpNode::local_with_runtime("custom-rt", root, Box::new(CountingRuntime))
                   → connect(bus) → node_online 含 capabilities.runtime={runtime:counting, mode:sequential-override}
                   → tool_exec → CountingRuntime::execute → ScriptTool execute → tool_result ok=true
file:line         : crates/arf-mcp/src/node.rs:54-67 local_with_runtime
                   crates/arf-mcp/src/node.rs:85-103 build_node_info
                   crates/arf-mcp/src/node.rs:131-141 dispatch (tool_call_set → runtime.execute)
                   crates/arf-mcp/src/node.rs:153-212 dispatch (tool_exec → runtime.execute)
                   ✓ public 入口端到端 OK
```

---

## §C 探查产出汇总

| 单元 | 等级 | 判定依据 |
|---|---|---|
| `custom_runtime_execute_strategy` | **D** | override execute() 完全自定义执行策略 OK |
| `custom_runtime_run_single` | **D** | override run_single() 加 pre/post 钩子 OK |
| `custom_runtime_capabilities_only` | **D** | override capabilities() only, execute 走 default OK |
| `mcp_node_inject_custom_runtime` | **D** | `McpNode::local_with_runtime` public 入口端到端 OK |

---

## §D 病灶登记

**本 task 无新增 F-lesion**。

### 框架实际行为（按 spec §3.3 输出）

- `McpNode::local_with_runtime(ns, root, Box<dyn RuntimeModule>)` —— **D 端到端**
  - 实证：CountingRuntime override execute() → 注入 McpNode → connect(bus)
    → node_online.payload.capabilities.runtime 正确反映自定义 capabilities
    → tool_exec → tool_result 端到端 work
- `RuntimeModule::capabilities()` 必须 override（abstract method）—— **D**
- `RuntimeModule::execute()` 有 default impl（调 executor::execute）—— **D**
- `RuntimeModule::run_single()` 有 default impl（调 tool.execute）—— **D**
- 三种 override 模式（execute / run_single / capabilities only）**均端到端 work**

### 与 9.12.1 的对比

| 扩展点 | public 入口 | 注入路径 |
|---|---|---|
| `DiscoveryBackend` (9.12.1) | ✗ 缺 | McpNode.discovery 字段 private |
| `RuntimeModule` (9.12.2) | ✓ `McpNode::local_with_runtime` | 端到端 work |

**RuntimeModule 是 L8 扩展点中端到端可用的**——这与 9.12.1 的 F-010 形成对比。

### 注意事项（潜在 issue，非 lesion）

1. **`local_with_runtime` 仍使用 `FsDiscovery::scan(root)`**（node.rs:60）—— 即使用户注入自定义 Runtime，DiscoveryBackend 仍 fixed 为 filesystem。**建议**：参照 `McpNode::with_discovery(ns, backend, runtime)` 同时允许自定义 Discovery + Runtime（见 F-010 修复方向）。
2. **`node_online.payload.capabilities.runtime` 嵌套 `runtime` key** —— `{"runtime": <RuntimeModule::capabilities() 返回>}`（node.rs:100），即两层嵌套。从测试看"runtime.runtime" 不直观，**建议** 改 `node_online.payload.capabilities.runtime_caps` 或类似清晰命名。这是 naming/结构小瑕疵，不构成 lesion。
3. **Bus `subscribe()` 不返回历史消息**（test4 实证：必须在 `connect` 前 subscribe 才能看到 node_online）—— 已有 9.1.x 实证（bus_exceptions test1 lagged_sender_never_blocks），无新增 F-lesion。

---

## §E 探查回归

- 9.12.1（custom_discovery_backend）4 test pass，未受本 task 影响
- 9.12.2 新增 4 test pass
- 综合：9.12.2 = 4 test，**4 pass, 0 新 F-lesion**
- F-001~F-009 + F-010 与本 task 无关
- **RuntimeModule 扩展点端到端 work——capability-matrix L8 的 `custom_runtime` 标记为 D 等级**

---

## §F 与父 task / spec 的关系

| 父 task 期望 | 实证结果 |
|---|---|
| 1 个 override execute() 自定义策略 test | ✓ test1 pass |
| 1 个 override run_single() pre/post 钩子 test | ✓ test2 pass |
| 1 个 override capabilities() only test | ✓ test3 pass |
| 1 个 local_with_runtime 端到端 test | ✓ test4 pass |
| 预期 0 新 F-lesion | ✓ 0 新 F-lesion |

> 结论：9.12.2 探查显示 framework `RuntimeModule` trait + `McpNode::local_with_runtime` public 入口端到端 work。app 端可自由 override `execute()` / `run_single()` / `capabilities()`，无 F-lesion。**与 9.12.1 F-010 形成对比**——同样 L8 扩展点，Runtime 有 public 入口，Discovery 没有。

---

## §G 提交状态

- 工作目录：`crates/arf-e2e/tests/custom_runtime_module.rs`（~330 行，4 test cases）
- task doc：`docs/v1.x/phase9/task-9.12.2.md`（新增）
- audit probe：本 doc
- lesion-registry：**未变**（0 新 F-lesion）
- 待 commit + push
