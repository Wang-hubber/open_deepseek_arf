# ARF py-arf Examples

Each script is self-contained. Run from the project root with the venv activated:

```bash
.venv/bin/python py-arf/python/arf/examples/ex01_minimal_mock.py
```

## Index

| Script | What it demonstrates | API key | Mock fallback |
|--------|---------------------|---------|---------------|
| `ex01_minimal_mock.py` | Minimal Engine run with mock model | No | — |
| `ex02_multi_round.py` | State accumulation across rounds | No | — |
| `ex03_tool_call.py` | ReAct tool call with mock MCP | No | — |
| `ex04_max_turns.py` | max_turns truncation | No | — |
| `ex05_timeout.py` | asyncio.wait_for timeout pattern | No | silent mock |
| `ex06_state_serialize.py` | EngineState JSON serialization | No | — |
| `ex08_phase6_overview.py` | Full phase 1-6 stack: Bus + Model + MCP + Engine + Checkpoint + Route + Pool | Yes | — |