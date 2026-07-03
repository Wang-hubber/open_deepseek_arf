# 任务 9.12.1：自定义 DiscoveryBackend（实现 tool 3 方法 + 留 skill 默认）

> Phase 9 — 9.12 L 扩展点实现大类 · 第 1 task（依赖 9.5.3）
> 父文档：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.5.3（McpNode + 自定义 DiscoveryBackend 探查）
> 输出物：`docs/v1.x/phase9/audit-probe-9.12.1.md`
> 探查结论：**不预设**——本 doc 不写任何预期结果

---

## 设计思路

9.5.x 探查了 `FsDiscovery`（filesystem）和 `HttpDiscovery`（JSON-RPC）两个 framework-supplied DiscoveryBackend。本 task 探查 **app 端能否实现自己的 `DiscoveryBackend` trait**——capability matrix L8 列出的扩展点。

**Framework 现状**（待探查确认）：
- `DiscoveryBackend` trait（`crates/arf-mcp/src/discovery.rs:32-65`）—— 3 tool 方法 + 7 skill 方法（默认返回 None/empty）
- `McpNode` 的 `discovery` 字段是 `Box<dyn DiscoveryBackend>`（node.rs:19）—— 接受任何实现
- `local_with_runtime(ns, root, runtime)`（node.rs:54-67）—— 但 **没有** `local_with_discovery` / `custom_discovery`
- app 想注入自己的 DiscoveryBackend **必须**自己组装 McpNode 字段（不通过 public API）

**关键探查问题**（不预设答案）：
1. `DiscoveryBackend` trait 的 3 tool 方法（list_tools / tool_map / resolve_tool）必须 override 吗？还是 skill 方法有默认 impl？
2. `McpNode` 是否有 public `new` / 字段访问器允许注入自定义 `Box<dyn DiscoveryBackend>`？
3. 如果只有 `local(root)` / `remote(config)` 入口，app 写自定义 DiscoveryBackend 必须复制整个 McpNode 构造逻辑——是否构成 E 等级（扩展可达）？
4. 3 tool 方法 override + skill 全部留默认（return None / empty vec）—— `McpNode::connect(bus)` 后 bus 看到的是不是预期形态？

按父 spec §3 探查 4 步流程 + §4 find signals 跑。

---

## 与现有测试的边界

- `crates/arf-mcp/src/tests/discovery_tests.rs`：FsDiscovery 单元测试（8 tests）
- `crates/arf-mcp/src/tests/node_tests.rs`：McpNode 单元测试
- `crates/arf-e2e/tests/mcp_fs_discovery.rs`：9.5.1 端到端 probe（4 tests）
- **本 task 不重复**：FsDiscovery 的扫描测试
- **本 task 聚焦**：app 自定义 `impl DiscoveryBackend for MyBackend` —— McpNode 是否能容纳 + 端到端 work

---

## 探查步骤（按父 spec §3.1）

### Step 1 — 写最小代码路径（app 层 ≤ 90 行）

`custom_discovery_backend.rs`，3-4 test cases：

| # | test | 探查 |
|---|---|---|
| 1 | `custom_backend_3_tool_methods` | `impl DiscoveryBackend for InMemoryBackend`：override 3 tool 方法（list_tools / tool_map / resolve_tool），skill 留默认 → unit test 验 trait 多方法 work |
| 2 | `custom_backend_skill_methods_default_to_none` | 同 InMemoryBackend + skill 5 方法全部留默认（return None / empty）—— 验 skill 默认行为 |
| 3 | `custom_backend_in_mcp_node_via_constructor` | 尝试通过 `McpNode::local_with_runtime(ns, root, custom_rt)` 注入——但 local_with_runtime 用的是 FsDiscovery，**不**让选 DiscoveryBackend。需直接构造 `McpNode` struct（`pub fields` access）—— 验 public API 限制 |
| 4 | `custom_backend_end_to_end_via_bus` | 注入的 InMemoryBackend + 自定义 Runtime（mirror LocalRuntime）→ 端到端 `tool_exec` → 自定义 `MyTool::execute` 真实 work |

### Step 2 — framework 接触点 file:line

```bash
grep -n "pub struct McpNode\|pub fn local\|pub fn local_with_runtime\|pub fn remote\|discovery:\|runtime:" crates/arf-mcp/src/node.rs
grep -n "pub trait DiscoveryBackend" crates/arf-mcp/src/discovery.rs
grep -n "fn list_tools\|fn tool_map\|fn resolve_tool" crates/arf-mcp/src/discovery.rs
```

逐行解释：
- `McpNode.discovery` 字段是 `Box<dyn DiscoveryBackend>`（node.rs:19）—— trait 边界 OK
- `McpNode` 字段是 `pub`（node.rs:16-22）—— app 可直接构造
- 但 `local()` / `local_with_runtime()` / `remote()` 三个 public 入口**均用 framework-supplied discovery**——app 想用自己的，必须直接构造

### Step 3 — framework 真实行为

```bash
cd /home/wangxie/open_deepseek_arf
cargo test -p arf-e2e --test custom_discovery_backend -- --nocapture --test-threads=1 2>&1 | tee /tmp/custom_discovery_backend_run.log
```

逐行解释：
- 4 test 应全过（自定义 DiscoveryBackend + 注入 McpNode + 端到端 execute）
- 任何 F-lesion 在 audit-probe §D 记录

**Read `/tmp/custom_discovery_backend_run.log` 后填 Step 4 `framework 行为`**。

### Step 4 — 判定 + 记录

按 §3.3 输出 schema 填 4 个单元的判定。

按 §4 跑 signals：
- A1：DiscoveryBackend 是否一个 root 管所有 tool？（atomic 化）
- A2：DiscoveryBackend 三个 tool 方法 + 七个 skill 方法是否正交？app 实现 3 tool + 留 skill 默认是否端到端 work？
- A3：tool name 跨 crate 唯一？
- A4：tool registration 集中？

**C. 输出**：`audit-probe-9.12.1.md`。
