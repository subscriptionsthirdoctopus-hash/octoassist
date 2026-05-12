"""At-rest encryption for tenant secrets (SMTP password, Graph client secret).

Uses Fernet (AES-128-CBC + HMAC-SHA256) from cryptography. Master key:
  1. OCTOASSIST_ENCRYPTION_KEY env var (preferred — set this in production)
  2. /srv/octoassist/.encryption.key file (auto-generated on first start)

Key rotation is NOT in scope for this build. Losing the master key means
losing the ability to decrypt existing encrypted columns; the system will
log a clear warning at startup and surface decrypt failures as empty
strings (so the UI degrades gracefully rather than 500'ing).

To migrate existing plaintext rows transparently, every encrypt() call
detects already-encrypted ciphertexts (Fernet token format) and is a
no-op for them; every decrypt() call detects plaintext input (no Fernet
token) and returns it unchanged. This means deploying the encryption
layer over an existing database doesn't require a migration step — rows
are upgraded on next write.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

log = logging.getLogger("octoassist.crypto")

_KEY_FILE_CANDIDATES = (
    Path("/srv/octoassist/.encryption.key"),
    Path("/var/lib/octoassist/encryption.key"),
)


def _resolve_key() -> bytes:
    env = os.environ.get("OCTOASSIST_ENCRYPTION_KEY", "").strip()
    if env:
        return env.encode("ascii")

    for p in _KEY_FILE_CANDIDATES:
        if p.exists():
            try:
                return p.read_text().strip().encode("ascii")
            except Exception as e:
                log.warning("encryption key file %s unreadable: %s", p, e)

    # Auto-generate
    new_key = Fernet.generate_key()
    for p in _KEY_FILE_CANDIDATES:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(new_key)
            try:
                os.chmod(p, 0o600)
            except Exception:
                pass
            log.warning(
                "OCTOASSIST_ENCRYPTION_KEY not set — generated a new master key at %s. "
                "BACK THIS FILE UP. Losing it means losing decryption of all encrypted secrets. "
                "For production, set the OCTOASSIST_ENCRYPTION_KEY env var explicitly.",
                p,
            )
            return new_key
        except Exception as e:
            log.warning("could not write key to %s: %s", p, e)

    # Last-resort: in-memory only. Encrypted data won't survive process restart.
    log.error(
        "OCTOASSIST_ENCRYPTION_KEY not set and no writable key path. "
        "Using ephemeral key — all encrypted data will be unrecoverable after restart."
    )
    return new_key


_KEY: bytes | None = None
_FERNET: Fernet | None = None


def _fernet() -> Fernet:
    global _KEY, _FERNET
    if _FERNET is None:
        _KEY = _resolve_key()
        _FERNET = Fernet(_KEY)
    return _FERNET


# Fernet tokens always start with "gAAAAA" after URL-safe base64 of the version
# byte 0x80. Use that as a cheap check to detect already-encrypted input.
_FERNET_PREFIX = "gAAAAA"


def looks_encrypted(value: str | None) -> bool:
    if not value:
        return False
    return value.startswith(_FERNET_PREFIX)


def encrypt(plaintext: str | None) -> str | None:
    """Encrypt plaintext. If input is already a Fernet token, return as-is.

    Returns None for None / empty input so the calling code can store NULL
    rather than an empty encrypted blob.
    """
    if plaintext is None:
        return None
    s = plaintext.strip()
    if not s:
        return None
    if looks_encrypted(s):
        return s
    return _fernet().encrypt(s.encode("utf-8")).decode("ascii")


def decrypt(ciphertext: str | None) -> str:
    """Decrypt a Fernet token. If input doesn't look encrypted, return as-is
    (legacy plaintext support). On token corruption or wrong key, return ""
    so the caller can fail loudly with "auth failed" rather than 500.
    """
    if not ciphertext:
        return ""
    if not looks_encrypted(ciphertext):
        return ciphertext  # legacy plaintext
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken:
        log.error("decrypt failed — InvalidToken (key rotated or data corrupted)")
        return ""
    except Exception as e:  # noqa: BLE001
        log.exception("decrypt failed: %s", e)
        return ""
