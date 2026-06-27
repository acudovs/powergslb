"""PowerDNS remote backend protocol handler."""

import json
import logging
import time
from collections import defaultdict
from typing import Any, ClassVar
from urllib.parse import parse_qs

import netaddr

from powergslb.client import ClientContext
from powergslb.routing import RoutingPolicy
from powergslb.server.http.handler.request import HTTPRequestHandler
from powergslb.view import ViewRule

__all__ = ['PowerDNSRequestHandler']


class PowerDNSRequestHandler(HTTPRequestHandler):
    """Answers PowerDNS remote backend queries on the DNS interface: lookup and getAllDomains.

    Lookup answers run a per-qtype pipeline: a view filter, a health filter, then the rrset's routing policy,
    which chooses the answers.
    """
    route: ClassVar[str] = 'dns'

    def _handle_route(self) -> None:
        """Answer GET /dns queries; any other method is not part of the remote backend protocol -> 404."""
        if self.command == 'GET':
            self._send_content(self.content())
        else:
            self.send_error(404)

    def _set_remote_ip(self) -> None:
        """Set the client IP, preferring a valid X-Remotebackend-Real-Remote header set by PowerDNS.

        An absent header falls back to the TCP peer silently.
        """
        header = self.headers.get('X-Remotebackend-Real-Remote')
        if header is not None:
            try:
                self.remote_ip = netaddr.IPNetwork(header).ip
                return
            except (netaddr.AddrFormatError, ValueError) as e:
                logging.error("'X-Remotebackend-Real-Remote' header invalid: %s: %s", type(e).__name__, e)

        self.remote_ip = netaddr.IPAddress(self.client_address[0])

    def _filter_records(self, qtype_records: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
        """Select the records to answer with, independently per qtype.

        Per qtype: keep only in-view records, and if none are in view answer nothing for that qtype (the routing
        policy is never resolved or called for an empty set). Then drop down records, unless that empties a
        non-empty in-view set, in which case keep them all ('all down = all up', so DNS never fails entirely).
        Finally the rrset's routing policy chooses the answers. A malformed policy is logged and drops that qtype
        group.
        """
        context = ClientContext(self.remote_ip)
        records: list[dict[str, Any]] = []
        for qtype, group in qtype_records.items():
            in_view = [record for record in group if self._is_in_view(record, context)]
            if not in_view:
                continue

            live = [record for record in in_view if not self.status_registry.is_down(record['id'])]
            candidates = live or in_view  # all down -> all up

            policy_json = group[0]['policy_json']  # one rrset per qtype group, so policy_json is identical
            try:
                records.extend(RoutingPolicy.resolve(policy_json).select(candidates, context))
            except Exception as e:  # pylint: disable=broad-exception-caught
                logging.error('%s %s routing policy invalid: %s: %s',
                              candidates[0]['qname'], qtype, type(e).__name__, e)

        return records

    def _get_all_domains(self) -> list[dict[str, Any]]:
        """Build the getAllDomains zone list; a domain with an unparsable SOA serial is skipped and logged."""
        # A flat flag, so the stdlib parser suffices; strict: honored only for exactly one 'true' value.
        include_disabled = parse_qs(self.query or '').get('includeDisabled') == ['true']

        result = []
        for domain in self.database.gslb_domains(include_disabled):
            try:
                serial = int(domain['soa_content'].split()[2])
            except (IndexError, ValueError):
                logging.error('domain id %s soa_content invalid', domain['id'])
                continue

            result.append({
                'id': domain['id'],
                'zone': domain['domain'] + '.',
                'kind': 'native',
                'serial': serial,
                'notified_serial': serial,
                'last_check': int(time.time()),
                'masters': [],
            })
        return result

    def _get_lookup(self) -> list[dict[str, Any]]:
        """Answer a lookup: fetch the records for qname/qtype, filter them, and shape the response fields."""
        self.dirs[2] = self.dirs[2].rstrip('.')
        records = self.database.gslb_records(*self.dirs[2:])
        qtype_records: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in records:
            qtype_records[record['qtype']].append(record)
        filtered_records = self._filter_records(qtype_records)
        return [{'qname': r['qname'], 'qtype': r['qtype'], 'content': r['content'], 'ttl': r['ttl']}
                for r in filtered_records]

    @staticmethod
    def _is_in_view(record: dict[str, Any], context: ClientContext) -> bool:
        """Return True when the client matches the record's view rule.

        A malformed rule is logged and treated as non-matching (returns False).
        """
        try:
            return ViewRule.resolve(record['rule']).matches(context)
        except ValueError as e:
            logging.error('record id %s view rule invalid: %s: %s', record['id'], type(e).__name__, e)
            return False

    def content(self) -> str:
        """Dispatch /dns/lookup/<qname>/<qtype> and /dns/getAllDomains; anything else yields a false result."""
        if len(self.dirs) == 4 and self.dirs[1] == 'lookup':
            content: dict[str, Any] = {'result': self._get_lookup()}
        elif len(self.dirs) == 2 and self.dirs[1] == 'getAllDomains':
            content = {'result': self._get_all_domains()}
        else:
            content = {'result': False}

        return json.dumps(content, separators=(',', ':'))
