# pylint: disable=missing-function-docstring

"""Tests for TlsCheck.execute() with socket.create_connection and ssl mocked."""

import ssl
from typing import Any

import pytest

from powergslb.monitor.check import tls as tls_module
from powergslb.monitor.check.tls import TlsCheck


class _Socket:
    """Stand-in for the socket returned by create_connection; records its close."""

    def __init__(self, address: tuple[str, int], closed: list[tuple[str, int]]) -> None:
        self._address = address
        self._closed = closed

    def __enter__(self) -> '_Socket':
        return self

    def __exit__(self, *_exc: object) -> None:
        self._closed.append(self._address)


class _TlsSocket:
    """Stand-in for the wrapped TLS socket; a no-op context manager."""

    def __enter__(self) -> '_TlsSocket':
        return self

    def __exit__(self, *_exc: object) -> None:
        pass


class _Context:
    """Minimal ssl context capturing verification settings and wrap_socket kwargs."""

    def __init__(self) -> None:
        self.check_hostname = True
        self.verify_mode = ssl.CERT_REQUIRED
        self.wrap_kwargs: dict[str, Any] = {}

    def wrap_socket(self, _sock: Any, **kwargs: Any) -> _TlsSocket:
        self.wrap_kwargs = kwargs
        return _TlsSocket()


class _SSLErrorContext(_Context):
    """A context whose handshake fails with ssl.SSLError."""

    def wrap_socket(self, _sock: Any, **kwargs: Any) -> _TlsSocket:
        self.wrap_kwargs = kwargs
        raise ssl.SSLError('handshake failed')


def _check(**overrides: Any) -> TlsCheck:
    params: dict[str, Any] = {'interval': 10, 'timeout': 1, 'fall': 2, 'rise': 2, 'ip': '192.0.2.1', 'port': 443}
    params.update(overrides)
    return TlsCheck(**params)


def _patch(monkeypatch: pytest.MonkeyPatch, context: _Context) -> list[tuple[str, int]]:
    connected: list[tuple[str, int]] = []
    closed: list[tuple[str, int]] = []

    def create_connection(address: tuple[str, int], _timeout: float) -> _Socket:
        connected.append(address)
        return _Socket(address, closed)

    monkeypatch.setattr(tls_module.socket, 'create_connection', create_connection)
    monkeypatch.setattr(tls_module.ssl, 'create_default_context', lambda: context)
    return closed


def test_handshake_is_healthy(monkeypatch: pytest.MonkeyPatch) -> None:
    context = _Context()
    closed = _patch(monkeypatch, context)
    assert _check().execute() is True
    assert closed == [('192.0.2.1', 443)]  # the socket is closed on block exit


def test_connects_to_ip_and_port(monkeypatch: pytest.MonkeyPatch) -> None:
    context = _Context()
    connected: list[tuple[str, int]] = []

    def create_connection(address: tuple[str, int], _timeout: float) -> _Socket:
        connected.append(address)
        return _Socket(address, [])

    monkeypatch.setattr(tls_module.socket, 'create_connection', create_connection)
    monkeypatch.setattr(tls_module.ssl, 'create_default_context', lambda: context)
    _check(ip='198.51.100.7', port=8443).execute()
    assert connected == [('198.51.100.7', 8443)]


def test_tls_verify_true_keeps_verification(monkeypatch: pytest.MonkeyPatch) -> None:
    context = _Context()
    _patch(monkeypatch, context)
    _check().execute()
    assert context.check_hostname is True
    assert context.verify_mode is ssl.CERT_REQUIRED


def test_tls_verify_false_disables_verification(monkeypatch: pytest.MonkeyPatch) -> None:
    context = _Context()
    _patch(monkeypatch, context)
    _check(tls_verify=False).execute()
    assert context.check_hostname is False
    assert context.verify_mode is ssl.CERT_NONE


def test_server_hostname_defaults_to_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    context = _Context()
    _patch(monkeypatch, context)
    _check().execute()
    assert context.wrap_kwargs['server_hostname'] == '192.0.2.1'


def test_host_overrides_server_hostname(monkeypatch: pytest.MonkeyPatch) -> None:
    context = _Context()
    _patch(monkeypatch, context)
    _check(host='alt.example.com').execute()
    assert context.wrap_kwargs['server_hostname'] == 'alt.example.com'


def test_handshake_failure_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    # A failed handshake raises ssl.SSLError; CheckThread.task() catches it and debounces as a failure.
    context = _SSLErrorContext()
    _patch(monkeypatch, context)
    with pytest.raises(ssl.SSLError):
        _check().execute()
