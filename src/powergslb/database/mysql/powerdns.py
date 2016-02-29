import abc

__all__ = ['AbstractPowerDNSDatabase']


class AbstractPowerDNSDatabase(object):
    """
    AbstractPowerDNSDatabase class contains PowerDNS related queries
    """
    __metaclass__ = abc.ABCMeta

    @abc.abstractmethod
    def _execute(self, operation, params):
        pass

    def gslb_checks(self):
        operation = """
            SELECT `contents_monitors`.`id`,
              `contents`.`content`,
              `monitors`.`monitor_json`
            FROM `contents_monitors`
              JOIN `contents` ON `contents_monitors`.`content_id` = `contents`.`id`
              JOIN `monitors` ON `contents_monitors`.`monitor_id` = `monitors`.`id`
        """

        return self._execute(operation)

    def gslb_records(self, qname, qtype):
        operation = """
            SELECT `names`.`name` AS `qname`,
              `types`.`type` AS `qtype`,
              `names_types`.`ttl`,
              `names_types`.`persistence`,
              `records`.`fallback`,
              `records`.`weight`,
              `contents_monitors`.`id`,
              `contents`.`content`
            FROM `names`
              JOIN `names_types` ON `names`.`id` = `names_types`.`name_id`
              JOIN `types` ON `names_types`.`type_value` = `types`.`value`
              JOIN `records` ON `names_types`.`id` = `records`.`name_type_id`
              JOIN `contents_monitors` ON `records`.`content_monitor_id` = `contents_monitors`.`id`
              JOIN `contents` ON `contents_monitors`.`content_id` = `contents`.`id`
        """

        if qtype == 'ANY':
            operation += """
                WHERE `names`.`name` = %s
                  AND `records`.`disabled` = 0
            """
            params = (qname,)
        else:
            operation += """
                WHERE `names`.`name` = %s
                  AND `types`.`type` = %s
                  AND `records`.`disabled` = 0
            """
            params = (qname, qtype)

        return self._execute(operation, params)
