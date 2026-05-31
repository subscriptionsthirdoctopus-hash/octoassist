"""Fernet encryption for secrets at rest (Entra client secrets, etc.).

Key lives in OCTOFLOW_ENCRYPTION_KEY env var. Generated once on first
boot if missing — the generated key is logged so the operator can copy
it into deploy/docker/.env for persistence across restarts. If the key
changes, all previously-encrypted secrets become unreadable.
"""
from __future__ import annotations

import logging
import os
from cryptography.fernet import Fernet, InvalidToken

log = logging.getLogger("octoflow.crypto")

_FERNET_PREFIX = "gAAAAA"  # All Fernet ciphertexts start with this
_KEY: bytes | None = None
_FERNET: Fernet | None = None


def _load() -> Fernet:
    global _KEY, _FERNET
    if _FERNET is not None:
        return _FERNET
    raw = os.environ.get("OCTOFLOW_ENCRYPTION_KEY", "").strip()
    if not raw:
        # Generate one on first boot. Operator should copy into .env so it
        # survives container restarts; otherwise encrypted secrets become
        # unreadable on every recreate.
        raw = Fernet.generate_key().decode("ascii")
        log.warning(
            "OCTOFLOW_ENCRYPTION_KEY not set — generated ephemeral key. "
            "Copy this into deploy/docker/.env to persist:  %s", raw,
        )
    _KEY = raw.encode("ascii") if isinstance(raw, str) else raw
    _FERNET = Fernet(_KEY)
    return _FERNET


def encrypt(plaintext: str | None) -> str | None:
    if not plaintext:
        return None
    return _load().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(ciphertext: str | None) -> str:
    if not ciphertext:
        return ""
    # If somebody stored plaintext (e.g. dev), pass it through.
    if not ciphertext.startswith(_FERNET_PREFIX):
        return ciphertext
    try:
        return _load().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken:
        log.error("Decryption failed — wrong OCTOFLOW_ENCRYPTION_KEY?")
        return ""


def is_encrypted(value: str | None) -> bool:
    return bool(value and value.startswith(_FERNET_PREFIX))
