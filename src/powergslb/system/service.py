"""systemd service supervisor."""

import logging
import os
import signal
import sys
import threading
from collections.abc import Sequence
from types import FrameType

import systemd.daemon

from powergslb.system.thread import ServiceThread

__all__ = ['SystemService']


class SystemService:
    """Supervises the service threads from the main thread: systemd readiness, watchdog, signal-driven shutdown.

    Exits non-zero when a service thread dies (so systemd Restart=on-failure kicks in) and zero on a requested stop.

    :param service_threads: Threads to start and supervise.
    :param default_interval: Upper bound on the supervision loop sleep, in seconds.
    :param shutdown_timeout: Per-thread wait when stopping, in seconds.
    """

    def __init__(self,
                 service_threads: Sequence[ServiceThread],
                 default_interval: float = 1.0,
                 shutdown_timeout: float = 3.0) -> None:
        self.service_threads = service_threads
        self.sleep_interval = self.watchdog_interval(default_interval)
        self.shutdown_timeout = shutdown_timeout
        self._shutdown = threading.Event()
        self._signum: int | None = None

    def _is_threads_alive(self) -> bool:
        return all(service_thread.is_alive() for service_thread in self.service_threads)

    def _on_signal(self, signum: int, _frame: FrameType | None) -> None:
        self._signum = signum
        self._shutdown.set()

    @staticmethod
    def systemd_notify(status: str, unset_environment: bool = False) -> None:
        """Send a status notification to systemd if the process was booted by it."""
        if systemd.daemon.booted():
            systemd.daemon.notify(status, unset_environment)

    @staticmethod
    def watchdog_interval(default_interval: float) -> float:
        """Return the loop sleep interval: half of WATCHDOG_USEC capped at default_interval, or default_interval."""
        if 'WATCHDOG_USEC' in os.environ:
            return min(default_interval, int(os.environ['WATCHDOG_USEC']) / 1000000 / 2)
        return default_interval

    def start(self) -> None:
        """Start all service threads, run the watchdog loop, and stop cleanly on a signal or a dead thread."""
        assert threading.current_thread() is threading.main_thread()  # signal.signal requires the main thread
        signal.signal(signal.SIGTERM, self._on_signal)
        signal.signal(signal.SIGINT, self._on_signal)

        for service_thread in self.service_threads:
            service_thread.start()

        self.systemd_notify('READY=1')

        while not self._shutdown.is_set() and self._is_threads_alive():
            self.systemd_notify(f'STATUS=Total threads: {threading.active_count()}; '
                                f'Service threads: {len(self.service_threads)}\nWATCHDOG=1')
            self._shutdown.wait(self.sleep_interval)

        self.systemd_notify('STOPPING=1')
        requested = self._shutdown.is_set()
        dead_threads = [thread.name for thread in self.service_threads if not thread.is_alive()]
        for service_thread in self.service_threads:
            service_thread.shutdown(self.shutdown_timeout)

        if requested:
            assert self._signum is not None
            logging.info('received %s, exiting', signal.Signals(self._signum).name)
            sys.exit(0)  # requested stop: clean, no restart

        logging.error('service thread(s) died: %s, exiting', dead_threads)
        sys.exit(1)  # a thread died: Restart=on-failure
