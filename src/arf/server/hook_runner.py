"""Hook runner -- loads .hooks.json, executes subprocess hooks on session events.

Events: SessionStart, PreModelCall, PostModelCall, PreToolUse, PostToolUse, SessionEnd

Exit-code contract (teaching version, unified):
  0 -- continue (stdout may contain JSON with extra data)
  1 -- block current action (stderr = reason)
  2 -- inject a message (stderr = message text)
"""

import json
import logging
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30
HOOK_EVENTS = ("SessionStart", "PreModelCall", "PostModelCall", "PreToolUse", "PostToolUse", "SessionEnd")

# Maximum size for env-var payloads (keep them under kernel limits)
MAX_ENV_PAYLOAD = 10000
# Maximum size for stdin payloads
MAX_STDIN_PAYLOAD = 200000


def _python_cmd() -> str:
    """Return a working Python command, verified by actual execution."""
    for candidate in ("python3", "python"):
        if shutil.which(candidate):
            try:
                r = subprocess.run(
                    [candidate, "-c", "print('ok')"],
                    capture_output=True, timeout=3,
                )
                if r.returncode == 0:
                    return candidate
            except Exception:
                continue
    return sys.executable


def _fix_python_cmd(command: str) -> str:
    """Replace a non-existent python command in a hook command string with
    one that is actually available on this system."""
    actual = _python_cmd()
    # Split on whitespace to check the first token
    parts = command.split(None, 1)
    if not parts:
        return command
    exe = parts[0]
    # Already correct — nothing to do
    if exe == actual:
        return command
    # If the command uses python3 or python but that binary isn't available,
    # swap to the one that exists.  Ignore absolute paths (e.g. /usr/bin/python3).
    if exe in ("python3", "python"):
        if not shutil.which(exe):
            return actual + (command[len(exe):] if command.startswith(exe) else command)
    return command


@dataclass
class HookResult:
    exit_code: int  # 0, 1, 2
    message: str = ""  # stderr content (reason for block, text for inject)
    data: dict | None = None  # parsed stdout JSON (for exit 0 with structured output)


@dataclass
class HookDefinition:
    name: str
    command: str
    timeout: int = DEFAULT_TIMEOUT
    enabled: bool = True
    matcher: str | None = None  # tool name filter for PreToolUse/PostToolUse


class HookRunner:
    """Loads hook definitions from .hooks.json and runs matching hooks via subprocess.

    Context is passed to hooks through environment variables (small payloads)
    and stdin JSON (large payloads like conversation history).

    Built-in hooks (title_generator, session_archiver, etc.) are trusted by
    default. User-added hooks are protected by the JWT-authenticated
    manage_hooks API.

    When _trace_collector is set, each hook execution emits a detailed trace
    event including command, exit_code, stdout, stderr, and duration.
    """

    def __init__(self, workspace: Path):
        self._workspace = workspace.resolve()
        self._hooks: dict[str, list[HookDefinition]] = {
            event: [] for event in HOOK_EVENTS
        }
        self._config_mtime: float = 0.0
        self._trace_collector = None
        self._load()

    # ---- config loading ---------------------------------------------------

    @property
    def config_path(self) -> Path:
        return self._workspace / ".hooks.json"

    def _load(self) -> None:
        path = self.config_path
        if not path.exists():
            return
        try:
            self._config_mtime = path.stat().st_mtime
            config = json.loads(path.read_text(encoding="utf-8"))
            raw_hooks = config.get("hooks", {})
            for event in HOOK_EVENTS:
                self._hooks[event] = [
                    HookDefinition(
                        name=h.get("name", "unnamed"),
                        command=_fix_python_cmd(h.get("command", "")),
                        timeout=h.get("timeout", DEFAULT_TIMEOUT),
                        enabled=h.get("enabled", True),
                        matcher=h.get("matcher"),
                    )
                    for h in raw_hooks.get(event, [])
                ]
        except (json.JSONDecodeError, Exception) as e:
            logger.warning("Failed to load hook config %s: %s", path, e)

    def set_trace_collector(self, collector) -> None:
        """Inject a TraceCollector for per-hook execution trace events."""
        self._trace_collector = collector

    def reload(self) -> None:
        """Re-read config if it changed on disk."""
        path = self.config_path
        if path.exists() and path.stat().st_mtime != self._config_mtime:
            self._load()
            logger.info("Hook config reloaded")

    # ---- run --------------------------------------------------------------

    def run(
        self,
        event: str,
        payload: dict | None = None,
        stdin_data: dict | None = None,
    ) -> HookResult:
        """Execute all enabled hooks for *event* in parallel.

        Each hook runs as an independent subprocess with its own timeout.
        Results are aggregated: block takes priority, inject messages are
        concatenated, stdout data dicts are merged.

        Args:
            event: Hook event name (SessionStart, PreToolUse, ...)
            payload: Small context dict -> passed as env vars
            stdin_data: Large context dict -> passed as stdin JSON

        Returns:
            HookResult -- aggregated result from all matching hooks.
        """
        payload = payload or {}

        # Auto-reload if config changed on disk
        self.reload()

        # Collect eligible hooks
        tasks: list[HookDefinition] = []
        for hook_def in self._hooks.get(event, []):
            if not hook_def.enabled or not hook_def.command:
                continue
            if hook_def.matcher and hook_def.matcher != "*":
                tool_name = payload.get("tool_name", "")
                if hook_def.matcher != tool_name:
                    continue
            tasks.append(hook_def)

        if not tasks:
            return HookResult(exit_code=0)

        # Run all hooks in parallel via thread pool (subprocess.run is I/O bound)
        from concurrent.futures import ThreadPoolExecutor, as_completed
        futures: dict = {}
        with ThreadPoolExecutor(max_workers=len(tasks)) as executor:
            for hd in tasks:
                futures[executor.submit(self._run_one, hd, event, payload, stdin_data)] = hd

        # Aggregate results
        merged_data: dict = {}
        merged_messages: list[str] = []
        blocked: HookResult | None = None

        for future in as_completed(futures):
            hd = futures[future]
            try:
                result = future.result()
            except Exception:
                logger.warning("Hook %s raised exception", hd.name)
                continue

            if result.exit_code == 1:
                blocked = result  # record block, but let others finish
            elif result.exit_code == 2:
                merged_messages.append(f"[{hd.name}]: {result.message}" if result.message else f"[{hd.name}]")
            if result.data:
                merged_data.update(result.data)

        if blocked:
            return blocked
        if merged_messages:
            return HookResult(exit_code=2, message="\n".join(merged_messages))
        return HookResult(exit_code=0, data=merged_data if merged_data else None)

    def _run_one(
        self,
        hook_def: HookDefinition,
        event: str,
        payload: dict,
        stdin_data: dict | None,
    ) -> HookResult:
        t0 = time.monotonic()
        exit_code = -1
        stdout_text = ""
        stderr_text = ""
        hook_status = "ok"
        error_msg = None

        try:
            env = self._build_env(event, payload)
            stdin_str = self._build_stdin(event, payload, stdin_data)
            r = subprocess.run(
                hook_def.command,
                shell=True,
                cwd=str(self._workspace),
                env=env,
                input=stdin_str,
                capture_output=True,
                encoding="utf-8",
                timeout=hook_def.timeout,
            )
            exit_code = r.returncode
            stdout_text = r.stdout.strip() if r.stdout else ""
            stderr_text = r.stderr.strip() if r.stderr else ""

            if r.returncode == 0:
                data = None
                if stdout_text:
                    try:
                        data = json.loads(stdout_text)
                    except json.JSONDecodeError:
                        pass  # stdout was not JSON, treat as informational
                result = HookResult(exit_code=0, data=data)

            elif r.returncode == 1:
                hook_status = "blocked"
                result = HookResult(exit_code=1, message=stderr_text or "Blocked by hook")

            elif r.returncode == 2:
                result = HookResult(exit_code=2, message=stderr_text)

            else:
                hook_status = "error"
                error_msg = f"Unexpected exit code {r.returncode}"
                logger.warning(
                    "Hook %s returned unexpected exit code %d",
                    hook_def.name, r.returncode,
                )
                result = HookResult(exit_code=0)

        except subprocess.TimeoutExpired:
            hook_status = "error"
            error_msg = f"Timeout after {hook_def.timeout}s"
            logger.warning("Hook %s timed out after %ds", hook_def.name, hook_def.timeout)
            result = HookResult(exit_code=0)
        except Exception as e:
            hook_status = "error"
            error_msg = f"Hook '{hook_def.name}' failed: {e}"
            logger.warning("Hook %s failed: %s", hook_def.name, e)
            result = HookResult(exit_code=0)

        duration_ms = (time.monotonic() - t0) * 1000

        # Emit per-hook execution trace for observability
        if self._trace_collector:
            meta = {
                "hook_name": hook_def.name,
                "hook_event": event,
                "command": hook_def.command,
                "exit_code": exit_code,
                "stdout": stdout_text[:2000] if stdout_text else "",
                "stderr": stderr_text[:2000] if stderr_text else "",
            }
            tool_name = (
                payload.get("tool_name", "")
                or payload.get("tool", "")
                or None
            )
            self._trace_collector.emit({
                "event_type": "lifecycle.hook_execution",
                "node": "hook",
                "tool_name": tool_name,
                "model": payload.get("model"),
                "status": hook_status,
                "duration_ms": round(duration_ms, 1),
                "error_msg": error_msg,
                "metadata": meta,
            })

        return result

    # ---- env / stdin builders ---------------------------------------------

    def _build_env(self, event: str, payload: dict) -> dict:
        env = dict(os.environ)
        env["ARF_HOOK_EVENT"] = event
        env["ARF_HOOK_WORKSPACE"] = str(self._workspace)

        # Always inject session_id: payload first, fallback to trace_collector
        sid = payload.get("session_id", "")
        if not sid and self._trace_collector:
            sid = self._trace_collector.current_session_id
        if sid:
            env["ARF_HOOK_SESSION_ID"] = str(sid)

        for key in ("session_title", "tool_name", "model", "status"):
            val = payload.get(key, "")
            if val:
                env[f"ARF_HOOK_{key.upper()}"] = str(val)

        # Turn number
        turn = payload.get("turn")
        if turn is not None:
            env["ARF_HOOK_TURN"] = str(turn)

        # Duration
        duration = payload.get("duration_ms")
        if duration is not None:
            env["ARF_HOOK_DURATION_MS"] = str(duration)

        # Token counts (for PostModelCall)
        for tk in ("prompt_tokens", "completion_tokens", "total_tokens"):
            val = payload.get(tk)
            if val is not None:
                env[f"ARF_HOOK_{tk.upper()}"] = str(val)

        # Tool input -> env var (truncated)
        tool_input = payload.get("tool_input")
        if tool_input is not None:
            raw = json.dumps(tool_input, ensure_ascii=False)[:MAX_ENV_PAYLOAD]
            env["ARF_HOOK_TOOL_INPUT"] = raw

        # Tool output -> env var (truncated)
        tool_output = payload.get("tool_output")
        if tool_output is not None:
            env["ARF_HOOK_TOOL_OUTPUT"] = str(tool_output)[:MAX_ENV_PAYLOAD]

        # Input/output snippets for PreModelCall/PostModelCall
        for snippet_key in ("input_snippet", "output_snippet"):
            val = payload.get(snippet_key, "")
            if val:
                env[f"ARF_HOOK_{snippet_key.upper()}"] = str(val)[:MAX_ENV_PAYLOAD]

        # Category (sys/user for tools)
        tool_category = payload.get("tool_category", "")
        if tool_category:
            env["ARF_HOOK_TOOL_CATEGORY"] = str(tool_category)

        # Finish reason (for PostModelCall)
        finish_reason = payload.get("finish_reason", "")
        if finish_reason:
            env["ARF_HOOK_FINISH_REASON"] = str(finish_reason)

        # Message count (for PreModelCall)
        message_count = payload.get("message_count")
        if message_count is not None:
            env["ARF_HOOK_MESSAGE_COUNT"] = str(message_count)

        return env

    def _build_stdin(
        self, event: str, payload: dict, stdin_data: dict | None
    ) -> str:
        """Bundle event + payload + extra data as stdin JSON for large payloads."""
        data: dict = {"event": event, "payload": payload}
        if stdin_data:
            data["data"] = stdin_data
        raw = json.dumps(data, ensure_ascii=False)
        if len(raw) > MAX_STDIN_PAYLOAD:
            logger.warning("Hook stdin payload too large (%d bytes), truncating", len(raw))
            # Truncate conversation data to fit
            if "data" in data and "conversation" in data["data"]:
                data["data"]["conversation"] = data["data"]["conversation"][-200:]
                data["data"]["truncated"] = True
            raw = json.dumps(data, ensure_ascii=False)[:MAX_STDIN_PAYLOAD]
        return raw

    # ---- manage hooks config ----------------------------------------------

    def list_hooks(self) -> dict:
        """Return the current hook configuration (for API / manage_hooks tool)."""
        return {
            event: [
                {
                    "name": h.name,
                    "command": h.command,
                    "timeout": h.timeout,
                    "enabled": h.enabled,
                    "matcher": h.matcher,
                }
                for h in hooks
            ]
            for event, hooks in self._hooks.items()
        }

    def add_hook(self, event: str, definition: dict) -> bool:
        """Add a hook to an event. Persists to .hooks.json."""
        if event not in HOOK_EVENTS:
            return False
        hd = HookDefinition(
            name=definition.get("name", "unnamed"),
            command=definition.get("command", ""),
            timeout=definition.get("timeout", DEFAULT_TIMEOUT),
            enabled=definition.get("enabled", True),
            matcher=definition.get("matcher"),
        )
        self._hooks[event].append(hd)
        self._save()
        return True

    def remove_hook(self, event: str, name: str) -> bool:
        """Remove a hook by name from an event. Persists."""
        if event not in HOOK_EVENTS:
            return False
        before = len(self._hooks[event])
        self._hooks[event] = [h for h in self._hooks[event] if h.name != name]
        if len(self._hooks[event]) < before:
            self._save()
            return True
        return False

    def update_hook(self, event: str, name: str, updates: dict) -> bool:
        """Update an existing hook's fields. Persists."""
        if event not in HOOK_EVENTS:
            return False
        for h in self._hooks[event]:
            if h.name == name:
                if "command" in updates:
                    h.command = updates["command"]
                if "timeout" in updates:
                    h.timeout = updates["timeout"]
                if "enabled" in updates:
                    h.enabled = updates["enabled"]
                if "matcher" in updates:
                    h.matcher = updates["matcher"]
                self._save()
                return True
        return False

    def _save(self) -> None:
        """Persist current hook config to .hooks.json."""
        config = {
            "version": 1,
            "hooks": {
                event: [
                    {
                        "name": h.name,
                        "command": h.command,
                        "timeout": h.timeout,
                        "enabled": h.enabled,
                        **( {"matcher": h.matcher} if h.matcher else {}),
                    }
                    for h in hooks
                ]
                for event, hooks in self._hooks.items()
            },
        }
        self.config_path.write_text(
            json.dumps(config, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._config_mtime = self.config_path.stat().st_mtime


def generate_default_config(workspace: Path) -> Path:
    """Write default .hooks.json if it doesn't exist. Returns the path."""
    config_path = workspace / ".hooks.json"
    if config_path.exists():
        return config_path

    python = _python_cmd()
    default = {
        "version": 1,
        "hooks": {
            "SessionStart": [
                {
                    "name": "system_log",
                    "command": f"{python} -m arf.hooks.system_log",
                    "timeout": 5,
                    "enabled": True,
                },
            ],
            "PreModelCall": [
                {
                    "name": "system_log",
                    "command": f"{python} -m arf.hooks.system_log",
                    "timeout": 5,
                    "enabled": True,
                },
            ],
            "PostModelCall": [
                {
                    "name": "system_log",
                    "command": f"{python} -m arf.hooks.system_log",
                    "timeout": 5,
                    "enabled": True,
                },
            ],
            "PreToolUse": [
                {
                    "name": "system_log",
                    "command": f"{python} -m arf.hooks.system_log",
                    "timeout": 5,
                    "enabled": True,
                },
            ],
            "PostToolUse": [
                {
                    "name": "system_log",
                    "command": f"{python} -m arf.hooks.system_log",
                    "timeout": 5,
                    "enabled": True,
                },
            ],
            "SessionEnd": [
                {
                    "name": "session_archiver",
                    "command": f"{python} -m arf.hooks.session_archiver",
                    "timeout": 30,
                    "enabled": True,
                },
                {
                    "name": "memory_extractor",
                    "command": f"{python} -m arf.hooks.memory_extractor",
                    "timeout": 120,
                    "enabled": True,
                },
                {
                    "name": "system_log",
                    "command": f"{python} -m arf.hooks.system_log",
                    "timeout": 5,
                    "enabled": True,
                },
            ],
        },
    }
    config_path.write_text(
        json.dumps(default, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("Generated default hook config at %s", config_path)
    return config_path
