"""Memory extractor — subprocess entry point.

Usage: python extractor.py --session-file <json> --memory-dir <dir> --session-id <id>
"""
import argparse
import asyncio
import json
import os
import shutil
import sys
from pathlib import Path

MAX_MEMORY_SIZE = 300 * 1024  # 300KB


def load_prompt(template_path: Path) -> str:
    return template_path.read_text(encoding="utf-8")


def load_existing_memory(memory_file: Path) -> str:
    if not memory_file.exists():
        return ""
    return memory_file.read_text(encoding="utf-8")


def build_prompt(template: str, existing_memory: str, messages: list) -> str:
    recent = messages[-20:]  # last 20 messages for context
    msgs_text = "\n".join(
        f"[{m.get('role', '?')}] {str(m.get('content', ''))[:500]}"
        for m in recent
    )
    prompt = template.replace(
        "{{EXISTING_MEMORY}}", existing_memory or "(no existing memories)"
    )
    prompt += f"\n\n## Conversations\n\n{msgs_text}"
    return prompt


async def call_sysmodel(prompt: str) -> str:
    """Call system model (deepseek-v4-flash, thinking=false, temp=0.3)."""
    from arf.core.model_adapter import ModelAdapter

    runtime_raw = os.environ.get("ARF_RUNTIME", "{}")
    runtime = json.loads(runtime_raw) if runtime_raw else {}
    env_vars = runtime.get("env_vars", os.environ)

    api_key = env_vars.get("DEEPSEEK_API_KEY", "")
    api_base = env_vars.get("DEEPSEEK_API_BASE", "https://api.deepseek.com")

    # Resolve model config from runtime: system_model → model_configs
    system_model = runtime.get("system_model", "quick")
    model_configs = runtime.get("model_configs", {})
    model_cfg = model_configs.get(system_model, {})
    model_name = model_cfg.get("model", "deepseek-v4-flash")

    adapter = ModelAdapter({
        "base_url": api_base,
        "api_key": api_key or "sk-placeholder",
        "model_name": model_name,
        "temperature": 0.3,
        "thinking_enabled": False,
        "max_tokens": 4096,
    })
    msg = await adapter.chat_complete(
        [{"role": "user", "content": prompt}],
        tools=None,
        max_tokens=4096,
    )
    return msg.content or ""


def atomic_write(content: str, target: Path) -> bool:
    """Write content to target atomically with backup."""
    tmp = target.with_suffix(".md.tmp")
    bak = target.with_suffix(".md.bak")

    tmp.write_text(content, encoding="utf-8")

    if target.exists():
        shutil.copy2(target, bak)

    os.replace(tmp, target)

    if bak.exists():
        os.remove(bak)

    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-file", required=True)
    parser.add_argument("--memory-dir", required=True)
    parser.add_argument("--session-id", default="default")
    args = parser.parse_args()

    messages = json.loads(Path(args.session_file).read_text())
    memory_dir = Path(args.memory_dir)
    memory_file = memory_dir / "memory.md"
    template_path = Path(__file__).parent / "prompt.md"

    existing = load_existing_memory(memory_file)
    template = load_prompt(template_path)
    prompt = build_prompt(template, existing, messages)

    try:
        output = asyncio.run(call_sysmodel(prompt))
    except Exception as e:
        print(f"EXTRACTION_FAILED: {e}", file=sys.stderr)
        sys.exit(1)

    if output.strip() == "NO_NEW_MEMORY":
        print("NO_NEW_MEMORY — nothing to extract")
        sys.exit(0)

    output_bytes = output.encode("utf-8")
    if len(output_bytes) > MAX_MEMORY_SIZE:
        output_bytes = output_bytes[:MAX_MEMORY_SIZE]
        output = output_bytes.decode("utf-8", errors="replace")
        output += "\n\n<!-- WARNING: memory truncated at 300KB -->\n"

    atomic_write(output, memory_file)
    print(f"Memory written: {memory_file} ({len(output_bytes)} bytes)")

    Path(args.session_file).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
