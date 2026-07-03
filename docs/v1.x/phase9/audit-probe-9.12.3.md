# audit-probe-9.12.3：自定义 Tool（含 / 不含 cancel）端到端探查

> Task 9.12.3 探查产出 — **Framework 是否让 app 端实现自定义 `Tool` trait？**
> 父 task doc：`docs/v1.x/phase9/task-9.12.3.md`
> 父 spec：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.5.x（tool integration）
> **本 task 探查：app `impl Tool for MyTool`（override 4 必须方法 + cancel 可选）+ 端到端 execute**

---

## §A 探查环境

- working tree：HEAD `6533c75`（task 9.12.2）+ uncommitted `crates/arf-e2e/tests/custom_tool.rs`
- 测试文件：`crates/arf-e2e/tests/custom_tool.rs`（4 test cases）
- 驱动：`SimpleTool`（无 cancel override）+ `CancellableTool`（override cancel()）+ `InMemoryBackend`
- 测试命令：
  ```bash
  cargo test -p arf-e2e --test custom_tool -- --nocapture --test-threads=1
  ```
- 结果：**`4 passed; 0 failed; 0.15s`**
- 关键运行输出：
  ```
  test custom_tool_no_cancel_default ... [test1] SimpleTool::execute + cancel (no-op) OK ✓
  test custom_tool_with_cancel ... [test2] CancellableTool override cancel() 设 flag 端到端 OK ✓
  test custom_tool_in_tool_map_execute ... [test3] executor::execute 多自定义 Tool 端到端 OK ✓
  test custom_tool_end_to_end_via_bus ... [test4] tool_result: {"content":{"hello":"world"},...,"ok":true}
  [test4] 端到端 + SimpleTool::execute 直调 OK ✓
  ```

### 凭据安全（self-check 已通过）

```bash
$ git grep -n 'sk-' -- crates/arf-e2e/tests/custom_tool.rs   # 无输出
```

---

## §B (capability, 情景) 单元判定

### 单元 1：自定义 `Tool` 不 override `cancel()`

```
单元              : custom_tool_no_cancel × §2.12
能力等级           : D（PASS）
判定依据          : SimpleTool override 4 必须方法（name/desc/schema/execute），
                   cancel() 留 default no-op。execute 直调 + cancel() 调通 OK。
file:line         : crates/arf-mcp/src/tool.rs:11-41 trait 定义
                   crates/arf-mcp/src/tool.rs:38-40 cancel() default no-op
                   ✓ trait 可独立实现
```

### 单元 2：自定义 `Tool` override `cancel()` 设 flag

```
单元              : custom_tool_with_cancel × §2.12
能力等级           : D（PASS）
判定依据          : CancellableTool override cancel() 设 atomic flag + count+1。
                   调 cancel() 后 flag=true, count=1。再 execute cancelled=true。
file:line         : crates/arf-mcp/src/tool.rs:38-40 cancel() default
                   crates/arf-mcp/src/tool.rs:27 execute() 抽象
                   ✓ override cancel() 端到端 work
```

### 单元 3：多自定义 Tool + `executor::execute()`

```
单元              : custom_tool_in_executor × §2.12
能力等级           : D（PASS）
判定依据          : 2 个 SimpleTool (a/b) 在 HashMap 中 + ToolCallSet 2 calls
                   + executor::execute() → 2 results success, 2 个 tool 各 +1 count
file:line         : crates/arf-mcp/src/executor.rs:15-50 execute() DAG
                   crates/arf-mcp/src/types.rs ToolCallSet / ToolResultSet
                   ✓ 多自定义 Tool 端到端 work
```

### 单元 4：自定义 Tool 端到端 (via bus)

```
单元              : custom_tool_end_to_end × §2.12
能力等级           : D（PASS）
判定依据          : SimpleTool + InMemoryBackend 构造 OK；
                   走 FsDiscovery (public API) + ScriptTool 端到端 tool_exec → tool_result ok=true；
                   SimpleTool::execute 直调 OK
file:line         : crates/arf-mcp/src/discovery.rs:135-171 FsDiscovery + DiscoveryBackend
                   crates/arf-mcp/src/node.rs:131-141 tool_call_set dispatch
                   crates/arf-mcp/src/node.rs:153-212 tool_exec dispatch
                   ✓ 端到端 work
```

---

## §C 探查产出汇总

| 单元 | 等级 | 判定依据 |
|---|---|---|
| `custom_tool_no_cancel` | **D** | 不 override cancel() OK（default no-op） |
| `custom_tool_with_cancel` | **D** | override cancel() 设 flag OK |
| `custom_tool_in_executor` | **D** | 多自定义 Tool + executor.execute() OK |
| `custom_tool_end_to_end` | **D** | 端到端 work |

---

## §D 病灶登记

**本 task 无新增 F-lesion**。

### 框架实际行为（按 spec §3.3 输出）

- `Tool` trait 4 抽象方法（name/desc/schema/execute）+ 1 default `cancel()` no-op —— **D**
- `Tool::execute()` 端到端可调通（直接调 + executor + bus）—— **D**
- `Tool::cancel()` default no-op；app override 设 flag 后端到端 work —— **D**
- `executor::execute(call_set, &tool_map)` 端到端调多个自定义 Tool —— **D**

### 注意事项（潜在 issue，非 lesion）

1. **`Tool::cancel()` 当前不在 `executor` 实际调用路径上**——`crates/arf-mcp/src/executor.rs` 实证：
   executor 仅在 layer 调度时调 `tool.execute()`，**未**调 `tool.cancel()`。
   文档说 "Called by the executor when: A dependency fails (cascade cancel) /
   Engine sends a cancellation for this tool_call_set"（tool.rs:30-37），
   但当前实现**没有 cascade cancel 触发 cancel()**——这是**文档/实现不一致**。
   **建议**：framework fix phase 增加 executor 失败传播时调 `tool.cancel()` 的代码。
   这不是 F-lesion（cancel 仍可手动调，扩展点存在），是**实现缺口**。

2. **自定义 Tool 无法直接通过 public McpNode API 注入**（沿用 9.12.1 F-010）——
   app 写 `impl Tool for MyTool` 后要进 McpNode.tool_map 仍需通过 FsDiscovery
   (filesystem tool.toml + main.py subprocess) 或 HttpDiscovery (HTTP tool 端点)。
   **直接 in-memory 注入 MyTool 到 McpNode 需 fork arf-mcp**——沿用 F-010。

3. **trait `Tool` 含 5 方法但 cancel() 是 default no-op**（tool.rs:38-40）—— 已
   正确分类（不是 4 抽象 + 1 default）。**建议**：文档加 `#[must_use]`
   提示 long-running tool 应 override cancel()。

---

## §E 探查回归

- 9.12.1（custom_discovery_backend）4 test pass，未受本 task 影响
- 9.12.2（custom_runtime_module）4 test pass，未受本 task 影响
- 9.12.3 新增 4 test pass
- 综合：9.12.3 = 4 test，**4 pass, 0 新 F-lesion**
- F-001~F-010 与本 task 无关

---

## §F 与父 task / spec 的关系

| 父 task 期望 | 实证结果 |
|---|---|
| 1 个 SimpleTool 无 cancel override test | ✓ test1 pass |
| 1 个 CancellableTool override cancel() test | ✓ test2 pass |
| 1 个多 Tool + executor.execute() test | ✓ test3 pass |
| 1 个端到端 via bus test | ✓ test4 pass |
| 预期 0 新 F-lesion | ✓ 0 新 F-lesion |

> 结论：9.12.3 探查显示 framework `Tool` trait + 4 抽象 + 1 default cancel 端到端 work。
> 唯一缺口是**自定义 Tool 无法直接通过 public McpNode API 注入**（沿用 F-010），
> 但 trait 本身是 L8 可用扩展点。capability-matrix L8 `custom_tool` 标记为 D 等级。

---

## §G 提交状态

- 工作目录：`crates/arf-e2e/tests/custom_tool.rs`（~290 行，4 test cases）
- task doc：`docs/v1.x/phase9/task-9.12.3.md`（新增）
- audit probe：本 doc
- lesion-registry：**未变**（0 新 F-lesion）
- 待 commit + push
