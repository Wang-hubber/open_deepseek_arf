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
       → Dispatch subagent: analyze trace for issues
       → Record findings
```

## Persona Strategy

Default: cycle through 4 personas in shared session. For deep testing, stay on one persona to push token usage and trigger compaction.

| Persona | Focus | Pattern |
|---------|-------|---------|
| coding | tools, sandbox, guardrails, A2A | File ops, code generation, handoff |
| writer | compaction, memory, routing | Long doc reading/writing, cross-reference |
| novelist | compaction, memory, checkpoint | Ultra-long context, recall testing |
| rpg | memory, checkpoint, undo | State tracking, undo, complex prompt |

## Trace Analysis Checklist

- **Compaction**: triggered at right threshold? summary generated? coherence after compaction?
- **Routing**: model classification correct? fallback works?
- **Memory**: store/retrieve events? retrieval relevant?
- **Tool Execution**: call_start/end complete? guard_pass/block correct? errors have context?
- **A2A Handoff**: handoff triggered? context transferred? sys_agent returned?
- **General**: usage data complete? unexpected 500? SSE done event present?

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
