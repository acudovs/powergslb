# pylint: disable=missing-function-docstring

"""Tests for the GeoIP reader and the view-rule geo-token syntax.

GeoIPReader.parse_geo_token classifies a single view-rule token (country / continent / malformed / non-geo CIDR
pass-through). GeoIPReader stays inert when no database is configured or the file is missing, and resolves a
client IP to its country and continent against a mocked maxminddb reader otherwise. No real MMDB is opened.
"""

from typing import Any

import pytest

import maxminddb

from powergslb.system import geoip as geoip_module
from powergslb.system.geoip import GeoIPReader


# parse_geo_token

@pytest.mark.parametrize('token, expected', [
    ('country:DE', ('country', 'DE')),
    ('country:de', ('country', 'DE')),  # normalised upper-case
    ('country:SS', ('country', 'SS')),  # a recently-assigned code (South Sudan)
    ('country:XK', ('country', 'XK')),  # user-assigned, emitted by the GeoIP databases
    ('CONTRY:typo', None),  # unknown prefix is not a geo token
    ('continent:EU', ('continent', 'EU')),
    ('continent:eu', ('continent', 'EU')),
])
def test_parse_geo_token_classifies(token: str, expected: Any) -> None:
    assert GeoIPReader.parse_geo_token(token) == expected


@pytest.mark.parametrize('token', ['10.0.0.0/8', '2001:db8::/32', '0.0.0.0/0', 'plain'])
def test_parse_geo_token_non_geo_returns_none(token: str) -> None:
    # A CIDR carries no ':' prefix (or, for IPv6, no country/continent prefix), so it is treated as a CIDR.
    assert GeoIPReader.parse_geo_token(token) is None


@pytest.mark.parametrize('token', ['country:ZZ', 'country:XY', 'continent:XX', 'continent:europe'])
def test_parse_geo_token_malformed_raises(token: str) -> None:
    with pytest.raises(ValueError, match='geo token invalid'):
        GeoIPReader.parse_geo_token(token)


def test_country_codes_are_well_formed() -> None:
    # Guard the table against stray edits: 249 ISO 3166-1 alpha-2 codes plus XK, each two upper-case letters.
    assert len(GeoIPReader.COUNTRY_CODES) == 250
    assert all(len(code) == 2 and code.isascii() and code.isalpha() and code.isupper()
               for code in GeoIPReader.COUNTRY_CODES)
    assert 'XK' in GeoIPReader.COUNTRY_CODES
    assert 'ZZ' not in GeoIPReader.COUNTRY_CODES


def test_parse_geo_token_ipv6_cidr_is_not_a_geo_token() -> None:
    # An IPv6 CIDR contains ':' but its prefix is neither 'country' nor 'continent', so it stays a CIDR.
    assert GeoIPReader.parse_geo_token('2001:db8::/32') is None


# GeoIPReader: inert

def test_reader_absent_database_is_inert() -> None:
    reader = GeoIPReader({})  # no 'database' option in the section
    assert reader.lookup('8.8.8.8') == (None, None)
    reader.close()  # close on an inert reader is a no-op


def test_reader_empty_database_is_inert() -> None:
    assert GeoIPReader({'database': ''}).lookup('8.8.8.8') == (None, None)


def test_reader_missing_file_is_inert(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    def raise_missing(_path: str) -> Any:
        raise FileNotFoundError(2, 'No such file or directory')

    monkeypatch.setattr(geoip_module.maxminddb, 'open_database', raise_missing)
    with caplog.at_level('WARNING'):
        reader = GeoIPReader({'database': '/nope.mmdb'})
    assert reader.lookup('8.8.8.8') == (None, None)
    assert any('geoip database /nope.mmdb unavailable:' in r.getMessage() for r in caplog.records)


def test_reader_invalid_database_is_inert(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_invalid(_path: str) -> Any:
        raise maxminddb.InvalidDatabaseError('corrupt')

    monkeypatch.setattr(geoip_module.maxminddb, 'open_database', raise_invalid)
    assert GeoIPReader({'database': '/bad.mmdb'}).lookup('8.8.8.8') == (None, None)


# GeoIPReader: loaded lookups

class _FakeReader:
    def __init__(self, record: Any) -> None:
        self.record = record
        self.queried: str | None = None
        self.closed = False

    def get(self, ip: str) -> Any:
        self.queried = ip
        if isinstance(self.record, Exception):
            raise self.record
        return self.record

    def close(self) -> None:
        self.closed = True


def _loaded_reader(monkeypatch: pytest.MonkeyPatch, record: Any) -> tuple[GeoIPReader, _FakeReader]:
    fake = _FakeReader(record)
    monkeypatch.setattr(geoip_module.maxminddb, 'open_database', lambda _path: fake)
    return GeoIPReader({'database': '/db.mmdb'}), fake


def test_reader_lookup_resolves_country_and_continent(monkeypatch: pytest.MonkeyPatch) -> None:
    reader, fake = _loaded_reader(monkeypatch, {'country': {'iso_code': 'US'}, 'continent': {'code': 'NA'}})
    assert reader.lookup('8.8.8.8') == ('US', 'NA')
    assert fake.queried == '8.8.8.8'


def test_reader_lookup_partial_record(monkeypatch: pytest.MonkeyPatch) -> None:
    # A record with only a continent yields a country of None, not a KeyError.
    reader, _ = _loaded_reader(monkeypatch, {'continent': {'code': 'EU'}})
    assert reader.lookup('2.2.2.2') == (None, 'EU')


def test_reader_lookup_no_record(monkeypatch: pytest.MonkeyPatch) -> None:
    reader, _ = _loaded_reader(monkeypatch, None)
    assert reader.lookup('192.0.2.1') == (None, None)


def test_reader_lookup_non_dict_record(monkeypatch: pytest.MonkeyPatch) -> None:
    reader, _ = _loaded_reader(monkeypatch, 'unexpected')
    assert reader.lookup('192.0.2.1') == (None, None)


def test_reader_lookup_malformed_address(monkeypatch: pytest.MonkeyPatch) -> None:
    reader, _ = _loaded_reader(monkeypatch, ValueError('bad address'))
    assert reader.lookup('not-an-ip') == (None, None)


def test_reader_lookup_none_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    reader, fake = _loaded_reader(monkeypatch, {'country': {'iso_code': 'US'}})
    assert reader.lookup(None) == (None, None)
    assert fake.queried is None  # the reader is never queried for a None address


def test_reader_close_closes_underlying_and_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    reader, fake = _loaded_reader(monkeypatch, {})
    reader.close()
    assert fake.closed is True
    assert reader.lookup('8.8.8.8') == (None, None)  # closed reader is inert
    reader.close()  # second close is a no-op, no AttributeError
