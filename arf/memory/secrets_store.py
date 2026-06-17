"""SecretsStore — XOR-encrypted key-value store for sensitive credentials."""
import json
import logging
from pathlib import Path

logger = logging.getLogger("arf.memory.secrets")


class SecretsStore:
    """XOR-based encrypted key-value store for local secrets.

    Key source: ARF_MASTER_KEY env var or agent.yaml master_key.
    File: {data_dir}/memory/secrets.enc
    """

    def __init__(self, data_dir: str, master_key: str) -> None:
        self._dir = Path(data_dir) / "memory"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._file = self._dir / "secrets.enc"
        self._key = master_key.encode()

    def load(self) -> dict[str, str]:
        """Decrypt and return all secrets. Returns {} if file absent."""
        if not self._file.exists():
            return {}
        try:
            raw = self._xor(self._file.read_bytes())
            return json.loads(raw)
        except Exception:
            logger.exception("Failed to decrypt secrets.enc")
            return {}

    def save(self, data: dict[str, str]) -> None:
        """Encrypt and persist *data*."""
        raw = json.dumps(data, ensure_ascii=False).encode()
        self._file.write_bytes(self._xor(raw))

    def get(self, name: str) -> str | None:
        return self.load().get(name)

    def set(self, name: str, value: str) -> None:
        data = self.load()
        data[name] = value
        self.save(data)

    def list_names(self) -> list[str]:
        return sorted(self.load().keys())

    def _xor(self, data: bytes) -> bytes:
        key = self._key
        return bytes(data[i] ^ key[i % len(key)] for i in range(len(data)))
