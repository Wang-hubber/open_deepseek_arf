---
name: arf-e2e-testing
description: Use when running E2E framework tests against the ARF app backend — subagent-driven dynamic conversation through real API calls, trace analysis every 5-10 rounds, focused on finding framework bugs in compaction/routing/memory/tools/A2A
---

# ARF E2E Testing

## Overview

Subagent-driven E2E testing of the ARF framework through the app backend API. Real API keys, real models, real conversation. The orchestrating agent dispatches subagents to dynamically generate user messages and analyze trace data — no pre-scripted dialogs.

## Pre-Test Setup

1. **Reduce context windows** in `models/deep.yaml` and `models/quick.yaml` — use 64K/32K to trigger compaction earlier
2. **Disable human_loop** — comment out the `human_loop` block in `agent.yaml` so tools execute without waiting for approval
3. **Disable guardrails `ask` list** — move all tools to `allow` in `agent.yaml` so tool calls aren't blocked
4. **Set sandbox checks** in `agent.yaml` → `advanced.sandbox.checks` — default only `workspace_containment: true`
5. **Start server** with `PYTHONPATH=. .venv/bin/python app/arf_default_assistant/server.py`
6. **Clean ONLY e2e state** — `rm -f /tmp/e2e_state.json` (never clean `memory/`, `workspaces/`, `traces/` without user approval)

## Execution Loop

```
for each round:
    1. python e2e_runner.py state → get persona + recent context
    2. Dispatch subagent: "You are [persona]. Generate next user message."
    3. python e2e_runner.py send "<message>" <persona_name>
    4. If trace_check_needed or error:
       → python e2e_runner.py trace
       → python e2e_runner.py verify-memory
       → Dispatch subagent: analyze trace + memory
       → Record findings
```

## Persona Strategy

Default: cycle through 4 personas in shared session. For deep testing, stay on one persona to push token usage and trigger compaction.

| Persona | Focus | Pattern |
|---------|-------|---------|
| coding | tools, sandbox, guardrails, **A2A handoff** | File ops, code gen, **trigger handoff via tools/ write** |
| writer | compaction, memory, routing | Long doc reading/writing, cross-reference |
| novelist | compaction, memory, checkpoint, **routing fallback** | Ultra-long context, **seed facts for recall test**, **503 fallback test** |
| rpg | memory, checkpoint, undo | State tracking, undo, complex prompt |

## Trace Analysis Checklist

- **Compaction**: triggered at right threshold? summary generated? coherence after compaction?
- **Routing**: model classification correct? fallback works?
- **Memory**: store/retrieve events? retrieval relevant?
- **Tool Execution**: call_start/end complete? guard_pass/block correct? errors have context?
- **A2A Handoff**: handoff triggered? context transferred? sys_agent returned?
- **General**: usage data complete? unexpected 500? SSE done event present?

## Memory Verification (File-System Check)

Trace events alone can miss silent failures — also check memory files on disk:

```bash
python e2e_runner.py verify-memory
```

- **memory.md exists?** non-empty? not just `NO_NEW_MEMORY`?
- **Has `## Category` headings** with `- ` list items
- **At least 3 categories** present (User Identity, Preferences, Project Structure...)
- **File size growing** between checks → extraction is accumulating
- **No truncation** (`<!-- WARNING: memory truncated` flag)
- **memory.json** (if exists): valid JSON, entries have required fields (id, content, category, timestamp)

## Memory Recall Test

Embed seed facts → wait for extraction → test recall:

1. Novelist persona naturally mentions identity/preferences (name, tools, habits)
2. After 10+ rounds, `verify-memory` confirms facts were extracted
3. Ask recall question: "你还记得我叫什么吗？我写作有什么习惯？我用什么工具？"
4. Verify agent response references stored facts: 陈远, 先画关系图, Obsidian, 至少2000字

## A2A Handoff Verification

Coding persona triggers handoff by requesting `tools/` path writes:

1. After md2html CLI is built, ask: "帮我在 tools/ 下创建一个 md2pdf 工具"
2. Agent attempts `file_writer` → rejected with "Call handoff" prompt
3. Agent calls `handoff` tool → `agent_switch` event → sys_agent executes
4. Verify: `model_call_start` with sys_agent system prompt, `file_writer` succeeds
5. sys_agent calls `handoff` to return → `agent_switch` back to main

Trace evidence: `agent_switch` events, `tool_call_start/end` for handoff x2

## Routing Fallback Verification

Simulate deep model returning 503, verify fallback to quick:

1. `python e2e_runner.py mock-deep-down` — starts mock 503 server, points deep at it
2. Novelist sends complex creative task → router classifies as "complex" → deep
3. Deep call fails with 503 (after ModelAdapter retries exhaust) → fallback to quick
4. `python e2e_runner.py mock-deep-restore` — restore config

Trace evidence: `model_call_start` (deep) → `model_call_start` (quick, fallback_from=deep) → `model_call_end` (quick)

## Issue Recording

Format: `module → severity → description → trace evidence`

```bash
python e2e_runner.py issue "<description>" --module <module> --severity high|medium|low
python e2e_runner.py report  # final report
```

## Key Rules

- **Never clean app runtime** (memory/, workspaces/, traces/) without user's explicit request
- **Observe self-healing** — when errors occur, let the framework attempt recovery before intervening
- **Use real API keys** — `.env` file in app directory
- **Post-test**: revert configs with `python e2e_runner.py cleanup`
