"""DNS read path: the queries the PowerDNS backend handler needs."""

import abc
from typing import Any

__all__ = ['PowerDNSMixIn']


class PowerDNSMixIn(abc.ABC):
    """PowerDNS related queries."""

    @abc.abstractmethod
    def select(self, operation: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        """Execute a result-set statement and return its rows as dicts."""

    def gslb_checks(self) -> list[dict[str, Any]]:
        """Return every record's id, content and monitor for health checking.

        :returns: One row per record with its id, content and monitor_json.
        """
        operation = """
            SELECT `records`.`id`,
              `records`.`content`,
              `monitors`.`monitor_json`
            FROM `records`
              JOIN `monitors` ON `records`.`monitor_id` = `monitors`.`id`
        """

        return self.select(operation)

    def gslb_domains(self, include_disabled: bool = False) -> list[dict[str, Any]]:
        """Return domains with an apex SOA and its content for the zone cache.

        :param include_disabled: When true, count a disabled SOA record as present.
        :returns: One row per domain with its id, name and SOA content.
        """
        operation = """
            SELECT `domains`.`id`,
              `domains`.`domain`,
              `records`.`content` AS `soa_content`
            FROM `domains`
              JOIN `rrsets` ON `rrsets`.`domain_id` = `domains`.`id` AND `rrsets`.`name` = '@'
              JOIN `types` ON `rrsets`.`type_value` = `types`.`value` AND `types`.`type` = 'SOA'
              JOIN `records` ON `records`.`rrset_id` = `rrsets`.`id`
        """

        if not include_disabled:
            operation += """
                WHERE `records`.`disabled` = 0
            """

        return self.select(operation)

    def gslb_records(self, qname: str, qtype: str) -> list[dict[str, Any]]:
        """Return all DNS records for qname and qtype, including view rules.

        The owning zone is resolved in SQL by longest-suffix match against `domains` (RIGHT(), never LIKE: '_'
        is a legal DNS label char); the relative record name is recovered with SUBSTRING, and the answer's FQDN is
        rebuilt with CASE/CONCAT. The NOT EXISTS guard makes the most-specific zone win when a parent and a
        delegated child both suffix-match.

        :param qname: The queried FQDN.
        :param qtype: The queried record type; 'ANY' matches every type.
        :returns: The enabled records at qname with their rrset, routing and view attributes.
        """
        operation = """
            SELECT
              CASE WHEN `rrsets`.`name` = '@' THEN `domains`.`domain`
                   ELSE CONCAT(`rrsets`.`name`, '.', `domains`.`domain`) END AS `qname`,
              `types`.`type` AS `qtype`,
              `rrsets`.`ttl`,
              `routings`.`policy_json`,
              `records`.`weight`,
              `records`.`id`,
              `records`.`content`,
              `views`.`rule`
            FROM `domains`
              JOIN `rrsets` ON `rrsets`.`domain_id` = `domains`.`id`
              JOIN `types` ON `rrsets`.`type_value` = `types`.`value`
              JOIN `routings` ON `rrsets`.`routing_id` = `routings`.`id`
              JOIN `records` ON `records`.`rrset_id` = `rrsets`.`id`
              JOIN `views` ON `records`.`view_id` = `views`.`id`
            WHERE (
                (%s = `domains`.`domain` AND `rrsets`.`name` = '@')
                OR (RIGHT(%s, CHAR_LENGTH(`domains`.`domain`) + 1) = CONCAT('.', `domains`.`domain`)
                    AND `rrsets`.`name` = SUBSTRING(%s, 1, CHAR_LENGTH(%s) - CHAR_LENGTH(`domains`.`domain`) - 1))
              )
              AND NOT EXISTS (
                SELECT 1 FROM `domains` `d2`
                WHERE CHAR_LENGTH(`d2`.`domain`) > CHAR_LENGTH(`domains`.`domain`)
                  AND (%s = `d2`.`domain` OR RIGHT(%s, CHAR_LENGTH(`d2`.`domain`) + 1) = CONCAT('.', `d2`.`domain`))
              )
        """

        if qtype == 'ANY':
            operation += """
                AND `records`.`disabled` = 0
            """
            params: tuple[str, ...] = (qname, qname, qname, qname, qname, qname)
        else:
            operation += """
                AND `types`.`type` = %s
                  AND `records`.`disabled` = 0
            """
            params = (qname, qname, qname, qname, qname, qname, qtype)

        return self.select(operation, params)
