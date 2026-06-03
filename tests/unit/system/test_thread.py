# pylint: disable=missing-function-docstring

"""Tests for the ServiceThread structural Protocol.

ServiceThread is the contract SystemService depends on: a class satisfies it by having start(), is_alive(),
shutdown(timeout) and a name - no inheritance required. AbstractThread (hence MonitorManager / CheckThread) and
HTTPServerManager all conform; the real classes are also checked statically where SystemService is constructed
(main.py types service_threads as Sequence[ServiceThread]).
"""

from powergslb.monitor.thread import AbstractThread
from powergslb.system.thread import ServiceThread


class _Conforming(AbstractThread):
    """Minimal AbstractThread subclass standing in for the real service threads."""

    def task(self) -> None:
        pass


class _NotAThread:
    """Has a name, start() and is_alive() but no shutdown(): must not pass as a ServiceThread."""

    name = 'Nope'

    def start(self) -> None:
        pass

    def is_alive(self) -> bool:
        return False


def _consume(thread: ServiceThread) -> str:
    # mypy checks the argument is structurally a ServiceThread; returns its name at runtime
    return thread.name


def test_abstractthread_subclass_is_servicethread() -> None:
    thread = _Conforming(name='Conforming')
    assert isinstance(thread, ServiceThread)
    assert _consume(thread) == 'Conforming'

    # the contract works end to end: start it, then stop it through the ServiceThread methods
    thread.start()
    thread.shutdown(timeout=1)
    thread.join(timeout=1)
    assert not thread.is_alive()


def test_missing_method_is_not_servicethread() -> None:
    incomplete = _NotAThread()
    incomplete.start()
    # has a name, start() and is_alive() but no shutdown(), so the structural check fails
    assert incomplete.is_alive() is False
    assert not hasattr(incomplete, 'shutdown')
    assert not isinstance(incomplete, ServiceThread)
