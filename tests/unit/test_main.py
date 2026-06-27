# pylint: disable=missing-function-docstring, redefined-outer-name

"""Tests for the PowerGSLB entry point.

PowerGSLB.main() parses -c, loads the config, configures logging, builds the monitor and the two server threads, and
hands them to SystemService.start(). Everything external is faked so nothing connects or serves.
"""

import sys
from typing import Any

import pytest

import powergslb.main
from powergslb.main import PowerGSLB
from powergslb.main import logging as main_logging


class _FakeConfig:
    def get(self, _section: str, option: str) -> str:
        return '%(message)s' if option == 'format' else 'DEBUG'

    def items(self, section: str) -> dict[str, str]:
        return {'section': section}


class _FakeService:
    last: '_FakeService | None' = None

    def __init__(self, service_threads: Any, *_a: Any, **_k: Any) -> None:
        self.service_threads = service_threads
        self.started = False
        _FakeService.last = self

    def start(self) -> None:
        self.started = True


@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    parsed: dict[str, Any] = {'config_path': None, 'threads': [], 'logging': {}}

    monkeypatch.setattr(sys, 'argv', ['powergslb', '-c', '/etc/powergslb/powergslb.toml'])
    monkeypatch.setattr(main_logging, 'basicConfig', lambda **kwargs: parsed['logging'].update(kwargs))

    def fake_config(path: str) -> _FakeConfig:
        parsed['config_path'] = path
        return _FakeConfig()

    monkeypatch.setattr(powergslb.main, 'Config', fake_config)
    monkeypatch.setattr(powergslb.main, 'SystemService', _FakeService)

    def fake_configure(geoip_config: Any) -> None:
        parsed['geoip'] = geoip_config

    def fake_monitor(_monitor_config: Any, _database_config: Any, _registry: Any, name: str) -> str:
        thread = f'monitor:{name}'
        parsed['threads'].append(thread)
        return thread

    def fake_server(config: Any, _database_config: Any, _registry: Any, handler: Any, name: str) -> str:
        thread = f'server:{name}:{config}:{handler.__name__}'
        parsed['threads'].append(thread)
        return thread

    monkeypatch.setattr(powergslb.main.ViewRule, 'configure', staticmethod(fake_configure))
    monkeypatch.setattr(powergslb.main, 'MonitorManager', fake_monitor)
    monkeypatch.setattr(powergslb.main, 'ServerManager', fake_server)
    _FakeService.last = None
    return parsed


def test_main_wires_threads_and_starts_service(patched: dict[str, Any]) -> None:
    PowerGSLB.main()

    assert patched['config_path'] == '/etc/powergslb/powergslb.toml'
    assert patched['geoip'] == {'section': 'geoip'}  # the [geoip] config section is passed to ViewRule.configure
    # one monitor plus the admin and server HTTP threads, in that order, each wired to its role handler
    assert patched['threads'] == [
        'monitor:Monitor',
        "server:Admin:{'section': 'admin'}:AdminRequestHandler",
        "server:Server:{'section': 'server'}:PowerDNSRequestHandler",
    ]
    assert _FakeService.last is not None
    assert _FakeService.last.started is True
    assert _FakeService.last.service_threads == patched['threads']


def test_main_passes_level_name_to_basic_config(patched: dict[str, Any]) -> None:
    # The configured level name is handed to basicConfig as-is; logging resolves it, so no getLevelName round-trip.
    PowerGSLB.main()
    assert patched['logging']['level'] == 'DEBUG'
    assert patched['logging']['format'] == '%(message)s'
