# 任务 9.5.6：McpNode + 自定义 RuntimeModule（sandbox）

> Phase 9 — 9.5 C 工具集成 / McpNode 大类 · 第 6 task（依赖 9.5.1）
> 父文档：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.5.1（McpNode + FsDiscovery OK）+ 9.5.4（LocalRuntime OK）
> 输出物：`docs/v1.x/phase9/audit-probe-9.5.6.md`
> 探查结论：**不预设**——本 doc 不写任何预期结果

---

## 设计思路

9.5.4 探查了 `LocalRuntime`（framework default executor）。本 task (9.5.6) 探查 **`RuntimeModule` trait 扩展点** —— app 能否实现自定义 runtime（如 sandbox / 重试 / 指标收集），并通过 `McpNode::local_with_runtime` 注入？

**Framework 现状**（待探查确认）：
- `crate::runtime::RuntimeModule` trait —— 3 方法（capabilities / execute / run_single）
- `execute(call_set, tools)` 有 default impl（runtime.rs:30-36）
- `run_single(call_id, tool, params)` 有 default impl（runtime.rs:39-49）
- `McpNode::local_with_runtime(ns, root, runtime)` —— 接受自定义 runtime（node.rs:54-68）

**关键探查问题**（不预设答案）：
1. `RuntimeModule` trait 是否能 app 自己实现？async_trait + Send + Sync？
2. 自定义 runtime 的 capabilities metadata 是否能被 McpNode 注入 NodeInfo？
3. 自定义 runtime 的 execute 覆盖（不 delegate executor）是否端到端 work？
4. app 能否通过自定义 runtime 实现 retry / metrics / sandbox 策略？

按父 spec §3 探查 4 步流程 + §4 find signals 跑。

---

## 与现有测试的边界

- 9.5.4 已测 LocalRuntime
- **本 task 聚焦**：app 自定义 RuntimeModule（覆盖 execute / capabilities）

---

## 探查步骤（按父 spec §3.1）

### Step 1 — 写最小代码路径（app 层 ≤ 200 行）

`mcp_custom_runtime.rs`，4 test cases：

| # | test | 探查 |
|---|---|---|
| 1 | `custom_runtime_capabilities_custom_metadata` | app 定义 `CountingRuntime` → capabilities() 返回自定义 JSON |
| 2 | `custom_runtime_default_execute_delegate` | app runtime 不 override execute → 默认 delegate executor → 端到端 work |
| 3 | `custom_runtime_override_execute_with_retry` | app runtime override execute → 加 retry 逻辑 → 端到端 work |
| 4 | `custom_runtime_via_local_with_runtime_in_mcp_node` | McpNode::local_with_runtime + 自定义 runtime → NodeInfo 含自定义 caps |

### Step 2 — framework 接触点 file:line

```bash
grep -n "pub trait RuntimeModule\|pub async fn execute\|pub async fn run_single" crates/arf-mcp/src/runtime.rs
grep -n "pub fn local_with_runtime" crates/arf-mcp/src/node.rs
```

逐行解释：
- `RuntimeModule` 3 方法 + async_trait（runtime.rs:20-50）
- `execute` + `run_single` 都有 default impl（runtime.rs:30-49）
- `local_with_runtime` 注入 Box<dyn RuntimeModule>（node.rs:54-68）

### Step 3 — framework 真实行为

```bash
cargo test -p arf-e2e --test mcp_custom_runtime -- --nocapture --test-threads=1 2>&1 | tee /tmp/mcp_custom_runtime_run.log
```

逐行解释：
- 4 test 应全过（in-memory runtime + tmpdir script tool）
- 任何 F-lesion 在 audit-probe §D 记录

**Read `/tmp/mcp_custom_runtime_run.log` 后填 Step 4 `framework 行为`**。

### Step 4 — 判定 + 记录

按 §3.3 输出 schema 填 4 个单元的判定。

按 §4 跑 signals：
- A1：`RuntimeModule` 一个职责（execute tool call set）？
- A2：`execute` 与 `run_single` 是同层抽象？
- A3：capabilities metadata 跨 module 是否唯一？
- A4：runtime 决策 vs executor 决策是否分开？

**C. 输出**：`audit-probe-9.5.6.md`。