# Session Lifecycle Analysis & Trace Coverage Verification

## Goal

Analyze ARF's full session lifecycle: document expected behavior at each node from code, cross-reference with the tracing system, identify gaps, and write test cases.

## Scope: 5 Lifecycle Stages

1. **ARF INIT start/reload** — CLI entry → agent bootstrap → resource loading → engine wiring
2. **API Key configuration** — detection of missing keys → configuration flow
3. **Session creation** — prompt pipeline, WebSocket handshake, session start criteria
4. **Conversation loop + Hooks** — hook timings during loop, active hooks, trace content
5. **Session end** — end criteria, cleanup actions, memory persistence, trace export

## Analysis Dimensions (per stage)

- **Entry point**: file/function that triggers the stage
- **Behavior chain**: ordered call sequence with expected behavior at each node
- **Trace coverage**: what tracing.py / hook_runner.py records
- **Gap**: expected-but-unrecorded behavior

## Key Files

| Stage | Files |
|-------|-------|
| INIT | cli.py, agent/__init__.py, agent/base.py, resources/manager.py |
| API Key | server/__init__.py, server/routes.py, server/session_manager.py |
| Session | server/ws.py, server/sessions.py, engine/graph.py, agent/__init__.py |
| Loop + Hook | engine/nodes.py, engine/graph.py, engine/dispatcher.py, server/hook_runner.py, hooks/*.py, engine/tracing.py |
| End | server/sessions.py, server/session_manager.py, hooks/session_archiver.py, hooks/memory_extractor.py |

## Deliverables

1. Lifecycle analysis report with trace coverage gaps
2. Test cases for critical lifecycle nodes
