"""
PyTaskForge – VaultGuard Encryption Layer
==========================================
Provides AES-256 symmetric encryption/decryption using the Fernet
algorithm from the ``cryptography`` package.

Key management:
  The encryption key is read from ``settings.VAULT_ENCRYPTION_KEY``.
  Generate a new key with:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

  Store the key securely (environment variable / secret manager).
  NEVER commit it to version control.

  WARNING: Losing the key means ALL stored secrets are permanently
  unrecoverable. Back it up.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Optional

logger = logging.getLogger(__name__)


class VaultNotConfiguredError(RuntimeError):
    """Raised when VAULT_ENCRYPTION_KEY is not set in the environment."""


@lru_cache(maxsize=1)
def _get_fernet():
    """Return a cached Fernet instance, loading the key from settings.

    Raises:
        VaultNotConfiguredError: VAULT_ENCRYPTION_KEY is not set.
    """
    from cryptography.fernet import Fernet, InvalidToken  # noqa: F401
    from backend.core.config import settings

    if not settings.VAULT_ENCRYPTION_KEY:
        raise VaultNotConfiguredError(
            "VAULT_ENCRYPTION_KEY is not configured. "
            "Generate one with: python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\" "
            "and set it as the VAULT_ENCRYPTION_KEY environment variable."
        )
    return Fernet(settings.VAULT_ENCRYPTION_KEY.encode())


def encrypt_secret(plaintext: str) -> str:
    """Encrypt *plaintext* using AES-256 Fernet and return a base64 ciphertext.

    Args:
        plaintext: The secret value to encrypt.

    Returns:
        URL-safe base64-encoded ciphertext string.

    Raises:
        VaultNotConfiguredError: VAULT_ENCRYPTION_KEY is not configured.
    """
    fernet = _get_fernet()
    return fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(ciphertext: str) -> str:
    """Decrypt a Fernet *ciphertext* and return the original plaintext.

    Args:
        ciphertext: URL-safe base64-encoded ciphertext produced by :func:`encrypt_secret`.

    Returns:
        Original plaintext string.

    Raises:
        VaultNotConfiguredError: VAULT_ENCRYPTION_KEY is not configured.
        cryptography.fernet.InvalidToken: The ciphertext is corrupt or the key is wrong.
    """
    fernet = _get_fernet()
    return fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")

