import hashlib

import bcrypt


def _prehash(plain: str) -> bytes:
    """SHA-256 the input first so bcrypt's 72-byte input limit doesn't
    silently truncate long passwords / passphrases."""
    return hashlib.sha256(plain.encode("utf-8")).digest()


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(_prehash(plain), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(_prehash(plain), hashed.encode("utf-8"))
    except Exception:
        return False
