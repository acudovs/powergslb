# pylint: disable=missing-function-docstring, protected-access, redefined-outer-name

"""Tests for SystemService thread supervision and graceful shutdown.

The watchdog loop runs while all service threads are alive and no stop was requested. It exits and the process exits
non-zero when a thread dies (so systemd restarts it on-failure), or zero when SIGTERM/SIGINT requested the stop. Either
way every thread is shut down. Threads are faked and signal.signal is monkeypatched so the suite never starts real
threads or mutates the process signal disposition.
"""

import logging
import signal
from typing import Any

import pytest
import systemd.daemon

from powergslb.system.service import SystemService


class _FakeThread:
    """Record start()/shutdown() and expose a controllable is_alive()."""

    def __init__(self, name: str, alive: bool = True) -> None:
        self.name = name
        self._alive = alive
        self.started = False
        self.shutdown_timeouts: list[float] = []

    def start(self) -> None:
        self.started = True

    def is_alive(self) -> bool:
        return self._alive

    def shutdown(self, timeout: float = 0) -> None:
        self.shutdown_timeouts.append(timeout)
        self._alive = False


@pytest.fixture
def no_signals(monkeypatch: pytest.MonkeyPatch) -> dict[int, Any]:
    """Capture handler registrations instead of touching the real process signal disposition."""
    registered: dict[int, Any] = {}

    def record(signum: int, handler: Any) -> None:
        registered[signum] = handler

    monkeypatch.setattr(signal, 'signal', record)
    return registered


@pytest.fixture
def silent_notify(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(SystemService, 'systemd_notify', staticmethod(lambda *a, **k: None))


# __init__

def test_init_sets_shutdown_defaults() -> None:
    service = SystemService([], default_interval=0.01)
    assert service.shutdown_timeout == 3.0
    assert service._signum is None
    assert not service._shutdown.is_set()


# _on_signal

def test_on_signal_records_and_sets_event() -> None:
    service = SystemService([], default_interval=0.01)
    service._on_signal(signal.SIGINT, None)
    assert service._signum == signal.SIGINT
    assert service._shutdown.is_set()


# start: signal-requested stop

@pytest.mark.usefixtures('silent_notify')
def test_start_exits_zero_on_signal_and_stops_all(
        monkeypatch: pytest.MonkeyPatch, no_signals: dict[int, Any]) -> None:
    alive = _FakeThread('Alive')
    admin = _FakeThread('Admin')
    service = SystemService([alive, admin], default_interval=0.01)

    # a SIGTERM lands while the loop is parked in the first wait()
    def fake_wait(_timeout: float | None = None) -> bool:
        service._on_signal(signal.SIGTERM, None)
        return True

    monkeypatch.setattr(service._shutdown, 'wait', fake_wait)

    with pytest.raises(SystemExit) as exc_info:
        service.start()

    assert exc_info.value.code == 0  # requested stop: clean, no restart
    # start() wired _on_signal to both signals
    assert no_signals[signal.SIGTERM].__func__ is SystemService._on_signal
    assert no_signals[signal.SIGINT].__func__ is SystemService._on_signal
    assert alive.started and admin.started
    # every thread is shut down with the configured timeout
    assert alive.shutdown_timeouts == [3.0]
    assert admin.shutdown_timeouts == [3.0]


# start: a thread dies

@pytest.mark.usefixtures('no_signals', 'silent_notify')
def test_start_exits_nonzero_when_thread_dies(caplog: pytest.LogCaptureFixture) -> None:
    alive = _FakeThread('Alive')
    dying = _FakeThread('Dying', alive=False)
    service = SystemService([alive, dying], default_interval=0.01)

    with caplog.at_level(logging.ERROR):
        with pytest.raises(SystemExit) as exc_info:
            service.start()

    assert exc_info.value.code == 1  # a thread died: Restart=on-failure
    # the log names only the dead thread, not the sibling stopped during shutdown
    assert 'Dying' in caplog.text
    assert 'Alive' not in caplog.text
    # both are still shut down on the way out
    assert alive.shutdown_timeouts == [3.0]
    assert dying.shutdown_timeouts == [3.0]


@pytest.mark.usefixtures('no_signals', 'silent_notify')
def test_start_stays_in_loop_while_all_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    alive = _FakeThread('Alive')
    service = SystemService([alive], default_interval=0.01)

    # the loop keeps waiting while alive and not stopped; let it iterate a few times, then a thread dies
    waits = 0

    def fake_wait(_timeout: float | None = None) -> bool:
        nonlocal waits
        waits += 1
        if waits >= 3:
            alive._alive = False  # thread dies, breaking the loop on the next condition check
        return False

    monkeypatch.setattr(service._shutdown, 'wait', fake_wait)

    with pytest.raises(SystemExit) as exc_info:
        service.start()

    assert exc_info.value.code == 1
    assert waits == 3  # looped while alive, exited once the thread died


# systemd_notify: only forwards to the socket when the process was booted by systemd

def test_systemd_notify_when_booted(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(systemd.daemon, 'booted', lambda: True)
    monkeypatch.setattr(systemd.daemon, 'notify', lambda status, unset: calls.append((status, unset)))

    SystemService.systemd_notify('READY=1')
    assert calls == [('READY=1', False)]


def test_systemd_notify_when_not_booted(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(systemd.daemon, 'booted', lambda: False)
    monkeypatch.setattr(systemd.daemon, 'notify', lambda *a, **k: calls.append(a))

    SystemService.systemd_notify('READY=1', unset_environment=True)
    assert not calls


# watchdog_interval: derives from WATCHDOG_USEC (half the period), else the default

def test_watchdog_interval_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('WATCHDOG_USEC', raising=False)
    assert SystemService.watchdog_interval(1.0) == 1.0


def test_watchdog_interval_uses_half_of_watchdog_usec(monkeypatch: pytest.MonkeyPatch) -> None:
    # 3_000_000 us = 3 s period -> ping at half = 1.5 s, which is below the 10 s default
    monkeypatch.setenv('WATCHDOG_USEC', '3000000')
    assert SystemService.watchdog_interval(10.0) == 1.5


def test_watchdog_interval_caps_at_default(monkeypatch: pytest.MonkeyPatch) -> None:
    # half the watchdog period (5 s) exceeds the smaller default, so the default wins
    monkeypatch.setenv('WATCHDOG_USEC', '10000000')
    assert SystemService.watchdog_interval(1.0) == 1.0


def test_init_sets_sleep_interval_from_watchdog(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('WATCHDOG_USEC', '4000000')
    service = SystemService([], default_interval=10.0)
    assert service.sleep_interval == 2.0
