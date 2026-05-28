# ARF Fact-Check Report — 2026-05-28 — A2A Communication

**Domain**: A2A Communication (`docs/a2a-communication.md` vs `arf/communication/`)
**Methodology**: TDD-style — 26 tests derived from doc claims. All passed.

---

## Summary

| Metric | Count |
|--------|-------|
| Total doc claims tested | 26 |
| Passed | 26 |
| Failed | 0 |
| Findings | 0 |

**Overall assessment**: A2A Communication domain is clean. All 6 components (AgentBus, PeerAgent, DictWorkspace, InMemoryLock, MajorityVoteConsensus, RoundRobinSupervisor) match their documentation. 7 Protocol classes, AgentMessage model, asyncio.Queue backpressure, TTL lock, round-robin scheduling — all verified.

---

## Verified Claims

### AgentMessage & AgentInfo — all consistent
- `AgentMessage`: sender, receiver, type, payload, correlation_id
- `AgentInfo`: name, description, capabilities

### Protocols (7 classes) — all consistent
- `AgentBus`: send, receive, register, discover
- `PeerAgent`: broadcast, negotiate
- `TaskDelegator`: delegate, get_result
- `Supervisor`: route_task, should_intervene, synthesize
- `SharedWorkspace`: write, read
- `Lock`: acquire, release (TTL default 30.0)
- `ConsensusProtocol`: propose, vote

### InMemoryAgentBus — all consistent
- `asyncio.Queue(maxsize=100)` per agent
- `receiver=None` → broadcast to all registered
- `receiver=name` → targeted delivery
- `discover(capability)` filters by capability
- `register(AgentInfo)` creates queue + records capabilities

### PeerAgent — all consistent
- Constructor: `(bus, info)`
- `start()` → `bus.register(info)`
- `broadcast(msg_type, payload)` → receiver=None
- `send_to(target, msg_type, payload)` → receiver=target
- `discover_peers(capability)` → `bus.discover(capability)` minus self
- `find_peer(capability)` → first match
- `negotiate(proposal, peers, timeout=30s)` → query+collect responses

### DictWorkspace — all consistent
- `write(key, value, owner)` — stores with `_owner` field
- `read(key)` → `dict | None`
- `write_history` list for audit

### InMemoryLock — all consistent
- `acquire(key, owner, ttl=30.0)` → True/False
- TTL expiration auto-releases
- `release(key, owner)` owner-checked
- `reset()` for test doubles

### MajorityVoteConsensus — all consistent
- `threshold=0.5` default
- `propose(proposal, voters)` → `{"proposal_id": ..., "status": "open"}`
- `vote(proposal_id, vote)` records entry

### RoundRobinSupervisor — all consistent
- `route_task(task, agents)` → `agents[_index % len(agents)].name`
- `should_intervene` always returns `False`
- `synthesize(results)` → `"\n".join(...)`
- Wraps around to first agent after last

### Module Exports — all consistent
- 6 classes exported from `arf.communication`
- All 7 source files exist

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
└── TestFileExistence (1 test)
```

Run: `pytest tests/fact_check/test_communication_domain.py -v`
