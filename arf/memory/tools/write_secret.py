"""write_secret — encrypt and persist a secret value."""
from arf.memory.secrets_store import SecretsStore

_store: SecretsStore | None = None


async def execute(name: str, value: str, **kwargs) -> dict:
    global _store
    if _store is None:
        return {"ok": False, "error": "Secrets store not initialized"}
    _store.set(name, value)
    return {"ok": True}
