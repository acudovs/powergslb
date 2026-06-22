"""Admin CRUD path: the queries the w2ui handler needs."""

import abc
from typing import Any, ClassVar

from powergslb.system.password import hash_password, verify_password

__all__ = ['W2UIDatabaseMixIn']


class W2UIDatabaseMixIn(abc.ABC):
    """w2ui related queries: CRUD for every table plus user authentication."""

    # get_users returns this placeholder instead of the password hash; the admin UI pre-fills it,
    # so save_users treats it as "keep the existing password".
    password_mask: ClassVar[str] = '********'

    def _delete(self, operation: str, ids: list[Any]) -> int:
        """Expand the operation's IN (%s) placeholder to the ids and delete; an empty list deletes nothing."""
        if not ids:
            return 0  # an empty IN () is a syntax error; nothing to delete
        params = tuple(ids)
        params_format = ', '.join(['%s'] * len(params))
        operation %= params_format

        return self._modify(operation, params)

    @abc.abstractmethod
    def _select(self, operation: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        pass

    @abc.abstractmethod
    def _modify(self, operation: str, params: tuple[Any, ...] = ()) -> int:
        pass

    @abc.abstractmethod
    def _execute_transaction(self, statements: list[tuple[str, tuple[Any, ...]]]) -> int:
        pass

    def check_user(self, user: str, password: str) -> list[dict[str, Any]]:
        """Return [{'valid': 1}] if the user/password pair is valid, an empty list otherwise.

        The stored crypt(3) hash carries its own salt, so the password is verified in Python rather than in SQL.
        An unknown user yields an empty stored hash, which verify_password rejects in constant time, so the timing
        does not reveal that the user is absent.
        """
        operation = """
            SELECT `password` FROM `users`
            WHERE `user` = %s
        """
        rows = self._select(operation, (user,))
        stored = rows[0]['password'] if rows else ''

        if verify_password(password, stored):
            return [{'valid': 1}]

        return []

    def delete_domains(self, ids: list[Any]) -> int:
        """Delete domain rows by id and return the count of deleted rows."""
        operation = """
            DELETE FROM `domains`
            WHERE `id` IN (%s)
        """

        return self._delete(operation, ids)

    def delete_monitors(self, ids: list[Any]) -> int:
        """Delete monitor rows by id and return the count of deleted rows."""
        operation = """
            DELETE FROM `monitors`
            WHERE `id` IN (%s)
        """

        return self._delete(operation, ids)

    def delete_records(self, ids: list[Any]) -> int:
        """Delete record rows by id and return the count of deleted rows."""
        operation = """
            DELETE FROM `records`
            WHERE `id` IN (%s)
        """

        return self._delete(operation, ids)

    def delete_types(self, values: list[Any]) -> int:
        """Delete type rows by value and return the count of deleted rows."""
        operation = """
            DELETE FROM `types`
            WHERE `value` IN (%s)
        """

        return self._delete(operation, values)

    def delete_users(self, ids: list[Any]) -> int:
        """Delete user rows by id and return the count of deleted rows."""
        operation = """
            DELETE FROM `users`
            WHERE `id` IN (%s)
        """

        return self._delete(operation, ids)

    def delete_views(self, ids: list[Any]) -> int:
        """Delete view rows by id and return the count of deleted rows."""
        operation = """
            DELETE FROM `views`
            WHERE `id` IN (%s)
        """

        return self._delete(operation, ids)

    def get_status(self) -> list[dict[str, Any]]:
        """Return all records with health and disabled status for the admin panel."""
        operation = """
            SELECT `domains`.`domain`,
              `rrsets`.`name`,
              `rrsets`.`ttl`,
              `rrsets`.`persistence`,
              `types`.`type` AS `name_type`,
              `records`.`disabled`,
              `records`.`fallback`,
              `records`.`weight`,
              `records`.`id`,
              `records`.`content`,
              `monitors`.`monitor`,
              `views`.`view`
            FROM `records`
              JOIN `rrsets` ON `records`.`rrset_id` = `rrsets`.`id`
              JOIN `domains` ON `rrsets`.`domain_id` = `domains`.`id`
              JOIN `types` ON `rrsets`.`type_value` = `types`.`value`
              JOIN `monitors` ON `records`.`monitor_id` = `monitors`.`id`
              JOIN `views` ON `records`.`view_id` = `views`.`id`
        """

        return self._select(operation)

    def get_domains(self, recid: int = 0) -> list[dict[str, Any]]:
        """Return all domains, or a single domain if recid is given."""
        operation = """
            SELECT `id` AS `recid`,
              `domain`
            FROM `domains`
        """
        params: tuple[Any, ...] = ()

        if recid:
            operation += """
                WHERE `id` = %s
            """
            params += (recid,)

        return self._select(operation, params)

    def get_monitors(self, recid: int = 0) -> list[dict[str, Any]]:
        """Return all monitors, or a single monitor if recid is given."""
        operation = """
            SELECT `id` AS `recid`,
              `monitor`,
              `monitor_json`
            FROM `monitors`
        """
        params: tuple[Any, ...] = ()

        if recid:
            operation += """
                WHERE `id` = %s
            """
            params += (recid,)

        return self._select(operation, params)

    def get_records(self, recid: int = 0) -> list[dict[str, Any]]:
        """Return all records, or a single record if recid is given."""
        operation = """
            SELECT `domains`.`domain`,
              `rrsets`.`name`,
              `rrsets`.`ttl`,
              `rrsets`.`persistence`,
              `types`.`type` AS `name_type`,
              `records`.`id` AS `recid`,
              `records`.`disabled`,
              `records`.`fallback`,
              `records`.`weight`,
              `records`.`content`,
              `monitors`.`monitor`,
              `views`.`view`
            FROM `records`
              JOIN `rrsets` ON `records`.`rrset_id` = `rrsets`.`id`
              JOIN `domains` ON `rrsets`.`domain_id` = `domains`.`id`
              JOIN `types` ON `rrsets`.`type_value` = `types`.`value`
              JOIN `monitors` ON `records`.`monitor_id` = `monitors`.`id`
              JOIN `views` ON `records`.`view_id` = `views`.`id`
        """
        params: tuple[Any, ...] = ()

        if recid:
            operation += """
                WHERE `records`.`id` = %s
            """
            params += (recid,)

        return self._select(operation, params)

    def get_types(self, recid: int = 0) -> list[dict[str, Any]]:
        """Return all types, or a single type if recid is given."""
        operation = """
            SELECT `value` AS `recid`,
              `type` AS `name_type`,
              `description`
            FROM `types`
        """
        params: tuple[Any, ...] = ()

        if recid:
            operation += """
                WHERE `value` = %s
            """
            params += (recid,)

        return self._select(operation, params)

    def get_users(self, recid: int = 0) -> list[dict[str, Any]]:
        """Return all users (password masked), or a single user if recid is given."""
        operation = """
            SELECT `id` AS `recid`,
              `user`,
              `name`,
              %s AS `password`
            FROM `users`
        """
        params: tuple[Any, ...] = (self.password_mask,)

        if recid:
            operation += """
                WHERE `id` = %s
            """
            params += (recid,)

        return self._select(operation, params)

    def get_views(self, recid: int = 0) -> list[dict[str, Any]]:
        """Return all views, or a single view if recid is given."""
        operation = """
            SELECT `id` AS `recid`,
              `view`,
              `rule`
            FROM `views`
        """
        params: tuple[Any, ...] = ()

        if recid:
            operation += """
                WHERE `id` = %s
            """
            params += (recid,)

        return self._select(operation, params)

    def save_domains(self, save_recid: int, domain: str, **_: Any) -> int:
        """Insert or update a domain row and return the row count."""
        if save_recid:
            operation = """
                UPDATE `domains`
                SET `domain` = %s
                WHERE `id` = %s
            """
            params: tuple[Any, ...] = (domain, save_recid)
        else:
            operation = """
                INSERT INTO `domains` (`domain`)
                VALUES (%s)
            """
            params = (domain,)

        return self._modify(operation, params)

    def save_monitors(self, save_recid: int, monitor: str, monitor_json: str, **_: Any) -> int:
        """Insert or update a monitor row and return the row count."""
        if save_recid:
            operation = """
                UPDATE `monitors`
                SET `monitor` = %s,
                  `monitor_json` = %s
                WHERE `id` = %s

            """
            params: tuple[Any, ...] = (monitor, monitor_json, save_recid)
        else:
            operation = """
                INSERT INTO `monitors` (`monitor`, `monitor_json`)
                VALUES (%s, %s)
            """
            params = (monitor, monitor_json)

        return self._modify(operation, params)

    def save_records(self, save_recid: int, domain: str, name: str, name_type: str, ttl: int, content: str,
                     monitor: str, view: str, disabled: Any = 0, fallback: Any = 0,
                     persistence: int = 0, weight: int = 0, **_: Any) -> int:
        """Insert or update a record across the rrset and record levels in one transaction.

        Statement one upserts the rrset (zone + relative record name + type carrying ttl/persistence) and pins its
        id with LAST_INSERT_ID; statement two writes the record, taking the rrset id from LAST_INSERT_ID() rather
        than a `rrsets` subquery (the record UPDATE can fire the GC trigger, and a subquery on `rrsets` in that
        same statement would raise error 1442). The summed affected-row count is returned so a ttl-only edit and a
        content-only edit both report success.
        """
        # The admin form posts 'toggle' value as string 'true'/'false'; coerce to int.
        disabled = int(str(disabled).lower() in ('1', 'true'))
        fallback = int(str(fallback).lower() in ('1', 'true'))

        rrset_upsert = ("""
            INSERT INTO `rrsets` (`domain_id`, `name`, `type_value`, `ttl`, `persistence`)
              SELECT (SELECT `id` FROM `domains` WHERE `domain` = %s), %s,
                (SELECT `value` FROM `types` WHERE `type` = %s), %s, %s
            ON DUPLICATE KEY UPDATE `id` = LAST_INSERT_ID(`id`), `ttl` = %s, `persistence` = %s
        """, (domain, name, name_type, ttl, persistence, ttl, persistence))

        record_write: tuple[str, tuple[Any, ...]]
        if save_recid:
            record_write = ("""
                UPDATE `records`
                SET `rrset_id` = LAST_INSERT_ID(),
                  `content` = %s,
                  `monitor_id` = (SELECT `id` FROM `monitors` WHERE `monitor` = %s),
                  `view_id` = (SELECT `id` FROM `views` WHERE `view` = %s),
                  `disabled` = %s,
                  `fallback` = %s,
                  `weight` = %s
                WHERE `id` = %s
            """, (content, monitor, view, disabled, fallback, weight, save_recid))
        else:
            record_write = ("""
                INSERT INTO `records`
                  (`rrset_id`, `content`, `monitor_id`, `view_id`, `disabled`, `fallback`, `weight`)
                  SELECT LAST_INSERT_ID(), %s,
                    (SELECT `id` FROM `monitors` WHERE `monitor` = %s),
                    (SELECT `id` FROM `views` WHERE `view` = %s), %s, %s, %s
            """, (content, monitor, view, disabled, fallback, weight))

        return self._execute_transaction([rrset_upsert, record_write])

    def save_types(self, save_recid: int, description: str, name_type: str, recid: int, **_: Any) -> int:
        """Insert or update a type row and return the row count."""
        if save_recid:
            operation = """
                UPDATE `types`
                SET `value` = %s,
                  `type` = %s,
                  `description` = %s
                WHERE `value` = %s

            """
            params: tuple[Any, ...] = (recid, name_type, description, save_recid)
        else:
            operation = """
                INSERT INTO `types` (`value`, `type`, `description`)
                VALUES (%s, %s, %s)
            """
            params = (recid, name_type, description)

        return self._modify(operation, params)

    def save_users(self, save_recid: int, user: str, name: str, password: str, **_: Any) -> int:
        """Insert or update a user row and return the row count."""
        if save_recid:
            if password == self.password_mask:
                operation = """
                    UPDATE `users`
                    SET `user` = %s,
                      `name` = %s
                    WHERE `id` = %s
                """
                params: tuple[Any, ...] = (user, name, save_recid)
            else:
                operation = """
                    UPDATE `users`
                    SET `user` = %s,
                      `name` = %s,
                      `password` = %s
                    WHERE `id` = %s
                """
                params = (user, name, hash_password(password), save_recid)
        else:
            operation = """
                INSERT INTO `users` (`user`, `name`, `password`)
                VALUES (%s, %s, %s)
            """
            params = (user, name, hash_password(password))

        return self._modify(operation, params)

    def save_views(self, save_recid: int, view: str, rule: str, **_: Any) -> int:
        """Insert or update a view row and return the row count."""
        if save_recid:
            operation = """
                UPDATE `views`
                SET `view` = %s,
                  `rule` = %s
                WHERE `id` = %s

            """
            params: tuple[Any, ...] = (view, rule, save_recid)
        else:
            operation = """
                INSERT INTO `views` (`view`, `rule`)
                VALUES (%s, %s)
            """
            params = (view, rule)

        return self._modify(operation, params)
