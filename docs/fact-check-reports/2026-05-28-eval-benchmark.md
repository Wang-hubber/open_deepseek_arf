# Fact-Check Report: Eval/Benchmark (2026-05-28)

**Source**: `docs/eval-benchmark.md` vs `arf/evaluation/` implementation

**Tests**: 76 total in `tests/fact_check/test_eval_benchmark.py`

| Result | Count |
|--------|-------|
| PASS   | 74    |
| FAIL   | 2     |
| Findings | 4   |

---

## PASS — 73 tests, no finding

All import paths, class/method signatures, constructor parameters, data model fields, metric behaviors, serialization round-trips, trace adapter conversions, and comparator logic match the documentation.

### Verified domains

| Domain | Tests | Key coverage |
|--------|-------|-------------|
| Top-level imports | 12 | All `arf.evaluation` exports, `FileTraceStore`, `events_to_trace` |
| File existence | 2 | All 8 module files + protocol file exist |
| `EvalCase` model | 4 | 5 fields, types, construction |
| `EvalBenchmark` model | 5 | 4 fields, to_json/from_json, optional handling |
| `EvalReport` model | 3 | 6 fields, JSON round-trip |
| `EvalSummary` model | 2 | 9 fields, zero defaults |
| `EvalDiff` model | 2 | `summary_diff`, `regressions`, `improvements` |
| `BenchmarkBuilder` | 5 | Constructor, build signature, session→cases, error handling |
| `EvalRunner` | 5 | Constructor, async signature, success/failure paths |
| `EvalComparator` | 4 | compare() signature, regression detection, benchmark name guard |
| `SuccessRateMetric` | 3 | No errors→1.0, errors→0.0, empty trace |
| `ToolAccuracyMetric` | 4 | Ordered matching, partial, no tools→1, no actual→0 |
| `TurnEfficiencyMetric` | 2 | Turn count, empty trace |
| `OutputContainsMetric` | 4 | All/partial matching, no keywords→1, case-insensitive |
| `events_to_trace` | 3 | Turn grouping, duration accumulation, error mapping |
| Module exports | 1 | `__all__` matches all documented symbols |

---

## FAIL — 2 tests (doc inaccuracies)

### Finding 1: `benchmarks/` directory does not exist

- **Doc claim** (Section 6): `benchmarks/` is a storage path for user-editable benchmark JSON.
- **Reality**: No `benchmarks/` directory exists anywhere in the project (checked at root, `app/`, and subdirectories).
- **Impact**: The API example `benchmark.to_json("benchmarks/file_ops_v1.json")` would fail with `FileNotFoundError` (parent dir missing). The directory is never auto-created by the framework.
- **Suggested fix**: Either create the directory and add a `.gitkeep`, or update the doc to note the user must create it, or have `EvalBenchmark.to_json()` auto-create parent directories.

### Finding 2: `reports/` directory does not exist

- **Doc claim** (Section 6): `reports/` is a storage path for runner output JSON.
- **Reality**: No `reports/` directory exists anywhere in the project.
- **Impact**: Same as Finding 1 — `report.to_json("reports/file_ops_v1_baseline.json")` would fail at runtime.
- **Suggested fix**: Same as Finding 1.

---

## Additional findings (tests pass but doc is inaccurate)

### Finding 3: `memory/traces/` path mismatch

- **Doc claim** (Section 6): `memory/traces/` is the FileTraceStore output directory.
- **Reality**: `FileTraceStore.__init__` defaults to `dir="./memory/sessions"`, not `"./memory/traces"`.
- **Doc Section 2 diagram** also shows `memory/traces/{session}.json`, which is inconsistent with code.
- **Suggested fix**: Update the doc to `memory/sessions/` to match the code, or change the code default to `./memory/traces` for consistency.

### Finding 4: Protocol-implementation divergence in `EvalReport`

- **Doc claim** (Section 4): EvalReport has `run_id`, `benchmark_name`, `agent_config_hash`, `timestamp`, `summary`, `per_case`.
- **Reality**: The protocol (`arf/core/protocols/evaluation.py`) defines `EvalReport` with `dataset_name` and `comparison` fields that the implementation (`arf/evaluation/models.py`) does not have. The implementation uses `benchmark_name` instead of `dataset_name`.
- **Impact**: Minor — the protocol and implementation are structurally out of sync. The implementations are what the doc describes, which is correct for the user-facing API, but the protocol layer is stale.
- **Suggested fix**: Either align the protocol to match the implementation, or update the protocol to reflect planned evolution and add a migration note.

---

## Summary

The eval/benchmark domain is well-documented. All four metrics, the runner, comparator, builder, trace adapter, and data models are accurately described in the doc. The only issues are:

1. **Missing directories** -- `benchmarks/` and `reports/` are referenced but don't exist (2 tests fail).
2. **Path inconsistency** -- `memory/traces/` in doc vs `./memory/sessions` in code.
3. **Protocol drift** -- `EvalReport` protocol has `dataset_name`/`comparison` fields not in implementation.

Severity: Low. The core API, models, and metrics are all accurately documented. The issues are confined to storage paths and the protocol layer.
