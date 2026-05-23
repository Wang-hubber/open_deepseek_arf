#!/usr/bin/env python3
"""Backend functional tests — covers sessions, chat, resources, trace, archive.

Usage:
    cd app/arf_default_assistant
    python test_backend.py

Requires the server to be running at http://127.0.0.1:8000
"""

import json
import sys
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

BASE = "http://127.0.0.1:8000"
PASS = 0
FAIL = 0
ERRORS: list[str] = []


def log(msg: str) -> None:
    print(f"  {msg}")


def ok(msg: str) -> None:
    global PASS
    PASS += 1
    print(f"  ✅ {msg}")


def bad(msg: str) -> None:
    global FAIL
    ERRORS.append(msg)
    FAIL += 1
    print(f"  ❌ {msg}")


# ── HTTP helpers ────────────────────────────────────────────────────────────

def get(path: str):
    try:
        resp = urlopen(f"{BASE}{path}", timeout=15)
        return resp.status, json.loads(resp.read())
    except HTTPError as e:
        body = e.read().decode(errors="replace")
        return e.code, body
    except URLError as e:
        return 0, str(e.reason)
    except Exception as e:
        return -1, str(e)


def post(path: str, body: dict | None = None):
    data = json.dumps(body).encode() if body else None
    try:
        req = Request(f"{BASE}{path}", data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        resp = urlopen(req, timeout=30)
        return resp.status, json.loads(resp.read())
    except HTTPError as e:
        body_bytes = e.read().decode(errors="replace")
        return e.code, body_bytes
    except URLError as e:
        return 0, str(e.reason)
    except Exception as e:
        return -1, str(e)


def sse_chat(message: str) -> list[dict]:
    """Send a streaming chat message, collect all SSE events."""
    events: list[dict] = []
    data = json.dumps({"message": message, "stream": True}).encode()
    try:
        req = Request(f"{BASE}/api/chat", data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        resp = urlopen(req, timeout=60)
        buf = ""
        while True:
            chunk = resp.read(4096)
            if not chunk:
                break
            buf += chunk.decode("utf-8", errors="replace")
            while "\n\n" in buf:
                line, buf = buf.split("\n\n", 1)
                if line.startswith("data: "):
                    try:
                        events.append(json.loads(line[6:]))
                    except json.JSONDecodeError:
                        pass
    except Exception as e:
        events.append({"type": "error", "detail": str(e)})
    return events


# ── Test cases ──────────────────────────────────────────────────────────────

def test_health():
    """1. Health check — server is alive."""
    print("\n── 1. Health ──")
    status, body = get("/api/health")
    if status == 200 and isinstance(body, dict) and body.get("status") == "ok":
        ok(f"status=ok, agent={body.get('agent')}")
    else:
        bad(f"status={status}, body={str(body)[:100]}")


def test_config_status():
    """2. Config status — reports key state."""
    print("\n── 2. Config Status ──")
    status, body = get("/api/config/status")
    if status == 200 and isinstance(body, dict):
        ok(f"configured={body.get('configured')}, models={body.get('models')}, tools={body.get('tool_count')}")
    else:
        bad(f"status={status}, body={str(body)[:100]}")


def test_sessions_flow():
    """3. Session lifecycle: create → active → list → messages."""
    print("\n── 3. Session Flow ──")

    # Create session
    status, body = post("/api/sessions")
    if status == 200 and isinstance(body, dict) and body.get("id"):
        ok(f"create session → id={body.get('id')}, title={body.get('title')}")
    else:
        bad(f"create session failed: {str(body)[:100]}")
        return

    # List sessions
    status, body = get("/api/sessions")
    if status == 200 and isinstance(body, list):
        ok(f"list sessions → {len(body)} sessions")
    else:
        bad(f"list sessions failed: {str(body)[:100]}")

    # Active session
    status, body = get("/api/sessions/active")
    if status == 200 and isinstance(body, dict) and body.get("id"):
        ok(f"active session → id={body.get('id')}, messages={body.get('message_count')}")
    else:
        bad(f"active session failed: {str(body)[:100]}")

    # Active messages
    status, body = get("/api/sessions/active/messages")
    if status == 200 and isinstance(body, list):
        ok(f"active messages → {len(body)} messages")
        # Show last 3 message roles for context
        roles = [m.get("role", "?") for m in body[-5:]]
        log(f"  last message roles: {roles}")
    else:
        bad(f"active messages failed: {str(body)[:100]}")


def test_resources():
    """4. Resource registration — tools, skills, models are listed."""
    print("\n── 4. Resources ──")

    status, body = get("/api/resources")
    if status != 200 or not isinstance(body, dict):
        bad(f"resources failed: {str(body)[:100]}")
        return

    tools = body.get("tools", [])
    skills = body.get("skills", [])
    models = body.get("models", [])

    tool_names = [t["name"] for t in tools]
    skill_names = [s["name"] for s in skills]
    model_names = [m["name"] for m in models]

    ok(f"tools ({len(tools)}): {', '.join(tool_names[:5])}{'...' if len(tool_names) > 5 else ''}")
    ok(f"skills ({len(skills)}): {', '.join(skill_names[:5])}{'...' if len(skill_names) > 5 else ''}")
    ok(f"models ({len(models)}): {', '.join(model_names)}")

    # Verify key tools exist
    essential = {"file_writer", "file_reader", "web_search", "web_fetch"}
    present = essential & set(tool_names)
    if present:
        ok(f"essential tools present: {present}")
    else:
        bad(f"essential tools missing, have: {tool_names}")

    # Verify tools-by-type endpoint
    status, body = get("/api/resources/tools")
    if status == 200 and isinstance(body, dict):
        ok(f"GET /resources/tools → {body.get('count', 0)} tools")
    else:
        bad(f"resources/tools failed: {str(body)[:100]}")


def test_chat_nonstream():
    """5. Non-streaming chat — send message, get response."""
    print("\n── 5. Chat (non-stream) ──")
    status, body = post("/api/chat", {"message": "say 'test ok' and nothing else", "stream": False})
    if status == 200 and isinstance(body, dict):
        content = body.get("content", "")
        ok(f"response received, length={len(content)}")
        if content:
            log(f"  content preview: {content[:80]}...")
        else:
            bad("empty response content — model may have returned nothing")
    else:
        bad(f"chat failed: status={status}, {str(body)[:150]}")


def test_chat_streaming():
    """6. Streaming chat — SSE events arrive, done has history."""
    print("\n── 6. Chat (streaming SSE) ──")
    events = sse_chat("say 'streaming test' in 3 words or less")

    types = [e.get("type") for e in events]
    log(f"  event types: {types}")

    chunk_events = [e for e in events if e["type"] == "chunk"]
    done_events = [e for e in events if e["type"] == "done"]
    error_events = [e for e in events if e["type"] == "error"]
    tool_call_events = [e for e in events if e["type"] == "tool_call"]

    if error_events:
        bad(f"streaming error: {error_events[0].get('detail', str(error_events[0]))}")
        return

    if chunk_events:
        total_content = "".join(e.get("content", "") for e in chunk_events)
        total_reasoning = "".join(e.get("reasoning", "") for e in chunk_events if e.get("reasoning"))
        ok(f"chunks: {len(chunk_events)} events, content={len(total_content)} chars, reasoning={len(total_reasoning)} chars")
    else:
        log("  no chunk events (model may use reasoning-only or stream differently)")

    if tool_call_events:
        ok(f"tool calls in stream: {len(tool_call_events)}")
        for tc in tool_call_events:
            log(f"  tool: {tc.get('name')} id={tc.get('id')}")

    if done_events:
        done = done_events[0]
        history = done.get("history", [])
        ok(f"done event: session={done.get('session_id')}, history={len(history)} messages")
    else:
        if not error_events:
            bad("no done event received — stream may have hung")


def test_archive():
    """7. Session archive — save and retrieve."""
    print("\n── 7. Archive ──")
    status, body = post("/api/save")
    if status == 200 and isinstance(body, dict):
        ok(f"save → {body}")
    else:
        bad(f"save failed: {str(body)[:100]}")

    status, body = get("/api/archive")
    if status == 200:
        if isinstance(body, dict) and body.get("messages"):
            ok(f"archive → {len(body.get('messages', []))} messages")
        else:
            ok(f"archive → returned (len={len(str(body))} chars)")
    else:
        bad(f"archive failed: status={status}")


def test_reload():
    """8. Reload — server reinitializes and stays alive."""
    print("\n── 8. Reload ──")
    status, body = post("/api/reload")
    if status == 200 and isinstance(body, dict):
        ok(f"reload → {body}")
    else:
        bad(f"reload failed: {str(body)[:100]}")

    # Verify still alive after reload
    time.sleep(1)
    status, body = get("/api/health")
    if status == 200:
        ok("server alive after reload")
    else:
        bad("server dead after reload")


def test_feedback():
    """9. Feedback — submit thumbs up."""
    print("\n── 9. Feedback ──")
    status, body = post("/api/feedback", {"rating": 1, "comment": "test feedback"})
    if status == 200 and isinstance(body, dict):
        ok(f"feedback → {body}")
    else:
        bad(f"feedback failed: {str(body)[:100]}")


def test_trace():
    """10. Trace collection & persistence — sessions, detail, summary."""
    print("\n── 10. Trace ──")

    # Summary
    status, body = get("/api/traces/summary")
    if status == 200 and isinstance(body, dict):
        ok(f"summary → sessions={body.get('total_sessions')}, events={body.get('total_events')}")
    else:
        bad(f"summary failed: {str(body)[:100]}")

    # Session detail
    status, body = get("/api/traces/sessions/default")
    if status == 200 and isinstance(body, dict):
        events = body.get("events", [])
        turns = body.get("turns", [])
        ok(f"default session → events={len(events)}, turns={len(turns)}")

        if events:
            from collections import Counter
            type_counts = Counter(e.get("type", "?") for e in events)
            log(f"  event types: {dict(type_counts)}")

            # Check for model_call_end with usage (token tracking)
            mce = [e for e in events if e["type"] == "model_call_end"]
            with_usage = [e for e in mce if e.get("data", {}).get("usage", {}).get("total_tokens", 0) > 0]
            if with_usage and mce:
                ok(f"token tracking: {len(with_usage)}/{len(mce)} model_call_end have usage data")
            elif mce:
                log(f"  {len(mce)} model_call_end, none with usage (may need newer events)")

            # Check for thinking_delta (streaming captured)
            td = [e for e in events if e["type"] == "thinking_delta"]
            if td:
                ok(f"streaming captured: {len(td)} thinking_delta events")
            else:
                log("  no thinking_delta events (streaming may not have run via current path)")
        else:
            bad("default session has 0 events — trace collection may be broken")
    else:
        bad(f"session detail failed: {str(body)[:100]}")


def test_usage():
    """11. Usage statistics."""
    print("\n── 11. Usage ──")
    status, body = get("/api/usage/summary?period=month")
    if status == 200 and isinstance(body, dict):
        ok(f"usage → tokens={body.get('total_tokens')}, calls={body.get('total_calls')}")
    else:
        bad(f"usage failed: {str(body)[:100]}")


def test_tool_registration():
    """12. Tool definitions — verify tools have correct structure."""
    print("\n── 12. Tool Definitions ──")
    status, body = get("/api/resources/tools")
    if status != 200 or not isinstance(body, dict):
        bad(f"failed: {str(body)[:100]}")
        return

    items = body.get("items", [])
    for item in items:
        name = item.get("name", "?")
        desc = item.get("description", "")
        if name and desc:
            log(f"  {name}: {desc[:60]}...")
        else:
            bad(f"tool '{name}' missing description")

    if items:
        ok(f"{len(items)} tools have definitions")
    else:
        bad("no tools registered")


def test_sse_error_propagation():
    """13. Error propagation — verify error events reach SSE stream."""
    print("\n── 13. SSE Error Propagation ──")
    # Force an error: chat with empty message
    events = sse_chat("")
    error_events = [e for e in events if e["type"] == "error"]
    done_events = [e for e in events if e["type"] == "done"]

    log(f"  events: {[e.get('type') for e in events]}")
    if done_events:
        ok("empty message handled gracefully (done with history)")
    elif error_events:
        ok(f"error propagated: {error_events[0].get('detail', '')[:80]}")
    else:
        log("  neither error nor done — may be expected for empty input")


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  ARF Backend Functional Tests")
    print("=" * 60)
    print(f"  Server: {BASE}")

    # Quick health check before running tests
    _, body = get("/api/health")
    if not isinstance(body, dict) or body.get("status") != "ok":
        print(f"\n  ❌ Server not reachable at {BASE}")
        print(f"     Start with: cd app/arf_default_assistant && python server.py")
        sys.exit(1)

    tests = [
        ("Health", test_health),
        ("Config Status", test_config_status),
        ("Session Flow", test_sessions_flow),
        ("Resources", test_resources),
        ("Tool Definitions", test_tool_registration),
        ("Chat (non-stream)", test_chat_nonstream),
        ("Chat (streaming)", test_chat_streaming),
        ("Archive", test_archive),
        ("Reload", test_reload),
        ("Feedback", test_feedback),
        ("Trace", test_trace),
        ("Usage", test_usage),
        ("SSE Error Propagation", test_sse_error_propagation),
    ]

    for name, fn in tests:
        try:
            fn()
        except Exception as e:
            bad(f"{name}: unhandled exception — {e}")

    print(f"\n{'=' * 60}")
    print(f"  Results: {PASS} passed, {FAIL} failed")
    print(f"{'=' * 60}")

    if ERRORS:
        print("\n  Failures:")
        for e in ERRORS:
            print(f"    - {e}")

    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
