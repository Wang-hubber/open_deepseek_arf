# audit-probe-9.5.4：McpNode + LocalRuntime（默认 DAG executor）端到端探查

> Task 9.5.4 探查产出 — **Framework 的 LocalRuntime（默认 RuntimeModule）是否端到端 work？DAG executor 的 layer-parallel + cascade cancel 是否生效？**
> 父 task doc：`docs/v1.x/phase9/task-9.5.4.md`
> 父 spec：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.5.1（McpNode + FsDiscovery OK）
> **本 task 探查：LocalRuntime::capabilities + execute 端到端 + layer-parallel 并发 + cascade cancel**

---

## §A 探查环境

- working tree：HEAD `cd5b6ed`（task 9.5.3）+ uncommitted `crates/arf-e2e/tests/mcp_local_runtime.rs`
- 测试文件：`crates/arf-e2e/tests/mcp_local_runtime.rs`（4 test cases）
- 驱动：3 个 mock tool（echo / slow_a / slow_b / failing / never_runs）+ 直接调 `LocalRuntime::execute`
- 测试命令：
  ```bash
  cargo test -p arf-e2e --test mcp_local_runtime -- --nocapture --test-threads=1
  ```
- 结果：**`4 passed; 0 failed; 0.30s`**
- 关键运行输出：
  ```
  test local_runtime_capabilities_metadata ... ok
  test local_runtime_cascade_cancel_on_failure ... [test4] cascade cancel elapsed = 45.482217ms
  [test4]   - call_id=call_0 status=error
  [test4]   - call_id=call_1 status=cancelled
  ok
  test local_runtime_default_executor_delegate ... ok
  test local_runtime_layer_parallel_concurrent ... [test3] parallel execute elapsed = 231.373487ms
  ok
  ```

### 凭据安全（self-check 已通过）

```bash
$ git grep -n 'sk-' -- crates/arf-e2e/tests/mcp_local_runtime.rs   # 无输出
```

---

## §B (capability, 情景) 单元判定

### 单元 1：`LocalRuntime::capabilities` 元数据

```
单元              : runtime_capabilities × §2.3
能力等级           : D（PASS）
判定依据          : LocalRuntime::capabilities() = {"runtime":"local","concurrency":"layer-parallel"}
file:line         : crates/arf-mcp/src/runtime.rs:59-61
                   ✓ 单一 JSON object，无副作用
                   ✓ framework 暴露 runtime 决策给上层
```

### 单元 2：`LocalRuntime::execute` 默认调 `executor::execute`

```
单元              : local_runtime_default_executor × §2.3
能力等级           : D（PASS）
判定依据          : LocalRuntime 默认 execute() 调 executor::execute(call_set, tools) (runtime.rs:30-36)
                   端到端 run echo tool → success + 参数 JSON 回传
file:line         : crates/arf-mcp/src/runtime.rs:30-36 default impl
                   crates/arf-mcp/src/executor.rs:15-114 executor::execute
                   ✓ 1 call ToolCallSet 端到端 work
                   ✓ status="success" + result 正确回传
```

### 单元 3：DAG layer-parallel 并发执行

```
单元              : dag_layer_parallel × §2.3
能力等级           : D（PASS）
判定依据          : 2 个 tool (slow_a + slow_b)，各 delay 200ms，无依赖
                   实测 elapsed = 231ms（>200ms 单个，<400ms 串行总和）
                   → 确认并发执行（join_all futures）
file:line         : crates/arf-mcp/src/executor.rs:54-71 layer by layer
                   crates/arf-mcp/src/executor.rs:65-68 tokio::spawn per call
                   ✓ join_all futures 触发真并发
                   ✓ Kahn topological sort 识别 layer 0（无依赖 call）
```

### 单元 4：DAG cascade cancel

```
单元              : dag_cascade_cancel × §2.3
能力等级           : D（PASS）
判定依据          : call_0 强制 fail（exit 1）→ call_1 blocked_by call_0 应被 cascade cancel
                   实测 elapsed = 45.48ms（远小于 call_1 的 5s delay）
                   call_1.status = "cancelled", error = "upstream dependency ... failed"
file:line         : crates/arf-mcp/src/executor.rs:88-91  cascade_cancel after layer fail
                   crates/arf-mcp/src/executor.rs:358-396 cascade_cancel 沿 blocking chain BFS
                   ✓ cascade cancel 立即触发，不等 downstream 完成
                   ✓ call_1 状态正确标 cancelled + 上游失败原因
```

---

## §C 探查产出汇总

| 单元 | 等级 | 判定依据 |
|---|---|---|
| `runtime_capabilities × §2.3` | **D** | LocalRuntime capabilities 端到端 |
| `local_runtime_default_executor × §2.3` | **D** | execute → executor 端到端 |
| `dag_layer_parallel × §2.3` | **D** | 2 无依赖并发 231ms < 串行 400ms |
| `dag_cascade_cancel × §2.3` | **D** | cascade cancel 45ms 立即触发 |

---

## §D 病灶登记

**本 task 无新增 F-lesion**（framework LocalRuntime + executor DAG 端到端 work）。

### 框架实际行为（按 spec §3.3 输出）

- `LocalRuntime::capabilities() = {"runtime":"local","concurrency":"layer-parallel"}` —— **D**
- `LocalRuntime::execute()` 默认 delegate `executor::execute(call_set, tools)` —— **D**
- `executor::execute` DAG 调度（Kahn topo sort + layer by layer + join_all）—— **D**
- Layer 0 无依赖 call 真并发执行（实测 231ms ≈ 单 call 200ms）—— **D**
- Cascade cancel 沿 blocking chain BFS，标 cancelled + error msg —— **D**

### 注意事项（潜在 issue，非 lesion）

1. **`LocalRuntime::execute` 默认 delegate `executor::execute`，但 runtime trait 没暴露更细粒度控制**（如 per-call timeout override）—— executor 已支持 `ToolCallSet.timeout_ms`，runtime 直接透传。**D**。
2. **Cascade cancel 不调 `tool.cancel()`**（executor.rs:88-91）—— 只标 cancelled 状态，没执行 Tool::cancel()。
   但 cascade 的语义是"还没运行的 tool"——所以也不需要 cancel 子进程。**不**算 lesion。
3. **`call_set.timeout_ms` 是 per-call 不区分**（executor.rs:60）—— 所有 call 共用一个 timeout。
   对 layer 0 并发足够；layer N 的 call 实际时间 = sum(layer_0_to_N) — 可能超时不及时。
   **不**算 lesion（per-call timeout 是过度设计）。

---

## §E 探查回归

- 9.5.1 既有 4 test pass（FsDiscovery）
- 9.5.2 既有 4 test pass（HttpDiscovery）
- 9.5.3 既有 4 test pass（custom backend）
- 9.5.4 新增 4 test pass（LocalRuntime DAG executor）
- 综合：9.5.1-9.5.4 = 16 test，**全 pass**，0 新 F-lesion
- 与 F-002/F-009（pool）/ F-010（custom discovery ctor）属于不同抽象层，互不影响

---

## §F 与父 task / spec 的关系

| 父 task 期望 | 实证结果 |
|---|---|
| 1 个 LocalRuntime::capabilities test | ✓ test1 pass |
| 1 个 LocalRuntime::execute → executor test | ✓ test2 pass |
| 1 个 layer-parallel 并发 test | ✓ test3 pass（231ms < 380ms） |
| 1 个 cascade cancel test | ✓ test4 pass（45ms 立即 cancel） |
| 预期 0 新 F-lesion | ✓ 0 新 F-lesion |

> 结论：9.5.4 探查显示 framework **LocalRuntime + executor DAG** 端到端 work —— capabilities metadata、default executor delegate、layer-parallel 并发、cascade cancel 全部行为正确。这是 phase 9 第二次在 execution 端探查无 F-lesion 的 task。

---

## §G 提交状态

- 工作目录：`crates/arf-e2e/tests/mcp_local_runtime.rs`（~210 行，4 test cases）
- task doc：`docs/v1.x/phase9/task-9.5.4.md`（新增）
- audit probe：本 doc
- lesion-registry：**未变**（0 新 F-lesion）
- 待 commit + push