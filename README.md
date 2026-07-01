# ARF — AI Resources & Runtime Framework

> **Engine drives ReAct loop. Model makes decisions. Agent holds state.**

ARF provides the full infrastructure for running agents — resource discovery, memory management, model dispatch, sandboxed tool execution, observability, and A2A communication. The framework defines interfaces via Protocols and assembles capabilities through dependency injection.

## Repository Layout

```
crates/       # Rust workspace (core framework)
py-arf/       # Python binding (PyO3 + maturin)
examples/
  rust/       # Cargo workspace members (domain_controller, recovery)
  python/     # Python scripts demonstrating py-arf usage
tests/        # Cross-language integration test entry point
docs/
  api/        # User API reference (PyTorch/LangGraph style)
  dev/        # Developer workflow, phase designs
  architecture/  # High-level design (session/round/turn, hooks, eval)
```

## Quickstart

### Rust

```bash
cargo test --workspace
```

### Python

```bash
pip install -e ".[dev]"
pytest tests/ -q
```

## Examples

See [examples/](examples/) for runnable demos — both Rust (`cargo run --bin ...`) and Python (`python examples/python/ex01_*.py`).

## Documentation

- [docs/api/](docs/api/) — User-facing API reference
- [docs/dev/](docs/dev/) — Developer workflow, phase designs
- [docs/architecture/](docs/architecture/) — Architectural concepts

## License

MIT — see [LICENSE](LICENSE).