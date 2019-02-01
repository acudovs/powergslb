import json
import logging

import netaddr

from powergslb.server.http.handler.abstract import AbstractContentHandler

import powergslb.monitor

__all__ = ['PowerDNSContentHandler']


class PowerDNSContentHandler(AbstractContentHandler):
    """
    PowerDNS content handler
    """

    def _filter_records(self, qtype_records):
        records = []
        for qtype in qtype_records:

            fallback_records = {}
            live_records = {}

            for record in qtype_records[qtype]:
                if not self._is_in_view(record):
                    continue

                if record['fallback']:
                    if record['weight'] not in fallback_records:
                        fallback_records[record['weight']] = []

                    fallback_records[record['weight']].append(record)

                if record['id'] not in powergslb.monitor.get_status():
                    if record['weight'] not in live_records:
                        live_records[record['weight']] = []

                    live_records[record['weight']].append(record)

            if live_records:
                filtered_records = live_records[max(live_records)]
            elif fallback_records:
                filtered_records = fallback_records[max(fallback_records)]
            else:
                filtered_records = []

            if not filtered_records:
                continue

            if filtered_records[0]['persistence']:
                records.append(self._remote_ip_persistence(filtered_records))
            else:
                records.extend(filtered_records)

        return records

    def _get_lookup(self):
        v3_format = True
        if self.dirs[2].endswith('.'):
            v3_format = False
            self.dirs[2] = self.dirs[2].rstrip('.')

        records = self.database.gslb_records(*self.dirs[2:])
        qtype_records = self._split_records(records, v3_format)
        filtered_records = self._filter_records(qtype_records)
        return self._strip_records(filtered_records, v3_format)

    def _is_in_view(self, record):
        result = False
        try:
            result = bool(netaddr.smallest_matching_cidr(self.remote_ip, record.get('rule').split()))
        except (AttributeError, netaddr.AddrFormatError, ValueError) as e:
            logging.error('{}: record id {} view rule invalid: {}: {}'.format(
                    type(self).__name__, record['id'], type(e).__name__, e))

        return result

    def _remote_ip_persistence(self, records):
        persistence_value = netaddr.IPAddress(self.remote_ip).value >> records[0]['persistence']
        return records[hash(persistence_value) % len(records)]

    def _split_records(self, records, v3_format=False):
        qtype_records = {}
        for record in records:
            if v3_format and record['qtype'] in ['MX', 'SRV']:
                content_split = record['content'].split()
                try:
                    record['priority'] = int(content_split[0])
                    record['content'] = ' '.join(content_split[1:])
                except (KeyError, ValueError) as e:
                    logging.error('{}: record id {} priority missing or invalid: {}: {}'.format(
                            type(self).__name__, record['id'], type(e).__name__, e))
                    continue

            if record['qtype'] not in qtype_records:
                qtype_records[record['qtype']] = []

            qtype_records[record['qtype']].append(record)

        return qtype_records

    @staticmethod
    def _strip_records(records, v3_format=False):
        result = []
        for record in records:
            if v3_format and record['qtype'] in ['MX', 'SRV']:
                names = ['qname', 'qtype', 'content', 'ttl', 'priority']
                values = [record['qname'], record['qtype'], record['content'], record['ttl'], record['priority']]
            else:
                names = ['qname', 'qtype', 'content', 'ttl']
                values = [record['qname'], record['qtype'], record['content'], record['ttl']]

            result.append(dict(zip(names, values)))

        return result

    def content(self):
        if len(self.dirs) == 4 and self.dirs[1] == 'lookup':
            content = {'result': self._get_lookup()}
        else:
            content = {'result': False}

        return json.dumps(content, separators=(',', ':'))
