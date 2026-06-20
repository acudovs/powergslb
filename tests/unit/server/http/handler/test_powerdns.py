# pylint: disable=missing-function-docstring, redefined-outer-name, protected-access

"""Tests for PowerDNSRequestHandler.

Remote-IP resolution (the PowerDNS header is honored only here), the GET-only route, view matching, client-IP
persistence selection, the live/fallback/weight record filter, the lookup and getAllDomains responses, and content()
dispatch. The handler is built with __new__ to skip the socket-opening __init__; the database and the shared status
set are faked so filtering needs no backend.
"""

import json
from typing import Any

import pytest

from powergslb.monitor.status import StatusRegistry
from powergslb.server.http.handler.powerdns import PowerDNSRequestHandler
from powergslb.system.geoip import GeoIPReader


class _FakeDatabase:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []
        self.domains: list[dict[str, Any]] = []
        self.gslb_records_args: tuple[Any, ...] = ()
        self.include_disabled: bool | None = None

    def gslb_records(self, qname: str, qtype: str) -> list[dict[str, Any]]:
        self.gslb_records_args = (qname, qtype)
        return self.records

    def gslb_domains(self, include_disabled: bool = False) -> list[dict[str, Any]]:
        self.include_disabled = include_disabled
        return self.domains


class _FakeGeoIP:
    """Stub GeoIP reader returning a fixed (country, continent) for every lookup."""

    parse_geo_token = staticmethod(GeoIPReader.parse_geo_token)  # the real token grammar

    def __init__(self, country: str | None = None, continent: str | None = None) -> None:
        self.country = country
        self.continent = continent
        self.queried: list[str | None] = []

    def lookup(self, ip: str | None) -> tuple[str | None, str | None]:
        self.queried.append(ip)
        return self.country, self.continent


def _handler(dirs: list[str], remote_ip: str = '203.0.113.5', query: Any = None,
             geoip_reader: Any = None, status_registry: Any = None) -> PowerDNSRequestHandler:
    """Build a handler without running __init__ (which would open a socket and call handle())."""
    handler = PowerDNSRequestHandler.__new__(PowerDNSRequestHandler)
    handler.body = None
    handler.database = _FakeDatabase()  # type: ignore[assignment]
    handler.dirs = dirs
    handler.headers = {}  # type: ignore[assignment]
    handler.path = '/' + '/'.join(dirs)
    handler.remote_ip = remote_ip
    handler.query = query
    handler.geoip_reader = geoip_reader or _FakeGeoIP()  # type: ignore[assignment]
    handler.status_registry = status_registry or StatusRegistry()
    return handler


@pytest.fixture
def status_registry() -> StatusRegistry:
    return StatusRegistry()


def _record(**overrides: Any) -> dict[str, Any]:
    record = {'id': 1, 'qname': 'example.com', 'qtype': 'A', 'content': '192.0.2.1', 'ttl': 60,
              'fallback': 0, 'weight': 0, 'persistence': 0, 'rule': '0.0.0.0/0 ::/0'}
    record.update(overrides)
    return record


# _set_remote_ip (the DNS interface honors the PowerDNS header)

def test_set_remote_ip_from_real_remote_header() -> None:
    handler = _handler(['dns'])
    handler.client_address = ('127.0.0.1', 1)  # type: ignore[assignment]
    handler.headers = {'X-Remotebackend-Real-Remote': '198.51.100.4/32'}  # type: ignore[assignment]
    handler._set_remote_ip()
    assert handler.remote_ip == '198.51.100.4'


def test_set_remote_ip_invalid_header_falls_back_to_peer() -> None:
    handler = _handler(['dns'])
    handler.client_address = ('127.0.0.1', 1)  # type: ignore[assignment]
    handler.headers = {'X-Remotebackend-Real-Remote': 'not-an-ip'}  # type: ignore[assignment]
    handler._set_remote_ip()
    assert handler.remote_ip == '127.0.0.1'


def test_set_remote_ip_without_header_uses_peer() -> None:
    handler = _handler(['dns'])
    handler.client_address = ('203.0.113.9', 4321)  # type: ignore[assignment]
    handler.headers = {}  # type: ignore[assignment]
    handler._set_remote_ip()
    assert handler.remote_ip == '203.0.113.9'


# _handle_route

def test_handle_route_get_sends_content() -> None:
    handler = _handler(['dns'])
    sent: list[str] = []
    handler._send_content = lambda content, **k: sent.append(content)  # type: ignore[method-assign]
    handler.content = lambda: 'body'  # type: ignore[method-assign]
    handler.command = 'GET'
    handler._handle_route()
    assert sent == ['body']


def test_handle_route_non_get_is_404() -> None:
    handler = _handler(['dns'])
    errors: list[int] = []
    handler.send_error = lambda code, *a, **k: errors.append(code)  # type: ignore[method-assign]
    handler.command = 'POST'
    handler._handle_route()
    assert errors == [404]


# _is_in_view

def test_is_in_view_matches_rule() -> None:
    handler = _handler(['dns'], remote_ip='203.0.113.5')
    assert handler._is_in_view(_record(rule='203.0.113.0/24')) is True


def test_is_in_view_no_match() -> None:
    handler = _handler(['dns'], remote_ip='203.0.113.5')
    assert handler._is_in_view(_record(rule='10.0.0.0/8')) is False


def test_is_in_view_none_rule_is_false() -> None:
    handler = _handler(['dns'])
    assert handler._is_in_view(_record(rule=None)) is False


def test_is_in_view_malformed_rule_is_false() -> None:
    handler = _handler(['dns'])
    assert handler._is_in_view(_record(rule='not-a-cidr')) is False


# _is_in_view: geo tokens

def test_is_in_view_country_matches() -> None:
    handler = _handler(['dns'], geoip_reader=_FakeGeoIP(country='US', continent='NA'))
    assert handler._is_in_view(_record(rule='country:US')) is True


def test_is_in_view_country_no_match() -> None:
    handler = _handler(['dns'], geoip_reader=_FakeGeoIP(country='DE', continent='EU'))
    assert handler._is_in_view(_record(rule='country:US')) is False


def test_is_in_view_continent_matches() -> None:
    handler = _handler(['dns'], geoip_reader=_FakeGeoIP(country='US', continent='NA'))
    assert handler._is_in_view(_record(rule='continent:NA')) is True


def test_is_in_view_mixed_cidr_and_geo_is_a_union() -> None:
    # A client outside the CIDR but inside the geo token still matches; the rule is a union.
    handler = _handler(['dns'], remote_ip='203.0.113.5', geoip_reader=_FakeGeoIP(country='US'))
    assert handler._is_in_view(_record(rule='10.0.0.0/8 country:US')) is True


def test_is_in_view_cidr_matches_without_consulting_geoip() -> None:
    # When the CIDR already matches, the geo reader is not queried (CIDR is checked first).
    geoip = _FakeGeoIP(country='DE')
    handler = _handler(['dns'], remote_ip='203.0.113.5', geoip_reader=geoip)
    assert handler._is_in_view(_record(rule='203.0.113.0/24 country:US')) is True
    assert not geoip.queried


def test_is_in_view_geo_inert_with_unloaded_geoip() -> None:
    # An unloaded reader resolves to no location, so geo tokens never match (CIDR behaviour is unchanged).
    handler = _handler(['dns'], geoip_reader=_FakeGeoIP(country=None, continent=None))
    assert handler._is_in_view(_record(rule='continent:EU')) is False


def test_is_in_view_malformed_geo_token_is_false(caplog: pytest.LogCaptureFixture) -> None:
    handler = _handler(['dns'], geoip_reader=_FakeGeoIP(country='US'))
    with caplog.at_level('ERROR'):
        assert handler._is_in_view(_record(rule='country:USA')) is False
    assert any('view rule invalid' in r.getMessage() for r in caplog.records)


def test_client_geo_is_memoized_per_client() -> None:
    # The geo lookup is constant per client, so checking several records queries the reader once.
    geoip = _FakeGeoIP(country='US')
    handler = _handler(['dns'], remote_ip='203.0.113.5', geoip_reader=geoip)
    assert handler._is_in_view(_record(rule='country:US')) is True
    assert handler._is_in_view(_record(rule='country:DE')) is False
    assert geoip.queried == ['203.0.113.5']

    # A different client (e.g. a new query on the keep-alive connection) invalidates the memo.
    handler.remote_ip = '198.51.100.7'
    assert handler._is_in_view(_record(rule='country:US')) is True
    assert geoip.queried == ['203.0.113.5', '198.51.100.7']


# _remote_ip_persistence

def test_remote_ip_persistence_is_deterministic() -> None:
    handler = _handler(['dns'], remote_ip='192.0.2.7')
    records = [_record(id=1, content='a'), _record(id=2, content='b'), _record(id=3, content='c')]
    chosen = handler._remote_ip_persistence(records)
    assert chosen in records
    # same client always gets the same answer
    assert handler._remote_ip_persistence(records) is chosen


def test_remote_ip_persistence_is_independent_of_input_order() -> None:
    handler = _handler(['dns'], remote_ip='192.0.2.7')
    ordered = [_record(id=1, content='a'), _record(id=2, content='b'), _record(id=3, content='c')]
    shuffled = [_record(id=3, content='c'), _record(id=1, content='a'), _record(id=2, content='b')]
    # the handler sorts by content internally, so caller order does not affect the choice
    assert handler._remote_ip_persistence(ordered)['content'] == handler._remote_ip_persistence(shuffled)['content']


# _filter_records

def test_filter_picks_highest_weight_live_group() -> None:
    handler = _handler(['dns'])
    records = {'A': [_record(id=1, weight=10, content='low'), _record(id=2, weight=20, content='high')]}
    result = handler._filter_records(records)
    assert [r['content'] for r in result] == ['high']


def test_filter_skips_records_out_of_view() -> None:
    handler = _handler(['dns'], remote_ip='203.0.113.5')
    records = {'A': [_record(id=1, rule='10.0.0.0/8', content='hidden')]}
    assert not handler._filter_records(records)


def test_filter_falls_back_when_all_live_down(status_registry: StatusRegistry) -> None:
    status_registry.add(1)
    status_registry.add(2)  # every record is down, so the live group is empty
    handler = _handler(['dns'], status_registry=status_registry)
    records = {'A': [_record(id=1, fallback=0, content='down'),
                     _record(id=2, fallback=1, content='fallback')]}
    result = handler._filter_records(records)
    assert [r['content'] for r in result] == ['fallback']


def test_filter_returns_empty_when_nothing_available(status_registry: StatusRegistry) -> None:
    status_registry.add(1)
    handler = _handler(['dns'], status_registry=status_registry)
    records = {'A': [_record(id=1, fallback=0, content='down')]}
    assert not handler._filter_records(records)


def test_filter_applies_persistence_to_single_answer() -> None:
    handler = _handler(['dns'], remote_ip='192.0.2.7')
    records = {'A': [_record(id=1, persistence=8, content='a'),
                     _record(id=2, persistence=8, content='b')]}
    result = handler._filter_records(records)
    assert len(result) == 1  # persistence collapses to one record


def test_filter_serves_healthy_fallback_alongside_normal_at_equal_weight() -> None:
    # The fallback flag is additive: a healthy fallback record at the same weight serves like any other.
    handler = _handler(['dns'])
    records = {'A': [_record(id=1, fallback=0, weight=0, content='normal'),
                     _record(id=2, fallback=1, weight=0, content='fallback')]}
    result = handler._filter_records(records)
    assert sorted(r['content'] for r in result) == ['fallback', 'normal']


def test_filter_holds_back_lower_weight_fallback_while_normal_is_up() -> None:
    # Weight tiering: a lower-weight fallback stays out of the answer while the higher-weight normal is up.
    handler = _handler(['dns'])
    records = {'A': [_record(id=1, fallback=0, weight=20, content='normal'),
                     _record(id=2, fallback=1, weight=10, content='fallback')]}
    result = handler._filter_records(records)
    assert [r['content'] for r in result] == ['normal']


def test_filter_higher_weight_fallback_wins_when_all_up() -> None:
    # Weight governs the live group; the fallback flag does not demote a higher-weight healthy record.
    handler = _handler(['dns'])
    records = {'A': [_record(id=1, fallback=0, weight=10, content='normal'),
                     _record(id=2, fallback=1, weight=20, content='fallback')]}
    result = handler._filter_records(records)
    assert [r['content'] for r in result] == ['fallback']


def test_filter_lower_weight_fallback_surfaces_when_normal_down(status_registry: StatusRegistry) -> None:
    # A healthy lower-weight fallback becomes the only live record once the higher-weight normal is down.
    status_registry.add(1)  # normal is down; the fallback record (id 2) stays up
    handler = _handler(['dns'], status_registry=status_registry)
    records = {'A': [_record(id=1, fallback=0, weight=20, content='normal'),
                     _record(id=2, fallback=1, weight=10, content='fallback')]}
    result = handler._filter_records(records)
    assert [r['content'] for r in result] == ['fallback']


def test_filter_down_fallback_served_when_normal_down(status_registry: StatusRegistry) -> None:
    # With nothing live, the fallback group answers regardless of the fallback record's own health.
    status_registry.add(1)
    status_registry.add(2)  # both down, including the fallback record
    handler = _handler(['dns'], status_registry=status_registry)
    records = {'A': [_record(id=1, fallback=0, weight=20, content='normal'),
                     _record(id=2, fallback=1, weight=10, content='fallback')]}
    result = handler._filter_records(records)
    assert [r['content'] for r in result] == ['fallback']


def test_filter_fallback_group_picks_highest_weight_when_all_down(status_registry: StatusRegistry) -> None:
    # With no live record, the highest-weight group among the fallback-flagged records wins.
    status_registry.add(1)
    status_registry.add(2)
    handler = _handler(['dns'], status_registry=status_registry)
    records = {'A': [_record(id=1, fallback=1, weight=10, content='low'),
                     _record(id=2, fallback=1, weight=20, content='high')]}
    result = handler._filter_records(records)
    assert [r['content'] for r in result] == ['high']


# _get_lookup

def test_get_lookup_strips_trailing_dot_and_projects() -> None:
    handler = _handler(['dns', 'lookup', 'example.com.', 'A'])
    handler.database.records = [_record(content='192.0.2.1', ttl=30)]  # type: ignore[attr-defined]
    result = handler._get_lookup()
    assert handler.database.gslb_records_args == ('example.com', 'A')  # type: ignore[attr-defined]
    assert result == [{'qname': 'example.com', 'qtype': 'A', 'content': '192.0.2.1', 'ttl': 30}]


# _get_all_domains

def test_get_all_domains_builds_zone_entries() -> None:
    handler = _handler(['dns', 'getAllDomains'])
    handler.database.domains = [  # type: ignore[attr-defined]
        {'id': 1, 'domain': 'example.com', 'soa_content': 'ns1 hostmaster 2024010101 7200 3600 1209600 3600'}]
    result = handler._get_all_domains()
    assert handler.database.include_disabled is False  # type: ignore[attr-defined]
    assert result[0]['zone'] == 'example.com.'
    assert result[0]['serial'] == 2024010101
    assert result[0]['kind'] == 'native'


def test_get_all_domains_honors_include_disabled_query() -> None:
    handler = _handler(['dns', 'getAllDomains'], query='includeDisabled=true')
    handler._get_all_domains()
    assert handler.database.include_disabled is True  # type: ignore[attr-defined]


def test_get_all_domains_odd_query_is_not_an_error(caplog: pytest.LogCaptureFixture) -> None:
    # The flag is read with stdlib parse_qs, which never raises; an odd query is simply not 'true', no error logged.
    handler = _handler(['dns', 'getAllDomains'], query='[abc=v')
    with caplog.at_level('ERROR'):
        handler._get_all_domains()
    assert handler.database.include_disabled is False  # type: ignore[attr-defined]
    assert not any(record.levelname == 'ERROR' for record in caplog.records)


def test_get_all_domains_include_disabled_is_strict() -> None:
    # Strict: enabled only for exactly one 'true'; a false, a non-'true' value, or duplicates leave it off.
    for query in ('includeDisabled=false', 'includeDisabled=1', 'includeDisabled=true&includeDisabled=false'):
        handler = _handler(['dns', 'getAllDomains'], query=query)
        handler._get_all_domains()
        assert handler.database.include_disabled is False  # type: ignore[attr-defined]


def test_get_all_domains_skips_invalid_soa() -> None:
    handler = _handler(['dns', 'getAllDomains'])
    handler.database.domains = [  # type: ignore[attr-defined]
        {'id': 1, 'domain': 'short.example', 'soa_content': 'ns1 hostmaster'},  # IndexError on serial
        {'id': 2, 'domain': 'nan.example', 'soa_content': 'ns1 hostmaster serial x x'},  # ValueError on int()
        {'id': 3, 'domain': 'ok.example', 'soa_content': 'ns1 hostmaster 42 1 1 1 1'}]
    result = handler._get_all_domains()
    assert [d['zone'] for d in result] == ['ok.example.']


# content dispatch

def test_content_lookup() -> None:
    handler = _handler(['dns', 'lookup', 'example.com', 'A'])
    handler.database.records = [_record()]  # type: ignore[attr-defined]
    payload = json.loads(handler.content())
    assert payload['result'][0]['qname'] == 'example.com'


def test_content_get_all_domains() -> None:
    handler = _handler(['dns', 'getAllDomains'])
    handler.database.domains = [  # type: ignore[attr-defined]
        {'id': 1, 'domain': 'example.com', 'soa_content': 'ns1 hostmaster 42 1 1 1 1'}]
    payload = json.loads(handler.content())
    assert payload['result'][0]['zone'] == 'example.com.'


def test_content_unknown_request_returns_false() -> None:
    handler = _handler(['dns', 'unknown'])
    assert json.loads(handler.content()) == {'result': False}
