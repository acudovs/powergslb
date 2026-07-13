# pylint: disable=missing-function-docstring, redefined-outer-name

"""Tests for the PowerDNSMixIn SQL builders.

gslb_checks, gslb_domains, and gslb_records. The mixin only assembles SQL and delegates to select; a fake select
records the operation/params so the assertions check the built query and bound parameters rather than a live
database.
"""

from typing import Any

import pytest

from powergslb.database.mysql.powerdns import PowerDNSMixIn


class _FakePowerDNSDatabase(PowerDNSMixIn):
    """Capture the last select call and return a canned result."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.result: list[dict[str, Any]] = []

    def select(self, operation: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        self.calls.append((operation, params))
        return self.result

    @property
    def sql(self) -> str:
        return ' '.join(self.calls[-1][0].split())

    @property
    def params(self) -> tuple[Any, ...]:
        return self.calls[-1][1]


@pytest.fixture
def database() -> _FakePowerDNSDatabase:
    return _FakePowerDNSDatabase()


def test_gslb_checks_selects_record_monitors(database: _FakePowerDNSDatabase) -> None:
    database.result = [{'id': 1}]
    assert database.gslb_checks() == [{'id': 1}]
    assert database.sql.startswith('SELECT')
    assert '`records`' in database.sql and '`monitors`' in database.sql
    assert database.params == ()


def test_gslb_domains_excludes_disabled_by_default(database: _FakePowerDNSDatabase) -> None:
    database.gslb_domains()
    assert "`types`.`type` = 'SOA'" in database.sql
    assert "`rrsets`.`name` = '@'" in database.sql
    assert '`records`.`disabled` = 0' in database.sql


def test_gslb_domains_include_disabled_drops_the_filter(database: _FakePowerDNSDatabase) -> None:
    database.gslb_domains(include_disabled=True)
    assert '`records`.`disabled` = 0' not in database.sql


def test_zone_suffixes_apex_is_single_candidate() -> None:
    assert PowerDNSMixIn.zone_suffixes('example.com') == ['example.com', 'com']


def test_zone_suffixes_subname_drops_leading_labels() -> None:
    assert PowerDNSMixIn.zone_suffixes('www.example.com') == ['www.example.com', 'example.com', 'com']


def test_zone_suffixes_deep_name_keeps_every_suffix() -> None:
    # '_' is a legal label char and is split like any other; the candidates run down to the last label.
    assert PowerDNSMixIn.zone_suffixes('_sip._tcp.a.example.com') == [
        '_sip._tcp.a.example.com', '_tcp.a.example.com', 'a.example.com', 'example.com', 'com']


def test_zone_suffixes_single_label() -> None:
    assert PowerDNSMixIn.zone_suffixes('com') == ['com']


def test_gslb_records_specific_qtype_binds_suffixes_qname_thrice_and_qtype(database: _FakePowerDNSDatabase) -> None:
    # the candidate suffixes bind first (indexed most-specific-zone lookup), then qname three times for the
    # apex/relative-name recovery, then the qtype
    database.gslb_records('www.example.com', 'A')
    assert database.params == ('www.example.com', 'example.com', 'com') + ('www.example.com',) * 3 + ('A',)
    assert '`types`.`type` = %s' in database.sql
    assert 'NOT EXISTS' not in database.sql  # the dependent most-specific-zone subquery is gone
    assert 'ORDER BY CHAR_LENGTH(`d`.`domain`) DESC LIMIT 1' in database.sql


def test_gslb_records_any_qtype_omits_type_filter(database: _FakePowerDNSDatabase) -> None:
    database.gslb_records('www.example.com', 'ANY')
    assert database.params == ('www.example.com', 'example.com', 'com') + ('www.example.com',) * 3
    assert '`types`.`type` = %s' not in database.sql


def test_gslb_records_apex_binds_two_suffixes(database: _FakePowerDNSDatabase) -> None:
    database.gslb_records('example.com', 'ANY')
    assert database.params == ('example.com', 'com', 'example.com', 'example.com', 'example.com')


def test_gslb_records_strips_trailing_dot(database: _FakePowerDNSDatabase) -> None:
    # PowerDNS sends the qname with a trailing dot; gslb_records normalizes it, so the bound params match the
    # dot-free form (a trailing dot would otherwise yield an empty-label suffix and break the SUBSTRING math).
    database.gslb_records('www.example.com.', 'ANY')
    assert database.params == ('www.example.com', 'example.com', 'com') + ('www.example.com',) * 3


def test_gslb_records_in_list_has_one_placeholder_per_suffix(database: _FakePowerDNSDatabase) -> None:
    database.gslb_records('a.b.example.com', 'ANY')
    # four candidate suffixes -> a four-placeholder IN list
    assert '`d`.`domain` IN (%s, %s, %s, %s)' in database.sql
    assert '`types`.`type` = %s' not in database.sql
