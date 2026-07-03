# 任务 9.6.4：skill_resource_load（`load_skill_resource` + `LoadedResource`）

> Phase 9 — 9.6 D Skill 渐进加载 · 第 4 task（依赖 9.5.1）
> 父文档：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.5.1（FsDiscovery 端到端 OK）
> 输出物：`docs/v1.x/phase9/audit-probe-9.6.4.md`
> 探查结论：**不预设**——本 doc 不写任何预期结果

---

## 设计思路

9.6.1-9.6.3 探查了 skill L1 list（9.6.1）+ L2 use_skill 协议（9.6.2）+ skill 内部 tool 执行（9.6.3）。本 task (9.6.4) 聚焦 **resource 单文件加载** —— framework 是否提供 `load_skill_resource` 协议 / `LoadedResource` 类型，让 app 按需加载 skill 内部单文件（references / assets / tools/.../main.py）？

**Framework 现状**（待探查确认）：
- `SkillIndex::load_resource_file(skill, path) -> Result<LoadedResource, String>`（skill.rs:147-179）
  - path 必须以 `tools/` / `references/` / `assets/` 开头
  - 拒绝 `..` 路径遍历 + 绝对路径
  - `tools/{tool}/...` 路径会附加 description + params_schema（来自 tool.toml）
- `LoadedResource` struct（skill.rs:48-55）：`content: String` + `description: Option<String>` + `params_schema: Option<Value>`
- `DiscoveryBackend::load_resource_file` 委托 SkillIndex（discovery.rs:162-164）
- `McpNode::dispatch("load_skill_resource")` 协议（node.rs:229-241）
  - payload: `{skill_name, resource_path}`
  - response: `skill_resource_loaded` 或 `skill_resource_error`

**关键探查问题**（不预设答案）：
1. `load_resource_file` 端到端 work？3 类 path prefix（tools / references / assets）？
2. `LoadedResource` 字段（content / description / params_schema）在 tools/ 路径下自动附加？
3. 路径遍历（`..`）与绝对路径（`/`）拒绝？
4. 不存在的 file / 不存在的 skill error path？
5. `load_skill_resource` 协议 round-trip（bus send → 收 skill_resource_loaded / error）？

按父 spec §3 探查 4 步流程 + §4 find signals 跑。

---

## 与现有测试的边界

- `crates/arf-mcp/src/tests/skill_tests.rs`：SkillIndex 单元测试（含 load_resource_file 路径遍历）
- **本 task 不重复**：unit-level path traversal
- **本 task 聚焦**：端到端 — `load_resource_file` 3 类 path + LoadedResource 字段 + 协议 round-trip + error path

---

## 探查步骤（按父 spec §3.1）

### Step 1 — 写最小代码路径（app 层 ≤ 100 行）

`skill_resource_load.rs`，3-4 test cases：

| # | test | 探查 |
|---|---|---|
| 1 | `load_resource_file_three_path_prefixes` | tmpdir + skill + references/api.md + assets/template.txt + tools/gen/main.py + tool.toml → 3 类 path 各自加载 OK |
| 2 | `loaded_resource_attaches_tool_metadata_for_tools_path` | tools/gen/main.py + tool.toml → LoadedResource.description + params_schema 自动附加（**非**空） |
| 3 | `load_resource_file_rejects_path_traversal` | path 含 `..` 或 `/` 开头 → Err 响应（path traversal blocked） |
| 4 | `load_skill_resource_protocol_end_to_end` | bus + McpNode + send load_skill_resource → 收 skill_resource_loaded / skill_resource_error |

### Step 2 — framework 接触点 file:line

```bash
grep -n "pub fn load_resource_file\|pub struct LoadedResource\|fn resolve_safe_path" crates/arf-mcp/src/skill.rs
grep -n '"load_skill_resource"\|skill_resource_loaded' crates/arf-mcp/src/node.rs
```

逐行解释：
- `SkillIndex::load_resource_file` 3 段逻辑：path validation → canonicalize → read
- `resolve_safe_path` 拒绝 `..` / 绝对路径 / 非允许 prefix
- `McpNode::dispatch("load_skill_resource")` 协议层

### Step 3 — framework 真实行为

```bash
cd /home/wangxie/open_deepseek_arf/.worktrees/group2-skill
cargo test -p arf-e2e --test skill_resource_load -- --nocapture --test-threads=1 2>&1 | tee /tmp/skill_resource_load_run.log
```

逐行解释：
- 4 test 应全过（mock + tmpdir）
- 任何 F-lesion 在 audit-probe §D 记录

**Read `/tmp/skill_resource_load_run.log` 后填 Step 4 `framework 行为`**。

### Step 4 — 判定 + 记录

按 §3.3 输出 schema 填 4 个单元的判定。

按 §4 跑 signals：
- A1：`load_resource_file` 单一职责（path validation + read）？
- A2：`LoadedResource` 字段与 3 类 path 行为对称？
- A3：tool metadata 提取（description / params_schema）单一来源？
- A4：path validation 集中？

**C. 输出**：`audit-probe-9.6.4.md`。
