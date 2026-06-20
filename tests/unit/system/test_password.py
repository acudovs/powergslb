# pylint: disable=missing-function-docstring, protected-access

"""Tests for the Linux shadow crypt(3) SHA-512 password helpers.

Hashing produces the $6$ format with a random salt, and verification accepts the correct password while rejecting a
wrong one or an empty stored hash.
"""

from typing import Any

import legacycrypt as crypt

import powergslb.system.password as password_module
from powergslb.system.password import hash_password, verify_password


def test_hash_is_sha512_crypt_format() -> None:
    assert hash_password('secret').startswith('$6$')


def test_hash_uses_random_salt() -> None:
    assert hash_password('secret') != hash_password('secret')


def test_verify_accepts_correct_password() -> None:
    assert verify_password('secret', hash_password('secret')) is True


def test_verify_rejects_wrong_password() -> None:
    assert verify_password('wrong', hash_password('secret')) is False


def test_verify_rejects_empty_stored_hash() -> None:
    assert verify_password('secret', '') is False


def test_verify_rejects_malformed_stored_hash() -> None:
    assert verify_password('secret', '*4ACFE3202A5FF5CF467898FC58AAB1D615029441') is False
    assert verify_password('secret', '!') is False


def test_verify_returns_false_when_crypt_raises(monkeypatch: Any) -> None:
    def _raise(*_: Any) -> str:
        raise OSError('crypt failed')

    monkeypatch.setattr('powergslb.system.password.crypt.crypt', _raise)
    assert verify_password('secret', '$6$salt$hash') is False


def test_verify_runs_full_crypt_for_empty_stored_hash(monkeypatch: Any) -> None:
    # An empty stored hash still drives a real crypt against the dummy hash, so timing does not reveal the
    # absent hash. Verification rejects it regardless of what the dummy crypt returns.
    salts: list[str] = []
    real_crypt = crypt.crypt

    def spy(password: str, salt: str) -> str:
        salts.append(salt)
        return real_crypt(password, salt)

    monkeypatch.setattr('powergslb.system.password.crypt.crypt', spy)
    assert verify_password('secret', '') is False
    assert salts == [password_module._DUMMY_HASH]


def test_verify_rejects_empty_stored_even_for_dummy_password() -> None:
    # The dummy hash is hash_password(''); an empty stored hash must never authenticate, not even the empty password.
    assert verify_password('', '') is False
