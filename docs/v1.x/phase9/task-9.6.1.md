# 任务 9.6.1：skill_list_progressive（只列名+描述，不加载 body）

> Phase 9 — 9.6 D Skill 渐进加载 · 第 1 task（依赖 9.5.1）
> 父文档：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.5.1（FsDiscovery 端到端 OK）
> 输出物：`docs/v1.x/phase9/audit-probe-9.6.1.md`
> 探查结论：**不预设**——本 doc 不写任何预期结果

---

## 设计思路

9.5.1 探查了 `FsDiscovery::scan` 扫 `tools/*/tool.toml` + `skills/*/SKILL.md` 的端到端能力。本 task (9.6.1) 聚焦 **L1 元数据** —— framework 是否能让 app **只列 skill 名+描述、不读 body**？这是 progressive disclosure 的第一阶段。

**Framework 现状**（待探查确认）：
- `SkillIndex::scan(root)` 扫 `root/skills/*/SKILL.md`，解析 frontmatter 拿 `name` + `description` + `compatibility`
- `SkillIndex::list_index()` 返回 `Vec<&SkillEntry>`（仅元数据，无 body）
- `DiscoveryBackend::list_skills()` 委托给 SkillIndex
- `McpNode::build_node_info` 把 skills 列表塞进 `NodeInfo.capabilities.skills`（name + description，**不**带 body）

**关键探查问题**（不预设答案）：
1. `FsDiscovery::scan` 扫 SKILL.md 时**只解析 frontmatter**，不读 body 全文？
2. `SkillIndex::list_index()` 返回的 `SkillEntry` 字段是哪些？`source_dir` 是否暴露？
3. `list_skills()` 返回的 N 个 skill 在 bus 端 `NodeInfo.capabilities.skills` 里**不**含 body？
4. 大 N skill（50+）的 scan 性能？
5. SKILL.md frontmatter 缺 `name` 或 `description` 时的行为？

按父 spec §3 探查 4 步流程 + §4 find signals 跑。

---

## 与现有测试的边界

- `crates/arf-mcp/src/tests/discovery_tests.rs`：FsDiscovery 单元测试
- `crates/arf-mcp/src/tests/skill_tests.rs`：SkillIndex 单元测试
- `crates/arf-e2e/tests/mcp_fs_discovery.rs`：9.5.1 端到端（4 tests，已含 `discovery_backend_trait_methods` 部分 skill 探查）
- **本 task 不重复**：unit-level 字段
- **本 task 聚焦**：progressive disclosure 角度——L1 list（name+desc only）+ 字段完整性 + 大 N 性能

---

## 探查步骤（按父 spec §3.1）

### Step 1 — 写最小代码路径（app 层 ≤ 100 行）

`skill_list_progressive.rs`，3-4 test cases：

| # | test | 探查 |
|---|---|---|
| 1 | `skill_list_returns_metadata_only` | tmpdir 写 2 SKILL.md，list_skills() 返回 2 个，验证字段是 name+description+compatibility（**无** body 字符串） |
| 2 | `skill_list_does_not_load_body` | 写 SKILL.md 含 5KB body，list_skills() 之后**不**应触发大 body 加载（验证 L1 list 不带 body） |
| 3 | `skill_list_advertised_via_node_info` | McpNode::local + connect(bus) 后，bus 上 NodeInfo.capabilities.skills 含 name+description（**无** body） |
| 4 | `skill_list_missing_frontmatter_skipped` | SKILL.md 无 frontmatter / 缺 name / 缺 description 时的行为（skip + warn） |

### Step 2 — framework 接触点 file:line

```bash
grep -n "pub fn list_index\|pub struct SkillEntry\|SkillIndex::scan" crates/arf-mcp/src/skill.rs
grep -n "list_skills\|skills: Vec" crates/arf-mcp/src/node.rs
grep -n "parse_frontmatter" crates/arf-mcp/src/skill.rs
```

逐行解释：
- `SkillIndex::scan` 解析 frontmatter（**只** 3 字段）→ entries
- `SkillIndex::list_index()` 返回 `Vec<&SkillEntry>`，**不**包含 body
- `McpNode::build_node_info` 把 skill `{name, description}` 塞进 capabilities

### Step 3 — framework 真实行为

```bash
cd /home/wangxie/open_deepseek_arf/.worktrees/group2-skill
cargo test -p arf-e2e --test skill_list_progressive -- --nocapture --test-threads=1 2>&1 | tee /tmp/skill_list_progressive_run.log
```

逐行解释：
- 4 test 应全过（mock + tmpdir）
- 任何 F-lesion 在 audit-probe §D 记录

**Read `/tmp/skill_list_progressive_run.log` 后填 Step 4 `framework 行为`**。

### Step 4 — 判定 + 记录

按 §3.3 输出 schema 填 4 个单元的判定。

按 §4 跑 signals：
- A1：`SkillIndex::scan` 一个方法扫所有 skill？（atomic 化）
- A2：frontmatter 字段定义与 code 一致？
- A3：skill name 跨 MCP namespace 唯一？
- A4：skill listing 集中？

**C. 输出**：`audit-probe-9.6.1.md`。
