# 任务 9.8.1：单 agent + 单 MCP pool（facade + lease）

> Phase 9 — 9.8 F MCP pool 大类 · 第 1 task（依赖 9.5.x）
> 父文档：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.5.1（McpNode + FsDiscovery 端到端 OK）
> 输出物：`docs/v1.x/phase9/audit-probe-9.8.1.md`
> 探查结论：**不预设**——本 doc 不写任何预期结果

---

## 设计思路

情景 §2.5 描述"多 agent + 共享 MCP pool"，但本 task 9.8.1 探查**单 agent + 单 MCP pool**（facade 模式 + 内部 lease 池）。`MCPPoolNode` 是 framework 提供的 bus facade：对外（top bus）暴露 1 个 `node_type="mcp"` 节点 + 声明 tool 集合；对内（sub bus）转发 tool_exec 到真正 McpNode + 受 `Pool<McpResource>` 限流（lease 机制）。

**Framework 现状**（待探查确认）：
- `MCPPoolNode`（mcp/pool_node.rs:36-45）—— 6 字段（node_id, top_bus, sub_bus, pool, advertised_tools, advertised_skills）
- `MCPPoolNode::connect`（pool_node.rs:48-84）—— top_bus 注册 listener（`tool_exec`）+ sub_bus 注册 listener（`tool_result`）+ spawn run_loop
- `run_loop`（pool_node.rs:86-152）—— 收 tool_exec → acquire lease → 转发到 sub_bus → 等 tool_result → 转发回 top_bus → drop lease
- `Pool<McpResource>`（pool/lib.rs:151-272）—— bounded pool with Overflow 策略

**关键探查问题**（不预设答案）：
1. `MCPPoolNode::connect` 能在双 Bus 拓扑上端到端 work？
2. top_bus 上声明 advertised_tools 走 `ResourceRegistry::build` 解析？
3. 端到端 tool_exec → 转发到 sub_bus → McpNode 处理 → tool_result → 回 top_bus → engine？
4. `Pool<McpResource>` 的 lease 释放时序是否正确？

按父 spec §3 探查 4 步流程 + §4 find signals 跑。

---

## 与现有测试的边界

- `crates/arf-e2e/tests/mcp_facade.rs`：单 facade（手写翻译 loop），不依赖 `MCPPoolNode`
- `crates/arf-e2e/tests/pool_node_facade.rs`：`ModelAdapterPoolNode`（model pool），不探查 MCP pool
- **本 task 不重复**：手写 facade / model pool
- **本 task 聚焦**：`MCPPoolNode`（mcp 域）端到端 + lease 验证

---

## 探查步骤（按父 spec §3.1）

### Step 1 — 写最小代码路径（app 层 ≤ 90 行）

`mcp_pool_facade.rs`，3-4 test cases：

| # | test | 探查 |
|---|---|---|
| 1 | `mcp_pool_node_advertises_tools_via_capabilities` | `MCPPoolNode::connect` 后 bus.graph() 节点 `node_type="mcp"` + `capabilities.tools` 含 advertised_tools |
| 2 | `mcp_pool_node_resource_registry_resolves_owner` | Engine build + ResourceSpec + `MCPPoolNode` advertised tool → `owner_of_tool` 命中 mcp pool 节点 NodeId |
| 3 | `mcp_pool_node_lease_released_after_tool_exec` | `Pool<McpResource>` max=1 + 1 顺序 tool_exec → 第 1 次 lease 立即 OK + drop 后 lease 回 idle |
| 4 | `mcp_pool_node_e2e_tool_exec_routed_through_pool` | Engine.run + 1 tool_call + 1 McpNode 真实 sub_bus 端 → tool_result 回 top_bus → engine 完成 round |

### Step 2 — framework 接触点 file:line

```bash
grep -n "pub struct MCPPoolNode\|pub async fn connect" crates/arf-mcp/src/pool_node.rs
grep -n "pub struct McpResource\|impl Resource for McpResource" crates/arf-mcp/src/pool_resource.rs
grep -n "pub struct Pool\|pub fn provision" crates/arf-pool/src/lib.rs
```

逐行解释：
- `MCPPoolNode` 6 字段——app 直接 struct literal 构造
- `MCPPoolNode::connect(self: Arc<Self>)` —— top_bus + sub_bus 各注册 listener
- `run_loop` —— `pool.acquire()` + forward + wait for tool_result + drop(lease)
- `McpResource` 实现 `Resource` trait（pool_resource.rs:39-57）
- `Pool::provision(f)` —— app 提供 resource 构造 closure

### Step 3 — framework 真实行为

```bash
cd /home/wangxie/open_deepseek_arf/.worktrees/group3-multimcp
cargo test -p arf-e2e --test mcp_pool_facade -- --nocapture --test-threads=1 2>&1 | tee /tmp/mcp_pool_facade_run.log
```

逐行解释：
- 4 test 应全过
- 任何 F-lesion 在 audit-probe §D 记录

**Read `/tmp/mcp_pool_facade_run.log` 后填 Step 4 `framework 行为`**。

### Step 4 — 判定 + 记录

按 §3.3 输出 schema 填 4 个单元的判定。

按 §4 跑 signals：
- A1：`MCPPoolNode` 单一职责？（facade 转发起 lease 池）
- A2：`MCPPoolNode` 与 `McpNode` 正交？（共享 McpResource trait）
- A3：`advertised_tools` vs McpNode 实际 tools 唯一性？
- A4：lease acquire/release 集中？

**C. 输出**：`audit-probe-9.8.1.md`。
