import abc

__all__ = ['PowerDNSDatabaseMixIn']


class PowerDNSDatabaseMixIn(object):
    """
    PowerDNSDatabaseMixIn class contains PowerDNS related queries
    """
    __metaclass__ = abc.ABCMeta

    @abc.abstractmethod
    def _execute(self, operation, params=()):
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
              `lbmethods`.`lbmethod`,
              `lboptions`.`lboption_json`,
              `contents_monitors`.`id`,
              `contents`.`content`,
              `views`.`rule`
            FROM `names`
              JOIN `names_types` ON `names`.`id` = `names_types`.`name_id`
              JOIN `types` ON `names_types`.`type_value` = `types`.`value`
              JOIN `records` ON `names_types`.`id` = `records`.`name_type_id`
              JOIN `contents_monitors` ON `records`.`content_monitor_id` = `contents_monitors`.`id`
              JOIN `contents` ON `contents_monitors`.`content_id` = `contents`.`id`
              JOIN `views` ON `records`.`view_id` = `views`.`id`
              LEFT JOIN `lbmethods` ON `lbmethods`.`id` = `names_types`.`lbmethod_id`
              LEFT JOIN `lboptions` ON `lboptions`.`id` = `names_types`.`lboption_id`
        """

        if qtype == 'ANY':
            operation += """
                WHERE `names`.`name` = %s
                  AND `records`.`disabled` = 0
            """

            # qname2 = qname.replace('*.','')

            params = (qname,)
        else:
            operation += """
                WHERE `names`.`name` = %s
                  AND `types`.`type` = %s
                  AND `records`.`disabled` = 0
            """
            params = (qname, qtype)

        return self._execute(operation, params)
