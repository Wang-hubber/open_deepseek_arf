# 任务 9.6.3：skill_tool_progressive_register（skill body → tool）

> Phase 9 — 9.6 D Skill 渐进加载 · 第 3 task（依赖 9.6.2）
> 父文档：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.6.2（use_skill 协议端到端 OK）
> 输出物：`docs/v1.x/phase9/audit-probe-9.6.3.md`
> 探查结论：**不预设**——本 doc 不写任何预期结果

---

## 设计思路

9.6.2 探查了 use_skill 协议加载 body+resources（L2）。本 task (9.6.3) 聚焦 **skill 内部 tool 的执行** —— framework 是否能让 app 通过 skill 内部定义的 tool（如 `tools/gen/main.py`）被调用并执行？

**Framework 现状**（待探查确认）：
- `SkillIndex::run_tool(skill_name, tool_name, params)` —— 加载 `tools/{tool_name}/tool.toml` + 执行 `main.py`（skill.rs:188-217）
  - 缺 tool.toml 时 `infer_tool_defaults`（skill.rs:275-298）自动推断（main.py / main.sh / main）
  - `config.name = "{skill_name}/{tool_name}"`（skill.rs:209）—— skill-scoped identity
- `DiscoveryBackend::run_skill_tool(skill, tool, params)` 委托 SkillIndex（discovery.rs:168-170）
- `McpNode::dispatch("run_skill_script")` 协议入口（node.rs:243-259）
  - payload: `{skill_name, tool_name, call_id, params, session_id}`
  - response: `skill_script_result` 含 `{status: "success"/"error", result, error}`

**关键探查问题**（不预设答案）：
1. `run_skill_tool` 端到端 work？`main.py` 实际执行？
2. 缺 `tool.toml` 时 `infer_tool_defaults` 自动推断（python / bash）是否 work？
3. skill tool 名称 scoped（`{skill_name}/{tool_name}`）？
4. `run_skill_script` 协议端到端（bus send → 收 skill_script_result）？
5. skill tool 与 root `tools/*/tool.toml` 工具**不**冲突？

按父 spec §3 探查 4 步流程 + §4 find signals 跑。

---

## 与现有测试的边界

- `crates/arf-mcp/src/tests/skill_tests.rs`：SkillIndex 单元测试（含 run_tool）
- **本 task 不重复**：unit-level execution test
- **本 task 聚焦**：端到端 — `run_skill_script` 协议 + skill tool scoped 命名 + auto-infer 缺 tool.toml

---

## 探查步骤（按父 spec §3.1）

### Step 1 — 写最小代码路径（app 层 ≤ 100 行）

`skill_tool_progressive.rs`，3-4 test cases：

| # | test | 探查 |
|---|---|---|
| 1 | `skill_tool_execute_via_run_skill_tool` | tmpdir + skill/tools/gen/ + main.py + SkillIndex::run_tool("gen", ...) → 实际 python 执行返 JSON |
| 2 | `skill_tool_auto_infer_without_tool_toml` | skill/tools/gen/main.py **不**写 tool.toml → `infer_tool_defaults` 自动推断 runtime=python + entrypoint=main.py |
| 3 | `skill_tool_scoped_name` | run_tool 返回 config.name = "{skill_name}/{tool_name}" |
| 4 | `run_skill_script_protocol_end_to_end` | bus + McpNode + send run_skill_script → 收 skill_script_result（含 status + result / error） |

### Step 2 — framework 接触点 file:line

```bash
grep -n "pub async fn run_tool\|infer_tool_defaults" crates/arf-mcp/src/skill.rs
grep -n '"run_skill_script"\|skill_script_result' crates/arf-mcp/src/node.rs
```

逐行解释：
- `SkillIndex::run_tool` 加载 tool.toml 或 infer 默认 + ScriptTool::new + execute
- `McpNode::dispatch("run_skill_script")` 协议层包装

### Step 3 — framework 真实行为

```bash
cd /home/wangxie/open_deepseek_arf/.worktrees/group2-skill
cargo test -p arf-e2e --test skill_tool_progressive -- --nocapture --test-threads=1 2>&1 | tee /tmp/skill_tool_progressive_run.log
```

逐行解释：
- 4 test 应全过（mock + tmpdir）
- 任何 F-lesion 在 audit-probe §D 记录

**Read `/tmp/skill_tool_progressive_run.log` 后填 Step 4 `framework 行为`**。

### Step 4 — 判定 + 记录

按 §3.3 输出 schema 填 4 个单元的判定。

按 §4 跑 signals：
- A1：`run_skill_script` 协议单一职责？
- A2：skill tool 与 root tool 命名空间独立？
- A3：scoped name 格式 `{skill_name}/{tool_name}` 跨 crate 一致？
- A4：tool execution 路径集中？

**C. 输出**：`audit-probe-9.6.3.md`。
