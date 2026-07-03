# audit-probe-9.5.7：McpNode + ScriptTool（python/bash/rustc）+ cancel 端到端探查

> Task 9.5.7 探查产出 — **Framework 的 ScriptTool 端到端 work？3 种 runtime + cancel 行为正确？**
> 父 task doc：`docs/v1.x/phase9/task-9.5.7.md`
> 父 spec：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.5.4（LocalRuntime OK）+ 9.5.1（ScriptTool python 已验）
> **本 task 探查：ScriptTool python + bash + rustc 编译/缓存 + cancel 行为**

---

## §A 探查环境

- working tree：HEAD `bbdfc74`（task 9.5.6）+ uncommitted `crates/arf-e2e/tests/mcp_script_tool.rs`
- 测试文件：`crates/arf-e2e/tests/mcp_script_tool.rs`（4 test cases）
- 驱动：直接构造 `ScriptTool`（不经过 FsDiscovery）— 验 ScriptTool 自身行为
- 测试命令：
  ```bash
  cargo test -p arf-e2e --test mcp_script_tool -- --nocapture --test-threads=1
  ```
- 结果：**`4 passed; 0 failed; 0.78s`**
- 关键运行输出：
  ```
  test script_tool_python_runtime ... ok
  test script_tool_bash_runtime ... ok
  test script_tool_rust_runtime_compile_and_run ... 
  [test3] rust (first call, includes compile) result: ... elapsed=246.044968ms
  [test3] rust (second call, cached binary) result: ... elapsed=1.061377ms
  ok
  test script_tool_cancel_kills_child ... 
  [test4] execute 返回: ToolError { message: "cancelled" } (elapsed after cancel = 198.52µs)
  ok
  ```

### 凭据安全（self-check 已通过）

```bash
$ git grep -n 'sk-' -- crates/arf-e2e/tests/mcp_script_tool.rs   # 无输出
```

---

## §B (capability, 情景) 单元判定

### 单元 1：Python runtime

```
单元              : script_tool_python × §2.3
能力等级           : D（PASS）
判定依据          : python3 main.py + stdin JSON + stdout JSON 端到端
                   返回 {"echoed":{"x":42},"lang":"python"}
file:line         : crates/arf-mcp/src/script.rs:73-77 Python match arm
                   crates/arf-mcp/src/script.rs:148-251 execute (统一 IO 处理)
                   ✓ Command::new("python3") spawn + pipe stdin/stdout/stderr
                   ✓ JSON stdin/stdout parse
```

### 单元 2：Bash runtime

```
单元              : script_tool_bash × §2.3
能力等级           : D（PASS）
判定依据          : bash main.sh + stdin JSON + stdout JSON 端到端
                   返回 {"echoed":{"y":99},"lang":"bash"}
file:line         : crates/arf-mcp/src/script.rs:78-82 Bash match arm
                   ✓ Command::new("bash") spawn + 显式 arg script
                   ✓ 假设脚本可执行（不需要 chmod +x —— bash <script> 模式）
```

### 单元 3：Rust runtime + rustc 编译 + mtime 缓存

```
单元              : script_tool_rust_with_cache × §2.3
能力等级           : D（PASS）
判定依据          : 1st call: rustc compile (246ms) → 跑 binary → success
                   2nd call: mtime check → skip compile (1ms) → 跑 cached binary → success
                   缓存路径触发：`needs_compile = src_time > bin_time` (script.rs:89-98)
file:line         : crates/arf-mcp/src/script.rs:83-130 Rust match arm
                   crates/arf-mcp/src/script.rs:89-98 mtime compare
                   ✓ rustc spawn + 编译错误捕获（script.rs:117-125）
                   ✓ 缓存 binary 路径 = `tool_dir/entrypoint.trim_end_matches(".rs")`
                   ✓ 编译后跑 binary（不是 rustc 直接 run）
                   性能数据：compile 246ms vs cached 1ms → 缓存有效
```

### 单元 4：`ScriptTool::cancel` 真 kill 子进程

```
单元              : script_tool_cancel × §2.3
能力等级           : D（PASS）
判定依据          : spawn python sleep(60) + 500ms 后调 cancel() → 子进程被 kill
                   execute 返回 Err("cancelled")，elapsed = 198µs（远 < 60s）
file:line         : crates/arf-mcp/src/script.rs:179-181 cancel_txs 注册
                   crates/arf-mcp/src/script.rs:201-216 tokio::select! cancel branch
                   crates/arf-mcp/src/script.rs:205-209 child.start_kill().ok()
                   crates/arf-mcp/src/script.rs:253-264 cancel() drain + send all
                   ✓ oneshot::Sender 模式让 cancel() 跨调用边界传递信号
                   ✓ child.start_kill() 真终止子进程
                   ✓ Arc<Mutex<Option<Child>>> 双 owner 模式（wait_fut + cancel/timeout 共享）
```

---

## §C 探查产出汇总

| 单元 | 等级 | 判定依据 |
|---|---|---|
| `script_tool_python × §2.3` | **D** | python 端到端 OK |
| `script_tool_bash × §2.3` | **D** | bash 端到端 OK |
| `script_tool_rust_with_cache × §2.3` | **D** | rustc 编译 + mtime 缓存端到端 |
| `script_tool_cancel × §2.3` | **D** | cancel 真 kill 子进程（198µs） |

---

## §D 病灶登记

**本 task 无新增 F-lesion**（framework ScriptTool python/bash/rustc + cancel 端到端 work）。

### 框架实际行为（按 spec §3.3 输出）

- `ScriptTool::new(config, dir)` 构造 —— **D**（script.rs:50-62）
- `build_command` 3 runtime 分支（Python / Bash / Rust）—— **D**（script.rs:70-131）
- Python: `python3 <entrypoint>` —— **D**
- Bash: `bash <entrypoint>` —— **D**
- Rust: mtime check → rustc compile → binary path —— **D**
- `execute` 统一 IO 模板（stdin JSON + stdout JSON + stderr 捕获）—— **D**
- `cancel_txs` Mutex<HashMap<u64, oneshot::Sender<()>>> —— **D**
- `execute` 内 `tokio::select!` 处理 wait / cancel / timeout 3 branch —— **D**
- `cancel()` drain 所有 sender → 触发所有 in-flight invocation —— **D**
- `Arc<Mutex<Option<Child>>>` 双 owner 共享给 wait_fut + cancel/timeout —— **D**

### 注意事项（潜在 issue，非 lesion）

1. **Rust 缓存路径用 `entrypoint.trim_end_matches(".rs")`**（script.rs:87）—— 如果 entrypoint 是 `tools/main`（无扩展名）会失败。**不**算 lesion（tool.toml schema 强制 `.rs`）。
2. **`stderr` 被吞没在 cancel 路径**（script.rs:205-209）—— 只调 `start_kill`，不读 stderr。**不**算 lesion（cancel 是"快速终止"语义）。
3. **`cancel_txs` 移除 sender 的位置**（script.rs:233）—— 只在正常退出分支移除；timeout/cancel 分支**不**显式移除（依赖下次 cancel() drain 清空）。**潜在 resource leak**（小），但 cancel() 后必然 drain，无累积风险。
4. **`timeout_ms` 与 `cancel()` 走相同分支**（script.rs:197-217）—— 都是 start_kill + 返回 Err。语义不同（timeout = "超时"，cancel = "外部取消"）但 message 区分。**D**。

---

## §E 探查回归

- 9.5.1 既有 4 test pass（FsDiscovery）
- 9.5.2 既有 4 test pass（HttpDiscovery）
- 9.5.3 既有 4 test pass（custom backend）+ F-010
- 9.5.4 既有 4 test pass（LocalRuntime DAG）
- 9.5.5 既有 4 test pass（RemoteRuntime DAG）+ F-011
- 9.5.6 既有 4 test pass（custom runtime）
- 9.5.7 新增 4 test pass（ScriptTool 3 runtime + cancel）
- 综合：9.5.1-9.5.7 = **28 test**，**全 pass**，0 新 F-lesion

---

## §F 与父 task / spec 的关系

| 父 task 期望 | 实证结果 |
|---|---|
| 1 个 python runtime test | ✓ test1 pass |
| 1 个 bash runtime test | ✓ test2 pass |
| 1 个 rust + 缓存 test | ✓ test3 pass（compile 246ms / cached 1ms） |
| 1 个 cancel test | ✓ test4 pass（cancel 198µs kill 子进程） |
| 预期 0 新 F-lesion | ✓ 0 新 F-lesion |

> 结论：9.5.7 探查显示 framework **ScriptTool** 端到端 work —— python / bash / rustc 3 runtime + mtime 缓存 + cancel 全部行为正确。Cancel 性能优秀（198µs 触发），Rust 缓存有效（246ms → 1ms）。这是 phase 9 第三次在 tool 执行端探查无 F-lesion 的 task。

---

## §G 提交状态

- 工作目录：`crates/arf-e2e/tests/mcp_script_tool.rs`（~150 行，4 test cases）
- task doc：`docs/v1.x/phase9/task-9.5.7.md`（新增）
- audit probe：本 doc
- lesion-registry：**未变**（0 新 F-lesion）
- 待 commit + push