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

    _authority_qtypes: ClassVar[frozenset[str]] = frozenset({'SOA', 'NS', 'DS'})

    # The shared per-request client data: the client network and its geolocation.
    context: ClientContext

    def _handle_route(self) -> None:
        """Answer GET /dns queries; any other method is not part of the remote backend protocol -> 404."""
        if self.command == 'GET':
            self._send_content(self.content())
        else:
            self.send_error(404)

    def _set_remote_ip(self) -> None:
        """Set the per-request client data from the PowerDNS remote backend headers.

        Sets remote_ip to the DNS client (the recursor) from the X-Remotebackend-Remote header and builds the
        ClientContext from the X-Remotebackend-Real-Remote header: the EDNS Client Subnet or the recursor as a host
        network (/32, /128) when no ECS was requested. A missing or malformed header falls back to the TCP peer.
        """
        client_ip = self.address_string()
        remote_ip = self.headers.get('X-Remotebackend-Remote', client_ip)
        try:
            self.remote_ip = netaddr.IPAddress(remote_ip)
        except (netaddr.AddrFormatError, ValueError) as e:
            logging.error("'X-Remotebackend-Remote' header invalid: %s: %s", type(e).__name__, e)
            self.remote_ip = netaddr.IPAddress(client_ip)

        real_remote = self.headers.get('X-Remotebackend-Real-Remote', client_ip)
        try:
            self.context = ClientContext(netaddr.IPNetwork(real_remote))
        except (netaddr.AddrFormatError, ValueError) as e:
            logging.error("'X-Remotebackend-Real-Remote' header invalid: %s: %s", type(e).__name__, e)
            self.context = ClientContext(netaddr.IPNetwork(client_ip))

    def _select_records(self, all_records: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
        """Select records to answer with, keyed by qtype.

        Per qtype: keep only in-view records and if none are in view drop that qtype. Then drop down records, unless
        that empties in-view set, in which case keep them all ('all down = all up', so DNS never fails entirely).
        Finally, the rrset's routing policy chooses the answers. A malformed policy is logged and drops that qtype.

        :param all_records: The records at the queried name, keyed by qtype.
        :returns: The chosen answers keyed by qtype; a qtype with no in-view records or a bad policy is absent.
        """
        context = self.context
        selected: dict[str, list[dict[str, Any]]] = {}
        for qtype, group in all_records.items():
            in_view = [record for record in group if self._is_in_view(record, context)]
            if not in_view:
                continue

            live = [record for record in in_view if not self.status_registry.is_down(record['id'])]
            candidates = live or in_view  # all down = all up

            try:
                selected[qtype] = RoutingPolicy.resolve(group[0]['policy_json']).select(candidates, context)
            except Exception as e:  # pylint: disable=broad-exception-caught
                logging.error('%s %s routing policy invalid: %s: %s',
                              candidates[0]['qname'], qtype, type(e).__name__, e)
        return selected

    def _get_all_domains(self) -> list[dict[str, Any]]:
        """Build the getAllDomains zone list; a domain with an unparsable SOA serial is skipped and logged.

        :returns: One zone entry per domain in the remote backend getAllDomains shape.
        """
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

    def _scope_prefix(self, group: list[dict[str, Any]]) -> int:
        """Compute the EDNS Client Subnet scope for one rrset (qtype group).

        SOA, NS and DS are always scope 0 (Google ECS guidance: a consistent view of delegation). Otherwise, scope 0
        only when every record's view matches all clients (ViewRule.matches_all) and the routing policy is
        client-independent, so resolvers may cache the shared answer globally. Any narrower view/policy makes the answer
        subnet specific and carries the client's source prefix. An ECS opt-out (source prefix 0) yields 0 naturally.

        :param group: The full rrset (all records of one qtype at the name), not just the selected answers.
        :returns: The scopeMask prefix length for the group's answers.
        """
        if not group or group[0]['qtype'] in self._authority_qtypes:
            return 0

        source_prefix = self.context.remote.prefixlen
        for record in group:
            try:
                match_all = ViewRule.resolve(record['rule']).matches_all
            except ValueError:
                match_all = False  # malformed rule -> scope subnet-wide, never cache globally
            if not match_all:
                return source_prefix

        prefix = RoutingPolicy.resolve(group[0]['policy_json']).network_prefix(self.context)
        if prefix is not None:
            return min(prefix, source_prefix)
        return 0

    def _get_lookup(self) -> list[dict[str, Any]]:
        """Answer a lookup: fetch the records for (qname, qtype), select per rrset, and shape the response fields.

        :returns: The answer rows with qname, qtype, content, ttl and scopeMask.
        """
        self.dirs[2] = self.dirs[2].rstrip('.')
        records = self.database.gslb_records(*self.dirs[2:])
        all_records: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in records:
            all_records[record['qtype']].append(record)
        selected_records = self._select_records(all_records)

        result: list[dict[str, Any]] = []
        for qtype, group in selected_records.items():
            scope_prefix = self._scope_prefix(all_records[qtype])  # scope on the full rrset
            result.extend({'qname': r['qname'], 'qtype': r['qtype'], 'content': r['content'], 'ttl': r['ttl'],
                           'scopeMask': scope_prefix} for r in group)
        return result

    @staticmethod
    def _is_in_view(record: dict[str, Any], context: ClientContext) -> bool:
        """Return True when the client matches the record's view rule.

        A malformed rule is logged and treated as non-matching (returns False).

        :param record: The record carrying its view 'rule'.
        :param context: Per-request client data the rule is matched against.
        :returns: True when the client is in the record's view.
        """
        try:
            return ViewRule.resolve(record['rule']).matches(context)
        except ValueError as e:
            logging.error('record id %s view rule invalid: %s: %s', record['id'], type(e).__name__, e)
            return False

    def content(self) -> str:
        """Dispatch /dns/lookup/<qname>/<qtype> and /dns/getAllDomains; anything else yields a false result.

        :returns: The JSON-encoded remote backend reply.
        """
        if len(self.dirs) == 4 and self.dirs[1] == 'lookup':
            content: dict[str, Any] = {'result': self._get_lookup()}
        elif len(self.dirs) == 2 and self.dirs[1] == 'getAllDomains':
            content = {'result': self._get_all_domains()}
        else:
            content = {'result': False}

        return json.dumps(content, separators=(',', ':'))
