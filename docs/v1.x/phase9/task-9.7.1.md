# 任务 9.7.1：多 MCP + Static route（Strict → multiple NodeIds）

> Phase 9 — 9.7 E 多 MCP 拓扑大类 · 第 1 task（依赖 9.5.1）
> 父文档：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.5.1（McpNode + FsDiscovery 端到端 OK）
> 输出物：`docs/v1.x/phase9/audit-probe-9.7.1.md`
> 探查结论：**不预设**——本 doc 不写任何预期结果

---

## 设计思路

9.5.x 探查了单 MCP tool/skill discovery。情景 §2.3 描述"单 agent + 多 MCP（route 表多选，Strict）"——app 部署 N 个不同 namespace 的 McpNode（每 node 不同 tool 集合），通过 `Route::Strict` 显式列出 NodeIds 决定 tool_exec 路由。

**Framework 现状**（待探查确认）：
- `Route::Strict(Vec<NodeId>)` —— 显式 NodeId 列表（route.rs:36）
- `Route::Discovery(Capability)` —— 能力匹配（route.rs:38）
- `AgentConfig.engine.routes: HashMap<String, Route>` —— 自定义 msg_type 路由
- `ResourceSpec` —— 声明 mcp 节点（agent/resource.rs:18）
- `EngineBuilder::build` —— 校验 Strict targets 在线（builder.rs:73-82）
- `resolve_route(route, graph, cache)` —— Route → NodeIds 解析（checkpoint.rs:87-96）

**关键探查问题**（不预设答案）：
1. `Route::Strict([mcp_a, mcp_b, mcp_c])` + `tool_exec` —— engine 真把 tool_exec 投到 3 个 mcp 节点？
2. 多 McpNode 同 tool namespace 不同 tool 集合 —— 各自 handle 自己的 tool（不冲突）？
3. `Route::Strict` 中含不在线 NodeId —— `BuildError::MissingNodes` 立即拒绝？
4. Static route 与 `ResourceRegistry` 推导的 `tool_exec` route 关系？

按父 spec §3 探查 4 步流程 + §4 find signals 跑。

---

## 与现有测试的边界

- `crates/arf-e2e/tests/mcp_fs_discovery.rs`：单 MCP discovery（9.5.1）
- `crates/arf-e2e/tests/mcp_facade.rs`：facade 转发
- `crates/arf-e2e/tests/pool_node_facade.rs`：MCPPoolNode
- **本 task 不重复**：单 MCP 探查
- **本 task 聚焦**：多 MCP + `Route::Strict` 端到端

---

## 探查步骤（按父 spec §3.1）

### Step 1 — 写最小代码路径（app 层 ≤ 90 行）

`multi_mcp_strict_route.rs`，3-4 test cases：

| # | test | 探查 |
|---|---|---|
| 1 | `strict_route_resolves_to_multiple_node_ids` | `Route::Strict([mcp_a, mcp_b, mcp_c])` + 3 个 mcp 在线 → `resolve_route_pure` 返回 3 个 NodeId |
| 2 | `strict_route_fails_build_when_node_offline` | `Route::Strict([mcp_online, mcp_ghost])` → `BuildError::MissingNodes` |
| 3 | `multi_mcp_nodes_distinct_tools_engines_resolve` | 3 McpNode 各有 1 个 tool（`echo_a`/`echo_b`/`echo_c`） + 3 ResourceSpec + 3 Strict NodeIds → Engine build 成功 + `owner_of_tool` 各 tool → 各 mcp 节点 |
| 4 | `multi_mcp_engine_executes_tool_via_correct_node` | Engine.run + scripted provider tool_call_response("echo_b") + 3 mcp + facade for tool_result → 端到端 echo_b 命中 mcp_b 节点 |

### Step 2 — framework 接触点 file:line

```bash
grep -n "pub enum Route\|pub fn strict\|pub fn resolve_route" crates/arf-core/src/route.rs crates/arf-engine/src/checkpoint.rs
grep -n "MissingNodes\|Route::Strict" crates/arf-engine/src/builder.rs
grep -n "pub struct ResourceSpec" crates/arf-agent/src/resource.rs
```

逐行解释：
- `Route::Strict(ids)` —— 显式 NodeId 列表
- `resolve_route` —— Route → NodeIds（Strict 直返，Discovery 走 cache）
- `BuildError::MissingNodes` —— Strict target 不在线立即拒绝
- `ResourceSpec` —— 声明一个 mcp 节点的 tool/skill 子集

### Step 3 — framework 真实行为

```bash
cd /home/wangxie/open_deepseek_arf/.worktrees/group3-multimcp
cargo test -p arf-e2e --test multi_mcp_strict_route -- --nocapture --test-threads=1 2>&1 | tee /tmp/multi_mcp_strict_route_run.log
```

逐行解释：
- 4 test 应全过（3 mock + 1 build 失败）
- 任何 F-lesion 在 audit-probe §D 记录

**Read `/tmp/multi_mcp_strict_route_run.log` 后填 Step 4 `framework 行为`**。

### Step 4 — 判定 + 记录

按 §3.3 输出 schema 填 4 个单元的判定。

按 §4 跑 signals：
- A1：`Route::Strict` vs `Route::Discovery` 单一职责？（2 个 variant vs 1 enum）
- A2：Strict IDs 与 Capability 是否正交？
- A3：`NodeId` 唯一？（全局 NodeId 空间）
- A4：route resolution 集中？（resolve_route 是 single seam）

**C. 输出**：`audit-probe-9.7.1.md`。
