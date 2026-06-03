# pylint: disable=missing-function-docstring

"""Tests for TcpCheck.execute() with socket.create_connection mocked."""

import pytest

from powergslb.monitor.check import tcp as tcp_module
from powergslb.monitor.check.tcp import TcpCheck


def _check() -> TcpCheck:
    return TcpCheck(interval=10, timeout=1, fall=2, rise=2, ip='192.0.2.1', port=80)


def test_connect_is_healthy(monkeypatch: pytest.MonkeyPatch) -> None:
    closed: list[tuple[str, int]] = []

    class _Socket:
        def __init__(self, address: tuple[str, int]) -> None:
            self._address = address

        def __enter__(self) -> '_Socket':
            return self

        def __exit__(self, *_exc: object) -> None:
            closed.append(self._address)  # the context manager closes the socket on block exit

    monkeypatch.setattr(tcp_module.socket, 'create_connection', lambda address, timeout: _Socket(address))
    assert _check().execute() is True
    assert closed == [('192.0.2.1', 80)]
