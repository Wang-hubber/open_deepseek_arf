# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install (editable)
pip install -e ".[dev]"
cd frontend && npm install && cd ..

# Run tests
pytest tests/ -v

# Run the app (backend + frontend together)
arf init my_workspace
arf start --workspace my_workspace

# Backend only
arf web --workspace my_workspace

# Stop / reload
arf stop --workspace my_workspace
arf reload --workspace my_workspace

# Frontend dev server only
cd frontend && npm run dev

# Type-check frontend
cd frontend && npx vue-tsc --noEmit

# List registered resources (from workspace root)
arf list [tools|skills|models]
arf validate          # check workspace resource integrity
arf clone tools <name>  # copy a system tool to workspace for customization
```

## Architecture

ARF is a filesystem-native AI agent framework. The core philosophy: **the filesystem bridges the control layer (YAML declarations) and the execution layer (LangGraph engine).**

### Control vs. Execution

- **Control layer** (`src/arf/agent/` + workspace YAMLs): `BaseAgent` reads config from `arf_user_agent.yaml` / `arf_sys_agent.yaml`. Configs are deep-merged: framework defaults are overridden by workspace copies. Every resource (model, tool, skill) is a directory with a YAML file — no decorators, no base classes, no registry server.
- **Execution layer** (`src/arf/engine/`): `GraphEngine` wraps a LangGraph `StateGraph`. It receives injected callables (`call_model`, `execute_tool`, `stream_model`, `run_hook`) and has no knowledge of specific models or tools.

### Agent Graph (`src/arf/engine/`)

The graph structure (`graph.py:59-103`):
```
START → [classify?] → compact → call_model → route:
            ├── execute_tools → compact (loop)
            ├── recovery → [continue?] → compact / respond
            └── respond → END
```

- **Classify node** (`classifier.py`): Two-tier model routing — `quick_thinking` for medium tasks, `deep_thinking` for complex ones. Classification prompt reads the user's query and outputs `medium` or `complex`.
- **Compact node** (`nodes.py`): Sliding-window context compaction when estimated tokens exceed threshold (~255K of 1M window). Summarizes old turns, keeps recent ones.
- **Call model node**: Two variants — `call_model_node` (sync, for `graph.invoke()`) and `call_model_node_stream` (async streaming, for `graph.astream()` with token-level events).
- **Execute tools node**: Calls the injected `execute_tool` callback, which resolves tool functions from `ResourceRegistry` by name.
- **Recovery node**: Handles model call failures with retry logic.

### Dual-Source Resource System (`src/arf/resources/`)

Resources come from two locations, loaded by `ResourceRegistry.manager.py`:

1. **System resources** (`src/arf/resources/system/`) — shipped with the framework, read-only. 18 tools, 18 skills.
2. **User resources** (`<workspace>/tools/`, `skills/`, `models/`) — created at runtime, override system resources by name.

A tool is a directory with `tool.yaml` (name, description, JSON Schema) + `function.py` (an `execute(**kwargs)` function). Skills are `skill.yaml` with a prompt template + tool list. `arp clone` copies a system resource to the workspace for customization.

### Active vs. Discoverable Tools

Only a subset of tools are "active" (sent in each API call's tool definitions). The rest are "discoverable" — the agent loads them on demand via `resource_loader`. Kernel tools (core set, always active) are configured in the agent YAML under `tools.kernel`. This is the **progressive disclosure** mechanism keeping the system prompt ~800 tokens.

### Agent Layer (`src/arf/agent/`)

- `BaseAgent` (`base.py`): Abstract base. Builds the system prompt via a priority-ordered pipeline (`_prompt_pipeline`), constructs OpenAI-format tool schemas from registry, wires everything into a `GraphEngine` via dependency injection.
- `UserAgent` / `SysAgent`: Subclasses that set `_config_filename` and inherit everything else.
- `Project` (`project.py`): Workspace creation, model config copying.

### Server (`src/arf/server/`)

FastAPI app (`__init__.py`) with:
- REST routes (`routes.py`): config status, model registration, chat (sync + SSE streaming), session management, traces, hooks
- WebSocket handler (`ws.py`): streaming chat with session lifecycle hooks
- `SessionManager` (`session_manager.py`): orchestrates workspace, registry, agent construction, session lifecycle
- SQLite trace database (`database.py`): 6 tables for observability
- `TraceCollector` (`trace_collector.py`): lifecycle event collection for waterfall visualization

### Hooks (`src/arf/hooks/`)

Subprocess-based lifecycle hooks (6 events: session_start, session_end, pre_tool_exec, post_tool_exec, pre_model_call, post_model_call). Built-in hooks: `memory_extractor`, `session_archiver`, `system_log`, `title_generator`. Exit codes: 0 = passthrough, 1 = block, 2 = inject message.

### CLI (`src/arf/cli.py`)

argparse-based CLI. Workspace discovery via `_find_workspace_root()` — walks up looking for `arf_agent.yaml`, or searches cwd subdirectories. `arf start` spawns both FastAPI (backend) and Vite (frontend) as subprocesses, managed via PID files in `<workspace>/.arf/`.

### Frontend (`frontend/`)

Vue 3 + TypeScript + Vite + Pinia + ECharts. 8 views, 13 components. Key directories: `views/` (pages), `components/` (shared), `composables/` (reusable logic), `stores/` (Pinia), `locales/` (i18n).

## Project Structure

```
src/arf/
├── cli.py              # CLI entry point
├── agent/              # Agent config, BaseAgent, prompt pipeline
│   ├── base.py         # BaseAgent ABC, YAML config merging, DI wiring
│   ├── sys_agent.py    # SysAgent subclass
│   ├── user_agent.py   # UserAgent subclass
│   └── project.py      # Workspace creation
├── engine/             # LangGraph-based execution engine
│   ├── graph.py        # GraphEngine, StateGraph builds
│   ├── nodes.py        # Graph nodes (classify, compact, call_model, execute_tools, respond, recovery)
│   ├── state.py        # AgentState TypedDict with custom reducers
│   ├── router.py       # Conditional edge logic
│   ├── classifier.py   # Model tier classifier
│   ├── dispatcher.py   # Tool dispatch
│   └── tracing.py      # DevTracer for observability
├── resources/          # Resource registry + system resources
│   ├── manager.py      # ResourceRegistry (dual-source loading)
│   ├── model_adapter.py # OpenAI-compatible API adapter
│   └── system/         # Built-in tools, skills, and models
│       ├── tools/      # 18 system tools
│       ├── skills/     # 18 system skills
│       └── models/     # Default model configs (deep_thinking, quick_thinking, etc.)
├── server/             # FastAPI web server
│   ├── __init__.py     # ARFServer, CORS, static files, lifespan
│   ├── routes.py       # REST API endpoints
│   ├── ws.py           # WebSocket handler
│   ├── session_manager.py # Session lifecycle orchestration
│   ├── database.py     # SQLite trace database
│   ├── trace_collector.py # Lifecycle event collection
│   ├── hook_runner.py  # Hook execution engine
│   ├── sessions.py     # Archive management
│   └── fast_model.py   # Lightweight model config validation
└── hooks/              # Built-in lifecycle hooks
    ├── memory_extractor.py
    ├── session_archiver.py
    ├── system_log.py
    └── title_generator.py
```

## Key Patterns

- **Dependency injection**: `GraphEngine` receives callables (`call_model`, `execute_tool`, `run_hook`) via its constructor. Nodes access them from `config["configurable"]`. This keeps the engine agnostic to specific models/tools.
- **Config merging**: `_deep_merge(base, override)` — lists are replaced, not merged. Framework defaults → workspace overrides.
- **YAML-driven agents**: No programmatic agent construction. Everything comes from YAML config. Adding a new tool means creating a directory with two files — no code in the framework changes.
- **Path sandboxing**: File tools resolve all paths within the workspace root and block traversal (`..` resolved and checked). System resources (`@sys/` prefix) are read-only.
- **Self-evolution**: The agent can scaffold new tools/skills at runtime via `resource_scaffold` + `file_writer`. Auto-activation on file write: if `_execute_tool` writes under `tools/`, `skills/`, or `models/`, the registry reloads and the new tool is auto-activated.
