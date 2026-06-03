# pylint: disable=missing-function-docstring

"""DNS record behaviour tests.

Each test creates records via the admin API and exercises the DNS backend. Owner names are stored relative to the
zone (the label left of the domain), so each test saves the relative ``name`` and looks the answer up by its FQDN.
Created rows are registered with the cleanup fixture so the container database is left clean regardless of outcome.
"""

from typing import Any

from .conftest import DNSClient, W2UIClient


# records lifecycle

def test_record_create_dns_verify_delete(
        w2ui: W2UIClient, dns: DNSClient, base_record: dict[str, Any], cleanup: list[tuple[str, int]]) -> None:
    name = 'crud-dns-test'
    fqdn = f'{name}.example.com'
    content = '192.0.2.88'
    r = w2ui.save('records', name=name, content=content, **base_record)
    assert r.json()['status'] == 'success'

    recid = w2ui.find_recid('records', name=name, content=content)
    assert recid is not None
    cleanup.append(('records', recid))

    result = dns.lookup(fqdn, 'A')
    assert len(result) == 1
    assert result[0]['qname'] == fqdn
    assert result[0]['qtype'] == 'A'
    assert result[0]['content'] == content
    assert result[0]['ttl'] == 300

    w2ui.delete('records', recid)
    cleanup.clear()
    assert dns.lookup(fqdn, 'A') == []


# disabled record

def test_disabled_record_excluded_from_dns(
        w2ui: W2UIClient, dns: DNSClient, base_record: dict[str, Any], cleanup: list[tuple[str, int]]) -> None:
    """A disabled record is filtered out of both a direct qtype lookup and an ANY lookup.

    gslb_records applies disabled = 0 in both query branches.
    """
    name = 'disabled-test'
    fqdn = f'{name}.example.com'
    content = '192.0.2.89'
    r = w2ui.save('records', name=name, content=content, **base_record)
    assert r.json()['status'] == 'success'
    recid = w2ui.find_recid('records', name=name, content=content)
    assert recid is not None
    cleanup.append(('records', recid))

    assert len(dns.lookup(fqdn, 'A')) == 1
    # the enabled record is also visible under an ANY lookup
    assert any(r['content'] == content for r in dns.lookup(fqdn, 'ANY'))

    r = w2ui.save('records', recid=recid, name=name, content=content,
                  **{**base_record, 'disabled': 1})
    assert r.json()['status'] == 'success'

    assert dns.lookup(fqdn, 'A') == []
    # disabled record is excluded from ANY as well
    assert dns.lookup(fqdn, 'ANY') == []


# view-based filtering

def test_view_based_filtering(
        w2ui: W2UIClient, dns: DNSClient, base_record: dict[str, Any], cleanup: list[tuple[str, int]]) -> None:
    """A record is returned only to a client IP that matches its view rule.

    Private view rule: 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16. Public IP 8.8.8.8 -> no match -> empty result.
    Private IP 10.0.0.1 -> match -> record returned.
    """
    name = 'view-test'
    fqdn = f'{name}.example.com'
    content = '192.0.2.90'
    r = w2ui.save('records', name=name, content=content,
                  **{**base_record, 'view': 'Private'})
    assert r.json()['status'] == 'success'
    recid = w2ui.find_recid('records', name=name, content=content)
    assert recid is not None
    cleanup.append(('records', recid))

    # public IP → not in private ranges → no record
    assert dns.lookup(fqdn, 'A', real_remote='8.8.8.8/32') == []

    # private IP → matches Private view → record returned
    result = dns.lookup(fqdn, 'A', real_remote='10.0.0.1/32')
    assert len(result) == 1
    assert result[0]['content'] == content


# weight-based selection

def test_weight_based_selection(
        w2ui: W2UIClient, dns: DNSClient, base_record: dict[str, Any], cleanup: list[tuple[str, int]]) -> None:
    """The higher-weight group always wins record selection.

    Two records for the same name with weight=5 and weight=10; only the weight=10 record is returned.
    """
    name = 'weight-test'
    fqdn = f'{name}.example.com'
    content_lo, content_hi = '192.0.2.91', '192.0.2.92'

    r = w2ui.save('records', name=name, content=content_lo, **{**base_record, 'weight': 5})
    assert r.json()['status'] == 'success'
    recid_lo = w2ui.find_recid('records', name=name, content=content_lo)
    assert recid_lo is not None
    cleanup.append(('records', recid_lo))

    r = w2ui.save('records', name=name, content=content_hi, **{**base_record, 'weight': 10})
    assert r.json()['status'] == 'success'
    recid_hi = w2ui.find_recid('records', name=name, content=content_hi)
    assert recid_hi is not None
    cleanup.append(('records', recid_hi))

    result = dns.lookup(fqdn, 'A')
    assert len(result) == 1
    assert result[0]['content'] == content_hi


# persistence-based selection

def test_persistence_based_selection(
        w2ui: W2UIClient, dns: DNSClient, base_record: dict[str, Any], cleanup: list[tuple[str, int]]) -> None:
    """Persistence ties a client subnet deterministically to one record.

    Records with persistence=24: IP >> 24 (top octet) determines which record is returned. Same top octet -> same
    record on every request. Different top octet -> different record.
    """
    name = 'persist-test'
    fqdn = f'{name}.example.com'
    content_a, content_b = '192.0.2.93', '192.0.2.94'

    r = w2ui.save('records', name=name, content=content_a, **{**base_record, 'persistence': 24})
    assert r.json()['status'] == 'success'
    recid_a = w2ui.find_recid('records', name=name, content=content_a)
    assert recid_a is not None
    cleanup.append(('records', recid_a))

    r = w2ui.save('records', name=name, content=content_b, **{**base_record, 'persistence': 24})
    assert r.json()['status'] == 'success'
    recid_b = w2ui.find_recid('records', name=name, content=content_b)
    assert recid_b is not None
    cleanup.append(('records', recid_b))

    # same top octet → always the same single record
    def lookup_with_ip(ip: str) -> str:
        result = dns.lookup(fqdn, 'A', real_remote=f'{ip}/32')
        assert len(result) == 1
        return result[0]['content']

    ip1_result = lookup_with_ip('1.0.0.1')
    assert lookup_with_ip('1.0.0.2') == ip1_result
    assert lookup_with_ip('1.0.0.3') == ip1_result

    # different top octet → potentially different record
    ip2_result = lookup_with_ip('2.0.0.1')
    assert ip2_result in (content_a, content_b)
    assert ip1_result != ip2_result


# fallback records

def test_fallback_record_returned_when_normal_all_down(
        w2ui: W2UIClient, dns: DNSClient, base_record: dict[str, Any], cleanup: list[tuple[str, int]]) -> None:
    """When the only normal record is disabled, the fallback record is returned instead.

    Disabled records are excluded by the DNS layer (disabled != fallback). The health-check-driven fallback path is
    covered in test_monitor_health.test_real_health_fallback.
    """
    name = 'fallback-test'
    fqdn = f'{name}.example.com'
    content_normal, content_fallback = '192.0.2.95', '192.0.2.96'

    r = w2ui.save('records', name=name, content=content_normal, **{**base_record, 'fallback': 0})
    assert r.json()['status'] == 'success'
    recid_normal = w2ui.find_recid('records', name=name, content=content_normal)
    assert recid_normal is not None
    cleanup.append(('records', recid_normal))

    r = w2ui.save('records', name=name, content=content_fallback, **{**base_record, 'fallback': 1})
    assert r.json()['status'] == 'success'
    recid_fallback = w2ui.find_recid('records', name=name, content=content_fallback)
    assert recid_fallback is not None
    cleanup.append(('records', recid_fallback))

    # both records visible in DNS when normal record is up
    contents = {r['content'] for r in dns.lookup(fqdn, 'A')}
    assert content_normal in contents
    # fallback is also returned when normal records exist (both live)
    assert content_fallback in contents

    # disable the normal record → only the fallback record remains
    r = w2ui.save('records', recid=recid_normal, name=name, content=content_normal,
                  **{**base_record, 'disabled': 1})
    assert r.json()['status'] == 'success'

    result = dns.lookup(fqdn, 'A')
    assert len(result) == 1
    assert result[0]['content'] == content_fallback


# IPv6 view matching

def test_ipv6_view_matching(
        w2ui: W2UIClient, dns: DNSClient, base_record: dict[str, Any], cleanup: list[tuple[str, int]]) -> None:
    """A view with an IPv6 CIDR rule matches an IPv6 client and excludes an IPv4 one.

    The rule is 2001:db8::/32 and the client IP is supplied via X-Remotebackend-Real-Remote. Exercises
    netaddr.smallest_matching_cidr on the IPv6 path end to end.
    """
    name = 'ipv6-view-test'
    fqdn = f'{name}.example.com'
    content = '2001:db8::abcd'

    r = w2ui.save('views', view='IPv6 View', rule='2001:db8::/32')
    assert r.json()['status'] == 'success'
    view_recid = w2ui.find_recid('views', view='IPv6 View')
    assert view_recid is not None
    cleanup.append(('views', view_recid))

    r = w2ui.save('records', name=name, content=content,
                  **{**base_record, 'name_type': 'AAAA', 'view': 'IPv6 View'})
    assert r.json()['status'] == 'success'
    record_recid = w2ui.find_recid('records', name=name, content=content)
    assert record_recid is not None
    cleanup.append(('records', record_recid))

    # IPv6 client inside 2001:db8::/32 -> record returned
    result = dns.lookup(fqdn, 'AAAA', real_remote='2001:db8::1/128')
    assert len(result) == 1
    assert result[0]['content'] == content

    # IPv4 client -> not in IPv6 view -> empty
    assert dns.lookup(fqdn, 'AAAA', real_remote='8.8.8.8/32') == []


# MX record without priority number

def test_mx_record_without_priority_number(
        w2ui: W2UIClient, dns: DNSClient, base_record: dict[str, Any], cleanup: list[tuple[str, int]]) -> None:
    """MX content is returned verbatim.

    A record whose content has no leading priority number does not crash the server - it is returned as-is.
    """
    name = 'mx-nonum-test'
    fqdn = f'{name}.example.com'
    content = 'mail-nonum.example.com'

    r = w2ui.save('records', name=name, content=content,
                  **{**base_record, 'name_type': 'MX', 'ttl': 3600})
    assert r.json()['status'] == 'success'
    recid = w2ui.find_recid('records', name=name, content=content)
    assert recid is not None
    cleanup.append(('records', recid))

    result = dns.lookup(fqdn, 'MX')
    assert len(result) == 1
    assert result[0]['qtype'] == 'MX'
    assert result[0]['content'] == content


# persistence: IPv6 client

def test_persistence_ipv6_client(
        w2ui: W2UIClient, dns: DNSClient, base_record: dict[str, Any], cleanup: list[tuple[str, int]]) -> None:
    """Persistence works for IPv6 clients, not just IPv4.

    persistence shifts the whole client-IP integer right by N bits. With persistence=64, clients sharing the top 64
    bits collapse to one record; a client in a different /64 lands deterministically on the other record (the two
    chosen prefixes hash to different buckets, mirroring the IPv4 test).
    """
    name = 'persist6-test'
    fqdn = f'{name}.example.com'
    content_a, content_b = '2001:db8:a::1', '2001:db8:a::2'

    for content in (content_a, content_b):
        r = w2ui.save('records', name=name, content=content,
                      **{**base_record, 'name_type': 'AAAA', 'persistence': 64})
        assert r.json()['status'] == 'success'
        recid = w2ui.find_recid('records', name=name, content=content)
        assert recid is not None
        cleanup.append(('records', recid))

    def lookup_with_ip(ip: str) -> str:
        result = dns.lookup(fqdn, 'AAAA', real_remote=f'{ip}/128')
        assert len(result) == 1, f'{ip}: {result}'
        return result[0]['content']

    # same /64 -> same record, on every request
    same_64 = lookup_with_ip('2001:db8:a::100')
    assert lookup_with_ip('2001:db8:a::200') == same_64
    assert lookup_with_ip('2001:db8:a::ffff') == same_64

    # different /64 -> the other record (deterministic)
    other_64 = lookup_with_ip('2001:db8:a:1::1')
    assert other_64 in (content_a, content_b)
    assert other_64 != same_64


# persistence: shift at or beyond the address width collapses to a single record

def test_persistence_at_address_width_collapses_to_one(
        w2ui: W2UIClient, dns: DNSClient, base_record: dict[str, Any], cleanup: list[tuple[str, int]]) -> None:
    """A persistence shift at or beyond the address width collapses every client to one record.

    persistence=32 on an IPv4 client shifts the 32-bit address to 0, so every client maps to index 0 -> the
    lexicographically smallest content. This verifies that maximum stickiness holds and does not raise for any
    client.
    """
    name = 'persist-wide-test'
    fqdn = f'{name}.example.com'
    content_lo, content_hi = '192.0.2.130', '192.0.2.131'

    for content in (content_lo, content_hi):
        r = w2ui.save('records', name=name, content=content,
                      **{**base_record, 'persistence': 32})
        assert r.json()['status'] == 'success'
        recid = w2ui.find_recid('records', name=name, content=content)
        assert recid is not None
        cleanup.append(('records', recid))

    # wildly different clients all collapse to the same single record (content_lo sorts first)
    for client in ('203.0.113.5', '8.8.8.8', '10.0.0.1', '198.51.100.200'):
        result = dns.lookup(fqdn, 'A', real_remote=f'{client}/32')
        assert len(result) == 1, f'{client}: {result}'
        assert result[0]['content'] == content_lo, f'{client}: expected {content_lo}, got {result[0]["content"]}'


# special-character content served verbatim by the DNS backend

def test_txt_special_content_served_by_dns(
        w2ui: W2UIClient, dns: DNSClient, base_record: dict[str, Any], cleanup: list[tuple[str, int]]) -> None:
    """A realistic SPF-style TXT value saved through the admin API is returned byte-for-byte by the DNS backend.

    The value has spaces, '+', '=' and ':', exercising the admin decode path and the JSON DNS response together.
    """
    name = 'spf-test'
    fqdn = f'{name}.example.com'
    content = 'v=spf1 include:_spf.example.com +all'

    r = w2ui.save('records', name=name, content=content,
                  **{**base_record, 'name_type': 'TXT', 'ttl': 3600})
    assert r.json()['status'] == 'success', r.json()
    recid = w2ui.find_recid('records', name=name, content=content)
    assert recid is not None, 'TXT content did not round-trip through the admin save'
    cleanup.append(('records', recid))

    result = dns.lookup(fqdn, 'TXT')
    assert len(result) == 1
    assert result[0]['qtype'] == 'TXT'
    assert result[0]['content'] == content
