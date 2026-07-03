# 任务 9.7.3：多 MCP + 跨 MCP dedup（同名 tool / AmbiguousTool）

> Phase 9 — 9.7 E 多 MCP 拓扑大类 · 第 3 task（依赖 9.5.1）
> 父文档：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.5.1（McpNode + FsDiscovery 端到端 OK）
> 输出物：`docs/v1.x/phase9/audit-probe-9.7.3.md`
> 探查结论：**不预设**——本 doc 不写任何预期结果

---

## 设计思路

情景 §2.3 / §2.4 都涉及"多 MCP tool 集合重叠"：app 部署多 McpNode 各自 tool 集合，**期望** framework 检测同名 tool 并拒绝构建（避免歧义）。本 task 探查 framework 跨 MCP dedup 行为。

**Framework 现状**（待探查确认）：
- `ResourceRegistry::build` 走 Subset filter 匹配 mcp 节点（registry.rs:62-73）
- 同一 tool 跨多 mcp 出现 → `BuildError::AmbiguousTool`（registry.rs:102-107）
- tool_index 静态构建（build 时冻结）
- 3 McpNode 各自 1 tool 不冲突 → tool_index 各自插入 OK

**关键探查问题**（不预设答案）：
1. 2 McpNode 各有 tool "shared" → `BuildError::AmbiguousTool` 立即拒绝？
2. 2 McpNode 各自 tool "shared" + 1 ResourceSpec Subset 指定 → 还能 dedup？
3. 3 McpNode 各自 1 tool "a/b/c" + 1 ResourceSpec All 选 2 → 不冲突 build OK？
4. 2 McpNode 各自 1 tool + 1 ResourceSpec Subset 指定非交集 → 不冲突 build OK？

按父 spec §3 探查 4 步流程 + §4 find signals 跑。

---

## 与现有测试的边界

- `crates/arf-e2e/tests/multi_mcp_strict_route.rs`：3 McpNode distinct tools（9.7.1）
- `crates/arf-engine/src/registry.rs:418-444` 单元测试 `registry_build_ambiguous_tool_fails`：直接构造 BusGraph + AgentConfig 测 dedup
- **本 task 不重复**：单元测试
- **本 task 聚焦**：端到端 probe——2 个真 McpNode + 真 Bus + Engine build + 期望 AmbiguousTool

---

## 探查步骤（按父 spec §3.1）

### Step 1 — 写最小代码路径（app 层 ≤ 90 行）

`multi_mcp_dedup.rs`，3-4 test cases：

| # | test | 探查 |
|---|---|---|
| 1 | `cross_mcp_same_tool_name_triggers_ambiguous` | 2 McpNode（独立 root）+ 各自 1 tool "shared" + 2 ResourceSpec → `BuildError::AmbiguousTool` |
| 2 | `cross_mcp_distinct_tools_no_ambiguity` | 2 McpNode 各自 tool "alpha" / "beta" + 2 ResourceSpec → build OK |
| 3 | `cross_mcp_same_tool_subset_filter_dedups` | 2 McpNode 各自 1 tool "shared" + 1 ResourceSpec Subset 包含 "shared" → 仍 AmbiguousTool（build 时 2 mcp 都匹配 Subset） |
| 4 | `cross_mcp_three_nodes_one_shared_tool` | 3 McpNode + tool "x" 出在 mcp_a + mcp_b + tool "y" 在 mcp_c + 3 ResourceSpec → AmbiguousTool 含 providers=[a, b] |

### Step 2 — framework 接触点 file:line

```bash
grep -n "AmbiguousTool\|tool_index" crates/arf-engine/src/registry.rs
grep -n "pub enum BuildError" crates/arf-engine/src/error.rs
```

逐行解释：
- `registry.rs:102-107` — tool 重复检测 + AmbiguousTool 错误
- `error.rs:36-41` — AmbiguousTool 错误定义

### Step 3 — framework 真实行为

```bash
cd /home/wangxie/open_deepseek_arf/.worktrees/group3-multimcp
cargo test -p arf-e2e --test multi_mcp_dedup -- --nocapture --test-threads=1 2>&1 | tee /tmp/multi_mcp_dedup_run.log
```

逐行解释：
- 4 test 应全过
- 任何 F-lesion 在 audit-probe §D 记录

**Read `/tmp/multi_mcp_dedup_run.log` 后填 Step 4 `framework 行为`**。

### Step 4 — 判定 + 记录

按 §3.3 输出 schema 填 4 个单元的判定。

按 §4 跑 signals：
- A1：registry.build 单职责？（只做 build 时 dedup，不做运行时）
- A2：AmbiguousTool 是单点错误？
- A3：tool_index key（tool name）唯一？
- A4：dedup 集中在 build 时？

**C. 输出**：`audit-probe-9.7.3.md`。
