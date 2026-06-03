"""Base class for the long-running monitor and check loops."""

import abc
import logging
import threading
from typing import Any

__all__ = ['AbstractThread']


class AbstractThread(threading.Thread, abc.ABC):
    """Daemon thread that repeats task() every sleep_interval seconds until shutdown.

    The sleep is interruptible: shutdown() wakes it at once, so a stop request takes effect mid-sleep.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.__shutdown_request = threading.Event()
        self.__stopped = threading.Event()
        self.daemon = True
        self.sleep_interval: float = 0

    def run(self) -> None:
        logging.debug('thread started')
        try:
            while not self.__shutdown_request.is_set():
                self.task()
                # Interruptible sleep: shutdown() wakes it at once
                self.__shutdown_request.wait(self.sleep_interval)
        finally:
            logging.debug('thread stopped')
            self.__stopped.set()

    def shutdown(self, timeout: float = 0) -> None:
        """Signal the thread to stop and wait up to timeout seconds for it to actually stop."""
        logging.debug('thread shutdown')
        self.__shutdown_request.set()
        self.__stopped.wait(timeout)

    @abc.abstractmethod
    def task(self) -> None:
        """Execute one iteration of the thread's work."""
