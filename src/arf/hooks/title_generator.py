"""title_generator hook -- generates a short session title from the first exchange.

Triggered on SessionStart (first user message + first assistant response).

Usage:
    echo '{"messages": [...]}' | python -m arf.hooks.title_generator

Context:
    Environment: ARF_HOOK_WORKSPACE, ARF_HOOK_SESSION_ID
    Stdin: {"event": "SessionStart", "data": {"messages": [{"role":"user","content":"..."}, {"role":"assistant","content":"..."}]}}

Output:
    stdout: {"title": "生成的标题"}
    exit 0
"""

import json
import os
import sys
from pathlib import Path

import yaml

DEFAULT_TITLE = "新会话"
MAX_CONVERSATION_CHARS = 3000


def _load_model_config(workspace: Path) -> dict | None:
    """Find the first configured model in workspace/models/ and return its config."""
    models_dir = workspace / "models"
    if not models_dir.exists():
        return None

    # Try fast model types first
    priority = ("quick_no_thinking", "quick_thinking", "deep_thinking")
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

    # Fallback: any configured model
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


def _chat(model_cfg: dict, prompt: str) -> str | None:
    """Simple OpenAI-compatible chat completion."""
    import urllib.request
    import urllib.error

    base_url = model_cfg["base_url"].rstrip("/")
    api_key = model_cfg["api_key"]
    model_name = model_cfg["model_name"]
    temperature = model_cfg.get("temperature", 0.3)
    max_tokens = model_cfg.get("max_tokens", 256)

    body = {
        "model": model_name,
        "messages": [{"role": "user", "content": prompt}],
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
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"]
    except Exception:
        return None


def _format_messages(messages: list[dict]) -> str:
    lines = []
    total = 0
    for m in messages:
        role = m.get("role", "?")
        content = m.get("content", "")
        if not content:
            continue
        if len(content) > 500:
            content = content[:500] + "..."
        line = f"[{role}]: {content}"
        if total + len(line) > MAX_CONVERSATION_CHARS:
            lines.append("...")
            break
        lines.append(line)
        total += len(line)
    return "\n\n".join(lines)


def main():
    sys.stdin.reconfigure(encoding="utf-8")
    workspace = Path(os.environ.get("ARF_HOOK_WORKSPACE", "."))

    # Read stdin
    stdin_raw = sys.stdin.read()
    if not stdin_raw.strip():
        print(json.dumps({"title": DEFAULT_TITLE}, ensure_ascii=False))
        sys.exit(0)

    try:
        input_data = json.loads(stdin_raw)
        messages = input_data.get("data", {}).get("messages", [])
    except json.JSONDecodeError:
        print(json.dumps({"title": DEFAULT_TITLE}, ensure_ascii=False))
        sys.exit(0)

    if not messages or len(messages) < 2:
        print(json.dumps({"title": DEFAULT_TITLE}, ensure_ascii=False))
        sys.exit(0)

    # Load model config from workspace
    model_cfg = _load_model_config(workspace)
    if not model_cfg:
        # No model configured -- can't generate title
        print(json.dumps({"title": DEFAULT_TITLE}, ensure_ascii=False))
        sys.exit(0)

    conv_text = _format_messages(messages)
    prompt = (
        "Based on the conversation below, generate a short title "
        "(3-6 words, in the same language as the conversation) "
        "that summarizes what the user was doing or asking about. "
        "Return ONLY the title text, no quotes, no prefixes, no other text.\n\n"
        f"{conv_text}"
    )

    title = _chat(model_cfg, prompt)
    if title:
        title = title.strip().strip('"').strip("'").strip("。").strip()[:60]

    print(json.dumps({"title": title or DEFAULT_TITLE}, ensure_ascii=False))
    sys.exit(0)


if __name__ == "__main__":
    main()
