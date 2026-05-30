#!/usr/bin/env python3
"""E2E test runner for ARF framework — subagent-driven dynamic conversation.

Usage:
    python e2e_runner.py prepare           # Apply pre-test config changes
    python e2e_runner.py cleanup           # Revert pre-test config changes
    python e2e_runner.py state              # Print current state JSON for subagent
    python e2e_runner.py send "<message>"   # Send message via API, update state
    python e2e_runner.py trace              # Fetch and print trace JSON for subagent
    python e2e_runner.py issue "<desc>"     # Record an issue

The orchestrating agent dispatches subagents for message generation and trace
analysis. This script handles HTTP communication and state tracking.
"""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


BASE = "http://127.0.0.1:8000"
STATE_FILE = Path("/tmp/e2e_state.json")
MOCK_PID_FILE = Path("/tmp/e2e_mock_server.pid")
MOCK_PORT = 19999


class E2ERunner:
    def __init__(self, app_dir: Path | None = None) -> None:
        if app_dir is None:
            app_dir = Path(__file__).parent.resolve()
        self.app_dir = Path(app_dir)
        self.personas_dir = self.app_dir / "personas"
        self.state: dict = {}

    # ── Personas ──────────────────────────────────────────────────────────

    def load_personas(self) -> list[dict]:
        import yaml
        personas = []
        for f in sorted(self.personas_dir.glob("*.yaml")):
            with open(f) as fh:
                personas.append(yaml.safe_load(fh))
        return personas

    # ── State management ──────────────────────────────────────────────────

    def init_state(self, total_rounds: int = 40) -> dict:
        self.state = {
            "started_at": datetime.now(timezone.utc).isoformat(),
            "round": 0,
            "total_rounds": total_rounds,
            "rounds": [],
            "issues": [],
            "last_trace_check": 0,
            "persona_lock": None,
        }
        self._save_state()
        return self.state

    def _save_state(self) -> None:
        STATE_FILE.write_text(json.dumps(self.state, ensure_ascii=False, indent=2, default=str))

    def load_state(self) -> dict:
        if STATE_FILE.exists():
            self.state = json.loads(STATE_FILE.read_text())
        else:
            self.init_state()
        return self.state

    def record_round(self, data: dict) -> None:
        self.state.setdefault("rounds", []).append(data)
        self.state["round"] = len(self.state["rounds"])
        self._save_state()

    def trace_check_needed(self, interval: int | None = None) -> bool:
        if interval is None:
            import random
            interval = random.randint(5, 10)
        last = self.state.get("last_trace_check", 0)
        current = self.state.get("round", 0)
        return (current - last) >= interval

    def mark_trace_checked(self) -> None:
        self.state["last_trace_check"] = self.state.get("round", 0)
        self._save_state()

    def record_issue(self, module: str, severity: str, description: str, evidence: str = "") -> None:
        issue = {
            "module": module,
            "severity": severity,
            "description": description,
            "evidence": evidence,
            "round": self.state.get("round", 0),
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        self.state.setdefault("issues", []).append(issue)
        self._save_state()
        print(f"[ISSUE] {module}/{severity}: {description}")

    # ── Context for subagent ──────────────────────────────────────────────

    def _get_persona(self) -> dict:
        personas = self.load_personas()
        lock = self.state.get("persona_lock")
        if lock:
            for p in personas:
                if p["name"] == lock:
                    return p
        return personas[self.state["round"] % len(personas)]

    def context_for_subagent(self) -> dict:
        """Return the context a subagent needs to generate the next message."""
        persona = self._get_persona()
        recent = self.state.get("rounds", [])[-8:]
        return {
            "round": self.state["round"] + 1,
            "total_rounds": self.state.get("total_rounds", 40),
            "persona": persona,
            "recent_rounds": recent,
        }

    # ── HTTP helpers ─────────────────────────────────────────────────────

    def _get(self, path: str):
        try:
            resp = urlopen(f"{BASE}{path}", timeout=15)
            return resp.status, json.loads(resp.read())
        except HTTPError as e:
            return e.code, e.read().decode(errors="replace")
        except URLError as e:
            return 0, str(e.reason)

    def _post(self, path: str, body: dict | None = None):
        data = json.dumps(body).encode() if body else None
        try:
            req = Request(f"{BASE}{path}", data=data, method="POST")
            req.add_header("Content-Type", "application/json")
            resp = urlopen(req, timeout=120)
            return resp.status, json.loads(resp.read())
        except HTTPError as e:
            return e.code, e.read().decode(errors="replace")
        except URLError as e:
            return 0, str(e.reason)

    # ── SSE chat ─────────────────────────────────────────────────────────

    def send_message(self, message: str, new_session: bool = False) -> dict:
        """Send a streaming chat message, return collected events and metadata."""
        payload = {"message": message, "stream": True, "new_session": new_session}
        data = json.dumps(payload).encode()
        events: list[dict] = []
        try:
            req = Request(f"{BASE}/api/chat", data=data, method="POST")
            req.add_header("Content-Type", "application/json")
            resp = urlopen(req, timeout=180)
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

        done_events = [e for e in events if e["type"] == "done"]
        response = done_events[0].get("response", "") if done_events else ""
        sse_types = [e["type"] for e in events]

        return {
            "events": events,
            "response": response,
            "sse_types": sse_types,
            "error": self._has_error(events),
            "is_empty": self._is_empty_response(events),
        }

    @staticmethod
    def _parse_sse(raw_body: str) -> list[dict]:
        events = []
        for line in raw_body.split("\n\n"):
            line = line.strip()
            if line.startswith("data: "):
                try:
                    events.append(json.loads(line[6:]))
                except json.JSONDecodeError:
                    pass
        return events

    @staticmethod
    def _has_error(events: list[dict]) -> bool:
        return any(e["type"] == "error" for e in events)

    @staticmethod
    def _is_empty_response(events: list[dict]) -> bool:
        done_events = [e for e in events if e["type"] == "done"]
        if not done_events:
            return True
        return not done_events[0].get("response", "")

    # ── Trace ────────────────────────────────────────────────────────────

    def fetch_trace(self, session_id: str = "default") -> dict:
        status, body = self._get(f"/api/traces/sessions/{session_id}")
        if status != 200 or not isinstance(body, dict):
            return {"events": [], "turns": []}
        return body

    def trace_for_subagent(self, focus_modules: list[str]) -> dict:
        """Return trace context tailored for subagent analysis."""
        trace = self.fetch_trace()
        return {
            "focus_modules": focus_modules,
            "events": trace.get("events", []),
            "turns": trace.get("turns", []),
            "current_round": self.state.get("round", 0),
        }

    # ── Issues export ────────────────────────────────────────────────────

    def export_issues_markdown(self) -> str:
        """Export recorded issues as a markdown report grouped by module."""
        issues = self.state.get("issues", [])
        if not issues:
            return "# E2E Issues\n\nNo issues found.\n"

        by_module: dict[str, list[dict]] = {}
        for iss in issues:
            by_module.setdefault(iss["module"], []).append(iss)

        lines = ["# E2E Issues Report\n", f"Total: {len(issues)} issues\n"]
        for module, items in sorted(by_module.items()):
            lines.append(f"## {module}")
            for item in items:
                lines.append(f"- **{item['severity'].capitalize()}** (round {item['round']}): {item['description']}")
                if item.get("evidence"):
                    lines.append(f"  - Evidence: {item['evidence']}")
        return "\n".join(lines) + "\n"

    # ── Memory verification ──────────────────────────────────────────────

    def verify_memory(self) -> dict:
        """Check memory files on disk for existence, validity, and growth."""
        memory_dir = self.app_dir / "memory"
        md_path = memory_dir / "memory.md"
        json_path = memory_dir / "memory.json"

        md_info: dict = {"exists": False}
        json_info: dict = {"exists": False}

        if md_path.exists():
            content = md_path.read_text(encoding="utf-8")
            md_info["exists"] = True
            md_info["size"] = len(content)
            md_info["valid"] = self._validate_memory_md(content)
            md_info["truncated"] = "<!-- WARNING: memory truncated" in content
            if md_info["valid"]:
                categories = self._parse_memory_md_categories(content)
                md_info["categories"] = len(categories)
                md_info["category_names"] = list(categories.keys())
                md_info["entries"] = sum(len(v) for v in categories.values())
            else:
                md_info["categories"] = 0
                md_info["category_names"] = []
                md_info["entries"] = 0

        if json_path.exists():
            json_info["exists"] = True
            json_info["valid"] = False
            try:
                data = json.loads(json_path.read_text())
                if isinstance(data, list) and all(self._validate_memory_entry(e) for e in data):
                    json_info["valid"] = True
                    json_info["entries"] = len(data)
            except (json.JSONDecodeError, ValueError):
                pass

        prev = self.state.get("last_memory_check", {})
        prev_size = prev.get("md_size", 0)
        growth: dict = {
            "current_size": md_info.get("size", 0),
            "previous_size": prev_size,
            "grown": md_info.get("size", 0) > prev_size,
        }

        self.state["last_memory_check"] = {
            "md_size": md_info.get("size", 0),
            "md_entries": md_info.get("entries", 0),
            "checked_at_round": self.state.get("round", 0),
        }
        self._save_state()

        return {"markdown": md_info, "json": json_info, "growth": growth}

    @staticmethod
    def _validate_memory_md(content: str) -> bool:
        stripped = content.strip()
        if not stripped or stripped == "NO_NEW_MEMORY":
            return False
        if "## " not in content:
            return False
        if "- " not in content:
            return False
        return True

    @staticmethod
    def _parse_memory_md_categories(content: str) -> dict:
        categories: dict[str, list[str]] = {}
        current_cat = ""
        for line in content.split("\n"):
            if line.startswith("## "):
                current_cat = line[3:].strip()
                categories.setdefault(current_cat, [])
            elif line.strip().startswith("- ") and current_cat:
                categories[current_cat].append(line.strip()[2:])
        return categories

    @staticmethod
    def _validate_memory_entry(entry: dict) -> bool:
        required = {"id", "content", "category", "timestamp"}
        valid_categories = {"fact", "preference", "decision", "context"}
        if not all(k in entry for k in required):
            return False
        if entry.get("category", "") not in valid_categories:
            return False
        return True


# ── CLI ──────────────────────────────────────────────────────────────────────

def cmd_prepare(runner: E2ERunner) -> None:
    """Apply pre-test config changes."""
    import yaml

    deep_path = runner.app_dir / "models" / "deep.yaml"
    deep = yaml.safe_load(deep_path.read_text())
    deep["context_window"] = 64000
    deep_path.write_text(yaml.dump(deep, default_flow_style=False, allow_unicode=True))
    print(f"[PREPARE] {deep_path.name}: context_window=64000")

    quick_path = runner.app_dir / "models" / "quick.yaml"
    quick = yaml.safe_load(quick_path.read_text())
    quick["context_window"] = 32000
    quick_path.write_text(yaml.dump(quick, default_flow_style=False, allow_unicode=True))
    print(f"[PREPARE] {quick_path.name}: context_window=32000")

    agent_path = runner.app_dir / "agent.yaml"
    cfg = yaml.safe_load(agent_path.read_text())
    if "advanced" in cfg and "human_loop" in cfg["advanced"]:
        del cfg["advanced"]["human_loop"]
        agent_path.write_text(yaml.dump(cfg, default_flow_style=False, allow_unicode=True))
        print(f"[PREPARE] {agent_path.name}: human_loop disabled")
    else:
        print(f"[PREPARE] {agent_path.name}: human_loop already disabled")


def cmd_cleanup(runner: E2ERunner) -> None:
    """Revert config changes via git checkout. Kill mock server if running."""
    import subprocess
    import os
    import signal

    if MOCK_PID_FILE.exists():
        pid = int(MOCK_PID_FILE.read_text().strip())
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
        MOCK_PID_FILE.unlink()
        print("[CLEANUP] Mock server killed")

    files = [
        str(runner.app_dir / "models" / "deep.yaml"),
        str(runner.app_dir / "models" / "quick.yaml"),
        str(runner.app_dir / "agent.yaml"),
    ]
    subprocess.run(["git", "checkout", "--"] + files, cwd=runner.app_dir, check=True)
    print("[CLEANUP] Config files reverted")


def cmd_state(runner: E2ERunner) -> None:
    """Print context JSON for subagent dispatch."""
    runner.load_state()
    ctx = runner.context_for_subagent()
    print(json.dumps(ctx, ensure_ascii=False, indent=2))


def cmd_send(runner: E2ERunner, message: str, persona_name: str = "") -> None:
    """Send a message and record the round."""
    runner.load_state()
    result = runner.send_message(message, new_session=(runner.state["round"] == 0))
    runner.record_round({
        "persona": persona_name,
        "message": message,
        "response_content": result["response"],
        "sse_types": result["sse_types"],
        "error": result["error"],
        "is_empty": result["is_empty"],
    })
    print(json.dumps({
        "round": runner.state["round"],
        "persona": persona_name,
        "sse_types": result["sse_types"],
        "response_preview": result["response"][:200] if result["response"] else "(empty)",
        "error": result["error"],
        "trace_check_needed": runner.trace_check_needed(),
    }, ensure_ascii=False, indent=2))


def cmd_trace(runner: E2ERunner) -> None:
    """Print trace context for subagent analysis."""
    runner.load_state()
    persona = runner._get_persona()
    trace_ctx = runner.trace_for_subagent(persona.get("focus_modules", []))
    print(json.dumps(trace_ctx, ensure_ascii=False, indent=2, default=str))
    runner.mark_trace_checked()


def cmd_issue(runner: E2ERunner, description: str, module: str = "", severity: str = "medium") -> None:
    """Record an issue."""
    runner.load_state()
    runner.record_issue(module=module, severity=severity, description=description)


def cmd_report(runner: E2ERunner) -> None:
    """Print issues report."""
    runner.load_state()
    print(runner.export_issues_markdown())


def cmd_verify_memory(runner: E2ERunner) -> None:
    """Check memory files on disk and report content quality."""
    runner.load_state()
    result = runner.verify_memory()
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_persona_lock(runner: E2ERunner, name: str) -> None:
    """Lock persona to a specific name, skipping auto-cycle."""
    runner.load_state()
    runner.state["persona_lock"] = name
    runner._save_state()
    print(json.dumps({"persona_lock": name}))


def cmd_persona_unlock(runner: E2ERunner) -> None:
    """Unlock persona, resume auto-cycle."""
    runner.load_state()
    runner.state["persona_lock"] = None
    runner._save_state()
    print(json.dumps({"persona_lock": None}))


def cmd_mock_deep_down(runner: E2ERunner) -> None:
    """Start a mock HTTP server returning 503, point deep model at it."""
    import subprocess
    import yaml

    server_code = f"""
import http.server
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_error(503, "Service Unavailable")
    def do_POST(self):
        self.send_error(503, "Service Unavailable")
    def log_message(self, format, *args):
        pass
http.server.HTTPServer(('127.0.0.1', {MOCK_PORT}), Handler).serve_forever()
"""
    proc = subprocess.Popen(
        [sys.executable, "-c", server_code],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    MOCK_PID_FILE.write_text(str(proc.pid))

    deep_path = runner.app_dir / "models" / "deep.yaml"
    deep = yaml.safe_load(deep_path.read_text())
    deep["api_base"] = f"http://127.0.0.1:{MOCK_PORT}/v1"
    deep_path.write_text(yaml.dump(deep, default_flow_style=False, allow_unicode=True))
    print(json.dumps({"mock": "deep-down", "pid": proc.pid, "port": MOCK_PORT}))


def cmd_mock_deep_restore(runner: E2ERunner) -> None:
    """Kill mock server and restore deep model config via git checkout."""
    import subprocess
    import os
    import signal

    if MOCK_PID_FILE.exists():
        pid = int(MOCK_PID_FILE.read_text().strip())
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
        MOCK_PID_FILE.unlink()

    subprocess.run(
        ["git", "checkout", "--", str(runner.app_dir / "models" / "deep.yaml")],
        cwd=runner.app_dir,
        check=True,
    )
    print(json.dumps({"mock": "restored"}))


def main():
    if len(sys.argv) < 2:
        print("Usage: python e2e_runner.py <prepare|cleanup|state|send|trace|issue|report|verify-memory|mock-deep-down|mock-deep-restore>")
        sys.exit(1)

    runner = E2ERunner()
    cmd = sys.argv[1]

    if cmd == "prepare":
        cmd_prepare(runner)
    elif cmd == "cleanup":
        cmd_cleanup(runner)
    elif cmd == "state":
        cmd_state(runner)
    elif cmd == "send":
        if len(sys.argv) < 3:
            print("Usage: python e2e_runner.py send '<message>' [persona_name]")
            sys.exit(1)
        cmd_send(runner, sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "")
    elif cmd == "trace":
        cmd_trace(runner)
    elif cmd == "issue":
        if len(sys.argv) < 3:
            print("Usage: python e2e_runner.py issue '<description>' [--module X] [--severity high|medium|low]")
            sys.exit(1)
        module = ""
        severity = "medium"
        args = sys.argv[2:]
        desc_parts = []
        i = 0
        while i < len(args):
            if args[i] == "--module" and i + 1 < len(args):
                module = args[i + 1]
                i += 2
            elif args[i] == "--severity" and i + 1 < len(args):
                severity = args[i + 1]
                i += 2
            else:
                desc_parts.append(args[i])
                i += 1
        cmd_issue(runner, " ".join(desc_parts), module=module, severity=severity)
    elif cmd == "report":
        cmd_report(runner)
    elif cmd == "verify-memory":
        cmd_verify_memory(runner)
    elif cmd == "persona-lock":
        if len(sys.argv) < 3:
            print("Usage: python e2e_runner.py persona-lock <name>")
            sys.exit(1)
        cmd_persona_lock(runner, sys.argv[2])
    elif cmd == "persona-unlock":
        cmd_persona_unlock(runner)
    elif cmd == "mock-deep-down":
        cmd_mock_deep_down(runner)
    elif cmd == "mock-deep-restore":
        cmd_mock_deep_restore(runner)
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
