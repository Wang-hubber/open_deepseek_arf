# 任务 9.5.3：McpNode + 自定义 DiscoveryBackend

> Phase 9 — 9.5 C 工具集成 / McpNode 大类 · 第 3 task（依赖 9.5.1）
> 父文档：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.5.1（McpNode + FsDiscovery 端到端 OK）+ 9.5.2（HttpDiscovery OK）
> 输出物：`docs/v1.x/phase9/audit-probe-9.5.3.md`
> 探查结论：**不预设**——本 doc 不写任何预期结果

---

## 设计思路

9.5.1/9.5.2 探查了 framework 自带的 `FsDiscovery`（扫 filesystem）+ `HttpDiscovery`（JSON-RPC）。本 task (9.5.3) 探查 **`DiscoveryBackend` trait 扩展点**——app 实现自己的 backend（如注册中心、内存 map、SQLite），能否 drop-in 替换？

**Framework 现状**（待探查确认）：
- `crate::discovery::DiscoveryBackend` trait —— 4 tool 方法 + 7 skill 方法（skill 有 default impl）
- `McpNode` 没有 public constructor 接 `Box<dyn DiscoveryBackend>` —— 但 trait 是公开的
- `McpNode::local_with_runtime` 接 `Box<dyn RuntimeModule>`

**关键探查问题**（不预设答案）：
1. `DiscoveryBackend` trait 是否能 app 自己实现？Async trait 签名？
2. 自定义 backend 能否与 `McpNode` 组合？（需要绕过 `McpNode::local/remote` 的预设 backend）
3. 自定义 backend 的 tool 能否经由 McpNode 的 message_loop 端到端执行？
4. 是否需要 framework 暴露 `McpNode::with_discovery(...)` 之类的构造器？

按父 spec §3 探查 4 步流程 + §4 find signals 跑。

---

## 与现有测试的边界

- 9.5.1/9.5.2 已测 framework-provided backend
- **本 task 聚焦**：app 自定义 `DiscoveryBackend` 实现端到端 work

---

## 探查步骤（按父 spec §3.1）

### Step 1 — 写最小代码路径（app 层 ≤ 150 行）

`mcp_custom_discovery.rs`，4 test cases：

| # | test | 探查 |
|---|---|---|
| 1 | `custom_discovery_backend_lists_tools` | app 定义 `MemoryDiscovery`（在 `HashMap` 装 tool）→ impl trait → list_tools / resolve_tool work |
| 2 | `custom_discovery_backend_skills_default` | skill 方法默认 impl（None / 空 vec）正确返回 |
| 3 | `custom_discovery_with_mcp_node_via_internal_field` | app 通过 `McpNode::local_with_runtime` 注入外部构造的 FsDiscovery 但跑自定义执行路径 |
| 4 | `custom_discovery_used_by_tool_exec_via_bus` | 自定义 backend 通过 McpNode message_loop 端到端 work |

### Step 2 — framework 接触点 file:line

```bash
grep -n "pub trait DiscoveryBackend" crates/arf-mcp/src/discovery.rs
grep -n "impl DiscoveryBackend" crates/arf-mcp/src/discovery.rs crates/arf-mcp/src/remote.rs
grep -n "pub fn local_with_runtime\|pub fn with_discovery" crates/arf-mcp/src/node.rs
```

逐行解释：
- `DiscoveryBackend` 定义在 `crates/arf-mcp/src/discovery.rs:32-65`（async_trait）
- `FsDiscovery` + `HttpDiscovery` impl 该 trait
- `McpNode` 当前只有 `local / remote / local_with_runtime` —— 没有 `with_discovery`

### Step 3 — framework 真实行为

```bash
cargo test -p arf-e2e --test mcp_custom_discovery -- --nocapture --test-threads=1 2>&1 | tee /tmp/mcp_custom_discovery_run.log
```

逐行解释：
- 4 test 应全过（in-memory backend）
- 任何 F-lesion 在 audit-probe §D 记录（如 `McpNode::with_discovery` 缺失）

**Read `/tmp/mcp_custom_discovery_run.log` 后填 Step 4 `framework 行为`**。

### Step 4 — 判定 + 记录

按 §3.3 输出 schema 填 4 个单元的判定。

按 §4 跑 signals：
- A1：`DiscoveryBackend` 一个职责（resource registry）？
- A2：`DiscoveryBackend` 与 `McpNode` 接口边界——app 替换 backend 是否需要改 McpNode？
- A3：tool/skill 元数据是否只在 `DiscoveryBackend` 一份？
- A4：`DiscoveryBackend` 的 4+7 方法是否多余？

**C. 输出**：`audit-probe-9.5.3.md`。