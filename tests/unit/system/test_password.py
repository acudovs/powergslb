# pylint: disable=missing-function-docstring

"""Tests for the Linux shadow crypt(3) SHA-512 password helpers.

Hashing produces the $6$ format with a random salt, and verification accepts the correct password while rejecting a
wrong one or an empty stored hash.
"""

from typing import Any

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
