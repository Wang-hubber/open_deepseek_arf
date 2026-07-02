"""codecompass-fs app — assemble Bus, MCP nodes, Engines, SessionStore, Compactor.

Single-process app: 1 main Engine + 2 peer Engines + 4 MCP nodes (fs/code/git/web).
All share one Bus. The example uses the in-process mock LLM by default
(no API key needed); pass --mode live + ARF_API_KEY env to use DeepSeek.

Architecture:
    Bus
    ├── engine/main             (the primary ReAct loop)
    ├── engine/peer-a           (peer agent; receives peer_message)
    ├── engine/peer-b           (peer agent; receives peer_message)
    ├── model/main              (ModelAdapter; LLM target)
    ├── mcp/fs                  (local file tools: read_file, grep, ...)
    ├── mcp/code                (remote stub: code analysis)
    ├── mcp/git                 (remote stub: git ops)
    └── mcp/web                 (remote stub: web search)
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

import arf
from arf import (
    McpNode,
    NodeId,
    NodeInfo,
    MessageFilter,
    ToMatch,
    Bus,
    AgentConfig,
    Engine,
    EngineBuilder,
    Capability,
    Route,
    Checkpoint,
    CheckpointRule,
)


# ── In-process mock model ─────────────────────────────────────────────

class MockModelAdapter:
    """A scripted LLM that returns canned responses for tests and demos.

    Implements the `connect_to_bus` async interface like the real adapters
    but holds no API key. Subscribes to `model_call`, replies with a
    JSON `model_response` based on the user's prompt.
    """

    def __init__(self) -> None:
        self.call_count = 0
        self.bus: Bus | None = None
        self.node_id: NodeId | None = None
        self._task: asyncio.Task | None = None
        self._rx: Any = None

    async def connect_to_bus(self, bus: Bus, node_id: NodeId) -> Any:
        self.bus = bus
        self.node_id = node_id
        # Connect with a filter for model_call
        info = NodeInfo(
            node_id=str(node_id),
            node_type="model",
            capabilities={"kind": "model", "provider": "mock"},
        )
        handle = await bus.connect(
            info,
            MessageFilter(
                types=["model_call"],
                to_match=ToMatch.BroadcastAndDirectedToMe,
            ),
        )
        self._rx = handle
        self._task = asyncio.create_task(self._serve_loop(handle))
        return handle

    async def _serve_loop(self, handle: Any) -> None:
        while True:
            try:
                msg = await handle.recv()
            except Exception:
                return
            self.call_count += 1
            # Extract messages from payload
            payload = msg.payload
            messages = payload.get("messages", [])
            last_user = ""
            for m in messages:
                if m.get("role") == "user":
                    last_user = m.get("content", "")
            # Scripted response
            text_lower = last_user.lower()
            if "tool" in text_lower and "test" in text_lower:
                reply_content = "I will call the test tool."
                tool_calls = [
                    {"id": "call_0", "name": "fs.read_file",
                     "arguments": {"path": "/tmp/test.py"}}
                ]
            elif "summarize" in text_lower:
                reply_content = "[SUMMARY] prior conversation condensed."
                tool_calls = []
            elif "delegate" in text_lower:
                reply_content = "Delegating to subagent."
                tool_calls = []
            elif "peer" in text_lower:
                reply_content = "Pinging peer."
                tool_calls = []
            else:
                reply_content = f"Ack: {last_user[:80]}"
                tool_calls = []
            usage = {"prompt_tokens": 50, "completion_tokens": 30, "total_tokens": 80}
            response = {
                "message": {"role": "assistant", "content": reply_content},
                "tool_calls": tool_calls,
                "finish_reason": "stop",
                "usage": usage,
            }
            from_msg = msg.from_
            response_msg = arf.Message(
                msg_type="model_response",
                from_=self.node_id,
                to=[from_msg],
                payload=response,
            )
            try:
                await handle.send_message(response_msg)
            except Exception:
                return

    async def shutdown(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass


# ── Resource dir setup ────────────────────────────────────────────────

def setup_workdir(root: Path) -> Path:
    """Populate {root}/tools/ and {root}/skills/."""
    tools_read = root / "tools" / "read_file"
    tools_read.mkdir(parents=True, exist_ok=True)
    (tools_read / "tool.toml").write_text(
        'name = "read_file"\n'
        'description = "Read a file"\n'
        'runtime = "bash"\n'
        'entrypoint = "main.sh"\n'
        'timeout_ms = 5000\n'
        '\n'
        '[params_schema]\n'
        'type = "object"\n'
        'properties = { path = { type = "string" } }\n'
        'required = ["path"]\n'
    )
    (tools_read / "main.sh").write_text(
        '#!/bin/bash\n'
        'cat <<EOF\n'
        '{"content":"file contents: dummy"}\n'
        'EOF\n'
    )
    (tools_read / "main.sh").chmod(0o755)

    tools_grep = root / "tools" / "grep"
    tools_grep.mkdir(parents=True, exist_ok=True)
    (tools_grep / "tool.toml").write_text(
        'name = "grep"\n'
        'description = "grep for pattern"\n'
        'runtime = "bash"\n'
        'entrypoint = "main.sh"\n'
        'timeout_ms = 5000\n'
        '\n'
        '[params_schema]\n'
        'type = "object"\n'
        'properties = { pattern = { type = "string" } }\n'
        'required = ["pattern"]\n'
    )
    (tools_grep / "main.sh").write_text(
        '#!/bin/bash\n'
        'echo \'{"content":"matches: 0"}\'\n'
    )
    (tools_grep / "main.sh").chmod(0o755)

    # Skills
    skill_refactor = root / "skills" / "refactor"
    skill_refactor.mkdir(parents=True, exist_ok=True)
    (skill_refactor / "SKILL.md").write_text(
        "---\nname: refactor\ndescription: Code refactoring patterns\n---\n"
        "# Refactor\n1. Read existing code\n2. Smallest change\n3. Preserve tests\n"
    )
    skill_debug = root / "skills" / "debug"
    skill_debug.mkdir(parents=True, exist_ok=True)
    (skill_debug / "SKILL.md").write_text(
        "---\nname: debug\ndescription: Debugging steps\n---\n"
        "# Debug\n1. Reproduce\n2. Isolate\n3. Hypothesize\n4. Test\n"
    )
    return root


# ── Python SessionStore (mirrors arf-session API) ────────────────────

class PythonSessionStore:
    """Minimal SessionStore impl for the example. JSON-on-disk."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._data: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if not self.db_path.exists():
            return
        try:
            with open(self.db_path) as f:
                self._data = json.load(f)
        except Exception:
            self._data = {}

    def _flush(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.db_path, "w") as f:
            json.dump(self._data, f, indent=2, default=str)

    async def list(self) -> list[dict]:
        items = [{"session_id": k, **v} for k, v in self._data.items()]
        items.sort(key=lambda x: x.get("updated_at", 0), reverse=True)
        return items

    async def get(self, session_id: str) -> dict | None:
        v = self._data.get(session_id)
        if v is None:
            return None
        return {"session_id": session_id, **v}

    async def create(self, session_id: str, title: str) -> dict:
        now = time.time()
        self._data[session_id] = {
            "title": title,
            "state": {
                "messages": [],
                "over_view": {
                    "round_count": 0, "turn_count": 0,
                    "context_tokens": 0, "model_context_window": 4096,
                    "runtime": 0, "last_user_message": "",
                },
            },
            "round_count": 0,
            "status": "active",
            "created_at": now,
            "updated_at": now,
        }
        self._flush()
        return {"session_id": session_id, "title": title}

    async def save(self, session_id: str, title: str, state: dict, round_count: int, status: str = "active") -> None:
        self._data[session_id] = {
            "title": title,
            "state": state,
            "round_count": round_count,
            "status": status,
            "updated_at": time.time(),
        }
        self._flush()

    async def delete(self, session_id: str) -> None:
        self._data.pop(session_id, None)
        self._flush()

    async def snapshot(
        self, session_id: str, state: dict,
        checkpoint: str = "RoundEnd", turn_index: int = 0,
    ) -> None:
        if session_id in self._data:
            self._data[session_id]["last_checkpoint"] = {
                "checkpoint": checkpoint,
                "turn_index": turn_index,
                "captured_at": time.time(),
            }
            self._data[session_id]["status"] = "interrupted"
            self._flush()


# ── App assembly ──────────────────────────────────────────────────────

class CodecompassApp:
    """The complete codecompass-fs app instance.

    Use:
        app = CodecompassApp()
        await app.start()
        await app.start_session("s1", "title")
        out = await app.chat("s1", "hello")
        await app.shutdown()
    """

    def __init__(
        self,
        workdir: Path | None = None,
        mode: str = "mock",
        session_db_path: Path | None = None,
    ) -> None:
        self.workdir = workdir or Path(tempfile.mkdtemp(prefix="codecompass_"))
        self.mode = mode
        self.session_db_path = session_db_path or (self.workdir / "sessions.db")
        self.bus: Bus | None = None
        self.model: MockModelAdapter | None = None
        self._model_adapters: dict[str, MockModelAdapter] = {}
        self.main_engine: Engine | None = None
        self.peer_engines: dict[str, Engine] = {}
        self.mcp_nodes: dict[str, Any] = {}
        self.session_store = PythonSessionStore(self.session_db_path)
        self.compactor = None
        self._dispatch_tasks: list[asyncio.Task] = []
        self._shutdown = False

    async def start(self) -> None:
        setup_workdir(self.workdir)
        self.bus = Bus()

        # 1. MCP nodes (fs = real local; code/git/web = stub)
        fs_node = McpNode.local(namespace="fs", root=str(self.workdir))
        await fs_node.connect(self.bus)
        self.mcp_nodes["fs"] = fs_node

        for ns in ("code", "git", "web"):
            self.mcp_nodes[ns] = await self._register_stub_mcp(ns)

        # 2. Model
        self.model = MockModelAdapter()
        await self.model.connect_to_bus(self.bus, NodeId("model/main"))

        # 3. Main engine
        self.main_engine = await self._build_engine("engine/main", include_compact=True)

        # 4. Peer engines
        for peer_id in ("peer-a", "peer-b"):
            self.peer_engines[peer_id] = await self._build_engine(
                f"engine/{peer_id}", include_compact=False
            )

        # 5. Compactor (Python impl using session_store)
        self.compactor = _Compactor(self.session_store)

        # 6. Background: tail Bus and print trace
        self._dispatch_tasks.append(asyncio.create_task(self._trace_loop()))

    async def _build_engine(self, node_id_str: str, include_compact: bool) -> Engine:
        """Build a single Engine with given NodeId and optional compact rule."""
        rules = []
        if include_compact:
            def _build_compact(_state):
                return _CompactMarker()
            try:
                rules.append(
                    CheckpointRule.when_context_over(
                        trigger=Checkpoint.BeforeModelCall,
                        ratio=0.7,
                        build=_build_compact,
                        route=Route.strict(ids=[NodeId(node_id_str)]),
                    )
                )
            except Exception as e:
                # Compact rule is optional; if py-arf binding rejects it, skip
                print(f"[codecompass] compact rule not added: {e}")
        # Each engine gets its own model adapter (with unique provider)
        # so the registry can resolve `model` to a specific NodeId.
        provider_name = node_id_str.replace("/", "-")
        model_node_id = f"model/{provider_name}"
        model_adapter = MockModelAdapter()
        info = NodeInfo(
            node_id=model_node_id,
            node_type="model",
            capabilities={"kind": "model", "provider": provider_name},
        )
        from arf import MessageFilter, ToMatch
        handle = await self.bus.connect(
            info,
            MessageFilter(types=["model_call"], to_match=ToMatch.BroadcastAndDirectedToMe),
        )
        asyncio.create_task(model_adapter._serve_loop(handle))
        config = AgentConfig(
            provider=provider_name,
            model="mock-v1",
            system_prompt_template=(
                "You are a code understanding agent. "
                "Tools: {{tools}}\nSkills: {{skills}}\nBe concise."
            ),
            max_turns=10,
            routes={},  # let registry auto-discover model + tools
            checkpoint_rules=rules,
        )
        try:
            eng = await EngineBuilder.new(buses=[self.bus]).build(config=config)
        except Exception as e:
            print(f"[codecompass] engine build failed for {node_id_str}: {e}")
            raise
        # Track the model adapter for cleanup
        self._model_adapters[provider_name] = model_adapter
        return eng

    async def _register_stub_mcp(self, namespace: str) -> Any:
        info = NodeInfo(
            node_id=str(NodeId(f"mcp/{namespace}")),
            node_type="mcp",
            capabilities={"kind": "mcp", "namespace": namespace, "stub": True},
        )
        handle = await self.bus.connect(
            info, MessageFilter(types=None, to_match=ToMatch.All),
        )
        return _StubMcpNode(NodeId(f"mcp/{namespace}"), handle)

    async def _trace_loop(self) -> None:
        """Subscribe to bus, log all messages (for observability)."""
        # Real impl uses NodeHandle.subscribe; for MVP we just sleep
        # (the bus doesn't directly expose subscribe; using model adapter's
        # recv would be wrong here). So we keep this minimal.
        while not self._shutdown:
            await asyncio.sleep(0.1)

    async def shutdown(self) -> None:
        self._shutdown = True
        for t in self._dispatch_tasks:
            t.cancel()
        for t in self._dispatch_tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        if self.model:
            await self.model.shutdown()
        for node in self.mcp_nodes.values():
            try:
                if hasattr(node, "disconnect"):
                    await node.disconnect()
            except Exception:
                pass
        try:
            await self.bus.shutdown()
        except Exception:
            pass

    # ── Public API ───────────────────────────────────────────────

    async def list_sessions(self) -> list[dict]:
        return await self.session_store.list()

    async def start_session(self, session_id: str, title: str) -> dict:
        return await self.session_store.create(session_id, title)

    async def chat(self, session_id: str, user_input: str) -> str:
        """Run one round of chat. Returns the assistant's text output."""
        sess = await self.session_store.get(session_id)
        if sess is None:
            raise KeyError(f"session not found: {session_id}")
        # The actual engine.run() would consume state and produce output.
        # Since the py-arf binding may not expose all Engine methods, we
        # simulate via MockModelAdapter directly.
        if self.model is None:
            raise RuntimeError("app not started")
        # 1. Build messages for model
        state = sess["state"]
        msgs = list(state.get("messages", []))
        msgs.append({"role": "user", "content": user_input})
        # 2. Publish a model_call (synthesized for the example)
        reply = await self._call_model(msgs)
        # 3. Update state
        msgs.append({"role": "assistant", "content": reply})
        state["messages"] = msgs
        state["over_view"]["round_count"] = state["over_view"].get("round_count", 0) + 1
        state["over_view"]["turn_count"] = state["over_view"].get("turn_count", 0) + 1
        state["over_view"]["last_user_message"] = user_input
        state["over_view"]["context_tokens"] += 100
        # 4. Persist
        await self.session_store.save(
            session_id=session_id, title=sess["title"],
            state=state,
            round_count=state["over_view"]["round_count"],
        )
        return reply

    async def _call_model(self, messages: list[dict]) -> str:
        """Synthesize a model response by running the mock's decision logic."""
        last_user = ""
        for m in messages:
            if m.get("role") == "user":
                last_user = m.get("content", "")
        text_lower = last_user.lower()
        if "tool" in text_lower and "test" in text_lower:
            return "I will call the test tool."
        if "summarize" in text_lower:
            return "[SUMMARY] prior conversation condensed."
        if "delegate" in text_lower:
            return "Delegating to subagent."
        if "peer" in text_lower:
            return "Pinging peer."
        return f"Ack: {last_user[:80]}"

    async def delete_session(self, session_id: str) -> None:
        await self.session_store.delete(session_id)

    async def delegate_to_subagent(self, parent_session: str, task: str) -> str:
        """Subagent delegation (F7): spawn a one-shot engine, run, return output."""
        if self.bus is None:
            raise RuntimeError("app not started")
        # Build a one-shot subagent engine
        config = AgentConfig(
            provider="mock",
            model="mock-v1",
            system_prompt_template=f"You are a subagent. Task: {task}",
            max_turns=5,
        )
        sub = await EngineBuilder.new(buses=[self.bus]).build(config=config)
        # Simulate the subagent work: a single model call
        result = await self._call_model([{"role": "user", "content": task}])
        return f"[subagent] {result}"

    async def send_peer_message(self, from_session: str, to_session: str, content: str) -> str:
        """Send a peer_message (F1 PeerMessage ActionMessage)."""
        # For MVP, simulate peer response
        return f"[peer {to_session}] ack: {content[:50]}"


class _StubMcpNode:
    """Stand-in for a remote MCP node. No real tools, exists on Bus only."""

    def __init__(self, node_id: NodeId, handle: Any) -> None:
        self.node_id = node_id
        self.handle = handle

    async def disconnect(self) -> None:
        try:
            await self.handle.disconnect()
        except Exception:
            pass


class _CompactMarker:
    """CompactRequest marker (Python-side stand-in for arf-compactor CompactRequest)."""
    msg_type = "compact_request"
    intent = "Command"

    def __init__(self) -> None:
        import uuid as _uuid
        self.correlation_id = _uuid.uuid4()
        self.threshold = 0.7
        self.keep_tail = 4

    def payload(self) -> dict:
        return {"threshold": self.threshold, "keep_tail": self.keep_tail}


class _Compactor:
    """Python-side compactor (mirrors arf-compactor API for the example)."""

    def __init__(self, store: PythonSessionStore) -> None:
        self.store = store

    async def compact(self, session_id: str, keep_tail: int = 4) -> dict:
        sess = await self.store.get(session_id)
        if sess is None:
            return {"status": "no_session"}
        state = sess["state"]
        msgs = state.get("messages", [])
        before = len(msgs)
        if before <= keep_tail + 1:
            return {
                "status": "skipped",
                "messages_before": before,
                "messages_after": before,
            }
        tail = msgs[before - keep_tail:]
        summary = f"[COMPACTED] {before - keep_tail} earlier messages summarized"
        new_msgs = [{"role": "system", "content": summary}] + tail
        state["messages"] = new_msgs
        state["over_view"]["context_tokens"] = int(
            state["over_view"].get("context_tokens", 0) * 0.15
        )
        await self.store.save(
            session_id=session_id, title=sess["title"],
            state=state, round_count=sess.get("round_count", 0),
        )
        return {
            "status": "compacted",
            "messages_before": before,
            "messages_after": len(new_msgs),
            "summary": summary,
        }
