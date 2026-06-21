# pylint: disable=missing-function-docstring, redefined-outer-name

"""Schema constraint surface, garbage-collection triggers, longest-zone-match resolution and case-insensitive lookup.

The constraint and GC tests drive raw SQL through `docker exec <container> mariadb powergslb` (root, via the
container's /root/.my.cnf), so they exercise the operator-facing manual-SQL path - the triggers, CHECK constraints and
unique keys that the admin API never reaches directly. They are skipped unless POWERGSLB_CONTAINER names a container.
The longest-zone-match and case-insensitive tests go through the HTTP admin and DNS clients.

Wire type values used in raw SQL: 1 = A, 5 = CNAME, 6 = SOA (RFC 1035, matching types.value).
"""

import os
import subprocess
from collections.abc import Iterator

import pytest

from .conftest import DNSClient

CONTAINER = os.environ.get('POWERGSLB_CONTAINER', '')

pytestmark = pytest.mark.skipif(
    not CONTAINER, reason='POWERGSLB_CONTAINER not set; the schema constraint tests need docker/mariadb control')

_SCRATCH = 'constraints-test.example'


def _sql(statement: str) -> subprocess.CompletedProcess[str]:
    """Run one SQL statement in the container's powergslb database as root and return the completed process."""
    return subprocess.run(['docker', 'exec', CONTAINER, 'mariadb', 'powergslb', '-N', '-B', '-e', statement],
                          capture_output=True, text=True, check=False)


def _ok(statement: str) -> str:
    result = _sql(statement)
    assert result.returncode == 0, f'{statement}\n{result.stderr}'
    return result.stdout.strip()


def _err(statement: str) -> str:
    """Run a statement that must fail and return its stderr (the ERROR line)."""
    result = _sql(statement)
    assert result.returncode != 0, f'expected failure, but it succeeded: {statement}'
    return result.stderr


def _scratch_id() -> int:
    return int(_ok(f"SELECT `id` FROM `domains` WHERE `domain` = '{_SCRATCH}'"))


@pytest.fixture
def scratch() -> Iterator[int]:
    """A throwaway zone for the constraint tests; every rrset and record under it is purged after each test."""
    _ok(f"INSERT INTO `domains` (`domain`) VALUES ('{_SCRATCH}')")
    domain_id = _scratch_id()
    try:
        yield domain_id
    finally:
        ids = _ok(f"SELECT `id` FROM `rrsets` WHERE `domain_id` = {domain_id}").split()
        if ids:
            # delete records by literal rrset id (never reference `rrsets` in a records DELETE - the AFTER-delete GC
            # trigger would raise 1442); the GC then drops the emptied rrsets
            _ok(f"DELETE FROM `records` WHERE `rrset_id` IN ({', '.join(ids)})")
        _ok(f"DELETE FROM `rrsets` WHERE `domain_id` = {domain_id}")
        _ok(f"DELETE FROM `domains` WHERE `id` = {domain_id}")


def _new_rrset(domain_id: int, name: str, type_value: int, ttl: int = 300) -> int:
    _ok(f"INSERT INTO `rrsets` (`domain_id`, `name`, `type_value`, `ttl`) "
        f"VALUES ({domain_id}, '{name}', {type_value}, {ttl})")
    return int(_ok(f"SELECT `id` FROM `rrsets` "
                   f"WHERE `domain_id` = {domain_id} AND `name` = '{name}' AND `type_value` = {type_value}"))


def _new_record(rrset_id: int, content: str, view_id: int = 1) -> int:
    _ok(f"INSERT INTO `records` (`rrset_id`, `content`, `monitor_id`, `view_id`) "
        f"VALUES ({rrset_id}, '{content}', 1, {view_id})")
    return int(_ok(f"SELECT `id` FROM `records` "
                   f"WHERE `rrset_id` = {rrset_id} AND `view_id` = {view_id} AND `content` = '{content}'"))


# apex CHECK and SOA uniqueness

def test_soa_rrset_off_apex_rejected(scratch: int) -> None:
    # rrsets_soa_apex_check: an SOA rrset whose name is not '@' fails the CHECK constraint
    err = _err(f"INSERT INTO `rrsets` (`domain_id`, `name`, `type_value`, `ttl`) "
               f"VALUES ({scratch}, 'notapex', 6, 86400)")
    assert 'ERROR 4025' in err


def test_second_soa_rrset_in_zone_rejected(scratch: int) -> None:
    # the (domain_id, name, type_value) unique key permits exactly one apex SOA rrset per zone
    _new_rrset(scratch, '@', 6, 86400)
    err = _err(f"INSERT INTO `rrsets` (`domain_id`, `name`, `type_value`, `ttl`) "
               f"VALUES ({scratch}, '@', 6, 86400)")
    assert 'ERROR 1062' in err


# SOA single record

def test_soa_rrset_allows_one_record(scratch: int) -> None:
    rrset_id = _new_rrset(scratch, '@', 6, 86400)
    soa = 'ns1.example. hostmaster.example. 1 1 1 1 1'
    _ok(f"INSERT INTO `records` (`rrset_id`, `content`, `monitor_id`, `view_id`) "
        f"VALUES ({rrset_id}, '{soa}', 1, 1)")
    err = _err(f"INSERT INTO `records` (`rrset_id`, `content`, `monitor_id`, `view_id`) "
               f"VALUES ({rrset_id}, 'ns2.example. hostmaster.example. 2 1 1 1 1', 1, 1)")
    assert 'ERROR 1644' in err


# CNAME exclusivity, both directions

def test_cname_rrset_excludes_other_type_at_name(scratch: int) -> None:
    _new_rrset(scratch, 'www', 5)  # CNAME
    err = _err(f"INSERT INTO `rrsets` (`domain_id`, `name`, `type_value`, `ttl`) "
               f"VALUES ({scratch}, 'www', 1, 300)")  # A at the same name
    assert 'ERROR 1644' in err


def test_other_type_at_name_excludes_cname(scratch: int) -> None:
    _new_rrset(scratch, 'mail', 1)  # A
    err = _err(f"INSERT INTO `rrsets` (`domain_id`, `name`, `type_value`, `ttl`) "
               f"VALUES ({scratch}, 'mail', 5, 300)")  # CNAME at the same name
    assert 'ERROR 1644' in err


# CNAME exclusivity is revalidated on rrset rename (rrsets_before_update -> rrset_guard)

def test_renaming_cname_onto_occupied_name_rejected(scratch: int) -> None:
    # rrset_guard branch A on UPDATE: renaming a CNAME rrset onto a name that already holds another rrset
    _new_rrset(scratch, 'host', 1)              # A
    cname_id = _new_rrset(scratch, 'alias', 5)  # CNAME, different name
    err = _err(f"UPDATE `rrsets` SET `name` = 'host' WHERE `id` = {cname_id}")
    assert 'ERROR 1644' in err
    assert 'CNAME rrset conflicts with other rrsets at this name' in err


def test_renaming_other_type_onto_cname_name_rejected(scratch: int) -> None:
    # rrset_guard branch B on UPDATE: renaming a non-CNAME rrset onto a name that already holds a CNAME
    _new_rrset(scratch, 'alias', 5)         # CNAME
    a_id = _new_rrset(scratch, 'host', 1)   # A, different name
    err = _err(f"UPDATE `rrsets` SET `name` = 'alias' WHERE `id` = {a_id}")
    assert 'ERROR 1644' in err
    assert 'name already has a CNAME rrset' in err


# changing a rrset's type to SOA while it holds multiple records (rrset_guard SOA branch, update-only)

def test_changing_multi_record_rrset_to_soa_rejected(scratch: int) -> None:
    # rrset_guard SOA branch fires only on UPDATE (insert passes a NULL rrset id, so the record count is 0)
    rrset_id = _new_rrset(scratch, '@', 1)  # apex A so the SOA apex CHECK still passes after the type change
    _new_record(rrset_id, '192.0.2.1')
    _new_record(rrset_id, '192.0.2.2')
    err = _err(f"UPDATE `rrsets` SET `type_value` = 6 WHERE `id` = {rrset_id}")
    assert 'ERROR 1644' in err
    assert 'SOA rrset allows exactly one record' in err


# CNAME one record per view

def test_cname_rrset_allows_one_record_per_view(scratch: int) -> None:
    rrset_id = _new_rrset(scratch, 'alias', 5)
    _ok(f"INSERT INTO `records` (`rrset_id`, `content`, `monitor_id`, `view_id`) "
        f"VALUES ({rrset_id}, 'target-a.example', 1, 1)")
    err = _err(f"INSERT INTO `records` (`rrset_id`, `content`, `monitor_id`, `view_id`) "
               f"VALUES ({rrset_id}, 'target-b.example', 1, 1)")  # same view
    assert 'ERROR 1644' in err


# moving a record between rrsets revalidates the SOA / CNAME single-record limits (records_before_update)

def test_moving_record_into_occupied_soa_rrset_rejected(scratch: int) -> None:
    # records_before_update SOA branch: relocating a record into an SOA rrset that already holds one
    soa_id = _new_rrset(scratch, '@', 6, 86400)
    _new_record(soa_id, 'ns1.example. hostmaster.example. 1 1 1 1 1')
    a_id = _new_rrset(scratch, 'host', 1)
    record_id = _new_record(a_id, '192.0.2.1')
    err = _err(f"UPDATE `records` SET `rrset_id` = {soa_id} WHERE `id` = {record_id}")
    assert 'ERROR 1644' in err
    assert 'SOA rrset allows exactly one record' in err


def test_moving_record_into_occupied_cname_view_rejected(scratch: int) -> None:
    # records_before_update CNAME branch: relocating a record into a CNAME rrset/view that already holds one
    cname_id = _new_rrset(scratch, 'alias', 5)
    _new_record(cname_id, 'target.example')
    a_id = _new_rrset(scratch, 'host', 1)
    record_id = _new_record(a_id, '192.0.2.1')  # same view (1) as the existing CNAME record
    err = _err(f"UPDATE `records` SET `rrset_id` = {cname_id} WHERE `id` = {record_id}")
    assert 'ERROR 1644' in err
    assert 'CNAME rrset allows one record per view' in err


# duplicate answer per view

def test_duplicate_answer_per_view_rejected(scratch: int) -> None:
    rrset_id = _new_rrset(scratch, 'dup', 1)
    _ok(f"INSERT INTO `records` (`rrset_id`, `content`, `monitor_id`, `view_id`) "
        f"VALUES ({rrset_id}, '192.0.2.1', 1, 1)")
    err = _err(f"INSERT INTO `records` (`rrset_id`, `content`, `monitor_id`, `view_id`) "
               f"VALUES ({rrset_id}, '192.0.2.1', 1, 1)")  # same (rrset, view, content)
    assert 'ERROR 1062' in err


# invalid monitor_json

def test_invalid_monitor_json_rejected() -> None:
    err = _err("INSERT INTO `monitors` (`monitor`, `monitor_json`) "
               "VALUES ('Constraint Bad JSON', '{not valid json')")
    assert 'ERROR 4025' in err


# GC: deleting the last record removes the rrset

def test_deleting_last_record_garbage_collects_rrset(scratch: int) -> None:
    rrset_id = _new_rrset(scratch, 'gc', 1)
    _ok(f"INSERT INTO `records` (`rrset_id`, `content`, `monitor_id`, `view_id`) "
        f"VALUES ({rrset_id}, '192.0.2.9', 1, 1)")
    record_id = int(_ok(f"SELECT `id` FROM `records` WHERE `rrset_id` = {rrset_id}"))

    _ok(f"DELETE FROM `records` WHERE `id` = {record_id}")

    assert _ok(f"SELECT COUNT(*) FROM `rrsets` WHERE `id` = {rrset_id}") == '0'


# GC: moving the last record out of a rrset removes the emptied rrset

def test_moving_last_record_garbage_collects_source_rrset(scratch: int) -> None:
    # records_after_update GC: relocating the only record off a rrset drops the now-empty source
    src_id = _new_rrset(scratch, 'src', 1)
    dst_id = _new_rrset(scratch, 'dst', 1)
    record_id = _new_record(src_id, '192.0.2.9')

    _ok(f"UPDATE `records` SET `rrset_id` = {dst_id} WHERE `id` = {record_id}")

    assert _ok(f"SELECT COUNT(*) FROM `rrsets` WHERE `id` = {src_id}") == '0'
    assert _ok(f"SELECT COUNT(*) FROM `records` WHERE `rrset_id` = {dst_id}") == '1'


# hard rule for the write path: a records DELETE/UPDATE must not reference `rrsets` (the AFTER GC trigger fires within
# the statement and would touch `rrsets`, raising MySQL error 1442)

def test_records_delete_referencing_rrsets_rejected(scratch: int) -> None:
    # the records_after_delete GC trigger touches `rrsets`; a DELETE that also reads `rrsets` in a subquery collides
    rrset_id = _new_rrset(scratch, 'hard', 1)
    _new_record(rrset_id, '192.0.2.1')
    err = _err(f"DELETE FROM `records` WHERE `rrset_id` IN "
               f"(SELECT `id` FROM `rrsets` WHERE `id` = {rrset_id})")
    assert 'ERROR 1442' in err
    # the record (and its rrset) survive the failed statement
    assert _ok(f"SELECT COUNT(*) FROM `records` WHERE `rrset_id` = {rrset_id}") == '1'


def test_records_update_reassignment_referencing_rrsets_rejected(scratch: int) -> None:
    # the records_after_update GC trigger touches `rrsets` when a reassignment empties the source rrset; an UPDATE that
    # resolves the new `rrset_id` from a `rrsets` subquery collides with it
    src_id = _new_rrset(scratch, 'src', 1)
    _new_rrset(scratch, 'dst', 1)
    record_id = _new_record(src_id, '192.0.2.9')
    err = _err(f"UPDATE `records` SET `rrset_id` = "
               f"(SELECT `id` FROM `rrsets` WHERE `domain_id` = {scratch} AND `name` = 'dst' AND `type_value` = 1) "
               f"WHERE `id` = {record_id}")
    assert 'ERROR 1442' in err
    # the record stays put on its original rrset
    assert _ok(f"SELECT `rrset_id` FROM `records` WHERE `id` = {record_id}") == str(src_id)


# case-insensitive lookup

def test_uppercase_qname_matches_lowercase_seed(dns: DNSClient) -> None:
    # the utf8mb4 default collation is case-insensitive, so an uppercase qname resolves the lowercase-seeded zone
    result = dns.lookup('EXAMPLE.COM', 'SOA')
    assert len(result) == 1
    assert result[0]['qtype'] == 'SOA'
    assert result[0]['content'].startswith('ns1.example.com.')
