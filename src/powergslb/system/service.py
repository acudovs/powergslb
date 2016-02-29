import os
import time
import threading

import systemd.daemon

__all__ = ['SystemService']


class SystemService(object):
    """
    systemd System Service
    """

    def __init__(self, service_threads, default_interval=1.0):
        self.service_threads = service_threads
        self.sleep_interval = self.watchdog_interval(default_interval)

    def _is_threads_alive(self):
        return all(service_thread.is_alive() for service_thread in self.service_threads)

    @staticmethod
    def systemd_notify(status, unset_environment=False):
        if systemd.daemon.booted():
            systemd.daemon.notify(status, unset_environment)

    @staticmethod
    def watchdog_interval(default_interval):
        if 'WATCHDOG_USEC' in os.environ:
            interval = min(default_interval, int(os.environ['WATCHDOG_USEC']) / 1000000 / 2)
        else:
            interval = default_interval

        return interval

    def start(self):
        for service_thread in self.service_threads:
            service_thread.start()

        self.systemd_notify('READY=1')

        while self._is_threads_alive():
            self.systemd_notify('STATUS=Total threads: {}; Service threads: {}\nWATCHDOG=1'.format(
                    threading.active_count(), len(self.service_threads)))

            time.sleep(self.sleep_interval)
