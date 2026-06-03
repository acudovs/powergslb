# pylint: disable=missing-function-docstring

"""PowerDNS remotebackend HTTP interface tests.

Queries the DNS backend directly over HTTP at /dns/lookup/<qname.>/<qtype> (the path-based protocol PowerDNS speaks to a
remote backend). Covers every seeded record type, the ANY lookup, deterministic result ordering, the routing corners
that return result false vs an empty list vs 404, the X-Remotebackend-Real-Remote client-IP header (valid, invalid,
IPv6), and the getAllDomains zone-cache method (including its includeDisabled flag, which a disabled apex SOA toggles).
The record lookups go through the dns fixture; the routing corners that need the raw response (status codes, result
false, non-lookup paths) use requests directly. test_powerdns.py covers the full dig path.
"""

from typing import Any

import requests

from .conftest import DNSClient, W2UIClient

_SEED_ZONES = {'example.com.', 'example.net.', 'example.org.'}
_SEED_SERIAL = 2016010101


# record type lookups

def test_lookup_soa(dns: DNSClient) -> None:
    result = dns.lookup('example.com', 'SOA')
    assert len(result) == 1
    r = result[0]
    assert r['qtype'] == 'SOA'
    assert r['qname'] == 'example.com'
    assert r['ttl'] == 86400
    assert set(r.keys()) == {'qname', 'qtype', 'content', 'ttl'}
    fields = r['content'].split()
    assert len(fields) == 7
    assert fields[0] == 'ns1.example.com.'
    assert fields[1] == 'hostmaster.example.com.'
    assert int(fields[2]) == 2016010101


def test_lookup_ns(dns: DNSClient) -> None:
    result = dns.lookup('example.com', 'NS')
    assert len(result) == 4
    assert all(r['qtype'] == 'NS' for r in result)
    assert all(r['qname'] == 'example.com' for r in result)
    assert all(r['ttl'] == 3600 for r in result)
    assert {r['content'] for r in result} == {
        'ns1.example.com', 'ns2.example.com', 'ns3.example.com', 'ns4.example.com'}


def test_lookup_a(dns: DNSClient) -> None:
    result = dns.lookup('example.com', 'A')
    assert len(result) == 4
    assert all(r['qtype'] == 'A' for r in result)
    assert all(r['qname'] == 'example.com' for r in result)
    assert all(r['ttl'] == 300 for r in result)
    assert all(set(r.keys()) == {'qname', 'qtype', 'content', 'ttl'} for r in result)
    assert all(r['content'].startswith('192.0.2.') for r in result)


def test_lookup_aaaa(dns: DNSClient) -> None:
    result = dns.lookup('example.com', 'AAAA')
    assert len(result) == 4
    assert all(r['qtype'] == 'AAAA' for r in result)
    assert all(r['qname'] == 'example.com' for r in result)
    assert all(r['ttl'] == 300 for r in result)
    assert all(r['content'].startswith('2001:db8::') for r in result)


def test_lookup_mx(dns: DNSClient) -> None:
    result = dns.lookup('example.com', 'MX')
    assert len(result) == 3
    assert all(r['qtype'] == 'MX' for r in result)
    assert all(r['qname'] == 'example.com' for r in result)
    assert all(r['ttl'] == 3600 for r in result)
    assert all(r['content'].split()[0].isdigit() for r in result)
    assert {r['content'] for r in result} == {
        '10 mail1.example.com', '20 mail2.example.com', '30 mail3.example.com'}


def test_lookup_txt(dns: DNSClient) -> None:
    result = dns.lookup('example.com', 'TXT')
    assert len(result) == 1
    r = result[0]
    assert r['qtype'] == 'TXT'
    assert r['qname'] == 'example.com'
    assert r['ttl'] == 3600
    assert r['content'] == 'v=spf1 ip4:192.0.2.0/24 2001:db8::/32 ~all'


def test_lookup_cname(dns: DNSClient) -> None:
    result = dns.lookup('www.example.com', 'CNAME')
    assert len(result) == 1
    r = result[0]
    assert r['qtype'] == 'CNAME'
    assert r['qname'] == 'www.example.com'
    assert r['ttl'] == 3600
    assert r['content'] == 'example.com'


def test_lookup_srv(dns: DNSClient) -> None:
    result = dns.lookup('_sip._tcp.example.com', 'SRV')
    assert len(result) == 1
    r = result[0]
    assert r['qtype'] == 'SRV'
    assert r['qname'] == '_sip._tcp.example.com'
    assert r['ttl'] == 3600
    assert r['content'] == '10 100 5060 sip.example.com'


def test_lookup_any_returns_multiple_qtypes(dns: DNSClient) -> None:
    result = dns.lookup('example.com', 'ANY')
    qtypes = {r['qtype'] for r in result}
    assert {'SOA', 'NS', 'A', 'AAAA', 'MX', 'TXT'}.issubset(qtypes)


def test_lookup_order_is_deterministic(dns: DNSClient) -> None:
    # gslb_records uses ORDER BY so a multi-record set comes back in a stable
    # order across queries - the property client IP persistence relies on
    first = dns.lookup('example.com', 'A')
    second = dns.lookup('example.com', 'A')
    assert len(first) == 4
    assert [r['content'] for r in first] == [r['content'] for r in second]
    assert [r['content'] for r in first] == sorted(r['content'] for r in first)


# additional seed-data names

def test_lookup_ns1_a_record(dns: DNSClient) -> None:
    result = dns.lookup('ns1.example.com', 'A')
    assert len(result) == 1
    r = result[0]
    assert r['qtype'] == 'A'
    assert r['qname'] == 'ns1.example.com'
    assert r['ttl'] == 300
    assert r['content'] == '192.0.2.1'


def test_lookup_m_a_records(dns: DNSClient) -> None:
    result = dns.lookup('m.example.com', 'A')
    assert len(result) == 4
    assert all(r['qtype'] == 'A' for r in result)
    assert all(r['qname'] == 'm.example.com' for r in result)
    assert all(r['ttl'] == 300 for r in result)
    assert all(r['content'].startswith('192.0.2.2') for r in result)


def test_lookup_mobile_cname(dns: DNSClient) -> None:
    result = dns.lookup('mobile.example.com', 'CNAME')
    assert len(result) == 1
    r = result[0]
    assert r['qtype'] == 'CNAME'
    assert r['qname'] == 'mobile.example.com'
    assert r['ttl'] == 3600
    assert r['content'] == 'm.example.com'


def test_lookup_sip_a_record(dns: DNSClient) -> None:
    result = dns.lookup('sip.example.com', 'A')
    assert len(result) == 1
    r = result[0]
    assert r['qtype'] == 'A'
    assert r['qname'] == 'sip.example.com'
    assert r['ttl'] == 300
    assert r['content'] == '192.0.2.40'


def test_lookup_soa_all_three_domains(dns: DNSClient) -> None:
    expected = {
        'example.com': 'ns1.example.com.',
        'example.net': 'ns1.example.net.',
        'example.org': 'ns1.example.org.',
    }
    for domain, primary_ns in expected.items():
        result = dns.lookup(domain, 'SOA')
        assert len(result) == 1
        r = result[0]
        assert r['qname'] == domain
        assert r['qtype'] == 'SOA'
        assert r['ttl'] == 86400
        assert r['content'].startswith(primary_ns)


# corner cases

def test_lookup_unknown_name_returns_empty(dns: DNSClient) -> None:
    assert dns.lookup('no.such.name', 'A') == []


def test_lookup_qtype_absent_for_name_returns_empty(dns: DNSClient) -> None:
    # www.example.com has only CNAME, no A record
    assert dns.lookup('www.example.com', 'A') == []


def test_dns_ptr_type_no_records(dns: DNSClient) -> None:
    # PTR exists in types table but no records are configured for it
    assert dns.lookup('example.com', 'PTR') == []


def test_lookup_path_too_short_returns_false(base_url: str) -> None:
    response = requests.get(f'{base_url}/dns/lookup/example.com.', timeout=10)
    assert response.status_code == 200
    assert response.json()['result'] is False


def test_lookup_unknown_dns_subpath_returns_false(base_url: str) -> None:
    response = requests.get(f'{base_url}/dns/getall', timeout=10)
    assert response.status_code == 200
    assert response.json()['result'] is False


def test_unknown_root_path_returns_404(base_url: str) -> None:
    response = requests.get(f'{base_url}/no/such/path', timeout=10)
    assert response.status_code == 404


# X-Remotebackend-Real-Remote header

def test_real_remote_header_valid_cidr(dns: DNSClient) -> None:
    # Public view (0.0.0.0/0) matches any IP; records must still be returned
    assert len(dns.lookup('example.com', 'A', real_remote='10.0.0.1/32')) > 0


def test_real_remote_header_invalid_falls_back(dns: DNSClient) -> None:
    # Invalid header: server logs error and falls back to actual client IP
    assert len(dns.lookup('example.com', 'A', real_remote='not-an-ip')) > 0


def test_real_remote_header_ipv6_matches_public(dns: DNSClient) -> None:
    """An IPv6 X-Remotebackend-Real-Remote header matches the seed Public view, so records are returned.

    The header is parsed by netaddr and matches the Public view (0.0.0.0/0 ::/0). IPv6 view matching is covered end
    to end in test_dns_records.test_ipv6_view_matching.
    """
    assert len(dns.lookup('example.com', 'A', real_remote='2001:db8::1/128')) > 0


# additional routing corners

def test_dns_wrong_subdir_returns_false(base_url: str) -> None:
    # /dns/other/... - dirs[1] != 'lookup' with correct count
    response = requests.get(f'{base_url}/dns/other/example.com./A', timeout=10)
    assert response.status_code == 200
    assert response.json()['result'] is False


def test_dns_too_many_parts_returns_false(base_url: str) -> None:
    # Five path segments - len(dirs) != 4
    response = requests.get(f'{base_url}/dns/lookup/example.com./A/extra', timeout=10)
    assert response.status_code == 200
    assert response.json()['result'] is False


def test_get_root_returns_404(base_url: str) -> None:
    response = requests.get(f'{base_url}/', timeout=10)
    assert response.status_code == 404


def test_post_to_dns_returns_404(base_url: str) -> None:
    # DNS endpoint only accepts GET
    response = requests.post(f'{base_url}/dns/lookup/example.com./A', timeout=10)
    assert response.status_code == 404


def test_lookup_without_trailing_dot(base_url: str) -> None:
    # rstrip('.') is applied unconditionally; no trailing dot behaves identically
    r_dot = requests.get(f'{base_url}/dns/lookup/example.com./A', timeout=10)
    r_no_dot = requests.get(f'{base_url}/dns/lookup/example.com/A', timeout=10)
    assert r_no_dot.status_code == 200
    assert r_no_dot.json()['result'] == r_dot.json()['result']


# getAllDomains zone-cache method

def _by_zone(dns: DNSClient) -> dict[str, dict[str, Any]]:
    return {d['zone']: d for d in dns.all_domains()}


def test_get_all_domains_entry_shape(dns: DNSClient) -> None:
    entry = _by_zone(dns)['example.com.']
    assert set(entry.keys()) == {
        'id', 'zone', 'kind', 'serial', 'notified_serial', 'last_check', 'masters'}
    assert entry['kind'] == 'native'
    assert entry['masters'] == []
    assert isinstance(entry['id'], int)
    assert isinstance(entry['last_check'], int)


def test_get_all_domains_serial_from_apex_soa(dns: DNSClient) -> None:
    by_zone = _by_zone(dns)
    for zone in _SEED_ZONES:
        assert by_zone[zone]['serial'] == _SEED_SERIAL
        assert by_zone[zone]['notified_serial'] == _SEED_SERIAL


def test_disabled_apex_soa_toggles_zone_visibility(
        w2ui: W2UIClient, dns: DNSClient, base_record: dict[str, Any], cleanup: list[tuple[str, int]]) -> None:
    """Disabling a domain's apex SOA record is how PowerGSLB expresses a disabled zone.

    The zone drops out of the default getAllDomains result but stays listed when includeDisabled=true.
    """
    zone = 'disabled-soa-test.example'
    serial = 2024093002
    soa = f'ns1.{zone}. hostmaster.{zone}. {serial} 21600 3600 1209600 300'
    soa_fields = {**base_record, 'domain': zone, 'name_type': 'SOA', 'ttl': 86400}

    r = w2ui.save('domains', domain=zone)
    assert r.json()['status'] == 'success'
    domain_recid = w2ui.find_recid('domains', domain=zone)
    assert domain_recid is not None
    cleanup.append(('domains', domain_recid))

    r = w2ui.save('records', name='@', content=soa, **soa_fields)
    assert r.json()['status'] == 'success'
    recid = w2ui.find_recid('records', name='@', content=soa)
    assert recid is not None
    cleanup.append(('records', recid))

    # enabled apex SOA: listed under both flag values
    assert zone + '.' in _by_zone(dns)

    # disable the apex SOA record
    r = w2ui.save('records', recid=recid, name='@', content=soa, **{**soa_fields, 'disabled': 1})
    assert r.json()['status'] == 'success'

    assert zone + '.' not in {d['zone'] for d in dns.all_domains(include_disabled='false')}
    assert zone + '.' in {d['zone'] for d in dns.all_domains(include_disabled='true')}


def test_get_all_domains_post_returns_404(base_url: str) -> None:
    # the DNS interface only accepts GET
    response = requests.post(f'{base_url}/dns/getAllDomains', timeout=10)
    assert response.status_code == 404


def test_new_domain_with_apex_soa_is_reflected(
        w2ui: W2UIClient, dns: DNSClient, base_record: dict[str, Any], cleanup: list[tuple[str, int]]) -> None:
    zone = 'zone-cache-test.example'
    serial = 2024093001
    soa = f'ns1.{zone}. hostmaster.{zone}. {serial} 21600 3600 1209600 300'

    r = w2ui.save('domains', domain=zone)
    assert r.json()['status'] == 'success'
    domain_recid = w2ui.find_recid('domains', domain=zone)
    assert domain_recid is not None
    cleanup.append(('domains', domain_recid))

    # apex SOA record (the '@' record name) supplies the serial
    r = w2ui.save('records', name='@', content=soa,
                  **{**base_record, 'domain': zone, 'name_type': 'SOA', 'ttl': 86400})
    assert r.json()['status'] == 'success'
    record_recid = w2ui.find_recid('records', name='@', content=soa)
    assert record_recid is not None
    cleanup.append(('records', record_recid))

    entry = _by_zone(dns).get(zone + '.')
    assert entry is not None
    assert entry['kind'] == 'native'
    assert entry['serial'] == serial
    assert entry['notified_serial'] == serial


def test_new_domain_without_apex_soa_is_excluded(
        w2ui: W2UIClient, dns: DNSClient, base_record: dict[str, Any], cleanup: list[tuple[str, int]]) -> None:
    # a domain whose apex has no SOA record is omitted: PowerDNS requires an SOA
    zone = 'no-soa-test.example'

    r = w2ui.save('domains', domain=zone)
    assert r.json()['status'] == 'success'
    domain_recid = w2ui.find_recid('domains', domain=zone)
    assert domain_recid is not None
    cleanup.append(('domains', domain_recid))

    # an A record at the apex, but no SOA
    r = w2ui.save('records', name='@', content='192.0.2.123',
                  **{**base_record, 'domain': zone})
    assert r.json()['status'] == 'success'
    record_recid = w2ui.find_recid('records', name='@', content='192.0.2.123')
    assert record_recid is not None
    cleanup.append(('records', record_recid))

    assert zone + '.' not in _by_zone(dns)


# longest-zone-match: a delegated child zone wins over its parent

def test_longest_zone_match_prefers_child(
        w2ui: W2UIClient, dns: DNSClient, base_record: dict[str, Any], cleanup: list[tuple[str, int]]) -> None:
    """A query for host.sub.example.com resolves in the child zone, not the parent.

    With both example.com and sub.example.com seeded, the NOT EXISTS guard makes the most-specific suffix win. A
    decoy record under the parent at the relative name 'host.sub' must not be returned.
    """
    child = 'sub.example.com'
    parent_content, child_content = '203.0.113.1', '198.51.100.1'

    r = w2ui.save('domains', domain=child)
    assert r.json()['status'] == 'success'
    child_recid = w2ui.find_recid('domains', domain=child)
    assert child_recid is not None
    cleanup.append(('domains', child_recid))

    # decoy under the parent zone: relative name 'host.sub' -> host.sub.example.com
    r = w2ui.save('records', **{**base_record, 'domain': 'example.com', 'name': 'host.sub',
                                'content': parent_content})
    assert r.json()['status'] == 'success'
    parent_rec = w2ui.find_recid('records', domain='example.com', name='host.sub', content=parent_content)
    assert parent_rec is not None
    cleanup.append(('records', parent_rec))

    # the real record in the child zone: relative name 'host' -> host.sub.example.com
    r = w2ui.save('records', **{**base_record, 'domain': child, 'name': 'host', 'content': child_content})
    assert r.json()['status'] == 'success'
    child_rec = w2ui.find_recid('records', domain=child, name='host', content=child_content)
    assert child_rec is not None
    cleanup.append(('records', child_rec))

    result = dns.lookup('host.sub.example.com', 'A')
    assert len(result) == 1, result
    assert result[0]['content'] == child_content
    assert result[0]['qname'] == 'host.sub.example.com'
