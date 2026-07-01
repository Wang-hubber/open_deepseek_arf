# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Removed
- `docs/architecture/` directory — was V0.x-only material (10-checkpoint Hook lifecycle, plugin system, PrimitiveAgent/AgentHarness, two-mode A2A, Eval benchmark). V1.x redesigned all of these; architecture overview now lives in `README.md` + `docs/dev/`.

## [1.0.0-alpha.0] - 2026-07-01

### Added
- Rust workspace: arf-core, arf-bus, arf-state, arf-model-adapter, arf-mcp, arf-engine, arf-agent, arf-pool, arf-e2e
- Python binding `py-arf` (PyO3 + maturin, zero runtime dependencies)
- Engine API: ReAct loop, Park/Resume, Checkpoint, Pool, Routing
- MCP support: Local + Remote + Script tools
- User API reference (`docs/api/`)
- Developer documentation (`docs/dev/`)
- Architecture documentation (`docs/architecture/`)

### Changed
- **BREAKING**: Complete rewrite from Python framework to Rust + PyO3
- Restructured repository directory layout (`examples/{rust,python}/`)
- Renamed `docs/v1.x/` to `docs/dev/`
- Consolidated `pyproject.toml` at root, built on maturin
- Version bumped to 1.0.0-alpha.0

### Removed
- Legacy Python framework (`arf/` directory, 179 files)
- Legacy Python tests for the old framework
- Legacy dependencies: fastapi, uvicorn, langgraph, websockets, jinja2, python-multipart, openai, pyyaml, watchfiles, httpx, pydantic
- Old benchmark JSON files (`benchmarks/`)