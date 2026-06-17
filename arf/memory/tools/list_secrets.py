"""list_secrets — list all secret names (no values exposed)."""
from arf.memory.secrets_store import SecretsStore

_store: SecretsStore | None = None


async def execute(**kwargs) -> dict:
    global _store
    if _store is None:
        return {"ok": False, "error": "DI failure: SecretsStore not wired. Set ARF_MASTER_KEY and ensure secrets.enabled=true in memory config."}
    return {"ok": True, "names": _store.list_names()}
