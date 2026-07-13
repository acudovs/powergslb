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

    @staticmethod
    def zone_suffixes(qname: str) -> list[str]:
        """Return qname and every parent suffix formed by dropping leading labels.

        :param qname: The queried FQDN without a trailing dot.
        :returns: qname first, then each shorter suffix down to the last label.
        """
        labels = qname.split('.')
        return ['.'.join(labels[index:]) for index in range(len(labels))]

    def gslb_records(self, qname: str, qtype: str) -> list[dict[str, Any]]:
        """Return all DNS records for qname and qtype, including view rules.

        The owning zone is the most-specific `domains` row whose name is a suffix of qname: the suffix candidates
        are matched by the unique index (`domain IN (...)`) and the longest is picked. The relative record name is
        recovered from that zone with SUBSTRING and the answer FQDN is rebuilt with CASE/CONCAT.

        :param qname: The queried FQDN.
        :param qtype: The queried record type; 'ANY' matches every type.
        :returns: The enabled records at qname with their rrset, routing and view attributes.
        """
        qname = qname.rstrip('.')
        suffixes = self.zone_suffixes(qname)
        placeholders = ', '.join(['%s'] * len(suffixes))
        operation = f"""
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
            WHERE `domains`.`domain` = (
                SELECT `d`.`domain` FROM `domains` `d`
                WHERE `d`.`domain` IN ({placeholders})
                ORDER BY CHAR_LENGTH(`d`.`domain`) DESC LIMIT 1
              )
              AND `rrsets`.`name` = (
                CASE WHEN %s = `domains`.`domain` THEN '@'
                  ELSE SUBSTRING(%s, 1, CHAR_LENGTH(%s) - CHAR_LENGTH(`domains`.`domain`) - 1) END
              )
        """

        if qtype == 'ANY':
            operation += """
                AND `records`.`disabled` = 0
            """
            params: tuple[str, ...] = (*suffixes, qname, qname, qname)
        else:
            operation += """
                AND `types`.`type` = %s
                AND `records`.`disabled` = 0
            """
            params = (*suffixes, qname, qname, qname, qtype)

        return self.select(operation, params)
