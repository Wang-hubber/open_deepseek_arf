"""memory_extractor hook -- extracts long-term memories from ended session.

Triggered on SessionEnd. Reads current memory_store, compares with
conversation, and writes merged memories.

Usage:
    echo '{"conversation": [...]}' | python -m arf.hooks.memory_extractor

Context:
    Environment: ARF_HOOK_WORKSPACE, ARF_HOOK_SESSION_ID
    Stdin: {"event": "SessionEnd", "data": {"conversation": [...], "message_count": N}}

Output:
    stdout: {"extracted": true, "entries": N}
    exit 0 (always non-blocking)
"""

import json
import os
import sys
from pathlib import Path

import yaml

MAX_CONVERSATION_CHARS = 20000
EXTRACTION_TIMEOUT = 120


def _load_model_config(workspace: Path) -> dict | None:
    """Find a configured model."""
    models_dir = workspace / "models"
    if not models_dir.exists():
        return None

    priority = ("quick_thinking", "deep_thinking", "quick_no_thinking")
    for mt in priority:
        for model_dir in sorted(models_dir.iterdir()):
            if not model_dir.is_dir():
                continue
            cfg_path = model_dir / "config.yaml"
            if not cfg_path.exists():
                continue
            try:
                cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            except Exception:
                continue
            model_cfg = cfg.get("config", cfg)
            if model_cfg.get("base_url") and model_cfg.get("api_key") and model_cfg.get("model_name"):
                return model_cfg

    return None


def _chat(model_cfg: dict, messages: list[dict]) -> str | None:
    """OpenAI-compatible chat completion with tool support."""
    import urllib.request
    import urllib.error

    base_url = model_cfg["base_url"].rstrip("/")
    api_key = model_cfg["api_key"]
    model_name = model_cfg["model_name"]
    temperature = model_cfg.get("temperature", 0.7)
    max_tokens = model_cfg.get("max_tokens", 4096)

    body = {
        "model": model_name,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    req = urllib.request.Request(
        f"{base_url}/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=EXTRACTION_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"]
    except Exception:
        return None


def _format_conversation(history: list[dict]) -> str:
    lines = []
    total = 0
    for m in history:
        role = m.get("role", "?")
        content = m.get("content", "")
        if not content:
            tc_id = m.get("tool_call_id", "")
            result = str(m.get("content_short", ""))[:200]
            line = f"[{role} / {tc_id}]: {result}" if tc_id else f"[{role}]: (no content)"
        else:
            if len(content) > 1000:
                content = content[:1000] + "... (truncated)"
            line = f"[{role}]: {content}"
        if total + len(line) > MAX_CONVERSATION_CHARS:
            lines.append("... (conversation truncated)")
            break
        lines.append(line)
        total += len(line)
    return "\n\n".join(lines)


def main():
    workspace = Path(os.environ.get("ARF_HOOK_WORKSPACE", "."))

    stdin_raw = sys.stdin.read()
    try:
        input_data = json.loads(stdin_raw) if stdin_raw.strip() else {}
    except json.JSONDecodeError:
        input_data = {}

    conversation = input_data.get("data", {}).get("conversation", [])
    if not conversation or len(conversation) < 2:
        print(json.dumps({"extracted": False, "reason": "too few messages"}))
        sys.exit(0)

    model_cfg = _load_model_config(workspace)
    if not model_cfg:
        print(json.dumps({"extracted": False, "reason": "no model configured"}))
        sys.exit(0)

    conv_text = _format_conversation(conversation)
    prompt = (
        "The conversation session has ended. Review the conversation below "
        "and extract any information worth storing in long-term memory.\n\n"
        "1. Identify NEW user preferences, facts, decisions, or explicit "
        "requests to remember.\n"
        "2. If nothing new: respond with 'No new information to store.'\n"
        "3. Format each memory as a brief, self-contained statement.\n\n"
        "Be selective. Only store what will be useful in future conversations.\n\n"
        "## Conversation\n\n"
        f"{conv_text}"
    )

    result = _chat(model_cfg, [{"role": "user", "content": prompt}])
    entries = 0
    if result and "No new information" not in result:
        # Store extracted memories
        memory_dir = workspace / "memory"
        memory_dir.mkdir(parents=True, exist_ok=True)
        memory_path = memory_dir / "extracted_memories.md"

        timestamp = input_data.get("data", {}).get("session_start", "")
        entry = f"\n## Session {timestamp}\n\n{result.strip()}\n"

        try:
            with open(memory_path, "a", encoding="utf-8") as f:
                f.write(entry)
            entries = len(result.strip().split("\n"))
        except Exception:
            pass

    print(json.dumps({
        "extracted": True,
        "entries": entries if result else 0,
    }, ensure_ascii=False))
    sys.exit(0)


if __name__ == "__main__":
    main()
