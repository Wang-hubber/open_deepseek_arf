# 任务 9.6.2：skill_load_on_demand（`use_skill` 协议）

> Phase 9 — 9.6 D Skill 渐进加载 · 第 2 task（依赖 9.6.1）
> 父文档：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.6.1（L1 list 端到端 OK）
> 输出物：`docs/v1.x/phase9/audit-probe-9.6.2.md`
> 探查结论：**不预设**——本 doc 不写任何预期结果

---

## 设计思路

9.6.1 探查了 L1 list 端到端。本 task (9.6.2) 聚焦 **L2 on-demand 加载** —— framework 是否提供 `use_skill` 协议，让 app 显式触发"按需加载 body + resources"？

**Framework 现状**（待探查确认）：
- `McpNode::dispatch("use_skill")` 处理 use_skill 消息（node.rs:214-227）
  - payload: `{name: "skill_name"}`
  - response: `skill_loaded` 或 `skill_error`
  - 内部：调 `discovery.load_skill_body(name)` + `discovery.load_skill_resources(name)`
- `McpNode::dispatch("load_skill_resource")` 处理单文件加载（node.rs:229-241）
  - payload: `{skill_name, resource_path}`
  - response: `skill_resource_loaded` 或 `skill_resource_error`
- 协议层：McpNode 注册 listener (`to=node_id`)，外部通过 `bus.send` 发 `use_skill` 消息

**关键探查问题**（不预设答案）：
1. `use_skill` 协议 payload schema？response schema？
2. 端到端（bus send → McpNode dispatch → bus receive response）work？
3. 不存在的 skill name 时返回 `skill_error` 而非 panic？
4. response 字段（namespace / name / description / body / resources）是否完整？
5. 与 9.6.1 L1 list 的边界：use_skill 触发后才出现 body，list 阶段**不**带 body

按父 spec §3 探查 4 步流程 + §4 find signals 跑。

---

## 与现有测试的边界

- `crates/arf-mcp/src/tests/node_tests.rs`：McpNode 单元测试（mock bus）
- **本 task 不重复**：unit-level field test
- **本 task 聚焦**：端到端 — bus send/receive + use_skill protocol + 不存在 skill 的 error path

---

## 探查步骤（按父 spec §3.1）

### Step 1 — 写最小代码路径（app 层 ≤ 100 行）

`skill_load_on_demand.rs`，3-4 test cases：

| # | test | 探查 |
|---|---|---|
| 1 | `use_skill_protocol_round_trip` | bus + McpNode::local + 1 SKILL.md + listener 收 use_skill → emit `skill_loaded` 响应（含 body + resources） |
| 2 | `use_skill_includes_resources_manifest` | 写 SKILL.md + tools/gen/main.py + references/api.md，response 含 `resources: {tools, references, assets}` 列表 |
| 3 | `use_skill_unknown_returns_error` | send `use_skill` 给不存在 skill 名 → response `skill_error` 包含 error msg |
| 4 | `use_skill_via_bus_does_not_load_body_at_scan` | scan 阶段 list_skills() 后**不**触发 body 读；use_skill 触发后才读 body |

### Step 2 — framework 接触点 file:line

```bash
grep -n '"use_skill"\|skill_loaded\|skill_error' crates/arf-mcp/src/node.rs
grep -n 'pub fn load_skill_body\|pub fn load_skill_resources' crates/arf-mcp/src/skill.rs
grep -n 'pub async fn send\|pub fn subscribe' crates/arf-bus/src/lib.rs
```

逐行解释：
- `McpNode::dispatch("use_skill")` 协议处理（node.rs:214-227）
- `bus.subscribe()` 拿 broadcast receiver
- `bus.send()` 发到 McpNode 节点

### Step 3 — framework 真实行为

```bash
cd /home/wangxie/open_deepseek_arf/.worktrees/group2-skill
cargo test -p arf-e2e --test skill_load_on_demand -- --nocapture --test-threads=1 2>&1 | tee /tmp/skill_load_on_demand_run.log
```

逐行解释：
- 4 test 应全过（mock + tmpdir）
- 任何 F-lesion 在 audit-probe §D 记录

**Read `/tmp/skill_load_on_demand_run.log` 后填 Step 4 `framework 行为`**。

### Step 4 — 判定 + 记录

按 §3.3 输出 schema 填 4 个单元的判定。

按 §4 跑 signals：
- A1：`use_skill` 处理路径单一职责？
- A2：use_skill / load_skill_resource 协议对称？
- A3：response 字段 schema 单一来源？
- A4：error handling 集中？

**C. 输出**：`audit-probe-9.6.2.md`。
