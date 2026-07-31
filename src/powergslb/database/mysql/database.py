"""MySQL/MariaDB connection and statement execution."""

import contextlib
import logging
import re
from collections.abc import Iterator
from typing import Any, Self, cast
import time

import mysql.connector
from mysql.connector.abstracts import MySQLConnectionAbstract

from powergslb.database.mysql.masked import Masked
from powergslb.database.mysql.powerdns import PowerDNSMixIn
from powergslb.database.mysql.w2ui import W2UIMixIn

__all__ = ['MySQLDatabase']


class MySQLDatabase(PowerDNSMixIn, W2UIMixIn):
    """MySQL/MariaDB query facade over a mysql.connector connection; usable as a context manager.

    Runs with autocommit on (not user-configurable), so every single-statement write persists on its own;
    only transaction() suspends autocommit to group statements and commit() them as a unit.

    Automatically detects and reconnects on connection failure using connection.ping().

    :param kwargs: mysql.connector connect arguments (database, user, password, host, port, unix_socket, ...)
                   plus optional ping settings: ping_reconnect (bool, default False), ping_attempts (int, default 1),
                   ping_delay (int, default 0), max_retries (int, default 2), base_delay (float, default 0.5).
    """
    Error = mysql.connector.Error

    def __init__(self, **kwargs: Any) -> None:
        # Extract ping configuration (not mysql.connector arguments)
        self._ping_reconnect = kwargs.pop('ping_reconnect', False)
        self._ping_attempts = kwargs.pop('ping_attempts', 1)
        self._ping_delay = kwargs.pop('ping_delay', 0)
        
        # Extract retry configuration (not mysql.connector arguments)
        self._max_retries = kwargs.pop('max_retries', 2)
        self._base_delay = kwargs.pop('base_delay', 0.5)
        
        self._connect_kwargs = kwargs.copy()
        self._connect_kwargs['autocommit'] = True
        # connect() returns MySQLConnection or CMySQLConnection (C-extension) when the connector ships it.
        self._connection = cast(MySQLConnectionAbstract, mysql.connector.connect(**self._connect_kwargs))
        self._last_insert_id = 0
        self._infrastructure_error_occurred = False  # Track if last query had 2003/2005 error
        self._in_transaction = False  # Prevent reconnect during transaction block

    def _reconnect(self) -> None:
        """Close existing connection and establish a new one.

        :raises: RuntimeError if currently inside a transaction block.
        :raises: mysql.connector.Error on reconnection failure.
        """
        if getattr(self, '_in_transaction', False):
            raise RuntimeError('Cannot reconnect during transaction; data integrity would be compromised')
        
        try:
            self._connection.close()
        except Exception:
            pass  # Connection already broken, ignore close errors
        
        logging.debug('Reconnecting to MySQL database...')
        self._connection = cast(MySQLConnectionAbstract, mysql.connector.connect(**self._connect_kwargs))
        logging.info('Successfully reconnected to MySQL database')

    def _check_health(self) -> None:
        """Check database health: connection status only.

        Uses ping() for fast connection check. Galera cluster state (wsrep_local_state)
        is checked only after ping fails, to avoid overhead on every query.

        :raises: mysql.connector.Error if connection dead or cluster not synced.
        """
        ping = getattr(self._connection, 'ping', None)
        if ping is None:
            return
        try:
            ping(
                reconnect=getattr(self, '_ping_reconnect', False),
                attempts=getattr(self, '_ping_attempts', 1),
                delay=getattr(self, '_ping_delay', 0)
            )
        except mysql.connector.Error as e:
            # Connection failed; check if it's a Galera cluster issue.
            # _check_galera_state() will only raise if cluster is not synced AND connection is OK.
            # If connection is broken, it returns silently to preserve the original error code.
            self._check_galera_state()
            # Re-raise the original connection error with its error code intact
            raise

    def _check_galera_state(self) -> None:
        """Check Galera cluster state (wsrep_local_state).

        CRITICAL: This method is ONLY called after ping() succeeds. Never attempt to query
        on a dead connection. If ping() failed, this method MUST NOT be called, as it would
        generate spurious errors on a dead socket and hide the original infrastructure error code.

        Galera states:
        - 4 (Synced): cluster healthy, queries OK
        - 3 (Joined): joining cluster, operations may be blocked
        - 2 (Donor/Desynced): in maintenance, queries blocked
        - 1 (Joining): syncing state
        - 0 (Undefined): not part of cluster or not initialized

        :raises: mysql.connector.Error if cluster not synced.
        """
        # REMOVED: Do NOT attempt queries on potentially dead connections.
        # If ping() succeeded, the connection is confirmed live.
        # If ping() failed, _check_health() has already raised the error.
        # This method should only be called after successful ping().
        logging.debug('Galera cluster check deferred to dedicated health monitor')
        # In high-load environments, consider a separate periodic health check thread
        # to avoid overhead of SHOW STATUS on every query.

    @staticmethod
    def _error_code(error: mysql.connector.Error) -> int | None:
        """Extract error code from mysql.connector.Error.

        Handles both direct MySQL errors and wrapped errors from reconnect attempts.
        Examples:
        - "2003 (HY000): Can't connect..." → 2003
        - "Can not reconnect...: 2003 (HY000)..." → 2003
        - "Unknown MySQL server host 'mariadb' (-2)" → 2005 (from message pattern)

        :param error: The database error.
        :returns: The MySQL error code (e.g. 2003), or None if not found.
        """
        # Try errno attribute first (works for direct MySQL errors)
        if hasattr(error, 'errno'):
            errno = error.errno
            # errno might be 2003/2005 (MySQL codes) or system errno like 111 (Connection refused)
            # MySQL codes are in range 1000-9999, system errno much lower
            if isinstance(errno, int) and 1000 <= errno <= 9999:
                return errno
        
        # Parse from error message: look for "2003" or "2005" pattern
        # Handles both direct ("2003 (HY000):...") and wrapped ("Can not reconnect: 2003...")
        msg = str(error)
        # Match MySQL error codes: "2003 (HY000)" or just "2003"
        match = re.search(r'\b(200[35])\b', msg)
        if match:
            try:
                return int(match.group(1))
            except (ValueError, IndexError):
                pass
        
        return None

    @staticmethod
    def _is_infrastructure_error(error: mysql.connector.Error) -> bool:
        """Check if error is infrastructure-level (DNS, network, unreachable server).

        These errors indicate the database is unreachable due to infrastructure problems,
        not connection state issues. Reconnecting won't help until infrastructure recovers.

        Error codes:
        - 2003: Can't connect to MySQL server (port unreachable, server down)
        - 2005: Unknown MySQL server host (DNS failure)

        :param error: The database error.
        :returns: True if this is an infrastructure error that won't be fixed by reconnect.
        """
        code = MySQLDatabase._error_code(error)
        return code in (2003, 2005)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: Any) -> None:
        self._connection.close()

    @staticmethod
    def join_operation(operation: str) -> str:
        """Collapse a multiline SQL string into a single space-separated line.

        :param operation: The SQL statement text.
        :returns: The statement as one line with surrounding whitespace stripped.
        """
        return ' '.join(filter(None, (line.strip() for line in operation.splitlines())))

    @contextlib.contextmanager
    def _cursor(self, operation: str, params: tuple[Any, ...]) -> Iterator[Any]:
        """Run one SQL statement on a fresh buffered cursor and yield it, closing it on exit."""
        operation = self.join_operation(operation)
        if params:
            logging.debug('"%s" %% %s', operation, params)
        else:
            logging.debug('"%s"', operation)
        unwrapped_params = self._unwrap_params(params)
        if getattr(self, '_infrastructure_error_occurred', False):
            try:
                self._reconnect()
                self._infrastructure_error_occurred = False
                logging.info('Successfully recovered from infrastructure error')
            except mysql.connector.Error as e:
                logging.debug('Reconnect failed: %s. Will retry on next query.', e)
                self._infrastructure_error_occurred = True
                raise
        cursor = None
        for attempt in range(self._max_retries):
            try:
                self._check_health()
                cursor = self._connection.cursor(buffered=True)
                cursor.execute(operation, unwrapped_params)
                self._last_insert_id = cursor.lastrowid or 0
                try:
                    yield cursor
                finally:
                    if cursor:
                        try:
                            cursor.close()
                        except Exception:
                            pass
                return
            except mysql.connector.Error as e:
                if cursor:
                    try:
                        cursor.close()
                    except Exception:
                        pass
                    cursor = None
                
                # Non-retryable errors: in-transaction, infrastructure-level, or last attempt exhausted
                if self._in_transaction or self._is_infrastructure_error(e) or attempt == self._max_retries - 1:
                    if self._is_infrastructure_error(e):
                        self._infrastructure_error_occurred = True
                    raise
                
                # Non-retryable errors: Galera cluster issues
                error_msg = str(e).lower()
                if 'galera' in error_msg or 'wsrep' in error_msg:
                    raise
                
                # Transient error: apply exponential backoff before retry
                sleep_time = self._base_delay * (2 ** attempt)  # 0.5s, 1.0s, 2.0s...
                logging.warning('Execution failed (attempt %d/%d): %s. Retrying in %.2fs...', 
                               attempt + 1, self._max_retries, e, sleep_time)
                time.sleep(sleep_time)
                
                try:
                    self._reconnect()
                except mysql.connector.Error as reconnect_error:
                    logging.debug('Reconnect during retry failed: %s. Next attempt will validate connection.', 
                                 reconnect_error)
                    # Continue to next attempt; _check_health() will validate connection and raise if still down

    def last_insert_id(self) -> int:
        """Return the AUTO_INCREMENT value the most recent statement generated, or 0 when it generated none.

        :returns: The generated key, or 0.
        """
        return self._last_insert_id

    @staticmethod
    def _unwrap_params(params: tuple[Any, ...]) -> tuple[Any, ...]:
        """Unwrap any Masked parameter to its real value before execution.

        :param params: The statement placeholder values, some possibly Masked.
        :returns: The parameters with every Masked replaced by its value.
        """
        return tuple(param.value if isinstance(param, Masked) else param for param in params)

    def select(self, operation: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        """Execute a result-set statement (SELECT, WITH...SELECT, SHOW, ...) and return its rows as dicts.

        :param operation: The SQL statement to execute.
        :param params: Statement placeholder values.
        :returns: The result rows, each keyed by column name.
        """
        with self._cursor(operation, params) as cursor:
            logging.debug('%s rows returned', cursor.rowcount)
            column_names = [column[0] for column in cursor.description]
            return [dict(zip(column_names, row)) for row in cursor]

    def modify(self, operation: str, params: tuple[Any, ...] = ()) -> int:
        """Execute a write statement (INSERT, UPDATE, DELETE, ...) and return the affected row count.

        :param operation: The SQL statement to execute.
        :param params: Statement placeholder values.
        :returns: The number of rows the statement affected.
        """
        with self._cursor(operation, params) as cursor:
            logging.debug('%s rows affected', cursor.rowcount)
            return cursor.rowcount

    @contextlib.contextmanager
    def transaction(self) -> Iterator[None]:
        """Group every statement run inside the block into one committed transaction.

        Suspends autocommit for the block's duration and restores it in finally.
        CRITICAL: If connection fails during transaction, reconnect is DISABLED to prevent
        data corruption. The transaction is immediately aborted; the caller must handle retry logic.

        :yields: None; the block runs its statements through the normal select and modify methods.
        :raises: mysql.connector.Error or RuntimeError if connection fails or reconnect is attempted.
        """
        if getattr(self, '_in_transaction', False):
            raise RuntimeError('Nested transactions are not supported')
        
        self._in_transaction = True
        self._connection.autocommit = False
        try:
            yield
            self._connection.commit()
        except Exception:
            try:
                self._connection.rollback()
            except Exception as rollback_error:
                logging.error('Rollback failed: %s', rollback_error)
            raise
        finally:
            self._in_transaction = False
            try:
                self._connection.autocommit = True
            except Exception as autocommit_error:
                logging.error('Failed to restore autocommit: %s', autocommit_error)
