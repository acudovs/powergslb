# pylint: disable=missing-function-docstring

"""Tests for AbstractThread.

The run loop calls task() repeatedly until shutdown, sets the daemon flag, exposes a sleep_interval, and signals
completion so shutdown(timeout) returns promptly.
"""

import threading
import time

import pytest

from powergslb.monitor.thread import AbstractThread


class _CountingThread(AbstractThread):
    """Count task() iterations; stop sleeping noticeably between them."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.sleep_interval = 0.001
        self.iterations = 0

    def task(self) -> None:
        self.iterations += 1


class _OneShotThread(AbstractThread):
    """Request its own shutdown after a single iteration via an Event the test can wait on."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.sleep_interval = 0
        self.ran = threading.Event()

    def task(self) -> None:
        self.ran.set()


def test_abstractthread_cannot_instantiate_without_task() -> None:
    with pytest.raises(TypeError):
        AbstractThread()  # type: ignore[abstract]  # pylint: disable=abstract-class-instantiated


def test_defaults_daemon_and_sleep_interval() -> None:
    thread = _OneShotThread(name='OneShot')
    assert thread.daemon is True
    assert thread.sleep_interval == 0


def test_run_loops_task_until_shutdown() -> None:
    thread = _CountingThread(name='Counter')
    thread.start()
    time.sleep(0.05)
    thread.shutdown(timeout=1)
    thread.join(timeout=1)

    assert not thread.is_alive()
    # the loop ran task() many times, not just once
    assert thread.iterations > 1


def test_shutdown_before_start_is_a_noop_wait() -> None:
    # shutdown() on a never-started thread sets the request flag, so the wait times out immediately and a
    # subsequent start() exits the run loop without ever calling task().
    thread = _CountingThread(name='NeverStarted')
    start = time.monotonic()
    thread.shutdown(timeout=0.05)
    elapsed = time.monotonic() - start
    assert elapsed >= 0.05

    thread.start()
    thread.join(timeout=1)
    assert not thread.is_alive()
    assert thread.iterations == 0


def test_shutdown_interrupts_the_sleep_interval() -> None:
    # A thread idling in a long sleep_interval must stop promptly on shutdown, not sleep out the interval.
    thread = _CountingThread(name='Sleeper')
    thread.sleep_interval = 30
    thread.start()
    time.sleep(0.05)  # let it run one task() and enter the long sleep

    start = time.monotonic()
    thread.shutdown(timeout=2)
    thread.join(timeout=2)
    elapsed = time.monotonic() - start

    assert not thread.is_alive()
    assert elapsed < 1  # woke from the sleep at once, did not wait out the 30s interval


def test_run_sets_shutdown_event_so_shutdown_returns_fast() -> None:
    thread = _OneShotThread(name='Event')
    thread.start()
    assert thread.ran.wait(timeout=1)

    # once the thread is asked to stop, the finally-block event lets shutdown() return well under the timeout
    start = time.monotonic()
    thread.shutdown(timeout=5)
    thread.join(timeout=1)
    assert time.monotonic() - start < 5
    assert not thread.is_alive()
