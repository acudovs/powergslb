"""Database tables: each read/write surface owns its SQL and runs it through a passed-in executor."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, ClassVar, Protocol

from powergslb.database.page import PageRequest
from powergslb.system.password import hash_password, verify_password

__all__ = ['Selector', 'Executor', 'Table',
           'DOMAINS', 'MONITORS', 'RECORDS', 'ROUTINGS', 'STATUS', 'TYPES', 'USERS', 'VIEWS', 'TABLES']


class Selector(Protocol):
    """The read-side execution contract a table's queries run through."""

    def select(self, operation: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        """Execute a result-set statement and return its rows as dicts.

        :param operation: The SQL statement to execute.
        :param params: Statement placeholder values.
        :returns: The result rows, each keyed by column name.
        """


class Executor(Selector, Protocol):
    """The full execution contract: the read side plus the write primitives."""

    def modify(self, operation: str, params: tuple[Any, ...] = ()) -> int:
        """Execute a write statement and return the affected row count.

        :param operation: The SQL statement to execute.
        :param params: Statement placeholder values.
        :returns: The number of rows the statement affected.
        """

    def execute_transaction(self, statements: list[tuple[str, tuple[Any, ...]]]) -> int:
        """Run statements in one transaction and return the summed affected-row count.

        :param statements: The (operation, params) pairs to run in order.
        :returns: The total number of rows affected across all statements.
        """


@dataclass(frozen=True, kw_only=True)
class Table:
    """One self-contained read/write surface over the database.

    Generates its SQL privately from name/key/fields/columns, composes the w2ui search, sort and paging clauses
    into it, and exposes only behavior: get, save and remove run through the executor passed in.
    A table with a custom join, transaction, password hashing, etc. is a subclass overriding the method that differs.

    :param name: The primary table name.
    :param key: The primary-key column, exposed as recid on read and matched on delete.
    :param fields: The exposed names the base SELECT projects, and the search/sort whitelist; recid first.
    :param columns: The exposed names written by insert/update, in bind order (recid included when the key is writable).
    :param aliases: Exposed field names that back onto a differently-named DB column (covers read and write).
    :param defaults: Fallback values applied to omitted columns before a save.
    """

    name: str
    key: str = 'id'
    fields: tuple[str, ...] = ()
    columns: tuple[str, ...] = ()
    aliases: Mapping[str, str] = field(default_factory=dict)
    defaults: Mapping[str, Any] = field(default_factory=dict)

    # An unusable but well-formed search clause matches nothing (unknown field, malformed value).
    _no_match: ClassVar[tuple[str, tuple[Any, ...]]] = ('0 = 1', ())

    _like_patterns: ClassVar[dict[str, str]] = {'begins': '{}%', 'contains': '%{}%', 'ends': '%{}'}

    _operators: ClassVar[dict[str, frozenset[str]]] = {
        'text': frozenset({'is', 'begins', 'contains', 'ends'}),
        'int': frozenset({'is', 'in', 'not in', 'between'}),
    }

    def _column(self, exposed: str) -> str:
        """Resolve an exposed field name to its DB column; recid is the key, aliases remap the rest.

        :param exposed: The exposed field name.
        :returns: The backing DB column name.
        """
        return self.key if exposed == 'recid' else self.aliases.get(exposed, exposed)

    def _projection(self, exposed: str) -> str:
        """Render one SELECT projection term, aliased when the exposed name differs from the column.

        :param exposed: The exposed field name.
        :returns: The SELECT projection term.
        """
        column = self._column(exposed)
        return f'`{column}` AS `{exposed}`' if column != exposed else f'`{exposed}`'

    @property
    def _select(self) -> str:
        """Build the base SELECT of every exposed field.

        :returns: The base SELECT statement.
        """
        projection = ', '.join(self._projection(exposed) for exposed in self.fields)
        return f'SELECT {projection} FROM `{self.name}`'

    @property
    def _select_one(self) -> str:
        """Build the base SELECT filtered to a single row by key.

        :returns: The base SELECT statement with a trailing WHERE on the key.
        """
        return f'{self._select} WHERE `{self.name}`.`{self.key}` = %s'

    @property
    def _insert(self) -> str:
        """Build the INSERT of the writable columns.

        :returns: The parametrized INSERT statement.
        """
        columns = ', '.join(f'`{self._column(name)}`' for name in self.columns)
        placeholders = ', '.join(['%s'] * len(self.columns))
        return f'INSERT INTO `{self.name}` ({columns}) VALUES ({placeholders})'

    def _update_of(self, columns: tuple[str, ...]) -> str:
        """Build an UPDATE of the given writable columns by key.

        :param columns: The exposed names to set, a subset of columns.
        :returns: The parametrized UPDATE statement.
        """
        assignments = ', '.join(f'`{self._column(name)}` = %s' for name in columns)
        return f'UPDATE `{self.name}` SET {assignments} WHERE `{self.key}` = %s'

    @property
    def _update(self) -> str:
        """Build the UPDATE of all writable columns by key.

        :returns: The parametrized UPDATE statement.
        """
        return self._update_of(self.columns)

    def _delete_of(self, count: int) -> str:
        """Build the DELETE of the given number of keys by an IN list.

        :param count: The number of key placeholders in the IN list.
        :returns: The parametrized DELETE statement.
        """
        placeholders = ', '.join(['%s'] * count)
        return f'DELETE FROM `{self.name}` WHERE `{self.key}` IN ({placeholders})'

    @staticmethod
    def _escape_like(value: str) -> str:
        """Escape the LIKE wildcards and the escape character itself in a search value.

        :param value: The raw search value.
        :returns: The value with backslash, percent and underscore escaped.
        """
        return value.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')

    @classmethod
    def _text_clause(cls, column: str, operator: str, value: Any) -> tuple[str, tuple[Any, ...]]:
        """Build one text search condition; equality relies on the ci collation for case-insensitivity.

        :param column: The backtick-quoted, whitelisted column.
        :param operator: The pre-validated w2ui text operator.
        :param value: The search value.
        :returns: The (condition, params) pair.
        """
        if operator == 'is':
            return f'{column} = %s', (str(value),)

        return f'{column} LIKE %s', (cls._like_patterns[operator].format(cls._escape_like(str(value))),)

    @classmethod
    def _int_clause(cls, column: str, operator: str, value: Any) -> tuple[str, tuple[Any, ...]]:
        """Build one int search condition; values are pre-coerced with int() so a bad value matches nothing.

        :param column: The backtick-quoted, whitelisted column.
        :param operator: The pre-validated w2ui int operator.
        :param value: The search value; a scalar or a list for in/not in operator, and a list for between operator.
        :returns: The (condition, params) pair or a no-match condition for a malformed value.
        """
        try:
            if operator == 'is':
                return f'{column} = %s', (int(value),)

            if operator in ('in', 'not in'):
                values = value if isinstance(value, list) else [value]
                if not values:
                    return cls._no_match

                placeholders = ', '.join(['%s'] * len(values))
                negate = 'NOT ' if operator == 'not in' else ''
                return f'{column} {negate}IN ({placeholders})', tuple(int(item) for item in values)

            if not isinstance(value, list):
                return cls._no_match

            return f'{column} BETWEEN %s AND %s', (int(value[0]), int(value[1]))

        except (IndexError, TypeError, ValueError):
            return cls._no_match

    def _search_clause(self, search: dict[str, Any]) -> tuple[str, tuple[Any, ...]] | None:
        """Build one WHERE condition from a w2ui search clause.

        :param search: The w2ui search clause (field, type, operator, value).
        :returns: The (condition, params) pair, None when the search type or operator is unknown,
            or a no-match condition for an unknown field or malformed value.
        """
        search_type = str(search.get('type'))
        operator = search.get('operator')
        if operator not in self._operators.get(search_type, frozenset()):
            return None

        field_name = search.get('field')
        if field_name not in self.fields:
            return self._no_match

        clause = self._text_clause if search_type == 'text' else self._int_clause
        return clause(f'`{field_name}`', operator, search.get('value'))

    def _search(self, page: PageRequest) -> tuple[str, tuple[Any, ...]]:
        """Compose the WHERE clause from the page's searches.

        Zero usable clauses keep the full set under AND, but match nothing under OR.

        :param page: The search/sort/paging request.
        :returns: The WHERE text (leading space) or an empty string, and its bound parameters.
        """
        clauses = [clause for clause in (self._search_clause(search) for search in page.searches)
                   if clause is not None]
        if not clauses:
            if page.searches and page.or_logic:
                return f' WHERE {self._no_match[0]}', ()
            return '', ()

        connector = ' OR ' if page.or_logic else ' AND '
        conditions = connector.join(condition for condition, _ in clauses)
        params = tuple(param for _, clause_params in clauses for param in clause_params)
        return f' WHERE {conditions}', params

    @staticmethod
    def _limit(page: PageRequest) -> tuple[str, tuple[Any, ...]]:
        """Compose the LIMIT/OFFSET clause from the page; no limit yields no clause.

        :param page: The search/sort/paging request.
        :returns: The LIMIT/OFFSET text (leading space) or an empty string, and its bound parameters.
        """
        if page.limit is None:
            return '', ()
        if page.offset is None:
            return ' LIMIT %s', (page.limit,)

        return ' LIMIT %s OFFSET %s', (page.limit, page.offset)

    def _order(self, page: PageRequest) -> str:
        """Compose the ORDER BY clause from the page's sorts; unknown fields are skipped.

        A LIMITed query always gets recid appended as a deterministic tiebreaker (unless already a sort key),
        since SQL row order without one is unspecified and consecutive pages could overlap or skip rows.

        :param page: The search/sort/paging request.
        :returns: The ORDER BY text (leading space) or an empty string.
        """
        sorts = [(sort['field'], sort.get('direction') == 'desc')
                 for sort in page.sorts if sort.get('field') in self.fields]
        terms = [f'`{name}` DESC' if desc else f'`{name}`' for name, desc in sorts]
        if page.limit is not None and not any(name == 'recid' for name, _ in sorts):
            terms.append('`recid`')
        if not terms:
            return ''
        return ' ORDER BY ' + ', '.join(terms)

    def _read(self, db: Selector, operation: str, params: tuple[Any, ...],
              page: PageRequest | None) -> tuple[list[dict[str, Any]], int]:
        """Run a base SELECT through the derived-table search/sort/page wrapper.

        The base query is wrapped as SELECT * FROM (...) so the clauses reference the output aliases (recid, ...)
        that w2ui searches by. When both limit and offset are set (paging) and the page cannot determine the total,
        it comes from a second COUNT(*) query.

        :param db: The executor to run the queries on.
        :param operation: The base SELECT statement.
        :param params: The base SELECT's bound parameters.
        :param page: The search/sort/paging request; None runs the base SELECT unwrapped.
        :returns: The matching rows and the total match count.
        """
        if page is None:
            rows = db.select(operation, params)
            return rows, len(rows)

        where, where_params = self._search(page)
        paging, paging_params = self._limit(page)

        wrapped = f'SELECT * FROM ({operation}) AS `t`{where}{self._order(page)}{paging}'
        rows = db.select(wrapped, params + where_params + paging_params)

        if page.limit is None or page.offset is None:
            return rows, len(rows)

        if len(rows) < page.limit and (rows or not page.offset):
            return rows, page.offset + len(rows)

        count = f'SELECT COUNT(*) AS `total` FROM ({operation}) AS `t`{where}'
        return rows, int(db.select(count, params + where_params)[0]['total'])

    def get(self, db: Selector, recid: int = 0, page: PageRequest | None = None,  # pylint: disable=unused-argument
            **kwargs: Any) -> tuple[list[dict[str, Any]], int]:
        """Read the table, whole or by recid, through the paging pipeline.

        :param db: The executor to run the queries on.
        :param recid: The key value to fetch; 0 fetches every row.
        :param page: The search/sort/paging request; None returns every matching row.
        :param kwargs: Extra arguments a subclass may consume.
        :returns: The matching rows and the total match count.
        """
        operation = self._select_one if recid else self._select
        params: tuple[Any, ...] = (recid,) if recid else ()
        return self._read(db, operation, params, page)

    def save(self, db: Executor, save_recid: int, **fields: Any) -> int:
        """Insert or update one row and return the row count.

        The bound values are read from fields in column order. The parameter is save_recid, not recid:
        a posted record may itself carry a recid field.

        :param db: The executor to run the statement on.
        :param save_recid: The key value to update; 0 inserts a new row.
        :param fields: The posted record; only the writable columns are read.
        :returns: The number of rows affected.
        """
        for column, value in self.defaults.items():
            fields.setdefault(column, value)

        values = tuple(fields[column] for column in self.columns)
        if save_recid:
            return db.modify(self._update, values + (save_recid,))

        return db.modify(self._insert, values)

    def remove(self, db: Executor, ids: list[Any]) -> int:
        """Expand the DELETE's IN (%s) placeholder to the ids and delete; an empty list deletes nothing.

        :param db: The executor to run the statement on.
        :param ids: The key values to delete.
        :returns: The number of rows deleted.
        """
        if not ids:
            return 0
        params = tuple(ids)
        return db.modify(self._delete_of(len(params)), params)


class Records(Table):
    """The records admin table: reads the records, writes the rrset and record in one transaction.

    All joins are inner joins on NOT NULL foreign keys, so the join is 1:1 with records: a page that operates only
    on records table fields is answered by paging records by primary key first, then joining to just that page.
    Only a search or sort on a joined field falls back to the full join. The total is COUNT over records alone.
    """
    # Exposed fields resolvable on the records table without the join; a page confined to these takes the fast path.
    _local_fields: ClassVar[frozenset[str]] = frozenset({'recid', 'disabled', 'weight', 'content'})

    @property
    def _select(self) -> str:
        """Build the records join: one row per record with its attributes, projected under the exposed names.

        :returns: The joined SELECT statement.
        """
        return """
            SELECT `records`.`id` AS `recid`,
              `domains`.`domain`,
              `rrsets`.`name`,
              `rrsets`.`ttl`,
              `routings`.`policy`,
              `types`.`type` AS `name_type`,
              `records`.`disabled`,
              `records`.`weight`,
              `records`.`content`,
              `monitors`.`monitor`,
              `views`.`view`
            FROM `records`
              JOIN `rrsets` ON `records`.`rrset_id` = `rrsets`.`id`
              JOIN `domains` ON `rrsets`.`domain_id` = `domains`.`id`
              JOIN `types` ON `rrsets`.`type_value` = `types`.`value`
              JOIN `routings` ON `rrsets`.`routing_id` = `routings`.`id`
              JOIN `monitors` ON `records`.`monitor_id` = `monitors`.`id`
              JOIN `views` ON `records`.`view_id` = `views`.`id`
        """

    def _local_select(self, **kwargs: Any) -> tuple[str, tuple[Any, ...]]:  # pylint: disable=unused-argument
        """Build the records-only page source projecting the fields a local page searches and sorts by.

        :param kwargs: Extra arguments a subclass may consume.
        :returns: The records-only SELECT statement and its bound parameters.
        """
        return """
            SELECT `records`.`id` AS `recid`,
              `records`.`disabled`,
              `records`.`weight`,
              `records`.`content`
            FROM `records`
        """, ()

    def _is_local(self, page: PageRequest) -> bool:
        """Report whether the page searches and sorts only by records-table fields.

        :param page: The search/sort/paging request.
        :returns: True when every referenced field is local, so the page can skip the full join.
        """
        referenced = {clause.get('field') for clause in page.searches}
        referenced.update(clause.get('field') for clause in page.sorts)
        return referenced <= self._local_fields

    def _join_page(self, db: Selector, id_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Join the lookup tables onto a page of record ids, preserving the page order.

        :param db: The executor to run the query on.
        :param id_rows: The page rows carrying each record id as recid, in order.
        :returns: The joined rows in the same order.
        """
        if not id_rows:
            return []
        ids = [row['recid'] for row in id_rows]
        placeholders = ', '.join(['%s'] * len(ids))
        rows = db.select(f'{self._select} WHERE `records`.`id` IN ({placeholders})', tuple(ids))
        # The join order is unspecified, so restore it from id_rows and skip any concurrent deleted ids.
        by_id = {row['recid']: row for row in rows}
        return [by_id[recid] for recid in ids if recid in by_id]

    def _full_read(self, db: Selector, recid: int,  # pylint: disable=unused-argument
                   page: PageRequest | None, **kwargs: Any) -> tuple[list[dict[str, Any]], int]:
        """Read via the full join wrapped by the base search/sort/paging pipeline.

        :param db: The executor to run the queries on.
        :param recid: The record id to fetch; 0 fetches every row.
        :param page: The search/sort/paging request.
        :param kwargs: Extra arguments a subclass may consume.
        :returns: The matching rows and the total match count.
        """
        return super().get(db, recid, page)

    def get(self, db: Selector, recid: int = 0, page: PageRequest | None = None,
            **kwargs: Any) -> tuple[list[dict[str, Any]], int]:
        """Read the records, whole or by recid, paging by primary key before the join when possible.

        A page that searches and sorts only by records-table fields selects its ids from records first, then joins
        the lookup tables to just that page; anything referencing a joined field falls back to the full join.
        Both shapes return identical rows, order and total.

        :param db: The executor to run the queries on.
        :param recid: The record id to fetch; 0 fetches every row.
        :param page: The search/sort/paging request; None returns every row.
        :param kwargs: Extra arguments a subclass may consume.
        :returns: The matching rows and the total match count.
        """
        if recid or page is None or not self._is_local(page):
            return self._full_read(db, recid, page, **kwargs)

        source, params = self._local_select(**kwargs)
        id_rows, total = self._read(db, source, params, page)
        return self._join_page(db, id_rows), total

    def save(self, db: Executor, save_recid: int, **fields: Any) -> int:
        """Insert or update a record across the rrset and record tables in one transaction.

        Statement one upserts the rrset and pins its id with LAST_INSERT_ID. Statement two writes the record,
        taking the rrset id from LAST_INSERT_ID() rather than a `rrsets` subquery (the record UPDATE can fire
        the GC trigger and raise SQL error 1442). The summed affected-row count is returned.

        :param db: The executor to run the transaction on.
        :param save_recid: The record id to update; 0 inserts a new record.
        :param fields: domain, name, name_type, ttl, policy, content, monitor, view, and optional disabled, weight.
        :returns: The summed affected-row count across both statements.
        """
        domain, name, name_type, ttl, policy = (
            fields['domain'], fields['name'], fields['name_type'], fields['ttl'], fields['policy']
        )
        rrset_upsert = ("""
            INSERT INTO `rrsets` (`domain_id`, `name`, `type_value`, `ttl`, `routing_id`)
              SELECT (SELECT `id` FROM `domains` WHERE `domain` = %s), %s,
                (SELECT `value` FROM `types` WHERE `type` = %s), %s,
                (SELECT `id` FROM `routings` WHERE `policy` = %s)
            ON DUPLICATE KEY UPDATE `id` = LAST_INSERT_ID(`id`), `ttl` = %s,
              `routing_id` = (SELECT `id` FROM `routings` WHERE `policy` = %s)
        """, (domain, name, name_type, ttl, policy, ttl, policy))

        content, monitor, view = fields['content'], fields['monitor'], fields['view']
        # The admin form posts the toggle value as string true/false; coerce to int.
        disabled = int(str(fields.get('disabled', 0)).lower() in ('1', 'true'))
        weight = fields.get('weight', 0)

        record_write: tuple[str, tuple[Any, ...]]
        if save_recid:
            record_write = ("""
                UPDATE `records`
                SET `rrset_id` = LAST_INSERT_ID(),
                  `content` = %s,
                  `monitor_id` = (SELECT `id` FROM `monitors` WHERE `monitor` = %s),
                  `view_id` = (SELECT `id` FROM `views` WHERE `view` = %s),
                  `disabled` = %s,
                  `weight` = %s
                WHERE `id` = %s
            """, (content, monitor, view, disabled, weight, save_recid))
        else:
            record_write = ("""
                INSERT INTO `records`
                  (`rrset_id`, `content`, `monitor_id`, `view_id`, `disabled`, `weight`)
                  SELECT LAST_INSERT_ID(), %s,
                    (SELECT `id` FROM `monitors` WHERE `monitor` = %s),
                    (SELECT `id` FROM `views` WHERE `view` = %s), %s, %s
            """, (content, monitor, view, disabled, weight))

        return db.execute_transaction([rrset_upsert, record_write])


class Status(Records):
    """The status admin table: a read-only table over records adding the computed On/Off column.

    The status is a SQL CASE over disabled and the down-id snapshot; both depend on records, so status
    is a local field and the fast page path (records first, then the join) still applies to its default
    status/recid sort. Only the fallback full path keeps the CASE on top of the wrapped join.
    """
    # The computed status rides on the records-only fast page too, so a status search or sort stays local.
    _local_fields: ClassVar[frozenset[str]] = Records._local_fields | {'status'}

    def _local_select(self, down_ids: Sequence[int] = (), **kwargs: Any) -> tuple[str, tuple[Any, ...]]:
        """Build the records-only page source with the computed On/Off status column.

        :param down_ids: The content ids currently marked down.
        :param kwargs: Extra arguments a subclass may consume.
        :returns: The records-only SELECT statement with the status CASE and its bound parameters.
        """
        down_condition = ''
        params: tuple[Any, ...] = ()
        if down_ids:
            placeholders = ', '.join(['%s'] * len(down_ids))
            down_condition = f' OR `records`.`id` IN ({placeholders})'
            params = tuple(down_ids)

        return f"""
            SELECT `records`.`id` AS `recid`,
              `records`.`disabled`,
              `records`.`weight`,
              `records`.`content`,
              CASE WHEN `records`.`disabled`{down_condition} THEN 'Off' ELSE 'On' END AS `status`
            FROM `records`
        """, params

    def _join_page(self, db: Selector, id_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Join the lookup tables onto the page and carry each row's computed status across.

        :param db: The executor to run the query on.
        :param id_rows: The page rows carrying each record id as recid and its status, in order.
        :returns: The joined rows with their status, in the same order.
        """
        rows = super()._join_page(db, id_rows)
        status = {row['recid']: row['status'] for row in id_rows}
        for row in rows:
            row['status'] = status[row['recid']]
        return rows

    def _full_read(self, db: Selector, recid: int, page: PageRequest | None,
                   down_ids: Sequence[int] = (), **kwargs: Any) -> tuple[list[dict[str, Any]], int]:
        """Read via the full join wrapped as a derived table with the status CASE on top.

        :param db: The executor to run the queries on.
        :param recid: The record id to fetch; 0 fetches every row.
        :param page: The search/sort/paging request.
        :param down_ids: The content ids currently marked down.
        :param kwargs: Extra arguments a subclass may consume.
        :returns: The matching status rows and the total match count.
        """
        down_condition = ''
        params: tuple[Any, ...] = ()
        if down_ids:
            placeholders = ', '.join(['%s'] * len(down_ids))
            down_condition = f' OR `r`.`recid` IN ({placeholders})'
            params = tuple(down_ids)

        operation = f"""
            SELECT `r`.*,
              CASE WHEN `r`.`disabled`{down_condition} THEN 'Off' ELSE 'On' END AS `status`
            FROM ({self._select_one if recid else self._select}) AS `r`
        """
        if recid:
            params += (recid,)

        return self._read(db, operation, params, page)

    def save(self, db: Executor, save_recid: int, **fields: Any) -> int:
        """Reject the write: the status table is read-only.

        :raises ValueError: Always.
        """
        raise ValueError('status is read-only')

    def remove(self, db: Executor, ids: list[Any]) -> int:
        """Reject the write: the status table is read-only.

        :raises ValueError: Always.
        """
        raise ValueError('status is read-only')


class Users(Table):
    """The users admin table: the password hash is never selected.

    The read projects a mask instead of the hash, so an update posting the mask keeps the existing password.
    """

    _mask: ClassVar[str] = '********'

    def _projection(self, exposed: str) -> str:
        """Render one SELECT projection term; password projects the constant mask, never the hash.

        :param exposed: The exposed field name.
        :returns: The projection term for the SELECT list.
        """
        return f"'{self._mask}' AS `password`" if exposed == 'password' else super()._projection(exposed)

    def save(self, db: Executor, save_recid: int, **fields: Any) -> int:
        """Insert or update a user row, hashing the password before binding.

        An update posting the mask value keeps the existing hash by updating only user and name.

        :param db: The executor to run the statement on.
        :param save_recid: The user id to update; 0 inserts a new row.
        :param fields: The posted record: user, name and the plaintext password.
        :returns: The number of rows affected.
        """
        user, name, password = fields['user'], fields['name'], fields['password']

        if not save_recid:
            return db.modify(self._insert, (user, name, hash_password(password)))

        if password == self._mask:
            return db.modify(self._update_of(('user', 'name')), (user, name, save_recid))

        return db.modify(self._update, (user, name, hash_password(password), save_recid))

    def check_user(self, db: Selector, user: str, password: str) -> list[dict[str, Any]]:
        """Return [{'valid': 1}] if the user/password pair is valid, an empty list otherwise.

        The stored crypt(3) hash carries its own salt, so the password is verified in Python rather than in SQL.
        An unknown user yields an empty stored hash, passed to verify_password.

        :param db: The executor to run the query on.
        :param user: The login name.
        :param password: The plaintext password to verify.
        :returns: [{'valid': 1}] on a valid pair, an empty list otherwise.
        """
        rows = db.select(f'SELECT `password` FROM `{self.name}` WHERE `user` = %s', (user,))
        stored = rows[0]['password'] if rows else ''

        if verify_password(password, stored):
            return [{'valid': 1}]

        return []


DOMAINS = Table(
    name='domains',
    fields=('recid', 'domain', 'description'),
    columns=('domain', 'description'),
    defaults={'description': ''}
)

MONITORS = Table(
    name='monitors',
    fields=('recid', 'monitor', 'monitor_json'),
    columns=('monitor', 'monitor_json')
)

# records never bind columns: save is a custom two-statement transaction.
RECORDS = Records(
    name='records',
    fields=('recid', 'domain', 'name', 'ttl', 'policy', 'name_type',
            'disabled', 'weight', 'content', 'monitor', 'view')
)

ROUTINGS = Table(
    name='routings',
    fields=('recid', 'policy', 'policy_json'),
    columns=('policy', 'policy_json')
)

STATUS = Status(
    name='records',
    fields=RECORDS.fields + ('status',)
)

TYPES = Table(
    name='types',
    key='value',
    fields=('recid', 'name_type', 'description'),
    columns=('recid', 'name_type', 'description'),
    aliases={'name_type': 'type'}
)

USERS = Users(
    name='users',
    fields=('recid', 'user', 'name', 'password'),
    columns=('user', 'name', 'password')
)

VIEWS = Table(
    name='views',
    fields=('recid', 'view', 'rule'),
    columns=('view', 'rule')
)

# The w2ui data token -> table registry.
TABLES: Mapping[str, Table] = MappingProxyType({
    'domains': DOMAINS,
    'monitors': MONITORS,
    'records': RECORDS,
    'routings': ROUTINGS,
    'status': STATUS,
    'types': TYPES,
    'users': USERS,
    'views': VIEWS,
})
