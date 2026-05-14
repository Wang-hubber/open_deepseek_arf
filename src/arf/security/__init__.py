"""ARF Security Framework -- password-based encryption for sensitive data."""

from .vault import (
    init_vault,
    unlock_vault,
    save_encrypted,
    load_decrypted,
    status,
)

__all__ = [
    "init_vault",
    "unlock_vault",
    "save_encrypted",
    "load_decrypted",
    "status",
]
