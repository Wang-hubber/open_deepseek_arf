# Architecture Overview

This document is the conceptual model for the ARF framework. It is meant for both users (to understand what they're configuring) and framework developers (to understand the boundaries they're working within).

## Architectural Layers

| Layer | Path | Responsibility |
|-------|------|----------------|
| **Framework core** | `crates/` | Execution engine, resource system, agent assembly, infrastructure, Protocol abstractions |
| **Python binding** | `py-arf/` | PyO3 exposure of Rust APIs |
| **Examples** | `examples/{rust,python}/` | Runnable demos by language |
| **Tests** | `tests/`, `crates/*/tests/`, `py-arf/tests/` | Unit + integration + E2E |

**Core principle**: the framework provides mechanisms (how to do things); applications choose what to do through configuration + instantiation. Dependency injection takes precedence over hard-coding concrete implementations.

## Repository Layout

```
crates/
  arf-core/              # Protocol definitions + ModelAdapter + config models
  arf-bus/               # Message bus (ReAct + A2A)
  arf-state/             # messages + tasks lifecycle, two-way locks
  arf-model-adapter/     # OpenAI/Anthropic/DeepSeek/MiniMax adapters
  arf-mcp/               # MCP tool bridge (Local + Remote + Script)
  arf-engine/            # ReAct main loop + HandoffManager
  arf-agent/             # DI assembly of all Protocol implementations
  arf-pool/              # Node pooling
  arf-e2e/               # Rust end-to-end tests

py-arf/
  src/                   # PyO3 binding source
  python/arf/            # Installed Python package (arf._arf.so + engine/)
  tests/                 # Python binding tests

examples/
  rust/{domain_controller,recovery}/   # Cargo workspace members
  python/                              # py-arf usage demos

docs/
  api/                   # User-facing API reference
  dev/                   # Developer workflow + phase designs
  architecture/          # This document + per-concept deep-dives
```

## Session Data Layout

```
data/
  {session_id}/
    traces/              # TracePlugin: data/{sid}/traces/{sid}.jsonl
    state/               # FileStateStore: data/{sid}/state/{sid}.json
    tool_outputs/        # CompactionPlugin externalization: turn_{n}_{name}_{hash}.txt
memory/                  # Cross-session, L1 resident memory
snapshots/               # Cross-session, env config hash → xml
```

- `FileStateStore`, `TracePlugin`, `CompactionPlugin` all accept `data_dir` (default `"./data"`); they construct session-scoped paths internally.
- `AppContext.state_dir` / `trace_dir` return `data_dir` (session subdirectory managed by each component).

## Hook Lifecycle (10 checkpoints)

Each checkpoint supports both `blocking` and `side` modes; plugins declare what they need; the engine imposes no hard restrictions.

| Checkpoint | When fired |
|------------|-----------|
| `session_start` | Session begins / resume |
| `before_round` | Each `chat()` entry — park happens here |
| `before_model` | Before model call |
| `after_model` | After model response |
| `before_tools` | Before tool execution |
| `after_tools` | After tool execution, before commit (externalization here) |
| `after_round` | Round ends |
| `before_break` | Before engine break (`task_complete` validation here) |
| `on_error` | On exception |
| `session_end` | Session ends, cleanup |

## Tool Output Externalization

`CompactionPlugin` subscribes to the `tool_output` hook. For non-read tool results above a threshold (default 500 chars), it externalizes the full output:

- **Excluded**: `read_file`, `search_content`, `search_files`, `directory_tree` (prevents truncate-read loops)
- **Persisted**: full result at `data/{sid}/tool_outputs/turn_{n}_{name}_{hash}.txt`
- **In context**: model sees `[Tool output externalized — {size} chars, full at {path}]\n{preview_head}...\n...{preview_tail}`
- **Config**: defaults in `compaction/plugin.yaml`, override via `agent.yaml → plugins_config.compaction.tool_output`

## Eval Benchmark

- `EvalCase` retains only contract fields: `id`, `input`, `session_id`, `expected_tools`, `expected_tool_calls` (with `result_preview`), `expected_output_contains`, `max_turns`.
- `golden_trajectory` and `original_output` removed — LLM metrics (`OutputQualityMetric`, `TrajectorySimilarityMetric`) read traces from `data/{sid}/traces/{sid}.jsonl` on demand.
- `OutputContainsMetric`: rule-based keyword match — checks whether the actual output contains all keywords in `expected_output_contains`.