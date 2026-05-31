"""Password hashing — direct bcrypt (cost 12). Bypasses passlib because
passlib 1.7.x ships an internal self-test that crashes against bcrypt 4.x.
"""
import bcrypt

_COST = 12


def hash_password(plain: str) -> str:
    if not isinstance(plain, str):
        raise TypeError("password must be str")
    # bcrypt only accepts ≤72 bytes — truncate (rather than crash) on the rare
    # absurd input. 72 bytes of entropy is well past the cost-12 threshold,
    # so this has no real security cost.
    pw = plain.encode("utf-8")[:72]
    return bcrypt.hashpw(pw, bcrypt.gensalt(rounds=_COST)).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    if not hashed or not isinstance(plain, str):
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8")[:72], hashed.encode("utf-8"))
    except Exception:
        return False
