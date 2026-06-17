# arf/memory/secrets_store.py
"""SecretsStore — Fernet-encrypted key-value store for sensitive credentials."""
import json
import logging
from pathlib import Path

logger = logging.getLogger("arf.memory.secrets")


class SecretsStore:
    """AES-128-CBC + HMAC encrypted key-value store.

    Key source: ARF_MASTER_KEY env var or master_key config field.
    File: {data_dir}/memory/secrets.enc
    """

    def __init__(self, data_dir: str, key: bytes) -> None:
        from cryptography.fernet import Fernet
        self._dir = Path(data_dir) / "memory"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._file = self._dir / "secrets.enc"
        self._fernet = Fernet(key)

    def load(self) -> dict[str, str]:
        """Decrypt and return all secrets. Returns {} if file absent."""
        if not self._file.exists():
            return {}
        try:
            raw = self._fernet.decrypt(self._file.read_bytes())
            return json.loads(raw)
        except Exception:
            logger.exception("Failed to decrypt secrets.enc")
            return {}

    def save(self, data: dict[str, str]) -> None:
        """Encrypt and persist *data*."""
        encrypted = self._fernet.encrypt(
            json.dumps(data, ensure_ascii=False).encode())
        self._file.write_bytes(encrypted)

    def get(self, name: str) -> str | None:
        return self.load().get(name)

    def set(self, name: str, value: str) -> None:
        data = self.load()
        data[name] = value
        self.save(data)

    def list_names(self) -> list[str]:
        return sorted(self.load().keys())

    @staticmethod
    def derive_key(master_key: str) -> bytes:
        """Derive a Fernet-compatible key from a user-provided master key."""
        import base64
        import hashlib
        digest = hashlib.sha256(master_key.encode()).digest()
        return base64.urlsafe_b64encode(digest)
