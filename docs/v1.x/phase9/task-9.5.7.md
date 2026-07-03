# 任务 9.5.7：McpNode + ScriptTool（python/bash/rustc）+ cancel

> Phase 9 — 9.5 C 工具集成 / McpNode 大类 · 第 7 task（依赖 9.5.4）
> 父文档：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.5.4（McpNode + LocalRuntime OK）
> 输出物：`docs/v1.x/phase9/audit-probe-9.5.7.md`
> 探查结论：**不预设**——本 doc 不写任何预期结果

---

## 设计思路

9.5.4 探查了 LocalRuntime DAG executor。本 task (9.5.7) 聚焦 **ScriptTool 本身** —— framework 默认的 `Tool` 实现，能跑 python / bash / rustc 3 种 runtime？cancel 是否 work？

**Framework 现状**（待探查确认）：
- `crate::script::ScriptTool` —— 封装 subprocess（python3 / bash / compiled rustc binary）
- `runtime: ScriptRuntime` —— Python / Bash / Rust 3 选 1（config.rs:9-15）
- `cancel_txs: Mutex<HashMap<u64, oneshot::Sender<()>>>` —— cancel signal per invocation
- `ScriptTool::cancel()` 调所有 sender 发 signal（script.rs:253-264）

**关键探查问题**（不预设答案）：
1. Python runtime 端到端 work（与 9.5.1 验过）？
2. Bash runtime 端到端 work？
3. Rust runtime（含 rustc 编译 + 缓存）端到端 work？
4. `ScriptTool::cancel()` 真能 kill 子进程？timeout_ms 路径也走 cancel？

按父 spec §3 探查 4 步流程 + §4 find signals 跑。

---

## 与现有测试的边界

- 9.5.1 已测 Python runtime echo（test4 tool_execute_via_script_tool）
- **本 task 聚焦**：3 runtime 端到端对比 + cancel 行为

---

## 探查步骤（按父 spec §3.1）

### Step 1 — 写最小代码路径（app 层 ≤ 220 行）

`mcp_script_tool.rs`，4 test cases：

| # | test | 探查 |
|---|---|---|
| 1 | `script_tool_python_runtime` | Python 脚本 + stdin/stdout JSON 端到端 |
| 2 | `script_tool_bash_runtime` | Bash 脚本 + stdin/stdout JSON 端到端 |
| 3 | `script_tool_rust_runtime_compile_and_run` | Rust 源 → rustc 编译 → 缓存 binary → 跑 binary |
| 4 | `script_tool_cancel_kills_child` | 长跑 tool + 调 cancel() → 子进程被 kill + 返回 Err("cancelled") |

### Step 2 — framework 接触点 file:line

```bash
grep -n "pub struct ScriptTool\|impl Tool for ScriptTool" crates/arf-mcp/src/script.rs
grep -n "pub fn cancel\|fn build_command" crates/arf-mcp/src/script.rs
```

逐行解释：
- `ScriptTool::build_command` 3 个 match 分支（script.rs:70-131）
- `cancel_txs` Mutex<HashMap> + oneshot 模式（script.rs:179-181, 253-264）
- `Tool::cancel` 默认 impl（tool.rs:38-40）

### Step 3 — framework 真实行为

```bash
cargo test -p arf-e2e --test mcp_script_tool -- --nocapture --test-threads=1 2>&1 | tee /tmp/mcp_script_tool_run.log
```

逐行解释：
- 4 test 应全过（python + bash + rustc + cancel）
- 任何 F-lesion 在 audit-probe §D 记录

**Read `/tmp/mcp_script_tool_run.log` 后填 Step 4 `framework 行为`**。

### Step 4 — 判定 + 记录

按 §3.3 输出 schema 填 4 个单元的判定。

按 §4 跑 signals：
- A1：`ScriptTool` 一个职责（subprocess + JSON IO）？
- A2：`build_command` 的 3 个 runtime 分支与 execute 路径是否一致？
- A3：rustc 编译缓存（mtime compare）与 build_command 边界？
- A4：cancel signal 集中 vs 散落？

**C. 输出**：`audit-probe-9.5.7.md`。