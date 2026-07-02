"""codecompass-fs — code understanding agent (Phase 8 example).

A complete agent example that exercises ARF's MVP capabilities:
- Multi-session archive + switch (arf-session)
- Multi-round dialogue (ReAct loop)
- Interrupt + recover (session_store snapshots)
- Multi-MCP node tool calls (4 namespaces: fs / code / git / web)
- DAG / concurrent tool execution
- Subagents (SubagentDelegate ActionMessage)
- Peer agents (PeerMessage / PeerReply)
- Skill progressive disclosure (SKILL.md)
- Context compaction (Compactor + when_context_over CheckpointRule)
- Memory operations (MemoryOp)

Run:
    cd examples/python/codecompass_fs
    python cli.py
"""
from __future__ import annotations
