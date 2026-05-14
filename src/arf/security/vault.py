"""Encrypted vault -- per-instance key management, encrypted JSON on disk.

Layout in workspace:
  .vault       -- encrypted JSON (AES-256-GCM)
  .vault_meta  -- plaintext metadata: {salt, iterations, password_hash, created_at}

The derived AES key never touches disk. Each SessionManager holds its own key.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from .crypto import (
    derive_key,
    encrypt,
    decrypt,
    hash_password,
    verify_password,
    generate_salt,
    DEFAULT_ITERATIONS,
)

logger = logging.getLogger(__name__)

VAULT_FILE = ".vault"
META_FILE = ".vault_meta"


def status(workspace_dir: str | Path) -> dict:
    ws = Path(workspace_dir)
    initialized = (ws / META_FILE).exists() and (ws / VAULT_FILE).exists()
    return {"initialized": initialized}


def init_vault(workspace_dir: str | Path, password: str) -> tuple[bytes, dict]:
    """Create a new vault. Returns (derived_key, empty_vault_data)."""
    ws = Path(workspace_dir)

    if (ws / VAULT_FILE).exists() or (ws / META_FILE).exists():
        raise FileExistsError(f"Vault already exists in {ws}")

    salt = generate_salt()
    key = derive_key(password, salt)
    pw_hash = hash_password(password, salt)

    meta = {
        "salt_b64": _b64(salt),
        "iterations": DEFAULT_ITERATIONS,
        "password_hash": pw_hash,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    empty_data = {"credentials": {}, "created_at": meta["created_at"]}

    ws.mkdir(parents=True, exist_ok=True)
    (ws / META_FILE).write_text(json.dumps(meta, indent=2))
    encrypted = encrypt(json.dumps(empty_data), key)
    (ws / VAULT_FILE).write_text(encrypted)

    logger.info("Vault initialized in %s", ws)
    return key, empty_data


def unlock_vault(workspace_dir: str | Path, password: str) -> tuple[bytes, dict]:
    """Unlock the vault. Returns (derived_key, vault_data)."""
    ws = Path(workspace_dir)

    meta_path = ws / META_FILE
    vault_path = ws / VAULT_FILE

    if not meta_path.exists() or not vault_path.exists():
        raise FileNotFoundError(f"No vault found in {ws}. Run init_vault first.")

    meta = json.loads(meta_path.read_text())
    salt = _from_b64(meta["salt_b64"])
    iterations = meta.get("iterations", DEFAULT_ITERATIONS)

    if not verify_password(password, salt, meta["password_hash"]):
        raise ValueError("Incorrect password")

    key = derive_key(password, salt, iterations)
    encrypted = vault_path.read_text()
    data = json.loads(decrypt(encrypted, key))

    logger.info("Vault unlocked")
    return key, data


def save_encrypted(ws: Path, key: bytes, data: dict) -> None:
    encrypted = encrypt(json.dumps(data), key)
    (ws / VAULT_FILE).write_text(encrypted)


def load_decrypted(ws: Path, key: bytes) -> dict:
    encrypted = (ws / VAULT_FILE).read_text()
    return json.loads(decrypt(encrypted, key))


def _b64(data: bytes) -> str:
    import base64
    return base64.b64encode(data).decode("ascii")


def _from_b64(s: str) -> bytes:
    import base64
    return base64.b64decode(s)
