"""read_secret — decrypt and return a secret value."""
import json
from arf.memory.secrets_store import SecretsStore

_store: SecretsStore | None = None


async def execute(name: str, **kwargs) -> dict:
    global _store
    if _store is None:
        return {"ok": False, "error": "DI failure: SecretsStore not wired. Set ARF_MASTER_KEY and ensure secrets.enabled=true in memory config."}
    raw = _store.get(name)
    if raw is None:
        return {"ok": False, "error": f"secret '{name}' not found"}
    try:
        data = json.loads(raw)
        return {"ok": True, "value": data.get("v", raw), "note": data.get("n", "")}
    except (json.JSONDecodeError, TypeError):
        return {"ok": True, "value": raw, "note": ""}
