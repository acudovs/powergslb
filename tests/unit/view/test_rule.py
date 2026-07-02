# pylint: disable=missing-function-docstring

"""Tests for ViewRule: compile a view rule once into a cached value object, then match clients without re-parsing.

resolve() splits the raw rule string into pre-built IPNetwork tokens and (kind, value) geo selectors and caches one
instance per string; matches() tests the pre-parsed client IP against the CIDRs first and only resolves the client
geo (once, via the configured reader) when the CIDRs miss. The GeoIP backend is driven through ViewRule._geoip.
"""

import netaddr
import pytest

from powergslb.client import ClientContext, ClientGeo
from powergslb.view import ViewRule
from powergslb.view.geoip import GeoIPReader


class _FakeGeoIP:
    """Inert stand-in for the GeoIP reader; counts lookups and returns a fixed ClientGeo."""

    def __init__(self, geo: ClientGeo) -> None:
        self.geo = geo
        self.calls = 0

    def lookup(self, _ip: netaddr.IPAddress) -> ClientGeo:
        self.calls += 1
        return self.geo


@pytest.fixture(autouse=True)
def _reset_geoip() -> object:
    """Keep ViewRule._geoip unconfigured by default and clear the resolve() cache between tests."""
    ViewRule.resolve.cache_clear()
    ViewRule._geoip = None  # pylint: disable=protected-access
    yield
    ViewRule.resolve.cache_clear()
    ViewRule._geoip = None  # pylint: disable=protected-access


def _context(ip: str) -> ClientContext:
    return ClientContext(netaddr.IPNetwork(ip))


# configure

def test_configure_opens_a_geoip_reader() -> None:
    # configure() installs a process-wide reader; an empty config yields an inert reader (no database).
    ViewRule.configure({})
    assert isinstance(ViewRule._geoip, GeoIPReader)  # pylint: disable=protected-access


# resolve: compilation and caching

def test_resolve_splits_cidrs_and_geos() -> None:
    rule = ViewRule.resolve('10.0.0.0/8 country:DE continent:EU')
    assert rule.cidrs == (netaddr.IPNetwork('10.0.0.0/8'),)
    assert rule.geos == (('country', 'DE'), ('continent', 'EU'))


def test_resolve_caches_per_string() -> None:
    assert ViewRule.resolve('10.0.0.0/8') is ViewRule.resolve('10.0.0.0/8')
    assert ViewRule.resolve('10.0.0.0/8') is not ViewRule.resolve('192.0.2.0/24')


@pytest.mark.parametrize('rule', ['', '   '])
def test_resolve_empty_rule_raises(rule: str) -> None:
    with pytest.raises(ValueError, match='at least one token'):
        ViewRule.resolve(rule)


def test_resolve_malformed_cidr_raises() -> None:
    with pytest.raises(ValueError, match='CIDR invalid'):
        ViewRule.resolve('10.0.0.0/8 not-a-cidr')


def test_resolve_bad_geo_token_raises() -> None:
    with pytest.raises(ValueError, match='geo token invalid'):
        ViewRule.resolve('country:ZZ')


def test_resolve_none_rule_raises_attribute_error() -> None:
    with pytest.raises(AttributeError):
        ViewRule.resolve(None)  # type: ignore[arg-type]


# matches: CIDR membership

@pytest.mark.parametrize('ip, expected', [('10.1.2.3', True), ('192.0.2.1', False)])
def test_matches_ipv4_cidr(ip: str, expected: bool) -> None:
    assert ViewRule.resolve('10.0.0.0/8').matches(_context(ip)) is expected


@pytest.mark.parametrize('ip, expected', [('2001:db8::1', True), ('2001:dead::1', False)])
def test_matches_ipv6_cidr(ip: str, expected: bool) -> None:
    assert ViewRule.resolve('2001:db8::/32').matches(_context(ip)) is expected


def test_matches_does_not_parse_client_ip() -> None:
    # matches() reads the pre-parsed address; an unconfigured geo rule simply misses, never raising on the IP.
    context = _context('203.0.113.7')
    assert ViewRule.resolve('10.0.0.0/8').matches(context) is False
    assert context.geo is None


# matches: geo selectors

def test_matches_geo_country(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeGeoIP(ClientGeo('DE', 'EU'))
    monkeypatch.setattr(ViewRule, '_geoip', fake)
    assert ViewRule.resolve('country:DE').matches(_context('203.0.113.7')) is True
    assert ViewRule.resolve('country:FR').matches(_context('203.0.113.7')) is False


def test_matches_geo_continent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ViewRule, '_geoip', _FakeGeoIP(ClientGeo('DE', 'EU')))
    assert ViewRule.resolve('continent:EU').matches(_context('203.0.113.7')) is True


def test_matches_cidr_short_circuits_geo(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeGeoIP(ClientGeo('DE', 'EU'))
    monkeypatch.setattr(ViewRule, '_geoip', fake)
    context = _context('10.1.2.3')
    assert ViewRule.resolve('10.0.0.0/8 country:DE').matches(context) is True
    assert fake.calls == 0  # the CIDR matched, so the geo backend is never consulted
    assert context.geo is None


def test_matches_geo_resolved_once(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeGeoIP(ClientGeo('DE', 'EU'))
    monkeypatch.setattr(ViewRule, '_geoip', fake)
    context = _context('203.0.113.7')
    ViewRule.resolve('country:FR').matches(context)   # miss, resolves geo and stores it
    ViewRule.resolve('country:DE').matches(context)   # reuses the stored geo
    assert fake.calls == 1
    assert context.geo == ClientGeo('DE', 'EU')


def test_matches_geo_unconfigured_never_matches() -> None:
    # With no reader configured, a geo-only rule cannot match and the context geo stays None.
    context = _context('203.0.113.7')
    assert ViewRule.resolve('country:DE').matches(context) is False
    assert context.geo is None


# matches_all (a rule that admits every client: a prefixlen-0 CIDR in both families)

def test_matches_all_both_families_true() -> None:
    assert ViewRule.resolve('0.0.0.0/0 ::/0').matches_all is True


def test_matches_all_ignores_token_order_and_extra_tokens() -> None:
    # Reordering and redundant narrower/geo tokens do not change that both families are fully covered.
    assert ViewRule.resolve('::/0 0.0.0.0/0').matches_all is True
    assert ViewRule.resolve('0.0.0.0/0 ::/0 10.0.0.0/8 country:DE').matches_all is True


def test_matches_all_single_family_false() -> None:
    # One family alone is not match-all: a client of the other family is out of view.
    assert ViewRule.resolve('0.0.0.0/0').matches_all is False
    assert ViewRule.resolve('::/0').matches_all is False


def test_matches_all_narrower_or_geo_only_false() -> None:
    assert ViewRule.resolve('10.0.0.0/8').matches_all is False
    assert ViewRule.resolve('0.0.0.0/0 country:DE').matches_all is False  # IPv6 clients only match if in DE
    assert ViewRule.resolve('country:DE continent:EU').matches_all is False
