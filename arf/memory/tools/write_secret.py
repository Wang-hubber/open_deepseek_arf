"""write_secret — encrypt and persist a secret value."""
import json
from arf.memory.secrets_store import SecretsStore

_store: SecretsStore | None = None


async def execute(name: str, note: str = "", content: str = "", **kwargs) -> dict:
    """Persist a secret value.

    Args:
        name: Variable name for the secret (e.g. "DB_PASSWORD").
        note: What this secret is used for.
        content: The secret value to store (password, token, key, etc.).
    """
    global _store
    if _store is None:
        return {"ok": False, "error": "DI failure: SecretsStore not wired. Set ARF_MASTER_KEY and ensure secrets.enabled=true in memory config."}
    _store.set(name, json.dumps({"v": content, "n": note}, ensure_ascii=False))
    return {"ok": True}
