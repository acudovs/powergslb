"""Health check orchestration."""

import json
import logging
import time
from typing import Any

import powergslb.database
from powergslb.monitor.check import Check, CheckThread
from powergslb.monitor.status import StatusRegistry
from powergslb.monitor.thread import AbstractThread

__all__ = ['MonitorManager']


class MonitorManager(AbstractThread):
    """Polls the database for monitor config and reconciles one CheckThread per content id.

    Unchanged checks keep their running thread (preserving rise/fall counters); removed or changed ones
    are stopped and replaced.

    :param monitor_config: The [monitor] section; update_interval is the poll period, the rest tunes the check types.
    :param database_config: mysql.connector connect kwargs.
    :param status_registry: Shared health status registry.
    :raises ValueError: When update_interval is below 1.
    """

    def __init__(self,
                 monitor_config: dict[str, Any],
                 database_config: dict[str, Any],
                 status_registry: StatusRegistry,
                 **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._threads: dict[int, CheckThread] = {}
        self._database_config = database_config
        self._status_registry = status_registry
        self.sleep_interval = monitor_config.pop('update_interval', 60)
        if self.sleep_interval < 1:
            raise ValueError('update_interval must be >= 1')
        Check.configure(monitor_config)

    def _clean_status(self, valid_ids: set[int]) -> None:
        """Drop status entries whose content id is no longer monitored.

        :param valid_ids: The content ids that still have a check.
        """
        stale_ids = self._status_registry.retain(valid_ids)
        if stale_ids:
            logging.debug('clean status for records: %s', ', '.join(map(str, stale_ids)))

    @classmethod
    def build_check(cls, check: dict[str, Any]) -> Check:
        """Parse one raw check row and build its Check.

        :param check: The raw row with 'monitor_json' and 'content'.
        :returns: The Check.
        :raises ValueError: When a check is malformed or invalid.
        """
        return Check.create(cls._parse(check))

    @classmethod
    def _parse(cls, check: dict[str, Any]) -> dict[str, Any]:
        """Parse 'monitor_json' as JSON, then substitute the record content into its string values.

        Parsing happens before substitution, so a literal '%', '$' or brace anywhere in the template is data, never a
        format directive, and a content value containing a quote cannot corrupt the JSON.

        :param check: The raw row with 'monitor_json' and 'content'.
        :returns: The parsed check spec with the record content substituted.
        :raises ValueError: When 'monitor_json' is not a JSON object (json.JSONDecodeError is a ValueError subclass).
        """
        template = json.loads(check['monitor_json'])
        if not isinstance(template, dict):
            raise ValueError('monitor_json must be a JSON object')
        return cls._substitute(template, '${content}', check['content'])

    @classmethod
    def _substitute(cls, value: Any, token: str, replacement: str) -> Any:
        """Replace every occurrence of token with replacement in every string value, recursing into lists and dicts.

        Non-string values pass through unchanged.

        :param value: The parsed JSON value to substitute in.
        :param token: The literal token to replace.
        :param replacement: The text that replaces the token.
        :returns: The value with every token in its string values replaced.
        """
        if isinstance(value, str):
            return value.replace(token, replacement)
        if isinstance(value, list):
            return [cls._substitute(item, token, replacement) for item in value]
        if isinstance(value, dict):
            return {key: cls._substitute(item, token, replacement) for key, item in value.items()}
        return value

    def _desired_checks(self) -> dict[int, Check] | None:
        """Build the desired checks keyed by content id; return None when the database is unavailable.

        :returns: The non-skipped checks keyed by content id, or None on a database error.
        """
        logging.debug('update checks from the database')
        try:
            with powergslb.database.Database(**self._database_config) as database:
                raw_checks = database.gslb_checks()
        except powergslb.database.Database.Error as e:
            logging.error('%s: %s', type(e).__name__, e)
            return None

        desired: dict[int, Check] = {}
        for raw in raw_checks:
            try:
                check = self.build_check(raw)
            except Exception as e:  # pylint: disable=broad-exception-caught
                logging.error('content id %s: %s: %s', raw['id'], type(e).__name__, e)
                continue
            if not check.skip:
                desired[raw['id']] = check
        return desired

    @staticmethod
    def _stop_threads(threads: list[CheckThread]) -> None:
        """Signal every thread in the list, then wait briefly for a clean exit; stragglers are abandoned.

        :param threads: The check threads to stop.
        """
        if not threads:
            return

        logging.debug('shutdown threads: %s', threads)

        alive_threads: list[CheckThread] = []
        shutdown_timeout: float = 0

        for thread in threads:
            if thread.is_alive():
                thread.shutdown()  # signal all first (no wait) so the checks stop in parallel
                alive_threads.append(thread)
                shutdown_timeout = max(shutdown_timeout, thread.check.timeout)

        # Join each on its stop event, sharing a 2 * timeout budget; a thread that outlives it is abandoned.
        deadline = time.monotonic() + 2 * shutdown_timeout
        for thread in alive_threads:
            thread.shutdown(max(0.0, deadline - time.monotonic()))
        alive_threads = [thread for thread in alive_threads if thread.is_alive()]

        if alive_threads:
            logging.warning('abandoning straggling check threads: %s', alive_threads)

    def _start_thread(self, content_id: int, check: Check) -> CheckThread:
        """Create and start the CheckThread for one content id.

        :param content_id: The content id the check monitors.
        :param check: The check to run.
        :returns: The started thread.
        """
        status_writer = self._status_registry.get_writer(content_id)
        thread = CheckThread(check, status_writer, name=f'Check-{content_id}')
        thread.start()
        return thread

    def _reconcile(self, desired: dict[int, Check]) -> None:
        """Diff the running threads against the desired checks.

        Stops removed, changed, or dead threads, starts new or replacement ones (with fresh rise/fall counters),
        and drops stale status entries.

        :param desired: The desired checks keyed by content id.
        """
        to_stop: list[CheckThread] = []
        to_start: dict[int, Check] = {}

        for content_id, thread in list(self._threads.items()):
            if content_id not in desired:
                to_stop.append(thread)
            elif not thread.is_alive():
                logging.error('Check-%d: unexpectedly stopped', content_id)
                to_stop.append(thread)
                to_start[content_id] = desired[content_id]
            elif thread.check != desired[content_id]:
                to_stop.append(thread)
                to_start[content_id] = desired[content_id]

        for content_id, check in desired.items():
            if content_id not in self._threads:
                to_start[content_id] = check

        self._stop_threads(to_stop)
        for thread in to_stop:
            self._threads.pop(thread.content_id, None)

        for content_id, check in to_start.items():
            self._threads[content_id] = self._start_thread(content_id, check)

        if to_stop or to_start:
            logging.debug('threads updated, running: %s', list(self._threads.keys()))

        self._clean_status(set(desired))

    def shutdown(self, timeout: float = 0) -> None:
        """Signal the check threads without joining them, then stop the monitor itself within timeout.

        :param timeout: Seconds to wait for the monitor thread to exit; 0 waits indefinitely.
        """
        for thread in list(self._threads.values()):  # snapshot: _reconcile mutates _threads
            thread.shutdown()  # signal only (no wait)
        super().shutdown(timeout)

    def task(self) -> None:
        """Refresh the desired checks from the database and reconcile the running threads."""
        desired = self._desired_checks()
        if desired is not None:
            self._reconcile(desired)
