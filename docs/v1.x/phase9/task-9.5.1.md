# 任务 9.5.1：McpNode + FsDiscovery（filesystem 扫描本地 tool/skill）

> Phase 9 — 9.5 C 工具集成 / McpNode 大类 · 第 1 task（依赖 9.4.x）
> 父文档：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.4.1-9.4.3（pool facade + 路由 + overflow 完整覆盖）
> 输出物：`docs/v1.x/phase9/audit-probe-9.5.1.md`
> 探查结论：**不预设**——本 doc 不写任何预期结果

---

## 设计思路

9.4.x 探查了 model 类（pool + capability 路由）。本 task (9.5.1) 探查 **tool/skill 发现**——framework 是否能让 app 通过 `FsDiscovery` 扫描本地目录，零代码注册 tool？

**Framework 现状**（待探查确认）：
- `arf-mcp::McpNode::local(namespace, root)` —— 从目录创建
- `arf-mcp::FsDiscovery::scan(root)` —— 扫描 tool.toml + skill
- `DiscoveryBackend` trait —— 4 方法（list_tools / get_tool / list_skills / get_skill）
- `connect(bus)` —— 注册到 bus

**关键探查问题**（不预设答案）：
1. `FsDiscovery::scan(root)` 是否真扫到 tool.toml 文件？返回什么？
2. `McpNode::local(root)` + `connect(bus)` 后，bus 上是否有 tool 可用？
3. `DiscoveryBackend` 4 方法各自端到端 work？哪些有 lesion？
4. tool.toml 文件 schema 是什么？必填字段？

按父 spec §3 探查 4 步流程 + §4 find signals 跑。

---

## 与现有测试的边界

- `crates/arf-mcp/src/tests/discovery_tests.rs`：FsDiscovery 单元测试（8 tests）
- `crates/arf-mcp/src/tests/node_tests.rs`：McpNode 单元测试
- **本 task 不重复**：单元 trait 测试
- **本 task 聚焦**：端到端 probe——FsDiscovery + McpNode + bus + engine chat 真实链路

---

## 探查步骤（按父 spec §3.1）

### Step 1 — 写最小代码路径（app 层 ≤ 90 行）

`mcp_fs_discovery.rs`，3-4 test cases：

| # | test | 探查 |
|---|---|---|
| 1 | `fs_discovery_scans_tool_toml` | tmpdir 写 2 个 tool.toml，FsDiscovery::scan 列出 2 个 tool — 验 schema |
| 2 | `mcp_node_local_connects_to_bus` | tmpdir tool.toml + McpNode::local + connect(bus) —— bus 上能否解析到 tool |
| 3 | `discovery_backend_trait_4_methods` | FsDiscovery 实现 DiscoveryBackend 4 方法（list_tools/get_tool/list_skills/get_skill）端到端 work |
| 4 | `e2e_tool_call_via_engine` | Engine + McpNode(tool "echo") + model 发出 tool_call —— framework 真转发 tool execution |

### Step 2 — framework 接触点 file:line

```bash
grep -n "pub struct FsDiscovery\|pub async fn scan" crates/arf-mcp/src/discovery.rs
grep -n "pub fn local\|pub async fn connect" crates/arf-mcp/src/node.rs
grep -n "impl DiscoveryBackend for FsDiscovery" crates/arf-mcp/src/discovery.rs
```

逐行解释：
- `FsDiscovery::scan` 扫描 root 下 tool.toml
- `McpNode::local` 包装 FsDiscovery + Runtime
- `connect` 注册 listener 到 bus

### Step 3 — framework 真实行为

```bash
cd /home/wangxie/open_deepseek_arf
cargo test -p arf-e2e --test mcp_fs_discovery -- --nocapture --test-threads=1 2>&1 | tee /tmp/mcp_fs_discovery_run.log
```

逐行解释：
- 4 test 应全过（mock + tmpdir）
- 任何 F-lesion 在 audit-probe §D 记录

**Read `/tmp/mcp_fs_discovery_run.log` 后填 Step 4 `framework 行为`**。

### Step 4 — 判定 + 记录

按 §3.3 输出 schema 填 4 个单元的判定。

按 §4 跑 signals：
- A1：FsDiscovery 是否一个 root 管所有 tool？（atomic 化）
- A2：tool.toml schema 与 code 一致？
- A3：tool name 跨 crate 唯一？
- A4：tool registration 集中？

**C. 输出**：`audit-probe-9.5.1.md`。