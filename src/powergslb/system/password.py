"""Admin password hashing and constant-time verification."""

import hmac

import legacycrypt as crypt

__all__ = ['hash_password', 'verify_password']


def hash_password(password: str) -> str:
    """Hash a password in Linux shadow crypt(3) SHA-512 ($6$) format with a random salt."""
    return crypt.crypt(password, crypt.mksalt(crypt.METHOD_SHA512))


def verify_password(password: str, stored: str) -> bool:
    """Verify a password against a stored crypt(3) hash in constant time; an empty or malformed hash never matches."""
    if not stored:
        return False
    try:
        computed = crypt.crypt(password, stored)
    except (TypeError, ValueError, OSError):
        return False
    return bool(computed) and hmac.compare_digest(computed, stored)
