# Python Examples

Demos for the `py-arf` Python binding. Each example is a standalone script that exercises one Engine pattern.

## Requirements

```bash
pip install -e ".[dev]"   # from repo root
. "$HOME/.cargo/env" && maturin develop --release   # build the binding
```

For examples that hit a live model API (`ex08_phase6_overview.py`, `phase1_bus_hello.py` against real providers), set `OPENAI_API_KEY` (or equivalent provider env var) before running.

## Examples

| File | What it shows |
|------|--------------|
| `ex01_minimal_mock.py` | Minimal Engine boot with a stub model |
| `ex02_multi_round.py` | Multi-round conversation |
| `ex03_tool_call.py` | Tool registration + execution |
| `ex04_max_turns.py` | `max_turns` cap behavior |
| `ex05_timeout.py` | Per-call timeout |
| `ex06_state_serialize.py` | State persistence round-trip |
| `ex08_phase6_overview.py` | Full Phase 1-6 surface demo |
| `phase0_hello.py` | Phase 0 scaffolding sanity |
| `phase1_bus_hello.py` | Phase 1 bus basics |
| `phase4_model_adapter.py` | Phase 4 model adapter usage |
| `phase6_flat/` | Multi-file Phase 6 example app |

## Run

```bash
python examples/python/ex01_minimal_mock.py
```

## See also

- [docs/api/](../../docs/api/) — User API reference
- [docs/architecture/](../../docs/architecture/) — Architectural concepts