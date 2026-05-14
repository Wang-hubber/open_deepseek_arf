"""Core cryptographic operations.

Key derivation: PBKDF2-HMAC-SHA256
Encryption: AES-256-GCM
Password hashing: SHA-256 (for verification only -- the derived key is never stored)
"""

import base64
import hashlib
import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEY_LENGTH = 32  # AES-256
SALT_LENGTH = 16
IV_LENGTH = 12  # GCM nonce
DEFAULT_ITERATIONS = 600_000


def generate_salt() -> bytes:
    return os.urandom(SALT_LENGTH)


def derive_key(password: str, salt: bytes, iterations: int = DEFAULT_ITERATIONS) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=KEY_LENGTH,
        salt=salt,
        iterations=iterations,
    )
    return kdf.derive(password.encode("utf-8"))


def encrypt(plaintext: str, key: bytes) -> str:
    """Encrypt plaintext with AES-256-GCM. Returns base64(nonce + ciphertext)."""
    nonce = os.urandom(IV_LENGTH)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.b64encode(nonce + ciphertext).decode("ascii")


def decrypt(payload_b64: str, key: bytes) -> str:
    """Decrypt a base64(nonce + ciphertext) payload with AES-256-GCM."""
    raw = base64.b64decode(payload_b64)
    nonce, ciphertext = raw[:IV_LENGTH], raw[IV_LENGTH:]
    aesgcm = AESGCM(key)
    plaintext = aesgcm.decrypt(nonce, ciphertext, None)
    return plaintext.decode("utf-8")


def hash_password(password: str, salt: bytes) -> str:
    """Hash password for verification. Uses SHA-256 -- the output is NOT the encryption key."""
    h = hashlib.sha256()
    h.update(salt)
    h.update(password.encode("utf-8"))
    return h.hexdigest()


def verify_password(password: str, salt: bytes, stored_hash: str) -> bool:
    return hash_password(password, salt) == stored_hash
