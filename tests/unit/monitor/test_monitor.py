# pylint: disable=missing-function-docstring, redefined-outer-name, protected-access

"""Tests for MonitorManager.

Check parsing/building, desired-check construction, the reconcile loop, stop/start thread helpers, status cleanup,
and the task() orchestration. External effects (database, CheckThread, status set, time.sleep) are mocked so nothing
connects, spawns, or blocks.
"""

from typing import Any

import pytest

from powergslb.monitor import monitor as monitor_module
from powergslb.monitor.monitor import MonitorManager
from powergslb.monitor.status import StatusRegistry


@pytest.fixture
def status_registry() -> StatusRegistry:
    return StatusRegistry()


@pytest.fixture
def monitor(status_registry: StatusRegistry) -> MonitorManager:
    return MonitorManager({'update_interval': 60}, {}, status_registry, name='Monitor')


def _tcp_json() -> str:
    return ('{"type": "tcp", "ip": "${content}", "port": 80, '
            '"interval": 10, "timeout": 1, "fall": 2, "rise": 2}')


class _FakeCheck:
    def __init__(self, value: Any = None, timeout: float = 0) -> None:
        self.value = value
        self.timeout = timeout

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _FakeCheck) and self.value == other.value

    def __ne__(self, other: object) -> bool:
        return not self.__eq__(other)

    def __hash__(self) -> int:
        return hash(self.value)


class _FakeCheckThread:
    def __init__(self, alive: Any = True, sleep_interval: float = 0, content_id: int = 1,
                 check: Any = None, timeout: float = 0) -> None:
        self._alive = alive
        self.sleep_interval = sleep_interval
        self.check = check if check is not None else _FakeCheck(timeout=timeout)
        self.content_id = content_id
        self.shutdown_called = False
        self.shutdown_timeouts: list[float] = []
        self.started = False
        self.thread_name = ''

    def is_alive(self) -> bool:
        if isinstance(self._alive, list):
            return self._alive.pop(0) if self._alive else False
        return self._alive

    def shutdown(self, timeout: float = 0) -> None:
        self.shutdown_called = True
        self.shutdown_timeouts.append(timeout)

    def start(self) -> None:
        self.started = True


# __init__

def test_init_sets_sleep_interval(monitor: MonitorManager) -> None:
    assert monitor.sleep_interval == 60
    assert monitor._threads == {}


@pytest.mark.parametrize('update_interval', [0, -5])
def test_init_rejects_non_positive_update_interval(
        status_registry: StatusRegistry, update_interval: int) -> None:
    # A zero or negative poll period would busy-spin the monitor loop; reject it at construction.
    with pytest.raises(ValueError, match='update_interval'):
        MonitorManager({'update_interval': update_interval}, {}, status_registry, name='Monitor')


# _parse

def test_parse_valid_json_substitutes_and_evaluates(monitor: MonitorManager) -> None:
    check = {'id': 1, 'content': '192.0.2.1', 'monitor_json': _tcp_json()}
    assert monitor._parse(check) == {'type': 'tcp', 'ip': '192.0.2.1', 'port': 80,
                                     'interval': 10, 'timeout': 1, 'fall': 2, 'rise': 2}


def test_parse_invalid_json_raises(monitor: MonitorManager) -> None:
    check = {'id': 1, 'content': 'x', 'monitor_json': 'this is not a dict'}
    with pytest.raises(ValueError):  # json.JSONDecodeError is a ValueError subclass
        monitor._parse(check)


@pytest.mark.parametrize('monitor_json', ['[1, 2, 3]', '42', '"a string"', 'null'])
def test_parse_non_object_json_raises(monitor: MonitorManager, monitor_json: str) -> None:
    # Valid JSON that decodes to something other than an object (array/scalar/null) is rejected: the top-level
    # value must be a dict so the check fields can be read by key.
    check = {'id': 1, 'content': 'x', 'monitor_json': monitor_json}
    with pytest.raises(ValueError, match='monitor_json must be a JSON object'):
        monitor._parse(check)


def test_parse_missing_content_field_raises(monitor: MonitorManager) -> None:
    check = {'id': 1, 'monitor_json': '{"ip": "${content}"}'}  # no 'content' key -> KeyError in _substitute
    with pytest.raises(KeyError):
        monitor._parse(check)


def test_parse_json_booleans(monitor: MonitorManager) -> None:
    # monitor_json is JSON, so booleans arrive as 'true'/'false'; the http/exec boolean fields
    # (tls_verify, redirect_error) depend on the parser accepting them.
    check = {'id': 1, 'content': 'x',
             'monitor_json': '{"type": "http", "url": "https://h/", "tls_verify": false}'}
    assert monitor._parse(check) == {'type': 'http', 'url': 'https://h/', 'tls_verify': False}


def test_parse_preserves_literal_percent_and_dollar(monitor: MonitorManager) -> None:
    # Substitution is post-parse and only touches the exact '${content}' token, so a literal '%', '$', or brace in any
    # value is data, not a directive. (Pre-parse %-formatting silently dropped these monitors.)
    monitor_json = ('{"type": "exec", "args": ["/bin/sh", "-c", "df / | grep 100%"], '
                    '"output_match": "load:\\\\s*\\\\$?[0-9]+", "interval": 3, "timeout": 1, "fall": 2, "rise": 2}')
    check: dict[str, Any] = {'id': 1, 'content': '192.0.2.1', 'monitor_json': monitor_json}
    assert monitor._parse(check) == {'type': 'exec', 'args': ['/bin/sh', '-c', 'df / | grep 100%'],
                                     'output_match': 'load:\\s*\\$?[0-9]+',
                                     'interval': 3, 'timeout': 1, 'fall': 2, 'rise': 2}


def test_parse_substitution_embedded_list_and_injection_safe(monitor: MonitorManager) -> None:
    # '${content}' is replaced everywhere it appears - embedded in a string and inside a list - and a content value
    # containing a double quote cannot corrupt the JSON because substitution happens after parsing.
    monitor_json = ('{"type": "http", "url": "http://${content}/status", '
                    '"args": ["--host", "${content}"]}')
    check: dict[str, Any] = {'id': 1, 'content': 'a"b', 'monitor_json': monitor_json}
    assert monitor._parse(check) == {'type': 'http', 'url': 'http://a"b/status', 'args': ['--host', 'a"b']}


# build_check

def test_build_check_valid_returns_check(monitor: MonitorManager) -> None:
    check = {'id': 1, 'content': '192.0.2.1', 'monitor_json': _tcp_json()}
    result = monitor.build_check(check)
    assert result is not None
    assert result.name == 'tcp'


def test_build_check_none_type_returns_skipped(monitor: MonitorManager) -> None:
    # "No monitoring" is the registered 'none' type: build_check returns it (validated); _desired_checks skips it.
    check: dict[str, Any] = {'id': 1, 'content': 'x', 'monitor_json': '{"type": "none"}'}
    result = monitor.build_check(check)
    assert result.name == 'none' and result.skip is True


def test_build_check_empty_type_raises(monitor: MonitorManager) -> None:
    # An empty type is not a registered token ('none' is), so it falls through to the unknown-type error.
    check: dict[str, Any] = {'id': 1, 'content': 'x', 'monitor_json': '{"type": ""}'}
    with pytest.raises(ValueError, match='unknown check type'):
        monitor.build_check(check)


def test_build_check_non_string_type_raises(monitor: MonitorManager) -> None:
    # A bad 'type' value raises so callers (admin save, _desired_checks) can surface or log the error.
    check: dict[str, Any] = {'id': 1, 'content': 'x', 'monitor_json': '{"type": ["icmp"]}'}
    with pytest.raises(ValueError, match="check parameter 'type' invalid"):
        monitor.build_check(check)


# _clean_status

def test_clean_status_removes_stale_ids(monitor: MonitorManager, status_registry: StatusRegistry) -> None:
    status_registry.add(1)
    status_registry.add(2)
    status_registry.add(99)
    monitor._clean_status({1, 2})
    assert status_registry.is_down(1)
    assert status_registry.is_down(2)
    assert not status_registry.is_down(99)


def test_clean_status_noop_when_nothing_stale(monitor: MonitorManager, status_registry: StatusRegistry) -> None:
    status_registry.add(1)
    monitor._clean_status({1, 2})
    assert status_registry.is_down(1)


# _stop_threads

def test_stop_threads_noop_on_empty(monitor: MonitorManager) -> None:
    monitor._stop_threads([])


def test_stop_threads_signals_alive_threads(monitor: MonitorManager) -> None:
    thread = _FakeCheckThread(alive=True, sleep_interval=0)
    monitor._stop_threads([thread])  # type: ignore[list-item]
    assert thread.shutdown_called is True


def test_stop_threads_skips_dead_thread(monitor: MonitorManager) -> None:
    dead = _FakeCheckThread(alive=False)
    monitor._stop_threads([dead])  # type: ignore[list-item]
    assert not dead.shutdown_called


def test_stop_threads_waits_until_threads_die(monitor: MonitorManager) -> None:
    # alive when signalled, gone by the time we re-check after joining: a clean exit, no straggler warning.
    thread = _FakeCheckThread(alive=[True, False], timeout=1)
    monitor._stop_threads([thread])  # type: ignore[list-item]
    assert thread.shutdown_called is True


def test_stop_threads_wait_bound_is_timeout_not_interval(
        monitor: MonitorManager, monkeypatch: pytest.MonkeyPatch) -> None:
    # The join budget before giving up on a straggling check is bounded by the check's timeout (how long one
    # execute() may run), not its interval (the interruptible idle sleep). With monotonic frozen, the join is
    # given the full 2 * timeout window; interval >> timeout shows the two bounds are not confused.
    monkeypatch.setattr(monitor_module.time, 'monotonic', lambda: 100.0)

    thread = _FakeCheckThread(alive=True, sleep_interval=10, timeout=2)  # interval 10, timeout 2
    monitor._stop_threads([thread])  # type: ignore[list-item]

    # signalled first (timeout 0), then joined with 2 * timeout = 4 (not 2 * interval = 20)
    assert thread.shutdown_timeouts == [0, 4]


def test_stop_threads_abandons_straggler(
        monitor: MonitorManager, caplog: pytest.LogCaptureFixture) -> None:
    # timeout=0 (default) -> budget 0 -> the join returns at once with the thread still alive.
    straggler = _FakeCheckThread(alive=True, sleep_interval=0, content_id=1)

    with caplog.at_level('WARNING'):
        monitor._stop_threads([straggler])  # type: ignore[list-item]

    assert straggler.shutdown_called is True
    assert 'abandoning straggling check threads' in caplog.text


def test_stop_threads_does_not_busy_poll(monitor: MonitorManager, monkeypatch: pytest.MonkeyPatch) -> None:
    # The join waits on each thread's stop event via shutdown(timeout); it must never poll with time.sleep.
    slept: list[float] = []
    monkeypatch.setattr(monitor_module.time, 'sleep', slept.append)
    clock = iter(float(i) for i in range(1000))
    monkeypatch.setattr(monitor_module.time, 'monotonic', lambda: next(clock))

    straggler = _FakeCheckThread(alive=True, sleep_interval=10, timeout=2)
    monitor._stop_threads([straggler])  # type: ignore[list-item]

    assert not slept


# _start_thread

def test_start_thread_creates_and_starts(monitor: MonitorManager, monkeypatch: pytest.MonkeyPatch) -> None:
    spawned: list[_FakeCheckThread] = []

    def fake_ctor(_check: Any, status_writer: Any, name: str) -> _FakeCheckThread:
        thread = _FakeCheckThread(content_id=status_writer.content_id)
        thread.thread_name = name
        spawned.append(thread)
        return thread

    monkeypatch.setattr(monitor_module, 'CheckThread', fake_ctor)
    result = monitor._start_thread(1, object())  # type: ignore[arg-type]
    assert len(spawned) == 1
    assert spawned[0].started
    assert result is spawned[0]
    assert spawned[0].content_id == 1
    assert spawned[0].thread_name == 'Check-1'


# _desired_checks

class _FakeDatabaseError(Exception):
    pass


class _FakeDatabase:
    Error = _FakeDatabaseError
    raise_error = False
    checks: list[dict[str, Any]] = []

    def __init__(self, **_kwargs: Any) -> None:
        pass

    def __enter__(self) -> '_FakeDatabase':
        if _FakeDatabase.raise_error:
            raise _FakeDatabaseError('db down')
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None

    def gslb_checks(self) -> list[dict[str, Any]]:
        return _FakeDatabase.checks


@pytest.fixture
def fake_db(monkeypatch: pytest.MonkeyPatch) -> type[_FakeDatabase]:
    _FakeDatabase.raise_error = False
    _FakeDatabase.checks = []
    monkeypatch.setattr(monitor_module.powergslb.database, 'Database', _FakeDatabase)
    return _FakeDatabase


def test_desired_checks_returns_dict(monitor: MonitorManager, fake_db: type[_FakeDatabase]) -> None:
    fake_db.checks = [{'id': 1, 'content': '192.0.2.1', 'monitor_json': _tcp_json()}]
    result = monitor._desired_checks()
    assert result is not None
    assert 1 in result


def test_desired_checks_filters_invalid(monitor: MonitorManager, fake_db: type[_FakeDatabase]) -> None:
    fake_db.checks = [
        {'id': 1, 'content': '192.0.2.1', 'monitor_json': _tcp_json()},  # valid -> kept
        {'id': 2, 'content': 'x', 'monitor_json': 'not a dict'},  # parse fails -> dropped
        {'id': 3, 'content': 'x', 'monitor_json': '{"type": "smtp"}'},  # unknown type -> dropped
        {'id': 4, 'content': 'x', 'monitor_json': '{"type": ""}'},  # empty type -> unknown type -> dropped
        {'id': 5, 'content': 'x', 'monitor_json': '{"type": "none"}'},  # none type -> built but skipped
    ]
    result = monitor._desired_checks()
    assert result is not None
    assert list(result.keys()) == [1]


def test_desired_checks_logs_skipped_invalid_with_content_id(
        monitor: MonitorManager, fake_db: type[_FakeDatabase], caplog: pytest.LogCaptureFixture) -> None:
    fake_db.checks = [{'id': 7, 'content': 'x', 'monitor_json': '{"type": "smtp"}'}]  # unknown type -> dropped
    with caplog.at_level('ERROR'):
        monitor._desired_checks()
    assert any('content id 7' in r.message for r in caplog.records)


def test_desired_checks_db_error_returns_none(monitor: MonitorManager, fake_db: type[_FakeDatabase]) -> None:
    fake_db.raise_error = True
    result = monitor._desired_checks()
    assert result is None


@pytest.mark.usefixtures('fake_db')
def test_desired_checks_logs_at_debug(
        monitor: MonitorManager, caplog: pytest.LogCaptureFixture) -> None:
    # The per-interval 'update checks' line is DEBUG, not INFO, so a steady-state monitor does not spam the log.
    with caplog.at_level('DEBUG'):
        monitor._desired_checks()
    records = [r for r in caplog.records if 'update checks from the database' in r.message]
    assert records and all(r.levelname == 'DEBUG' for r in records)


# _reconcile

def test_reconcile_unchanged_keeps_thread(
        monitor: MonitorManager, monkeypatch: pytest.MonkeyPatch) -> None:
    # Same Check object over two polls: the thread must not be replaced.
    check = _FakeCheck('a')
    thread = _FakeCheckThread(content_id=1, alive=True, check=check)
    monitor._threads = {1: thread}  # type: ignore[dict-item]

    stopped: list[Any] = []
    monkeypatch.setattr(monitor, '_stop_threads', stopped.extend)
    monitor._reconcile({1: check})  # type: ignore[dict-item]

    assert not stopped
    assert monitor._threads[1] is thread


def test_reconcile_changed_replaces_thread(
        monitor: MonitorManager, monkeypatch: pytest.MonkeyPatch) -> None:
    old_check = _FakeCheck('old')
    new_check = _FakeCheck('new')
    old_thread = _FakeCheckThread(content_id=1, alive=True, check=old_check)
    monitor._threads = {1: old_thread}  # type: ignore[dict-item]

    stopped: list[Any] = []
    monkeypatch.setattr(monitor, '_stop_threads', stopped.extend)
    new_thread = _FakeCheckThread(content_id=1, alive=True, check=new_check)
    monkeypatch.setattr(monitor, '_start_thread', lambda cid, chk: new_thread)

    monitor._reconcile({1: new_check})  # type: ignore[dict-item]

    assert old_thread in stopped
    assert monitor._threads[1] is new_thread


def test_reconcile_single_change_leaves_others(
        monitor: MonitorManager, monkeypatch: pytest.MonkeyPatch) -> None:
    # Only the edited id's thread is replaced; the other unchanged thread is untouched.
    check1 = _FakeCheck('c1')
    old_check2 = _FakeCheck('old')
    new_check2 = _FakeCheck('new')
    thread1 = _FakeCheckThread(content_id=1, alive=True, check=check1)
    thread2 = _FakeCheckThread(content_id=2, alive=True, check=old_check2)
    monitor._threads = {1: thread1, 2: thread2}  # type: ignore[dict-item]

    stopped: list[Any] = []
    monkeypatch.setattr(monitor, '_stop_threads', stopped.extend)
    new_thread2 = _FakeCheckThread(content_id=2, alive=True, check=new_check2)
    monkeypatch.setattr(monitor, '_start_thread', lambda cid, chk: new_thread2)

    monitor._reconcile({1: check1, 2: new_check2})  # type: ignore[dict-item]

    assert thread1 not in stopped
    assert thread2 in stopped
    assert monitor._threads[1] is thread1
    assert monitor._threads[2] is new_thread2


def test_reconcile_removed_thread(
        monitor: MonitorManager, monkeypatch: pytest.MonkeyPatch) -> None:
    check = _FakeCheck('a')
    thread = _FakeCheckThread(content_id=1, alive=True, check=check)
    monitor._threads = {1: thread}  # type: ignore[dict-item]

    stopped: list[Any] = []
    monkeypatch.setattr(monitor, '_stop_threads', stopped.extend)

    monitor._reconcile({})  # type: ignore[dict-item]

    assert thread in stopped
    assert 1 not in monitor._threads


def test_reconcile_removed_clears_status(
        monitor: MonitorManager, monkeypatch: pytest.MonkeyPatch,
        status_registry: StatusRegistry) -> None:
    check = _FakeCheck('a')
    thread = _FakeCheckThread(content_id=1, alive=True, check=check)
    monitor._threads = {1: thread}  # type: ignore[dict-item]
    status_registry.add(1)

    monkeypatch.setattr(monitor, '_stop_threads', lambda threads: None)

    monitor._reconcile({})  # type: ignore[dict-item]

    assert not status_registry.is_down(1)


def test_reconcile_dead_restarts_thread(
        monitor: MonitorManager, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture) -> None:
    check = _FakeCheck('a')
    dead_thread = _FakeCheckThread(content_id=1, alive=False, check=check)
    monitor._threads = {1: dead_thread}  # type: ignore[dict-item]

    stopped: list[Any] = []
    monkeypatch.setattr(monitor, '_stop_threads', stopped.extend)
    new_thread = _FakeCheckThread(content_id=1, alive=True, check=check)
    monkeypatch.setattr(monitor, '_start_thread', lambda cid, chk: new_thread)

    with caplog.at_level('ERROR'):
        monitor._reconcile({1: check})  # type: ignore[dict-item]

    assert 'unexpectedly stopped' in caplog.text
    assert dead_thread in stopped
    assert monitor._threads[1] is new_thread


def test_reconcile_new_thread(
        monitor: MonitorManager, monkeypatch: pytest.MonkeyPatch) -> None:
    check = _FakeCheck('a')
    monitor._threads = {}
    new_thread = _FakeCheckThread(content_id=1, alive=True, check=check)
    monkeypatch.setattr(monitor, '_start_thread', lambda cid, chk: new_thread)

    monitor._reconcile({1: check})  # type: ignore[dict-item]

    assert monitor._threads[1] is new_thread


def test_reconcile_changed_retains_status(
        monitor: MonitorManager, monkeypatch: pytest.MonkeyPatch,
        status_registry: StatusRegistry) -> None:
    # A changed check keeps the same content id; the existing down/up status must be preserved across the restart.
    old_check = _FakeCheck('old')
    new_check = _FakeCheck('new')
    thread = _FakeCheckThread(content_id=1, alive=True, check=old_check)
    monitor._threads = {1: thread}  # type: ignore[dict-item]
    status_registry.add(1)

    monkeypatch.setattr(monitor, '_stop_threads', lambda threads: None)
    new_thread = _FakeCheckThread(content_id=1, alive=True, check=new_check)
    monkeypatch.setattr(monitor, '_start_thread', lambda cid, chk: new_thread)

    monitor._reconcile({1: new_check})  # type: ignore[dict-item]

    assert status_registry.is_down(1)


# shutdown: signals every check thread (no wait) then stops the monitor itself

def test_shutdown_signals_all_threads(
        monitor: MonitorManager, monkeypatch: pytest.MonkeyPatch) -> None:
    base_timeouts: list[float] = []
    monkeypatch.setattr(monitor_module.AbstractThread, 'shutdown',
                        lambda _self, timeout=0: base_timeouts.append(timeout))
    first = _FakeCheckThread(content_id=1)
    second = _FakeCheckThread(content_id=2)
    monitor._threads = {1: first, 2: second}  # type: ignore[dict-item]

    monitor.shutdown(timeout=3)

    assert first.shutdown_called and second.shutdown_called  # every check signalled
    assert base_timeouts == [3]  # then the monitor's own (base) shutdown ran with the caller's timeout


# task orchestration

def test_task_calls_reconcile_when_db_succeeds(
        monitor: MonitorManager, fake_db: type[_FakeDatabase],
        monkeypatch: pytest.MonkeyPatch) -> None:
    fake_db.checks = []
    reconciled: list[Any] = []
    monkeypatch.setattr(monitor, '_reconcile', reconciled.append)
    monitor.task()
    assert len(reconciled) == 1
    assert reconciled[0] == {}


def test_task_skips_reconcile_on_db_error(
        monitor: MonitorManager, fake_db: type[_FakeDatabase],
        monkeypatch: pytest.MonkeyPatch) -> None:
    fake_db.raise_error = True
    reconciled: list[Any] = []
    monkeypatch.setattr(monitor, '_reconcile', reconciled.append)
    monitor.task()
    assert not reconciled
