# ARF Fact-Check Report — 2026-05-28 — A2A Communication

**Domain**: A2A Communication (`docs/a2a-communication.md` vs `arf/communication/` + `arf/engine/handoff.py`)
**Methodology**: TDD-style — 32 tests. 31 pass, 1 xfail.

---

## Summary

| Metric | Count |
|--------|-------|
| Total doc claims tested | 32 |
| Passed | 31 |
| Xfail (known finding) | 1 |

## Findings

### Warning

**F1. Doc AgentMessage table missing `reply_to` field** (`xfail`)

Doc 2.3 table lists 5 fields: `sender`, `receiver`, `type`, `payload`, `correlation_id`. The actual `AgentMessage` dataclass has 6 fields — `reply_to` is not documented.

Fix: add `reply_to: str | None = None` to the doc table.

### Info

**F2. HandoffManager not covered in initial fact-check**

Doc section 2.2 (the largest section in the doc) covers `HandoffManager` in `arf/engine/handoff.py` — a cross-directory component. Initial fact-check skipped it because the test file was scoped to `arf/communication/`.

Added retroactively: HandoffManager existence, method checks, and behavior verification.

### Info

**F3. Stale line numbers for HandoffManager engine integration**

Doc references `graph.py:779-791` and `graph.py:1110-1124`. File is now 1155 lines, handoff code has moved. Recommend removing line numbers.

## Verified Claims (31 passing)

AgentMessage/AgentInfo data models, 7 Protocol classes, InMemoryAgentBus (asyncio.Queue, broadcast/targeted, discovery), PeerAgent (broadcast/send_to/negotiate/find_peer), DictWorkspace (write/read/write_history), InMemoryLock (TTL acquire/release), MajorityVoteConsensus (propose/vote, threshold=0.5), RoundRobinSupervisor (round-robin, should_intervene=False), HandoffManager (detect/resolve/build_target_context), all 6 module exports, all 7 files exist.

## Test Suite

```
tests/fact_check/test_communication_domain.py
├── TestAgentMessage (2 tests)
├── TestCommunicationProtocols (4 tests)
├── TestInMemoryAgentBus (4 tests)
├── TestPeerAgent (4 tests)
├── TestDictWorkspace (2 tests)
├── TestInMemoryLock (2 tests)
├── TestMajorityVoteConsensus (3 tests)
├── TestRoundRobinSupervisor (3 tests)
├── TestModuleExports (1 test)
├── TestFileExistence (1 test)
├── TestAgentMessageDocCompleteness (1 xfail)
├── TestHandoffManagerCoverage (3 tests)
└── TestHandoffStaleLineNumbers (1 test)
```
