import abc

__all__ = ['W2UIDatabaseMixIn']


class W2UIDatabaseMixIn(object):
    """
    W2UIDatabaseMixIn class contains w2ui related queries
    """
    __metaclass__ = abc.ABCMeta

    def _delete(self, operation, ids):
        params = tuple(ids)
        params_format = ', '.join(['%s'] * len(params))
        operation %= params_format

        return self._execute(operation, params)

    @abc.abstractmethod
    def _execute(self, operation, params):
        pass

    def _clean_contents(self, content_monitor_id):
        operation = """
            DELETE IGNORE `contents_monitors`,
               `contents`
            FROM `contents_monitors`
              JOIN `contents` ON `contents_monitors`.`content_id` = `contents`.`id`
            WHERE `contents_monitors`.`id` = %s
        """
        params = (content_monitor_id,)

        return self._execute(operation, params)

    def _clean_names(self, name_type_id):
        operation = """
            DELETE IGNORE `names_types`,
               `names`
            FROM `names_types`
              JOIN `names` ON `names_types`.`name_id` = `names`.`id`
            WHERE `names_types`.`id` = %s
        """
        params = (name_type_id,)

        return self._execute(operation, params)

    def _insert_names(self, domain, name):
        operation = """
            INSERT IGNORE INTO `names` (`domain_id`, `name`)
              SELECT
                (SELECT `id`
                 FROM `domains`
                 WHERE `domain` = %s) AS `domain_id`,
                %s AS `name`
        """
        params = (domain, name)

        return self._execute(operation, params)

    def _insert_names_types(self, domain, name, name_type, ttl, persistence):
        operation = """
            INSERT INTO `names_types` (`name_id`, `type_value`, `ttl`, `persistence`)
              SELECT
                (SELECT `names`.`id`
                 FROM `names`
                   JOIN `domains` ON `names`.`domain_id` = `domains`.`id`
                 WHERE `name` = %s
                   AND `domains`.`domain` = %s) AS `name_id`,
                (SELECT `value`
                 FROM `types`
                 WHERE `type` = %s) AS `type_value`,
                %s AS `ttl`,
                %s AS `persistence`
            ON DUPLICATE KEY UPDATE
              `ttl` = %s,
              `persistence` = %s
        """
        params = (name, domain, name_type, ttl, persistence, ttl, persistence)

        return self._execute(operation, params)

    def _insert_contents(self, content):
        operation = """
            INSERT IGNORE INTO `contents` (`content`)
            VALUES (%s)
        """
        params = (content,)

        return self._execute(operation, params)

    def _insert_contents_monitors(self, content, monitor):
        operation = """
            INSERT IGNORE INTO `contents_monitors` (`content_id`, `monitor_id`)
              SELECT
                (SELECT `id`
                 FROM `contents`
                 WHERE `content` = %s) AS `content_id`,
                (SELECT `id`
                 FROM `monitors`
                 WHERE `monitor` = %s) AS `monitor_id`
        """
        params = (content, monitor)

        return self._execute(operation, params)

    def check_user(self, user, password):
        operation = """
            SELECT 1 FROM `users`
            WHERE `user` = %s
              AND `password` = PASSWORD(%s)
        """
        params = (user, password)

        return self._execute(operation, params)

    def delete_domains(self, ids):
        operation = """
            DELETE FROM `domains`
            WHERE `id` IN (%s)
        """

        return self._delete(operation, ids)

    def delete_monitors(self, ids):
        operation = """
            DELETE FROM `monitors`
            WHERE `id` IN (%s)
        """

        return self._delete(operation, ids)

    def delete_records(self, ids):
        operation = """
            DELETE IGNORE `records`,
              `names_types`,
              `names`,
              `contents_monitors`,
              `contents`
            FROM `records`
              JOIN `names_types` ON `records`.`name_type_id` = `names_types`.`id`
              JOIN `names` ON `names_types`.`name_id` = `names`.`id`
              JOIN `contents_monitors` ON `records`.`content_monitor_id` = `contents_monitors`.`id`
              JOIN `contents` ON `contents_monitors`.`content_id` = `contents`.`id`
            WHERE `records`.`id` = %s
        """

        return sum(self._execute(operation, (id,)) for id in ids)

    def delete_types(self, values):
        operation = """
            DELETE FROM `types`
            WHERE `value` IN (%s)
        """

        return self._delete(operation, values)

    def delete_users(self, ids):
        operation = """
            DELETE FROM `users`
            WHERE `id` IN (%s)
        """

        return self._delete(operation, ids)

    def delete_views(self, ids):
        operation = """
            DELETE FROM `views`
            WHERE `id` IN (%s)
        """

        return self._delete(operation, ids)

    def get_status(self):
        operation = """
            SELECT `domains`.`domain`,
              `names`.`name`,
              `names_types`.`ttl`,
              `names_types`.`persistence`,
              `types`.`type` AS `name_type`,
              `records`.`disabled`,
              `records`.`fallback`,
              `records`.`weight`,
              `contents_monitors`.`id`,
              `contents`.`content`,
              `monitors`.`monitor`,
              `views`.`view`
            FROM `domains`
              JOIN `names` ON `domains`.`id` = `names`.`domain_id`
              JOIN `names_types` ON `names`.`id` = `names_types`.`name_id`
              JOIN `types` ON `names_types`.`type_value` = `types`.`value`
              JOIN `records` ON `names_types`.`id` = `records`.`name_type_id`
              JOIN `contents_monitors` ON `records`.`content_monitor_id` = `contents_monitors`.`id`
              JOIN `contents` ON `contents_monitors`.`content_id` = `contents`.`id`
              JOIN `monitors` ON `contents_monitors`.`monitor_id` = `monitors`.`id`
              JOIN `views` ON `records`.`view_id` = `views`.`id`
        """

        return self._execute(operation)

    def get_domains(self, recid=0):
        operation = """
            SELECT `id` AS `recid`,
              `domain`
            FROM `domains`
        """
        params = ()

        if recid:
            operation += """
                WHERE `id` = %s
            """
            params += (recid,)

        return self._execute(operation, params)

    def get_monitors(self, recid=0):
        operation = """
            SELECT `id` AS `recid`,
              `monitor`,
              `monitor_json`
            FROM `monitors`
        """
        params = ()

        if recid:
            operation += """
                WHERE `id` = %s
            """
            params += (recid,)

        return self._execute(operation, params)

    def get_records(self, recid=0):
        operation = """
            SELECT `domains`.`domain`,
              `names`.`name`,
              `names_types`.`ttl`,
              `names_types`.`persistence`,
              `types`.`type` AS `name_type`,
              `records`.`id` AS `recid`,
              `records`.`disabled`,
              `records`.`fallback`,
              `records`.`weight`,
              `contents`.`content`,
              `monitors`.`monitor`,
              `views`.`view`
            FROM `domains`
              JOIN `names` ON `domains`.`id` = `names`.`domain_id`
              JOIN `names_types` ON `names`.`id` = `names_types`.`name_id`
              JOIN `types` ON `names_types`.`type_value` = `types`.`value`
              JOIN `records` ON `names_types`.`id` = `records`.`name_type_id`
              JOIN `contents_monitors` ON `records`.`content_monitor_id` = `contents_monitors`.`id`
              JOIN `contents` ON `contents_monitors`.`content_id` = `contents`.`id`
              JOIN `monitors` ON `contents_monitors`.`monitor_id` = `monitors`.`id`
              JOIN `views` ON `records`.`view_id` = `views`.`id`
        """
        params = ()

        if recid:
            operation += """
                WHERE `records`.`id` = %s
            """
            params += (recid,)

        return self._execute(operation, params)

    def get_types(self, recid=0):
        operation = """
            SELECT `value` AS `recid`,
              `type` AS `name_type`,
              `description`
            FROM `types`
        """
        params = ()

        if recid:
            operation += """
                WHERE `value` = %s
            """
            params += (recid,)

        return self._execute(operation, params)

    def get_users(self, recid=0):
        operation = """
            SELECT `id` AS `recid`,
              `user`,
              `name`,
              `password`
            FROM `users`
        """
        params = ()

        if recid:
            operation += """
                WHERE `id` = %s
            """
            params += (recid,)

        return self._execute(operation, params)

    def get_views(self, recid=0):
        operation = """
            SELECT `id` AS `recid`,
              `view`,
              `rule`
            FROM `views`
        """
        params = ()

        if recid:
            operation += """
                WHERE `id` = %s
            """
            params += (recid,)

        return self._execute(operation, params)

    def save_domains(self, save_recid, domain, **_):
        if save_recid:
            operation = """
                UPDATE `domains`
                SET `domain` = %s
                WHERE `id` = %s
            """
            params = (domain, save_recid)
        else:
            operation = """
                INSERT INTO `domains` (`domain`)
                VALUES (%s)
            """
            params = (domain,)

        return self._execute(operation, params)

    def save_monitors(self, save_recid, monitor, monitor_json, **_):
        if save_recid:
            operation = """
                UPDATE `monitors`
                SET `monitor` = %s,
                  `monitor_json` = %s
                WHERE `id` = %s

            """
            params = (monitor, monitor_json, save_recid)
        else:
            operation = """
                INSERT INTO `monitors` (`monitor`, `monitor_json`)
                VALUES (%s, %s)
            """
            params = (monitor, monitor_json)

        return self._execute(operation, params)

    def save_records(self, save_recid, domain, name, name_type, ttl, content, monitor, view, disabled=0, fallback=0,
                     persistence=0, weight=0, **_):

        count = 0
        count += self._insert_names(domain, name)
        count += self._insert_names_types(domain, name, name_type, ttl, persistence)
        count += self._insert_contents(content)
        count += self._insert_contents_monitors(content, monitor)

        save_recids = None

        if save_recid:
            operation = """
                SELECT `names_types`.`id` AS `name_type_id`,
                  `contents_monitors`.`id` AS `content_monitor_id`
                FROM `records`
                  JOIN `names_types` ON `records`.`name_type_id` = `names_types`.`id`
                  JOIN `contents_monitors` ON `records`.`content_monitor_id` = `contents_monitors`.`id`
                WHERE `records`.`id` = %s
            """
            params = (save_recid,)

            save_recids = self._execute(operation, params)[0]

            operation = """
                UPDATE `records`
                SET
                  `name_type_id` =
                    (SELECT `names_types`.`id`
                     FROM `names_types`
                       JOIN `names` ON `names_types`.`name_id` = `names`.`id`
                       JOIN `domains` ON `names`.`domain_id` = `domains`.`id`
                       JOIN `types` ON `names_types`.`type_value` = `types`.`value`
                     WHERE `names`.`name` = %s
                       AND `domains`.`domain` = %s
                       AND `types`.`type` = %s),
                  `content_monitor_id` =
                    (SELECT `contents_monitors`.`id`
                     FROM `contents_monitors`
                       JOIN `contents` ON `contents_monitors`.`content_id` = `contents`.`id`
                       JOIN `monitors` ON `contents_monitors`.`monitor_id` = `monitors`.`id`
                     WHERE `contents`.`content` = %s
                       AND `monitors`.`monitor` = %s),
                  `view_id` =
                    (SELECT `views`.`id`
                     FROM `views`
                     WHERE `views`.`view` = %s),
                  `disabled` = %s,
                  `fallback` = %s,
                  `weight` = %s
                WHERE `records`.`id` = %s
            """
            params = (name, domain, name_type, content, monitor, view, disabled, fallback, weight, save_recid)
        else:
            operation = """
                INSERT INTO `records` (`name_type_id`, `content_monitor_id`, `view_id`, `disabled`, `fallback`, `weight`)
                  SELECT
                    (SELECT `names_types`.`id`
                     FROM `names_types`
                       JOIN `names` ON `names_types`.`name_id` = `names`.`id`
                       JOIN `types` ON `names_types`.`type_value` = `types`.`value`
                     WHERE `names`.`name` = %s
                       AND `types`.`type` = %s) AS `name_type_id`,
                    (SELECT `contents_monitors`.`id`
                     FROM `contents_monitors`
                       JOIN `contents` ON `contents_monitors`.`content_id` = `contents`.`id`
                       JOIN `monitors` ON `contents_monitors`.`monitor_id` = `monitors`.`id`
                     WHERE `contents`.`content` = %s
                       AND `monitors`.`monitor` = %s) AS `content_monitor_id`,
                    (SELECT `views`.`id`
                     FROM `views`
                     WHERE `views`.`view` = %s) AS `view_id`,
                    %s AS `disabled`,
                    %s AS `fallback`,
                    %s AS `weight`
            """
            params = (name, name_type, content, monitor, view, disabled, fallback, weight)

        count += self._execute(operation, params)

        if save_recids:
            count += self._clean_names(save_recids.get('name_type_id'))
            count += self._clean_contents(save_recids.get('content_monitor_id'))

        return count

    def save_types(self, save_recid, description, name_type, recid):
        if save_recid:
            operation = """
                UPDATE `types`
                SET `value` = %s,
                  `type` = %s,
                  `description` = %s
                WHERE `value` = %s

            """
            params = (recid, name_type, description, save_recid)
        else:
            operation = """
                INSERT INTO `types` (`value`, `type`, `description`)
                VALUES (%s, %s, %s)
            """
            params = (recid, name_type, description)

        return self._execute(operation, params)

    def save_users(self, save_recid, user, name, password, **_):
        if save_recid:
            operation = """
                UPDATE `users`
                SET `user` = %s,
                  `name` = %s,
                  `password` = PASSWORD(%s)
                WHERE `id` = %s

            """
            params = (user, name, password, save_recid)
        else:
            operation = """
                INSERT INTO `users` (`user`, `name`, `password`)
                VALUES (%s, %s, PASSWORD(%s))
            """
            params = (user, name, password)

        return self._execute(operation, params)

    def save_views(self, save_recid, view, rule, **_):
        if save_recid:
            operation = """
                UPDATE `views`
                SET `view` = %s,
                  `rule` = %s
                WHERE `id` = %s

            """
            params = (view, rule, save_recid)
        else:
            operation = """
                INSERT INTO `views` (`view`, `rule`)
                VALUES (%s, %s)
            """
            params = (view, rule)

        return self._execute(operation, params)
