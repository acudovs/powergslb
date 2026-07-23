"""Admin CRUD path: routes each w2ui data token to its table and audits every write."""

import abc
import json
from contextlib import AbstractContextManager
from typing import Any

from powergslb.database.mysql.tables import AUDIT, AuditRow, Table, TABLES, USERS
from powergslb.database.page import PageRequest, SearchClause
from powergslb.database.serialize import json_default
from powergslb.database.user import UserContext

__all__ = ['W2UIMixIn']


class W2UIMixIn(abc.ABC):
    """w2ui related queries: token-dispatched CRUD for every table plus user authentication.

    Every public method resolves the w2ui data token against the TABLES registry and delegates to the table,
    passing itself as the executor.
    """

    @staticmethod
    def _table(data: str) -> Table:
        """Resolve a w2ui data token to its registered table.

        :param data: The table token from the query.
        :returns: The table instance.
        :raises ValueError: When the token names no registered table.
        """
        table = TABLES.get(data)
        if table is None:
            raise ValueError(f"'{data}' not implemented")
        return table

    @abc.abstractmethod
    def select(self, operation: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        """Execute a result-set statement and return its rows as dicts."""

    @abc.abstractmethod
    def modify(self, operation: str, params: tuple[Any, ...] = ()) -> int:
        """Execute a write statement and return the affected row count."""

    @abc.abstractmethod
    def last_insert_id(self) -> int:
        """Return the AUTO_INCREMENT value the most recent statement generated, or 0 when it generated none."""

    @abc.abstractmethod
    def transaction(self) -> AbstractContextManager[None]:
        """Group every statement run inside the block into one committed transaction."""

    def check_user(self, user: str, password: str) -> list[dict[str, Any]]:
        """Return the identity row (without the password) if the user/password pair is valid, else an empty list.

        :param user: The login name.
        :param password: The plaintext password to verify.
        :returns: [{id, user, name}] on a valid pair, an empty list otherwise.
        """
        return USERS.check_user(self, user, password)

    def delete_data(self, data: str, ids: list[Any], user_context: UserContext) -> int:
        """Delete rows of a token's table by key and audit the deletion, in one transaction.

        The transaction reads the targeted rows first, so the deleted set is exactly the audited set.

        :param data: The table token from the query.
        :param ids: The key values selected for deletion.
        :param user_context: The user identity to record the deletion under.
        :returns: The number of rows deleted.
        :raises ValueError: When the token names no registered table.
        """
        table = self._table(data)
        search = SearchClause(field='recid', type='int', operator='in', value=list(ids))

        with self.transaction():
            records, _ = table.get(self, page=PageRequest(searches=(search,)))
            deleted = table.remove(self, [record['recid'] for record in records])
            if deleted:
                audit_rows = [self._audit_row('delete', data, user_context, record['recid'], record, None)
                              for record in records]
                AUDIT.record(self, audit_rows)
            return deleted

    def get_data(self, data: str, recid: int = 0, page: PageRequest | None = None,
                 **kwargs: Any) -> tuple[list[dict[str, Any]], int]:
        """Read a token's table, whole or by recid, searched, sorted and paged in SQL.

        :param data: The table token from the query.
        :param recid: The key value to fetch; 0 fetches every row.
        :param page: The search/sort/paging request; None returns every matching row.
        :param kwargs: Extra arguments the table may consume.
        :returns: The matching rows and the total match count.
        :raises ValueError: When the token names no registered table.
        """
        return self._table(data).get(self, recid, page, **kwargs)

    def save_data(self, data: str, save_recid: int, user_context: UserContext, **fields: Any) -> int:
        """Insert or update one row of a token's table and audit the write, in one transaction.

        An update reads its row before writing and the write reads it back after, so the trail carries the stored
        state on both sides. An insert has no before state.

        :param data: The table token from the query.
        :param save_recid: The key value to update; 0 inserts a new row.
        :param user_context: The user identity to record the write under.
        :param fields: The posted record; only the table's writable columns are read.
        :returns: The number of rows affected.
        :raises ValueError: When the token names no registered table, or the written row reads back missing.
        """
        table = self._table(data)

        with self.transaction():
            before = self._read_row(table, save_recid) if save_recid else None
            affected = table.save(self, save_recid, **fields)
            if affected:
                recid = table.written_recid(self, save_recid, **fields)
                after = self._read_row(table, recid)
                if after is None:
                    raise ValueError(f"'{data}' record {recid} not found")
                audit_rows = [self._audit_row('save', data, user_context, recid, before, after)]
                AUDIT.record(self, audit_rows)
            return affected

    def _audit_row(self, action: str, data: str, user_context: UserContext, record_id: int,
                   before: dict[str, Any] | None, after: dict[str, Any] | None) -> AuditRow:
        """Build the audit trail row describing one written record.

        :param action: The write action, save or delete.
        :param data: The table token the write targets.
        :param user_context: The user identity to record the row under.
        :param record_id: The written row id.
        :param before: The stored record read before the write, None for an insert.
        :param after: The stored record read after the write, None for a delete.
        :returns: The audit row.
        :raises TypeError: When a record holds a value JSON cannot serialize.
        """
        return AuditRow(user_context.user, user_context.client_ip, action, data, record_id,
                        self._record_json(before), self._record_json(after))

    def _read_row(self, table: Table, recid: int) -> dict[str, Any] | None:
        """Read one row of a table by key.

        :param table: The table to read.
        :param recid: The key value to fetch.
        :returns: The row, or None when the key matches nothing.
        """
        rows, _ = table.get(self, recid)
        return rows[0] if rows else None

    @staticmethod
    def _record_json(record: dict[str, Any] | None) -> str | None:
        """Serialize one record for the audit trail.

        :param record: The record to serialize, or None for the side of the write that has no state.
        :returns: The compact JSON text, or None.
        :raises TypeError: When the record holds a value JSON cannot serialize.
        """
        if record is None:
            return None
        return json.dumps(record, separators=(',', ':'), default=json_default)
