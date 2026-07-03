"""Admin password hashing and constant-time verification."""

import hmac

import legacycrypt as crypt

__all__ = ['hash_password', 'verify_password']


def hash_password(password: str) -> str:
    """Hash a password in Linux shadow crypt(3) SHA-512 ($6$) format with a random salt.

    :param password: The plaintext password to hash.
    :returns: The $6$ hash string.
    """
    return crypt.crypt(password, crypt.mksalt(crypt.METHOD_SHA512))


# A valid $6$ hash used whenever the stored hash is empty (an unknown user), so verification still runs a
# full crypt and compare_digest. The result is forced to False, so this dummy never authenticates anyone.
_DUMMY_HASH = hash_password('')


def verify_password(password: str, stored: str) -> bool:
    """Verify a password against a stored crypt(3) hash in constant time.

    An empty stored hash (an unknown user) never matches, yet verification still performs a full crypt and
    comparison against a dummy hash, so the response time does not reveal that the hash was absent. A malformed
    stored hash is rejected outright.

    :param password: The plaintext password to verify.
    :param stored: The stored crypt(3) hash; empty for an unknown user.
    :returns: True when the password matches the stored hash.
    """
    accept = bool(stored)
    salt = stored if accept else _DUMMY_HASH
    try:
        computed = crypt.crypt(password, salt)
    except (TypeError, ValueError, OSError):
        return False
    matched = bool(computed) and hmac.compare_digest(computed, salt)
    return accept and matched
