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


def test_gslb_records_specific_qtype_binds_qname_six_times_and_qtype(database: _FakePowerDNSDatabase) -> None:
    # the qname binds six times (apex/suffix match plus the NOT EXISTS most-specific-zone guard), then the qtype
    database.gslb_records('www.example.com', 'A')
    assert database.params == ('www.example.com',) * 6 + ('A',)
    assert '`types`.`type` = %s' in database.sql
    assert 'NOT EXISTS' in database.sql


def test_gslb_records_any_qtype_binds_only_qname(database: _FakePowerDNSDatabase) -> None:
    database.gslb_records('www.example.com', 'ANY')
    assert database.params == ('www.example.com',) * 6
    assert '`types`.`type` = %s' not in database.sql
