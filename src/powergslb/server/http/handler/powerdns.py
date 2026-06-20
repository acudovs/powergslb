"""PowerDNS remote backend protocol handler."""

import json
import logging
import operator
import time
from collections import defaultdict
from typing import Any, ClassVar
from urllib.parse import parse_qs

import netaddr

from powergslb.server.http.handler.request import HTTPRequestHandler

__all__ = ['PowerDNSRequestHandler']


class PowerDNSRequestHandler(HTTPRequestHandler):
    """Answers PowerDNS remote backend queries on the DNS interface: lookup and getAllDomains.

    Lookup answers are filtered by view, health, weight, fallback, and client IP persistence.
    """
    route: ClassVar[str] = 'dns'

    # Per-request memo of (client IP, (country, continent)).
    _geo_cache: tuple[Any, tuple[str | None, str | None]] = (object(), (None, None))

    def _handle_route(self) -> None:
        """Answer GET /dns queries; any other method is not part of the remote backend protocol -> 404."""
        if self.command == 'GET':
            self._send_content(self.content())
        else:
            self.send_error(404)

    def _set_remote_ip(self) -> None:
        """Set the client IP, preferring a valid X-Remotebackend-Real-Remote header (set by PowerDNS)."""
        remote_ip = self.client_address[0]
        if 'X-Remotebackend-Real-Remote' in self.headers:
            try:
                real_remote_header = self.headers['X-Remotebackend-Real-Remote']
                remote_ip = netaddr.IPNetwork(real_remote_header).ip.format()
            except (netaddr.AddrFormatError, ValueError) as e:
                logging.error("'X-Remotebackend-Real-Remote' header invalid: %s: %s", type(e).__name__, e)

        self.remote_ip = remote_ip

    def _filter_records(self, qtype_records: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
        """Select the records to answer with, independently per qtype.

        Within a qtype, only in-view records are considered. A record is live while it is up, and the
        highest-weight live group wins; the fallback flag is additive, so a healthy fallback record serves in
        that group like any other. Only when no record is live is the highest-weight group among the
        fallback-flagged records answered, regardless of their health, so a failed check is ignored as a last
        resort. Give fallback records a lower weight than the primaries to keep them out of normal responses.
        Persistence then collapses the chosen group to a single record per client subnet.
        """
        records: list[dict[str, Any]] = []
        for qtype in qtype_records:

            fallback_records: dict[int, list[dict[str, Any]]] = defaultdict(list)
            live_records: dict[int, list[dict[str, Any]]] = defaultdict(list)

            for record in qtype_records[qtype]:
                if not self._is_in_view(record):
                    continue

                if record['fallback']:
                    fallback_records[record['weight']].append(record)

                if not self.status_registry.is_down(record['id']):
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

    def _is_in_view(self, record: dict[str, Any]) -> bool:
        """Return True when the client IP matches the record's view rule; an invalid rule never matches.

        A rule is a space-separated list or CIDR and geo tokens. The client IP matches when it satisfies any one token.
        """
        result = False
        try:
            cidr_tokens: list[str] = []
            geo_tokens: list[tuple[str, str]] = []
            for token in record.get('rule').split():  # type: ignore[union-attr]
                geo = self.geoip_reader.parse_geo_token(token)
                if geo is None:
                    cidr_tokens.append(token)
                else:
                    geo_tokens.append(geo)

            if cidr_tokens and netaddr.smallest_matching_cidr(self.remote_ip, cidr_tokens):
                result = True
            elif geo_tokens:
                country, continent = self._client_geo()
                result = any((kind == 'country' and value == country) or
                             (kind == 'continent' and value == continent) for kind, value in geo_tokens)
        except (AttributeError, netaddr.AddrFormatError, ValueError) as e:
            logging.error('record id %s view rule invalid: %s: %s', record['id'], type(e).__name__, e)

        return result

    def _client_geo(self) -> tuple[str | None, str | None]:
        """Resolve the client's country and continent, caching the result by client IP.

        The client geo is constant for a query yet '_is_in_view' runs per record, so the GeoIP lookup is memoized
        and recomputed only when 'remote_ip' changes (a new query reuses the keep-alive connection's handler).
        """
        ip, geo = self._geo_cache
        if ip != self.remote_ip:
            geo = self.geoip_reader.lookup(self.remote_ip)
            self._geo_cache = (self.remote_ip, geo)
        return geo

    def _remote_ip_persistence(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        """Pick one record for the client, deterministically.

        The client IP is taken as a whole integer (IPv4 or IPv6) and shifted right by the record's 'persistence' bits,
        so every address in the same subnet collapses to one value. A 'persistence' value at or above the client address
        width collapses every client to a single record (maximum stickiness).
        """
        records = sorted(records, key=operator.itemgetter('content'))
        persistence_value = netaddr.IPAddress(self.remote_ip).value >> records[0]['persistence']
        return records[persistence_value % len(records)]

    def content(self) -> str:
        """Dispatch /dns/lookup/<qname>/<qtype> and /dns/getAllDomains; anything else yields a false result."""
        if len(self.dirs) == 4 and self.dirs[1] == 'lookup':
            content: dict[str, Any] = {'result': self._get_lookup()}
        elif len(self.dirs) == 2 and self.dirs[1] == 'getAllDomains':
            content = {'result': self._get_all_domains()}
        else:
            content = {'result': False}

        return json.dumps(content, separators=(',', ':'))
