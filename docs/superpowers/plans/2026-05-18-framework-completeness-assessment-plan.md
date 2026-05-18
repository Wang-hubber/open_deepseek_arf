# Framework Completeness Assessment — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute the 4-phase assessment defined in `docs/superpowers/specs/2026-05-18-framework-completeness-assessment-design.md`, producing a completed capability matrix, empirical test results, and a gap analysis report.

**Architecture:** The assessment has 4 sequential phases: (1) Quick scan of all system resources to produce an inventory CSV, (2) Capability matrix fill against the 4-dimension matrix, (3) Empirical testing of all 23 scenarios, (4) Gap analysis and final report. Each phase builds on the previous — Phase 1 data feeds Phase 2, Phase 2 gaps inform Phase 3 test priorities, Phase 3 results feed Phase 4.

**Tech Stack:** Python 3.10+ for scan scripts, pytest for test execution, manual inspection of YAML/Python files for capability verification.

**Key Input Files:**
- Spec: `docs/superpowers/specs/2026-05-18-framework-completeness-assessment-design.md`
- Resources: `src/arf/resources/system/tools/*/`, `src/arf/resources/system/skills/*/`, `src/arf/resources/system/models/*/`
- Source: `src/arf/engine/`, `src/arf/agent/`, `src/arf/server/`, `src/arf/hooks/`, `src/arf/resources/manager.py`
- Tests: `tests/test_dual_agent.py`, `tests/test_audit_fixes.py`
- Config: `default_workspace/arf_agent.yaml`
- Frontend: `frontend/src/`

**Output Files:**
- `docs/superpowers/assessment/resource_inventory.csv` — Phase 1 output
- `docs/superpowers/assessment/capability_matrix.md` — Phase 2 output
- `docs/superpowers/assessment/test_results.md` — Phase 3 output
- `docs/superpowers/assessment/gap_analysis_report.md` — Phase 4 final report

---

### Task 1: Phase 0 — Create assessment directory and scan script

**Files:**
- Create: `docs/superpowers/assessment/`
- Create: `scripts/scan_resources.py`

- [ ] **Step 1: Create assessment output directory**

```bash
mkdir -p docs/superpowers/assessment
```

- [ ] **Step 2: Write the resource scanner script**

`scripts/scan_resources.py`:
```python
"""Scan all system resources and produce a CSV inventory.

Columns: type, name, has_yaml, has_function_py, has_config_default,
         description, source, depends_on, required, configured
"""
import csv
import sys
from pathlib import Path

SRC = Path(__file__).parent.parent / "src" / "arf" / "resources" / "system"

HEADER = [
    "type", "name", "has_yaml", "has_function_py", "has_config_default",
    "description", "source", "depends_on", "required", "configured",
    "notes",
]

def scan_tools(tools_dir: Path) -> list[dict]:
    rows = []
    if not tools_dir.exists():
        return rows
    for sub in sorted(tools_dir.iterdir()):
        if not sub.is_dir():
            continue
        name = sub.name
        has_yaml = (sub / "tool.yaml").exists()
        has_func = (sub / "function.py").exists()
        has_cfg = (sub / "config_default.yaml").exists()
        desc = ""
        depends_on = ""
        required = ""
        if has_yaml:
            import yaml
            with open(sub / "tool.yaml") as f:
                data = yaml.safe_load(f) or {}
            desc = data.get("description", "")
        if has_cfg:
            import yaml
            with open(sub / "config_default.yaml") as f:
                data = yaml.safe_load(f) or {}
            depends_on = str(data.get("depends_on", []))
            required = str(data.get("required", False))
        notes = ""
        if not has_func:
            notes = "CONFIG_STUB: no function.py"
        rows.append({
            "type": "tool", "name": name,
            "has_yaml": str(has_yaml), "has_function_py": str(has_func),
            "has_config_default": str(has_cfg), "description": desc,
            "source": "system", "depends_on": depends_on,
            "required": required, "configured": "True",
            "notes": notes,
        })
    return rows

def scan_skills(skills_dir: Path) -> list[dict]:
    rows = []
    if not skills_dir.exists():
        return rows
    for sub in sorted(skills_dir.iterdir()):
        if not sub.is_dir():
            continue
        name = sub.name
        has_yaml = (sub / "skill.yaml").exists()
        has_cfg = (sub / "config_default.yaml").exists()
        desc = ""
        depends_on = ""
        required = ""
        tools_ref = ""
        if has_yaml:
            import yaml
            with open(sub / "skill.yaml") as f:
                data = yaml.safe_load(f) or {}
            desc = data.get("description", "")
            tools_ref = str(data.get("tools", []))
        if has_cfg:
            import yaml
            with open(sub / "config_default.yaml") as f:
                data = yaml.safe_load(f) or {}
            depends_on = str(data.get("depends_on", []))
            required = str(data.get("required", False))
        notes = ""
        if not has_yaml:
            notes = "CONFIG_STUB: no skill.yaml"
        rows.append({
            "type": "skill", "name": name,
            "has_yaml": str(has_yaml), "has_function_py": "N/A",
            "has_config_default": str(has_cfg), "description": desc,
            "source": "system", "depends_on": depends_on,
            "required": required, "configured": "True",
            "notes": notes,
        })
    return rows

def scan_models(models_dir: Path) -> list[dict]:
    rows = []
    if not models_dir.exists():
        return rows
    for sub in sorted(models_dir.iterdir()):
        if not sub.is_dir():
            continue
        name = sub.name
        has_cfg = (sub / "config_default.yaml").exists()
        desc = ""
        model_type = ""
        depends_on = ""
        required = ""
        context_window = ""
        if has_cfg:
            import yaml
            with open(sub / "config_default.yaml") as f:
                data = yaml.safe_load(f) or {}
            desc = data.get("description", "")
            model_type = data.get("model_type", "")
            depends_on = str(data.get("depends_on", []))
            required = str(data.get("required", False))
            context_window = str(data.get("context_window", ""))
        rows.append({
            "type": "model", "name": name,
            "has_yaml": "N/A", "has_function_py": "N/A",
            "has_config_default": str(has_cfg), "description": desc,
            "source": "system", "depends_on": depends_on,
            "required": required, "configured": "False",
            "notes": f"model_type={model_type} context_window={context_window}",
        })
    return rows

def main():
    all_rows = []
    all_rows.extend(scan_tools(SRC / "tools"))
    all_rows.extend(scan_skills(SRC / "skills"))
    all_rows.extend(scan_models(SRC / "models"))

    out = Path("docs/superpowers/assessment/resource_inventory.csv")
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=HEADER, extrasaction="ignore")
        w.writeheader()
        w.writerows(all_rows)

    # Summary
    tools = [r for r in all_rows if r["type"] == "tool"]
    skills = [r for r in all_rows if r["type"] == "skill"]
    models = [r for r in all_rows if r["type"] == "model"]

    tool_stubs = [t for t in tools if "CONFIG_STUB" in t["notes"]]
    skill_stubs = [s for s in skills if "CONFIG_STUB" in s["notes"]]

    print(f"Tools: {len(tools)} total, {len(tool_stubs)} config-only stubs ({', '.join(t['name'] for t in tool_stubs)})")
    print(f"Skills: {len(skills)} total, {len(skill_stubs)} config-only stubs ({', '.join(s['name'] for s in skill_stubs)})")
    print(f"Models: {len(models)} total")
    print(f"")
    print(f"Inventory written to {out}")

if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run the scanner**

```bash
cd /home/wangxie/open_deepseek_arf && python scripts/scan_resources.py
```

Expected output: counts of tools/skills/models and stub lists. Verify this matches:
- Tools: 18 total, ~6 config-only stubs
- Skills: 14 total, ~1 config-only stub
- Models: 9 total

- [ ] **Step 4: Commit**

```bash
git add scripts/scan_resources.py docs/superpowers/assessment/resource_inventory.csv
git commit -m "feat: resource scanner script and initial inventory CSV
"
```

---

### Task 2: Phase 2a — Capability matrix: Resource CRUD (维度1)

**Files:**
- Create: `docs/superpowers/assessment/capability_matrix.md`
- Read: `src/arf/resources/system/tools/resource_scaffold/`
- Read: `src/arf/resources/system/tools/validate_tool/`
- Read: `src/arf/resources/system/tools/resource_registrar/function.py`
- Read: `src/arf/resources/system/tools/resource_loader/function.py`
- Read: `src/arf/resources/system/tools/manage_hooks/function.py`
- Read: `src/arf/resources/manager.py`

- [ ] **Step 1: Verify resource_scaffold capabilities**

Read `src/arf/resources/system/skills/resource_scaffold/skill.yaml` and check prompt_template for mentions of "model" generation.

Expected: The prompt_template only covers `tool` and `skill` resource types. Look for: `If generating a TOOL... If generating a SKILL...`. Absence of a MODEL section means resource_scaffold does NOT support model scaffold generation.

Record finding: `resource_scaffold` supports tool + skill scaffold, NOT model scaffold.

- [ ] **Step 2: Verify validate_tool capabilities**

Read `src/arf/resources/system/skills/validate_tool/skill.yaml` (note: there is no config_default.yaml — only skill.yaml).

Check: Does it validate all resource types or only tools? Does it verify schema, dependencies, function signatures?

Record findings in matrix.

- [ ] **Step 3: Verify resource_registrar capabilities**

Read `src/arf/resources/system/tools/resource_registrar/function.py` (already read — supports `check`, `list_pending`, `check_deps` actions, but NOT a `register` action that writes files). Read `src/arf/resources/system/tools/resource_registrar/tool.yaml` for declared description.

Record: resource_registrar is QUERY-only (check status, list pending, check deps). It does NOT write files to register a new resource. Registration happens via file_writer + hot-reload.

- [ ] **Step 4: Verify resource_loader capabilities**

Read `src/arf/resources/system/tools/resource_loader/function.py` (already read — supports `activate`, `deactivate`, `list_active` actions, with dependency checking on activate).

Record: resource_loader handles tool activation (add to active set). Does NOT scan workspace for new resources — that's ResourceRegistry.reload_user() via hot-reload.

- [ ] **Step 5: Verify manage_hooks capabilities**

Read `src/arf/resources/system/tools/manage_hooks/function.py`.

Check: Does it support all 4 CRUD operations (add, list, update, remove)? Does it persist to .hooks.json? Is there auth protection?

Record: Verifies CRUD completeness for Hook lifecycle.

- [ ] **Step 6: Fill 维度1 capability matrix**

Open `docs/superpowers/assessment/capability_matrix.md` and write the first section with markdown tables matching the spec's Part 1 维度1 structure. For each row, assign `✅` / `⚠️` / `❌` / `🔧` based on steps 1-5 findings, with a 1-line justification.

Example row format:
```markdown
| Tool Scaffold | resource_scaffold skill | ✅ | Supports tool.yaml + function.py generation |
| Tool Validate | validate_tool skill | ⚠️ | Has skill.yaml, no config_default; scope unclear |
| Model Scaffold | resource_scaffold skill | ❌ | Only supports tool + skill, not model |
```

- [ ] **Step 7: Commit**

```bash
git add docs/superpowers/assessment/capability_matrix.md
git commit -m "feat: capability matrix — dimension 1 (Resource CRUD)
"
```

---

### Task 3: Phase 2b — Capability matrix: Agent runtime autonomy (维度2)

**Files:**
- Modify: `docs/superpowers/assessment/capability_matrix.md`
- Read: `src/arf/engine/classifier.py`
- Read: `src/arf/engine/router.py`
- Read: `src/arf/agent/base.py`
- Read: `src/arf/engine/graph.py`
- Read: `src/arf/server/hook_runner.py`
- Read: `src/arf/resources/manager.py` (check_deps, reload_user)

- [ ] **Step 1: Verify model routing (classifier + router)**

Read `src/arf/engine/classifier.py` and `src/arf/engine/router.py` (already read).

Check:
- Does classifier use a fast model or hardcoded heuristics?
- How many complexity tiers (simple / medium / complex)?
- Does router._requests_model_change() handle multilingual triggers?
- Does UserAgent config set `classifier_enabled: true` and SysAgent set `classifier_enabled: false`?

- [ ] **Step 2: Verify memory system (3-layer)**

Read `src/arf/hooks/memory_extractor.py` and `src/arf/hooks/session_archiver.py`.

Check:
- memory_extract: extracts key info from conversation → writes to long_term.md?
- session_archiver: saves session to `sessions/*.json` on SessionEnd?
- memory_store tool: writes explicit user preferences?
- Is the 3-layer flow (session → long_term → archive) complete?

- [ ] **Step 3: Verify context compaction**

Read relevant sections of `src/arf/engine/graph.py` and `src/arf/agent/base.py` for compaction logic.

Check: At what threshold (75% of context_window?) does compaction trigger? Is there a recovery node that injects summary?

- [ ] **Step 4: Verify progressive disclosure (kernel tools)**

Read `src/arf/agent/user_agent.py` and `src/arf/agent/sys_agent.py` for kernel_tools definition.

Check:
- UserAgent kernel tools = ? (expected: file_reader, file_writer, file_deleter, file_download, memory_store, web_fetch, handoff_to_sys + a few more)
- SysAgent kernel tools = ? (expected: all tools including resource_loader, resource_registrar, model_manager, etc.)
- Does resource_loader correctly handle activate/deactivate with dependency checking?

- [ ] **Step 5: Fill 维度2 capability matrix**

Append to `docs/superpowers/assessment/capability_matrix.md` the second section matching the spec's Part 1 维度2 structure, with status markers and justifications.

- [ ] **Step 6: Commit**

```bash
git add docs/superpowers/assessment/capability_matrix.md
git commit -m "feat: capability matrix — dimension 2 (Runtime Autonomy)
"
```

---

### Task 4: Phase 2c — Capability matrix: User task coverage (维度3)

**Files:**
- Modify: `docs/superpowers/assessment/capability_matrix.md`
- Read: `src/arf/resources/system/tools/web_fetch/function.py`
- Read: `src/arf/resources/system/skills/db_operator/skill.yaml`
- Read: `src/arf/resources/system/skills/rag_operator/config_default.yaml`
- Read: `src/arf/resources/system/tools/file_reader/function.py`

- [ ] **Step 1: Verify Category A — File Operations**

Check each sub-capability from spec:
- file_reader: Read the function.py — verify it handles text, PDF, etc.
- file_writer: Already verified (path restrictions work, tested in test_dual_agent.py)
- file_deleter: Already verified
- file_download: Already verified (tested)

- [ ] **Step 2: Verify Category B — Information Retrieval**

Check:
- web_fetch: Read function.py — does it handle HTTP + HTML→markdown?
- web_search: config_default.yaml only, NO function.py → stub
- rag_operator: config_default.yaml only, NO skill.yaml → stub

- [ ] **Step 3: Verify Category C — Resource Creation**

Check:
- tool_generator: skill.yaml exists → references resource_scaffold + file_reader
- skill_generator: skill.yaml exists → references resource_scaffold + file_reader
- No model_generator skill exists
- resource_scaffold (already verified: no model support)

- [ ] **Step 4: Verify Category D — Data Analysis**

Check:
- db_operator: Read skill.yaml — does it support SQLite? Other databases?
- No built-in chart/graph generation tool
- No Excel/CSV parsing tool (file_reader handles raw text)

- [ ] **Step 5: Verify Category G — Dialogue Enhancement**

Check:
- memory_store: Read function.py — explicit write to long_term.md?
- memory_management skill: Read skill.yaml — does it orchestrate the full memory lifecycle?
- model_configurator: Read skill.yaml — what does it configure?

- [ ] **Step 6: Fill 维度3 capability matrix**

Append to `docs/superpowers/assessment/capability_matrix.md` the third section, with status markers and justifications for all sub-capabilities across categories A/B/C/D/G.

- [ ] **Step 7: Commit**

```bash
git add docs/superpowers/assessment/capability_matrix.md
git commit -m "feat: capability matrix — dimension 3 (User Task Coverage)
"
```

---

### Task 5: Phase 2d — Capability matrix: Cross-cutting concerns (维度4)

**Files:**
- Modify: `docs/superpowers/assessment/capability_matrix.md`
- Read: `src/arf/engine/tracing.py`
- Read: `src/arf/server/database.py`
- Read: `src/arf/server/ws.py`
- Read: `src/arf/server/routes.py`
- Read: `src/arf/engine/dispatcher.py`
- Read: `tests/test_dual_agent.py`, `tests/test_audit_fixes.py`

- [ ] **Step 1: Verify hot-reload**

Check `ResourceRegistry.reload_user()` and how `SessionManager.reset_resource_state()` triggers it.

Key question: Does hot-reload require a restart? Is there a file watcher (watchfiles dependency in pyproject.toml)?

- [ ] **Step 2: Verify observability**

Read `src/arf/server/database.py` — check SQLite schema (6 tables claimed in README). Check if trace export works.

Read `src/arf/engine/tracing.py` — DevTracer is a fallback logger. Where does the production trace path live?

- [ ] **Step 3: Verify streaming + handoff events**

Read `src/arf/engine/dispatcher.py` run_stream() method (already read).

Check: Is `{"type": "handoff", ...}` event emitted? Does the frontend handle it? Read `frontend/src/composables/useChat.ts` for handoff event handling.

- [ ] **Step 4: Verify testing coverage**

Count test functions per module:
```bash
cd /home/wangxie/open_deepseek_arf
python -m pytest tests/ --collect-only -q 2>&1 | tail -5
```

Compare test count against spec's testing strategy (8 items listed in original dual-agent design doc).

- [ ] **Step 5: Fill 维度4 capability matrix**

Append to `docs/superpowers/assessment/capability_matrix.md` the fourth section.

- [ ] **Step 6: Commit**

```bash
git add docs/superpowers/assessment/capability_matrix.md
git commit -m "feat: capability matrix — dimension 4 (Cross-cutting Concerns)
"
```

---

### Task 6: Phase 3a — Empirical testing: Group 1 (Resource Self-Evolution, S1-S6)

**Files:**
- Create: `docs/superpowers/assessment/test_results.md`

- [ ] **Step 1: Write test_results.md header and Group 1 section**

`docs/superpowers/assessment/test_results.md`:
```markdown
# Framework Completeness — Empirical Test Results

Date: 2026-05-18
Assessor: [TBD]
Framework version: main branch, commit [TBD]

Each scenario records:
- **User message**: Exact text used
- **Intent translation**: What UserAgent inferred (correct/incorrect/partial)
- **Handoff?**: Did handoff trigger correctly?
- **Result**: Pass / Fail / Partial
- **Framework modification needed?**: Yes/No, and which file if yes
- **Issues**: Any anomalies

---

## Group 1: Resource Self-Evolution
```

- [ ] **Step 2: Execute S1 — "我想要个能查汇率的"**

**Precondition:** ARF server running with a configured deep_thinking model. Workspace initialized.

**Action:** Send via WebSocket/API: `"我想要个能查汇率的，输入币种和金额就行"`

**Record:**
- UserAgent response: Does it ask clarifying questions (which currencies? what data source?) or directly handoff?
- If handoff: Did SysAgent receive correct `intent`, `required_actions`, `reason`?
- Did SysAgent call resource_scaffold or tool_generator?
- Did the generated files (tool.yaml, function.py) appear in workspace `tools/currency_converter/`?
- Did validate_tool run? Result?
- After creation, did the agent confirm the tool is available?
- **Can the tool be invoked in the same session?**

Record all observations in test_results.md.

- [ ] **Step 3: Execute S2 — "能不能帮我弄个东西，让我每天下班前能自动看看今天干了啥"**

Same recording format as S1.

**Key check:** Does UserAgent recognize this as a "create skill" rather than a "create tool"? The phrase "每天下班前能自动" implies scheduled/recurring — does the agent recognize this goes beyond a single tool call?

- [ ] **Step 4: Execute S3 — "之前那个汇率的东西，能不能加个功能"**

**Precondition:** S1 completed, currency_converter tool exists.

**Action:** `"之前那个汇率的东西，能不能加个功能，让它也支持查过去某一天的汇率"`

**Key check:** Does the agent read the existing tool.yaml, modify it, write back, and confirm hot-reload?

- [ ] **Step 5: Execute S4 — "汇率那个工具我不要了"**

**Precondition:** currency_converter tool exists in workspace.

**Action:** `"汇率那个工具我不要了，帮我删了吧"`

**Key check:** Who deletes? UserAgent (can't — tools/ is restricted) → handoff → SysAgent uses file_deleter.

- [ ] **Step 6: Execute S5 — "我手动在 tools/ 下放了个东西"**

**Precondition:** Manually create a minimal tool directory in workspace `tools/` before the session.

**Action:** `"我手动在 tools/ 下放了个东西，你帮我看看能不能用"`

**Key check:** Does resource_loader find it? Does hot-reload auto-detect it or does agent need to explicitly call resource_loader?

- [ ] **Step 7: Execute S6 — "帮我把 web_fetch 复制一份出来"**

**Action:** `"帮我把 web_fetch 那个系统工具复制一份出来，我想改改它默认的 timeout"`

**Key check:** Is there an `arf clone` mechanism? If not, does SysAgent fall back to file_reader + file_writer? Does the user-space copy correctly override the system version?

- [ ] **Step 8: Commit**

```bash
git add docs/superpowers/assessment/test_results.md
git commit -m "test: empirical results — Group 1 (Resource Self-Evolution) S1-S6
"
```

---

### Task 7: Phase 3b — Empirical testing: Group 2 (Runtime Autonomy, S7-S11)

**Files:**
- Modify: `docs/superpowers/assessment/test_results.md`

- [ ] **Step 1: Execute S7 — Simple question, no model switch**

**Action:** `"今天天气怎么样"` (in a workspace without weather tools)

**Record:** Did the response route through quick_thinking? Check trace for model_type.

- [ ] **Step 2: Execute S8 — Complex question, auto switch to deep_thinking**

**Action:** `"帮我设计一个支持多租户的 SaaS 权限系统，要包含 RBAC 和 ABAC"`

**Record:** Did classifier detect complex? Did model switch to deep_thinking? Check trace.

- [ ] **Step 3: Execute S9 — Explicit model switch**

**Action:** `"用深度思考模式，分析一下这个项目里的 dispatcher.py 的架构"`

**Record:** Did router detect the explicit switch trigger phrase? Did it re-enter classify → deep_thinking?

- [ ] **Step 4: Execute S10 — Long conversation, compaction trigger**

**Action:** Run 30+ turns of progressively longer messages (paste a long document, ask questions about it, repeat).

**Record:** At what turn did compaction trigger? Was the summary accurate? Did the agent continue correctly?

- [ ] **Step 5: Execute S11 — Memory persistence across sessions**

**Action:**
1. Session 1: `"以后所有回复都用中文，我喜欢简洁的风格"`
2. End session, start new session
3. Session 2: `"你还记得我之前跟你说过什么吗"`

**Record:** Did memory_store capture the preference? Does long_term.md contain it? Does the new session load it?

- [ ] **Step 6: Record all results and commit**

```bash
git add docs/superpowers/assessment/test_results.md
git commit -m "test: empirical results — Group 2 (Runtime Autonomy) S7-S11
"
```

---

### Task 8: Phase 3c — Empirical testing: Groups 3, 4, 5 (S12-S23)

**Files:**
- Modify: `docs/superpowers/assessment/test_results.md`

- [ ] **Step 1: Execute S12-S16 (Category coverage)**

Run scenarios:
- S12: `"把这个文件读一下..."` (file_reader test)
- S13: `"帮我搜一下最近关于 LangGraph 的新闻..."` (web_search + web_fetch — note web_search is a stub!)
- S14: `"查一下数据库里最近 7 天的 session 数量..."` (db_operator)
- S15: `"把这三个 Excel 文件读出来..."` (file_reader multi-format)
- S16: `"帮我把这个长对话里关于部署流程的部分提取出来..."` (memory_extract + deep_thinking)

Record results. Pay special attention to S13 (web_search is stub — does the agent handle this gracefully?).

- [ ] **Step 2: Execute S17-S20 (Boundary cases)**

- S17: `"帮我装一个能发微信消息的工具"` (external API — should flag missing deps)
- S18: Artificially break a tool's function.py — test error_handler
- S19: Max turns exhaustion — test messaging
- S20: Missing model dependency — test check_deps() error messaging

- [ ] **Step 3: Execute S21-S23 (Cross-cutting)**

- S21: If frontend is available — run S1 flow through Web UI
- S22: Check TraceView for S1's trace waterfall
- S23: Concurrent sessions (if possible with current setup)

- [ ] **Step 4: Record all results and commit**

```bash
git add docs/superpowers/assessment/test_results.md
git commit -m "test: empirical results — Groups 3/4/5 (S12-S23)
"
```

---

### Task 9: Phase 4 — Gap analysis and final report

**Files:**
- Create: `docs/superpowers/assessment/gap_analysis_report.md`
- Read: `docs/superpowers/assessment/capability_matrix.md`
- Read: `docs/superpowers/assessment/test_results.md`

- [ ] **Step 1: Categorize all gaps**

Open `docs/superpowers/assessment/capability_matrix.md`. For every `⚠️`, `❌`, and `🔧` marker, create a gap entry following the format from the spec Part 3:

```markdown
## [阻断/功能/架构/测试] <简短标题>
**影响范围**：
**当前表现**：
**期望行为**：
**修复方向**：
**优先级**：P0/P1/P2/P3
```

Cross-reference with test_results.md to see which gaps were confirmed empirically (a test scenario that failed due to this gap).

- [ ] **Step 2: Calculate coverage metrics**

```python
# From capability matrix, count:
# P0 (blocking): gaps that prevent self-evolution loop from completing
# P1 (high): gaps that significantly degrade experience
# P2 (medium): gaps that limit scope but don't block core path
# P3 (low): nice-to-have

# From test results:
# Total scenarios: 23
# Passed: N
# Failed: M
# Partial: K
# Pass rate: N / 23
# High-priority pass rate: (S1-S16 + S20) pass count / 17
```

Include these metrics in the report.

- [ ] **Step 3: Write the 80-90% threshold analysis**

Given the spec's target (80-90% of user tasks achievable without framework modification):

```markdown
## Threshold Analysis

**Self-Evolution Loop**: S1-S6 results → [闭环完整 / 有阻断]
**Self-Management Loop**: S7-S11 results → [闭环完整 / 有阻断]
**High-Priority Scenario Pass Rate**: X/17 = Y%
**Conclusion**: [Meets target / Does not meet target / Needs [specific] fixes first]
```

- [ ] **Step 4: Prioritized action items**

List the top-N actions ordered by priority:

```markdown
## Recommended Actions (Priority Order)

1. **[P0]** Fix [gap] — blocks S[N]
2. **[P1]** Implement [gap] — needed for S[N]
...
```

- [ ] **Step 5: Commit final report**

```bash
git add docs/superpowers/assessment/gap_analysis_report.md
git commit -m "docs: gap analysis report and final assessment

"
```

---

### Task 10: Final commit — push all assessment artifacts

- [ ] **Step 1: Verify all output files exist**

```bash
ls -la docs/superpowers/assessment/
```

Expected:
- `resource_inventory.csv`
- `capability_matrix.md`
- `test_results.md`
- `gap_analysis_report.md`

- [ ] **Step 2: Create a summary commit**

```bash
git add docs/superpowers/assessment/
git commit -m "docs: complete framework completeness assessment

Resource inventory, capability matrix, empirical test results,
and gap analysis report per assessment design doc.

"
```
