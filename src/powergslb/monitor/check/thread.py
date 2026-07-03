"""Check execution loop with rise/fall debounce."""

import logging
from typing import Any

from powergslb.monitor.check.base import Check
from powergslb.monitor.status import StatusWriter
from powergslb.monitor.thread import AbstractThread

__all__ = ['CheckThread']


class CheckThread(AbstractThread):
    """Runs one Check on its interval and debounces the status updates.

    :param check: The check to run.
    :param status_writer: Write access to the checked content's health status.
    """

    def __init__(self, check: Check, status_writer: StatusWriter, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._fall = 0
        self._rise = 0
        self.check = check
        self.status_writer = status_writer
        self.sleep_interval = check.interval

    @property
    def content_id(self) -> int:
        """The monitored content id."""
        return self.status_writer.content_id

    def _check_fall(self) -> None:
        """Count a failed run; after 'fall' consecutive failures mark the content down."""
        self._fall += 1
        self._rise = 0

        if self._fall >= self.check.fall and not self.status_writer.is_down():
            logging.error('%s: status fall', self.check)
            self.status_writer.set_down()

    def _check_rise(self) -> None:
        """Count a successful run; after 'rise' consecutive successes mark the content up."""
        self._fall = 0
        self._rise += 1

        if self._rise >= self.check.rise and self.status_writer.is_down():
            logging.info('%s: status rise', self.check)
            self.status_writer.set_up()

    def task(self) -> None:
        """Run the check once and feed the result into the rise/fall debounce; an exception counts as a failure."""
        try:
            if self.check.execute():
                logging.debug('%s: return True', self.check)
                self._check_rise()
            else:
                logging.debug('%s: return False', self.check)
                self._check_fall()
        except Exception as e:  # pylint: disable=broad-exception-caught
            logging.debug('%s: raise Exception: %s: %s', self.check, type(e).__name__, e)
            self._check_fall()
