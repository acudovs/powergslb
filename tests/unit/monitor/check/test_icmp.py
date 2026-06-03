# pylint: disable=missing-function-docstring

"""Tests for IcmpCheck.execute() with icmplib.ping and the privileged ClassVar."""

from typing import Any

import pytest
from icmplib import NameLookupError

from powergslb.monitor.check import icmp as icmp_module
from powergslb.monitor.check.icmp import IcmpCheck


def _check() -> IcmpCheck:
    return IcmpCheck(interval=10, timeout=1, fall=2, rise=2, ip='192.0.2.1')


def test_alive_is_healthy(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_ping(ip: str, **kwargs: Any) -> Any:
        captured['ip'] = ip
        captured.update(kwargs)
        return type('Host', (), {'is_alive': True})()

    monkeypatch.setattr(icmp_module, 'ping', fake_ping)
    monkeypatch.setattr(IcmpCheck, 'privileged', True)
    assert _check().execute() is True
    assert captured['ip'] == '192.0.2.1'
    assert captured['privileged'] is True


def test_unprivileged_from_config(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_ping(_ip: str, **kwargs: Any) -> Any:
        captured.update(kwargs)
        return type('Host', (), {'is_alive': False})()

    monkeypatch.setattr(icmp_module, 'ping', fake_ping)
    monkeypatch.setattr(IcmpCheck, 'privileged', False)
    assert _check().execute() is False
    assert captured['privileged'] is False


def test_unknown_host_raises_runtimeerror(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_ping(*_a: Any, **_k: Any) -> Any:
        raise NameLookupError('no such host')

    monkeypatch.setattr(icmp_module, 'ping', fake_ping)
    with pytest.raises(NameLookupError, match="The name 'no such host' cannot be resolved"):
        _check().execute()
