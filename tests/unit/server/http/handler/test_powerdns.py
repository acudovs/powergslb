# pylint: disable=missing-function-docstring, redefined-outer-name, protected-access

"""Tests for PowerDNSRequestHandler.

Remote-IP resolution (the PowerDNS header is honored only here), the GET-only route, view matching, the
view -> health -> routing-policy filter pipeline (empty-in-view short-circuit, all-down=all-up keep-all, policy
delegation, malformed-policy drop), the lookup and getAllDomains responses, and content() dispatch. The handler is
built with __new__ to skip the socket-opening __init__; the database and the shared status set are faked so
filtering needs no backend. Geo is driven through ViewRule._geoip (an inert reader by default).
"""

import json
import logging
from typing import Any

import netaddr
import pytest

from powergslb.client import ClientContext, ClientGeo
from powergslb.monitor.status import StatusRegistry
from powergslb.routing import RoutingPolicy
from powergslb.server.http.handler.powerdns import PowerDNSRequestHandler
from powergslb.view import ViewRule


@pytest.fixture(autouse=True)
def _clear_caches() -> Any:
    """Clear the cached policy/rule instances and keep ViewRule._geoip unconfigured between tests."""
    RoutingPolicy.resolve.cache_clear()
    ViewRule.resolve.cache_clear()
    ViewRule._geoip = None
    yield
    RoutingPolicy.resolve.cache_clear()
    ViewRule.resolve.cache_clear()
    ViewRule._geoip = None


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
    """Stub GeoIP reader returning a fixed ClientGeo for every lookup, recording the addresses queried."""

    def __init__(self, country: str | None = None, continent: str | None = None) -> None:
        self.geo = ClientGeo(country, continent)
        self.queried: list[netaddr.IPAddress] = []

    def lookup(self, ip: netaddr.IPAddress) -> ClientGeo:
        self.queried.append(ip)
        return self.geo


def _handler(dirs: list[str], remote_ip: str = '203.0.113.5', query: Any = None,
             status_registry: Any = None) -> PowerDNSRequestHandler:
    """Build a handler without running __init__ (which would open a socket and call handle())."""
    handler = PowerDNSRequestHandler.__new__(PowerDNSRequestHandler)
    handler.body = None
    handler.database = _FakeDatabase()  # type: ignore[assignment]
    handler.dirs = dirs
    handler.headers = {}  # type: ignore[assignment]
    handler.path = '/' + '/'.join(dirs)
    handler.remote_ip = netaddr.IPAddress(remote_ip)
    handler.context = ClientContext(netaddr.IPNetwork(remote_ip))
    handler.query = query
    handler.status_registry = status_registry or StatusRegistry()
    return handler


@pytest.fixture
def status_registry() -> StatusRegistry:
    return StatusRegistry()


def _record(**overrides: Any) -> dict[str, Any]:
    record = {'id': 1, 'qname': 'example.com', 'qtype': 'A', 'content': '192.0.2.1', 'ttl': 60,
              'weight': 0, 'rule': '0.0.0.0/0 ::/0', 'policy_json': '{"type": "round-robin"}'}
    record.update(overrides)
    return record


def _in_view(handler: PowerDNSRequestHandler, record: dict[str, Any]) -> bool:
    """Run the handler's view test with a fresh context carrying the handler's client network."""
    return handler._is_in_view(record, ClientContext(handler.context.remote))


# _set_remote_ip (the DNS interface honors the PowerDNS headers)

def test_set_remote_ip_from_real_remote_header() -> None:
    handler = _handler(['dns'])
    handler.client_address = ('127.0.0.1', 1)  # type: ignore[assignment]
    handler.headers = {  # type: ignore[assignment]
        'X-Remotebackend-Remote': '172.22.0.1', 'X-Remotebackend-Real-Remote': '198.51.100.0/24'}
    handler._set_remote_ip()
    assert handler.remote_ip.format() == '172.22.0.1'  # remote_ip is the recursor, not the ECS subnet
    assert handler.context.remote.ip.format() == '198.51.100.0'
    assert handler.context.remote.prefixlen == 24  # the header prefix is the ECS source prefix


def test_set_remote_ip_invalid_real_remote_falls_back_to_peer(caplog: pytest.LogCaptureFixture) -> None:
    handler = _handler(['dns'])
    handler.client_address = ('127.0.0.1', 1)  # type: ignore[assignment]
    handler.headers = {'X-Remotebackend-Real-Remote': 'not-an-ip'}  # type: ignore[assignment]
    with caplog.at_level(logging.ERROR):
        handler._set_remote_ip()
    assert handler.context.remote.ip.format() == '127.0.0.1'
    assert handler.context.remote.prefixlen == 32  # the peer is taken as a host network
    assert 'header invalid' in caplog.text  # a present but malformed header is logged


def test_set_remote_ip_invalid_remote_falls_back_to_peer(caplog: pytest.LogCaptureFixture) -> None:
    handler = _handler(['dns'])
    handler.client_address = ('127.0.0.1', 1)  # type: ignore[assignment]
    handler.headers = {'X-Remotebackend-Remote': 'not-an-ip'}  # type: ignore[assignment]
    with caplog.at_level(logging.ERROR):
        handler._set_remote_ip()
    assert handler.remote_ip.format() == '127.0.0.1'  # a malformed recursor header falls back to the peer
    assert 'header invalid' in caplog.text


def test_set_remote_ip_without_header_uses_peer(caplog: pytest.LogCaptureFixture) -> None:
    handler = _handler(['dns'])
    handler.client_address = ('203.0.113.9', 4321)  # type: ignore[assignment]
    handler.headers = {}  # type: ignore[assignment]
    with caplog.at_level(logging.ERROR):
        handler._set_remote_ip()
    assert handler.remote_ip.format() == '203.0.113.9'
    assert handler.context.remote.ip.format() == '203.0.113.9'
    assert handler.context.remote.prefixlen == 32  # no header means the peer as a host network
    assert caplog.text == ''  # an absent header falls back silently


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
    assert _in_view(handler, _record(rule='203.0.113.0/24')) is True


def test_is_in_view_no_match() -> None:
    handler = _handler(['dns'], remote_ip='203.0.113.5')
    assert _in_view(handler, _record(rule='10.0.0.0/8')) is False


def test_is_in_view_empty_rule_is_false() -> None:
    handler = _handler(['dns'])
    assert _in_view(handler, _record(rule='')) is False


def test_is_in_view_malformed_rule_is_false() -> None:
    handler = _handler(['dns'])
    assert _in_view(handler, _record(rule='not-a-cidr')) is False


# _is_in_view: geo tokens (driven via ViewRule._geoip)

def test_is_in_view_country_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ViewRule, '_geoip', _FakeGeoIP(country='US', continent='NA'))
    handler = _handler(['dns'])
    assert _in_view(handler, _record(rule='country:US')) is True


def test_is_in_view_country_no_match(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ViewRule, '_geoip', _FakeGeoIP(country='DE', continent='EU'))
    handler = _handler(['dns'])
    assert _in_view(handler, _record(rule='country:US')) is False


def test_is_in_view_continent_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ViewRule, '_geoip', _FakeGeoIP(country='US', continent='NA'))
    handler = _handler(['dns'])
    assert _in_view(handler, _record(rule='continent:NA')) is True


def test_is_in_view_mixed_cidr_and_geo_is_a_union(monkeypatch: pytest.MonkeyPatch) -> None:
    # A client outside the CIDR but inside the geo token still matches; the rule is a union.
    monkeypatch.setattr(ViewRule, '_geoip', _FakeGeoIP(country='US'))
    handler = _handler(['dns'], remote_ip='203.0.113.5')
    assert _in_view(handler, _record(rule='10.0.0.0/8 country:US')) is True


def test_is_in_view_cidr_matches_without_consulting_geoip(monkeypatch: pytest.MonkeyPatch) -> None:
    # When the CIDR already matches, the geo reader is not queried (CIDR is checked first).
    geoip = _FakeGeoIP(country='DE')
    monkeypatch.setattr(ViewRule, '_geoip', geoip)
    handler = _handler(['dns'], remote_ip='203.0.113.5')
    assert _in_view(handler, _record(rule='203.0.113.0/24 country:US')) is True
    assert not geoip.queried


def test_is_in_view_geo_inert_with_unloaded_geoip(monkeypatch: pytest.MonkeyPatch) -> None:
    # An unloaded reader resolves to no location, so geo tokens never match (CIDR behaviour is unchanged).
    monkeypatch.setattr(ViewRule, '_geoip', _FakeGeoIP(country=None, continent=None))
    handler = _handler(['dns'])
    assert _in_view(handler, _record(rule='continent:EU')) is False


def test_is_in_view_geo_unconfigured_is_false() -> None:
    # With no reader configured at all, a geo-only rule never matches.
    handler = _handler(['dns'])
    assert _in_view(handler, _record(rule='continent:EU')) is False


def test_is_in_view_malformed_geo_token_is_false(caplog: pytest.LogCaptureFixture) -> None:
    handler = _handler(['dns'])
    with caplog.at_level('ERROR'):
        assert _in_view(handler, _record(rule='country:USA')) is False
    assert any('view rule invalid' in r.getMessage() for r in caplog.records)


# _select_records: view filter

def test_filter_skips_records_out_of_view() -> None:
    handler = _handler(['dns'], remote_ip='203.0.113.5')
    records = {'A': [_record(id=1, rule='10.0.0.0/8', content='hidden')]}
    assert not handler._select_records(records)


def test_filter_empty_in_view_never_resolves_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    # The empty-in-view short-circuit must skip the qtype without resolving or calling the policy.
    calls: list[str] = []
    monkeypatch.setattr(RoutingPolicy, 'resolve', calls.append)
    handler = _handler(['dns'], remote_ip='203.0.113.5')
    records = {'A': [_record(id=1, rule='10.0.0.0/8', content='hidden')]}
    assert not handler._select_records(records)
    assert not calls  # the policy was never resolved


# _select_records: routing policy delegation (round-robin default)

def test_filter_picks_highest_weight_live_group() -> None:
    handler = _handler(['dns'])
    records = {'A': [_record(id=1, weight=10, content='low'), _record(id=2, weight=20, content='high')]}
    result = handler._select_records(records)
    assert [r['content'] for r in result['A']] == ['high']


def test_filter_drops_down_records(status_registry: StatusRegistry) -> None:
    status_registry.add(1)  # the down record is dropped; the live one at the same tier remains
    handler = _handler(['dns'], status_registry=status_registry)
    records = {'A': [_record(id=1, weight=0, content='down'), _record(id=2, weight=0, content='up')]}
    result = handler._select_records(records)
    assert [r['content'] for r in result['A']] == ['up']


def test_filter_all_down_keeps_all_in_view(status_registry: StatusRegistry) -> None:
    # 'all down = all up': when health empties a non-empty in-view set, the down records are kept and the policy
    # picks the highest-weight tier (the primary, as last resort).
    status_registry.add(1)
    status_registry.add(2)
    handler = _handler(['dns'], status_registry=status_registry)
    records = {'A': [_record(id=1, weight=10, content='backup'), _record(id=2, weight=20, content='primary')]}
    result = handler._select_records(records)
    assert [r['content'] for r in result['A']] == ['primary']


def test_filter_keep_all_does_not_resurrect_out_of_view(status_registry: StatusRegistry) -> None:
    # The keep-all rule resurrects down records, never out-of-view ones.
    status_registry.add(1)
    handler = _handler(['dns'], remote_ip='203.0.113.5', status_registry=status_registry)
    records = {'A': [_record(id=1, rule='10.0.0.0/8', content='hidden')]}
    assert not handler._select_records(records)


def test_filter_delegates_candidates_and_context(monkeypatch: pytest.MonkeyPatch) -> None:
    # The live, in-view candidates and a ClientContext carrying the client IP reach the resolved policy's select().
    seen: dict[str, Any] = {}

    class _SpyPolicy:
        def select(self, candidates: list[dict[str, Any]], context: ClientContext) -> list[dict[str, Any]]:
            seen['candidates'] = candidates
            seen['context'] = context
            return candidates[:1]

    monkeypatch.setattr(RoutingPolicy, 'resolve', lambda policy_json: _SpyPolicy())
    handler = _handler(['dns'], remote_ip='198.51.100.9')
    records = {'A': [_record(id=1, content='a'), _record(id=2, content='b')]}
    result = handler._select_records(records)
    assert [r['content'] for r in result['A']] == ['a']
    assert {r['content'] for r in seen['candidates']} == {'a', 'b'}
    # the view rule is CIDR-only, so context.geo stays None and the contexts compare equal
    assert seen['context'] == ClientContext(netaddr.IPNetwork('198.51.100.9'))


def test_filter_malformed_policy_drops_qtype(caplog: pytest.LogCaptureFixture) -> None:
    handler = _handler(['dns'])
    records = {'A': [_record(id=1, content='a', policy_json='{not json}')]}
    with caplog.at_level('ERROR'):
        assert not handler._select_records(records)
    assert any('routing policy invalid' in r.getMessage() for r in caplog.records)


def test_filter_processes_qtypes_independently() -> None:
    handler = _handler(['dns'])
    records = {'A': [_record(id=1, qtype='A', weight=20, content='a-high'),
                     _record(id=2, qtype='A', weight=10, content='a-low')],
               'AAAA': [_record(id=3, qtype='AAAA', weight=0, content='aaaa')]}
    result = handler._select_records(records)
    assert sorted(r['content'] for group in result.values() for r in group) == ['a-high', 'aaaa']


# _scope_prefix (the ECS scope returned to PowerDNS)

def test_scope_prefix_match_all_view_is_zero() -> None:
    # Every record's view matches all clients -> globally cacheable.
    handler = _handler(['dns'])
    handler.context.remote.prefixlen = 24
    assert handler._scope_prefix([_record(rule='0.0.0.0/0 ::/0')]) == 0


def test_scope_prefix_specific_view_is_source_prefix() -> None:
    # A narrower view shaped the answer -> scope the client's source prefix.
    handler = _handler(['dns'])
    handler.context.remote.prefixlen = 24
    assert handler._scope_prefix([_record(rule='10.0.0.0/8')]) == 24


def test_scope_prefix_mixed_view_is_source_prefix() -> None:
    # If any record's view is not match-all, the rrset as a whole is subnet specific.
    handler = _handler(['dns'], remote_ip='2001:db8::1')
    handler.context.remote.prefixlen = 56
    records = [_record(rule='0.0.0.0/0 ::/0'), _record(rule='2001:db8::/32')]
    assert handler._scope_prefix(records) == 56


def test_scope_prefix_single_family_rule_is_source_prefix() -> None:
    # A single-family match-all rule is not globally cacheable (other-family clients are out of view).
    handler = _handler(['dns'])
    handler.context.remote.prefixlen = 24
    assert handler._scope_prefix([_record(rule='0.0.0.0/0')]) == 24


def test_scope_prefix_malformed_rule_is_source_prefix() -> None:
    # A malformed view rule is treated as not match-all, so the rrset scopes to the source prefix.
    handler = _handler(['dns'])
    handler.context.remote.prefixlen = 24
    assert handler._scope_prefix([_record(rule='not-a-cidr')]) == 24


def test_scope_prefix_opt_out_source_prefix_is_zero() -> None:
    # A narrower view but an ECS opt-out (source prefix 0) resolves to scope 0.
    handler = _handler(['dns'])
    handler.context.remote.prefixlen = 0
    assert handler._scope_prefix([_record(rule='10.0.0.0/8')]) == 0


def test_scope_prefix_empty_answer_is_zero() -> None:
    handler = _handler(['dns'])
    handler.context.remote.prefixlen = 24
    assert handler._scope_prefix([]) == 0


def test_scope_prefix_infra_qtypes_always_zero() -> None:
    # SOA/NS/DS use global /0 scope regardless of view, even a narrower one (Google: consistent delegation).
    handler = _handler(['dns'])
    handler.context.remote.prefixlen = 24
    for qtype in ('SOA', 'NS', 'DS'):
        assert handler._scope_prefix([_record(qtype=qtype, rule='10.0.0.0/8')]) == 0, qtype


def test_scope_prefix_sticky_hash_match_all_is_min_prefix_source() -> None:
    # A match-all rrset under sticky-hash still varies by client network: scope = min(prefix, source).
    handler = _handler(['dns'], remote_ip='198.51.100.5')  # IPv4 client -> ipv4_prefix
    handler.context.remote.prefixlen = 24
    coarse = '{"type": "sticky-hash", "ipv4_prefix": 16}'
    assert handler._scope_prefix([_record(rule='0.0.0.0/0 ::/0', policy_json=coarse)]) == 16  # prefix < source
    fine = '{"type": "sticky-hash", "ipv4_prefix": 28}'
    assert handler._scope_prefix([_record(rule='0.0.0.0/0 ::/0', policy_json=fine)]) == 24  # capped at source


def test_scope_prefix_sticky_hash_opt_out_is_zero() -> None:
    # ECS opt-out (source prefix 0): the policy varies by network but there is no source prefix to scope to.
    handler = _handler(['dns'], remote_ip='198.51.100.5')
    handler.context.remote.prefixlen = 0
    sticky = '{"type": "sticky-hash"}'
    assert handler._scope_prefix([_record(rule='0.0.0.0/0 ::/0', policy_json=sticky)]) == 0


# _get_lookup

def test_get_lookup_strips_trailing_dot_and_projects() -> None:
    handler = _handler(['dns', 'lookup', 'example.com.', 'A'])
    handler.database.records = [_record(content='192.0.2.1', ttl=30)]  # type: ignore[attr-defined]
    result = handler._get_lookup()
    assert handler.database.gslb_records_args == ('example.com', 'A')  # type: ignore[attr-defined]
    assert result == [{'qname': 'example.com', 'qtype': 'A', 'content': '192.0.2.1', 'ttl': 30, 'scopeMask': 0}]


def test_get_lookup_specific_view_carries_source_prefix_scope() -> None:
    handler = _handler(['dns', 'lookup', 'example.com.', 'A'], remote_ip='10.1.2.3')
    handler.context.remote.prefixlen = 24
    handler.database.records = [_record(content='10.9.9.9', rule='10.0.0.0/8')]  # type: ignore[attr-defined]
    result = handler._get_lookup()
    assert result == [{'qname': 'example.com', 'qtype': 'A', 'content': '10.9.9.9', 'ttl': 60, 'scopeMask': 24}]


def test_get_lookup_scopes_each_qtype_independently() -> None:
    # PowerDNS sends every lookup as ANY, so one lookup returns the whole rrset bundle at the name. A
    # subnet-specific A rrset must not push its scope onto the NS rrset in the same response.
    handler = _handler(['dns', 'lookup', 'example.com.', 'ANY'], remote_ip='10.1.2.3')
    handler.context.remote.prefixlen = 24
    handler.database.records = [  # type: ignore[attr-defined]
        _record(id=1, qtype='NS', content='ns1.example.com'),
        _record(id=2, qtype='NS', content='ns2.example.com'),
        _record(id=3, qtype='A', content='192.0.2.1'),
        _record(id=4, qtype='A', content='10.9.9.9', rule='10.0.0.0/8')]
    scope = {(r['qtype'], r['content']): r['scopeMask'] for r in handler._get_lookup()}
    assert scope[('NS', 'ns1.example.com')] == 0  # NS is always scope 0 (infra/delegation)
    assert scope[('NS', 'ns2.example.com')] == 0
    assert scope[('A', '192.0.2.1')] == 24  # the A rrset as a whole is subnet specific
    assert scope[('A', '10.9.9.9')] == 24


def test_get_lookup_public_client_mixed_rrset_scopes_source() -> None:
    # A public client matches only the match-all record, but the rrset has a narrower-view sibling, so the answer
    # is subnet specific: scope is the source prefix, not 0, or a resolver would cache it globally and withhold
    # the Private record from private clients.
    handler = _handler(['dns', 'lookup', 'example.com.', 'A'], remote_ip='198.51.100.5')
    handler.context.remote.prefixlen = 24
    handler.database.records = [  # type: ignore[attr-defined]
        _record(id=1, content='192.0.2.1', rule='0.0.0.0/0 ::/0'),
        _record(id=2, content='10.9.9.9', rule='10.0.0.0/8')]
    result = handler._get_lookup()
    assert [r['content'] for r in result] == ['192.0.2.1']  # public client gets only the match-all record
    assert result[0]['scopeMask'] == 24  # but scoped to the source prefix: the rrset is view-differentiated


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
