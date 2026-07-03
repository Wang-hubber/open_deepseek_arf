# 任务 9.6.5：Skill 全套联合（4 项联动）

> Phase 9 — 9.6 D Skill 渐进加载 · 第 5 task（依赖 9.6.1）
> 父文档：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.6.1（L1 list 端到端 OK）
> 输出物：`docs/v1.x/phase9/audit-probe-9.6.5.md`
> 探查结论：**不预设**——本 doc 不写任何预期结果

---

## 设计思路

9.6.1-9.6.4 探查了 skill L1 list（9.6.1）+ L2 use_skill 协议（9.6.2）+ skill 内部 tool execution（9.6.3）+ resource 单文件加载（9.6.4）。本 task (9.6.5) 聚焦 **4 项联动** —— framework 是否能让 app 端到端走完"list → load on demand → run skill tool → load resource" 完整 progressive 链？

**Framework 现状**（待探查确认）：
- 4 能力分别由 4 个独立端点 / 协议承担：
  - L1 list: `FsDiscovery::list_skills()` + `McpNode::build_node_info` advertised
  - L2 use_skill: `bus.send("use_skill", ...)` 协议
  - L3 run skill tool: `bus.send("run_skill_script", ...)` 协议
  - L4 load resource: `bus.send("load_skill_resource", ...)` 协议
- 完整 progressive 链：scan → L1 list (no body) → use_skill (body + resources list) → run_skill_script (实际执行) → load_skill_resource (单文件)

**关键探查问题**（不预设答案）：
1. 4 步链端到端 work？（同一 McpNode 上一致性）
2. L1 阶段 vs L2 阶段 body 长度一致？
3. resources 清单（list_skills 阶段 vs use_skill 阶段）一致？
4. 4 协议并发（同一 requester 注册 4 个 filter）能 work？
5. advertised skills（scan 阶段）vs loaded skills（use_skill 阶段）名字 / 描述一致？

按父 spec §3 探查 4 步流程 + §4 find signals 跑。

---

## 与现有测试的边界

- 9.6.1-9.6.4 已分别测 4 能力
- **本 task 不重复**：单能力单元测试
- **本 task 聚焦**：**联动** —— 同一 McpNode + 同一 requester 4 步链端到端，验证 framework 跨能力状态一致

---

## 探查步骤（按父 spec §3.1）

### Step 1 — 写最小代码路径（app 层 ≤ 100 行）

`skill_full_progressive.rs`，3-4 test cases：

| # | test | 探查 |
|---|---|---|
| 1 | `full_progressive_chain_end_to_end` | tmpdir 写完整 skill（SKILL.md + tools + references + assets）+ McpNode + requester 走完 4 步：list → use_skill → run_skill_script → load_skill_resource |
| 2 | `progressive_state_consistency` | 同一 skill 在 4 步中元数据一致（name / description / resources 清单） |
| 3 | `concurrent_four_protocols_on_same_mcp` | 同一 McpNode 4 协议并发 round-trip（4 个 send） |
| 4 | `large_skill_full_chain` | 大 body（5KB）+ 多 resources 完整链端到端（验 scalability） |

### Step 2 — framework 接触点 file:line

```bash
grep -n "discovery.list_skills\|load_skill_body\|load_skill_resources\|run_skill_tool\|load_resource_file" crates/arf-mcp/src/discovery.rs
grep -n '"use_skill"\|"run_skill_script"\|"load_skill_resource"' crates/arf-mcp/src/node.rs
```

逐行解释：
- 4 方法都在 `DiscoveryBackend` trait
- 4 协议在 `McpNode::dispatch` 4 个 match 分支

### Step 3 — framework 真实行为

```bash
cd /home/wangxie/open_deepseek_arf/.worktrees/group2-skill
cargo test -p arf-e2e --test skill_full_progressive -- --nocapture --test-threads=1 2>&1 | tee /tmp/skill_full_progressive_run.log
```

逐行解释：
- 4 test 应全过
- 任何 F-lesion 在 audit-probe §D 记录

**Read `/tmp/skill_full_progressive_run.log` 后填 Step 4 `framework 行为`**。

### Step 4 — 判定 + 记录

按 §3.3 输出 schema 填 4 个单元的判定。

按 §4 跑 signals：
- A1：4 步链每步单一职责（联动 = 顺序组合）？
- A2：4 协议 schema 独立但内部共享 SkillIndex 状态？
- A3：SkillEntry 状态在 4 步中一致？
- A4：filter / validate 跨协议对称？

**C. 输出**：`audit-probe-9.6.5.md`。
