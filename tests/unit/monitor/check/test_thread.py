# pylint: disable=missing-function-docstring, protected-access, redefined-outer-name

"""Tests for CheckThread.

The rise/fall debounce that flips a content id in the shared status set, and task() dispatch including the broad
except that counts a raising check as a fall. The held Check's execute() is mocked.
"""

from typing import Any

import pytest

from powergslb.monitor.check.tcp import TcpCheck
from powergslb.monitor.check.thread import CheckThread
from powergslb.monitor.status import StatusRegistry


def _check(**overrides: Any) -> TcpCheck:
    params: dict[str, Any] = {'interval': 10, 'timeout': 1, 'fall': 2, 'rise': 2, 'ip': '192.0.2.1', 'port': 80}
    params.update(overrides)
    return TcpCheck(**params)


def _thread(check: TcpCheck, content_id: int = 1, registry: Any = None) -> CheckThread:
    if registry is None:
        registry = StatusRegistry()
    status_writer = registry.get_writer(content_id)
    return CheckThread(check, status_writer, name=f'Check-{content_id}')


@pytest.fixture
def registry() -> StatusRegistry:
    return StatusRegistry()


# __init__

def test_sleep_interval_from_check_interval() -> None:
    assert _thread(_check(interval=42)).sleep_interval == 42


def test_content_id_exposed_from_writer() -> None:
    assert _thread(_check(), content_id=5).content_id == 5


# _check_fall / _check_rise debounce

def test_fall_marks_down_only_after_fall_threshold(registry: StatusRegistry) -> None:
    thread = _thread(_check(fall=2), content_id=5, registry=registry)
    thread._check_fall()
    assert not registry.is_down(5)  # one failure is below the threshold
    thread._check_fall()
    assert registry.is_down(5)  # second consecutive failure marks it down


def test_rise_clears_down_only_after_rise_threshold(registry: StatusRegistry) -> None:
    registry.add(5)
    thread = _thread(_check(rise=2), content_id=5, registry=registry)
    thread._check_rise()
    assert registry.is_down(5)  # one success is below the threshold
    thread._check_rise()
    assert not registry.is_down(5)  # second consecutive success clears it


def test_rise_resets_fall_counter(registry: StatusRegistry) -> None:
    thread = _thread(_check(fall=2), content_id=5, registry=registry)
    thread._check_fall()  # fall = 1
    thread._check_rise()  # resets fall to 0
    thread._check_fall()  # fall = 1 again, still below threshold
    assert not registry.is_down(5)


def test_fall_when_already_down_does_not_re_add(registry: StatusRegistry) -> None:
    registry.add(5)
    thread = _thread(_check(fall=1), content_id=5, registry=registry)
    thread._check_fall()  # already down: the add branch is skipped
    assert registry.is_down(5)


# task dispatch

def test_task_up_triggers_rise(registry: StatusRegistry, monkeypatch: pytest.MonkeyPatch) -> None:
    registry.add(1)
    thread = _thread(_check(rise=1), content_id=1, registry=registry)
    monkeypatch.setattr(thread.check, 'execute', lambda: True)
    thread.task()
    assert not registry.is_down(1)  # a passing check rose the status


def test_task_down_triggers_fall(registry: StatusRegistry, monkeypatch: pytest.MonkeyPatch) -> None:
    thread = _thread(_check(fall=1), content_id=1, registry=registry)
    monkeypatch.setattr(thread.check, 'execute', lambda: False)
    thread.task()
    assert registry.is_down(1)  # a failing check fell the status


def test_task_exception_counts_as_fall(registry: StatusRegistry, monkeypatch: pytest.MonkeyPatch) -> None:
    thread = _thread(_check(fall=1), content_id=1, registry=registry)

    def boom() -> bool:
        raise OSError('connection refused')

    monkeypatch.setattr(thread.check, 'execute', boom)
    thread.task()
    assert registry.is_down(1)  # a raising check is treated as a fall
