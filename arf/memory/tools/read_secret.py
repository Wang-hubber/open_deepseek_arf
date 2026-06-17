"""read_secret — decrypt and return a secret value."""
from arf.memory.secrets_store import SecretsStore

_store: SecretsStore | None = None  # set by BaseAgent at init


async def execute(name: str, **kwargs) -> dict:
    global _store
    if _store is None:
        return {"ok": False, "error": "DI failure: SecretsStore not wired. Set ARF_MASTER_KEY and ensure secrets.enabled=true in memory config."}
    val = _store.get(name)
    if val is None:
        return {"ok": False, "error": f"secret '{name}' not found"}
    return {"ok": True, "value": val}
