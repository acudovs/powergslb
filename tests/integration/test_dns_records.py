# pylint: disable=missing-function-docstring

"""DNS record behaviour tests.

Each test creates records via the admin API and exercises the DNS backend. Owner names are stored relative to the
zone (the label left of the domain), so each test saves the relative `name` and looks the answer up by its FQDN.
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


# sticky-hash routing: a client network maps deterministically to one record

def test_sticky_hash_selection(
        w2ui: W2UIClient, dns: DNSClient, base_record: dict[str, Any], cleanup: list[tuple[str, int]]) -> None:
    """The sticky-hash policy ties a client network to one record via rendezvous hashing.

    With the default ipv4_mask=24, clients sharing a /24 collapse to the same single record on every request;
    across many /24s the hashing spreads clients over both records, so both are reachable.
    """
    name = 'sticky-test'
    fqdn = f'{name}.example.com'
    content_a, content_b = '192.0.2.93', '192.0.2.94'

    for content in (content_a, content_b):
        r = w2ui.save('records', name=name, content=content, **{**base_record, 'policy': 'Sticky hash'})
        assert r.json()['status'] == 'success'
        recid = w2ui.find_recid('records', name=name, content=content)
        assert recid is not None
        cleanup.append(('records', recid))

    def lookup_with_ip(ip: str) -> str:
        result = dns.lookup(fqdn, 'A', real_remote=f'{ip}/32')
        assert len(result) == 1
        return result[0]['content']

    # same /24 -> always the same single record
    ip1_result = lookup_with_ip('1.0.0.1')
    assert lookup_with_ip('1.0.0.2') == ip1_result
    assert lookup_with_ip('1.0.0.3') == ip1_result
    assert ip1_result in (content_a, content_b)

    # across many distinct /24s both records are reachable (the hash spreads networks)
    seen = {lookup_with_ip(f'{octet}.0.0.1') for octet in range(1, 60)}
    assert seen == {content_a, content_b}


# weight-tier backup: the documented replacement for the old fallback flag

def test_lower_weight_tier_is_backup_only(
        w2ui: W2UIClient, dns: DNSClient, base_record: dict[str, Any], cleanup: list[tuple[str, int]]) -> None:
    """A lower-weight record under round-robin serves only once the higher-weight tier is gone.

    This reproduces backup-only behavior without a fallback flag: the primary (higher weight) tier wins while any
    of it is live, and the backup (lower weight) tier takes over once the primary is excluded.
    """
    name = 'backup-test'
    fqdn = f'{name}.example.com'
    content_primary, content_backup = '192.0.2.95', '192.0.2.96'

    r = w2ui.save('records', name=name, content=content_primary, **{**base_record, 'weight': 20})
    assert r.json()['status'] == 'success'
    recid_primary = w2ui.find_recid('records', name=name, content=content_primary)
    assert recid_primary is not None
    cleanup.append(('records', recid_primary))

    r = w2ui.save('records', name=name, content=content_backup, **{**base_record, 'weight': 10})
    assert r.json()['status'] == 'success'
    recid_backup = w2ui.find_recid('records', name=name, content=content_backup)
    assert recid_backup is not None
    cleanup.append(('records', recid_backup))

    # while the primary tier is up, only the primary is served (the backup stays out of the answer)
    result = dns.lookup(fqdn, 'A')
    assert len(result) == 1
    assert result[0]['content'] == content_primary

    # disable the primary -> the backup tier becomes the highest live tier and is served
    r = w2ui.save('records', recid=recid_primary, name=name, content=content_primary,
                  **{**base_record, 'weight': 20, 'disabled': 1})
    assert r.json()['status'] == 'success'

    result = dns.lookup(fqdn, 'A')
    assert len(result) == 1
    assert result[0]['content'] == content_backup


# IPv6 view matching

def test_ipv6_view_matching(
        w2ui: W2UIClient, dns: DNSClient, base_record: dict[str, Any], cleanup: list[tuple[str, int]]) -> None:
    """A view with an IPv6 CIDR rule matches an IPv6 client and excludes an IPv4 one.

    The rule is 2001:db8::/32 and the client IP is supplied via X-Remotebackend-Real-Remote. Exercises the compiled
    ViewRule CIDR-membership test on the IPv6 path end to end.
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


# geo (country / continent) view matching

def test_geo_view_matching(
        w2ui: W2UIClient, dns: DNSClient, base_record: dict[str, Any], cleanup: list[tuple[str, int]]) -> None:
    """A view with a country/continent rule matches a client by geolocation, resolved from the bundled MMDB.

    The image ships the DB-IP IP-to-Country Lite database, so geo tokens are live. Rule `country:US continent:NA`
    matches a stable US client (8.8.8.8) and excludes a stable European one (193.0.6.139, RIPE NCC, NL).
    """
    name = 'geo-view-test'
    fqdn = f'{name}.example.com'
    content = '192.0.2.120'

    r = w2ui.save('views', view='Geo US', rule='country:US continent:NA')
    assert r.json()['status'] == 'success', r.json()
    view_recid = w2ui.find_recid('views', view='Geo US')
    assert view_recid is not None
    cleanup.append(('views', view_recid))

    r = w2ui.save('records', name=name, content=content, **{**base_record, 'view': 'Geo US'})
    assert r.json()['status'] == 'success'
    record_recid = w2ui.find_recid('records', name=name, content=content)
    assert record_recid is not None
    cleanup.append(('records', record_recid))

    # US client -> matches country:US / continent:NA -> record returned
    result = dns.lookup(fqdn, 'A', real_remote='8.8.8.8/32')
    assert len(result) == 1
    assert result[0]['content'] == content

    # European client -> not US and not NA -> empty
    assert dns.lookup(fqdn, 'A', real_remote='193.0.6.139/32') == []


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


# sticky-hash: IPv6 client

def test_sticky_hash_ipv6_client(
        w2ui: W2UIClient, dns: DNSClient, base_record: dict[str, Any], cleanup: list[tuple[str, int]]) -> None:
    """The sticky-hash policy works for IPv6 clients, masking to the default ipv6_mask=64.

    Clients sharing a /64 collapse to one record on every request; across many /64s the hashing spreads clients
    over both records, so both are reachable.
    """
    name = 'sticky6-test'
    fqdn = f'{name}.example.com'
    content_a, content_b = '2001:db8:a::1', '2001:db8:a::2'

    for content in (content_a, content_b):
        r = w2ui.save('records', name=name, content=content,
                      **{**base_record, 'name_type': 'AAAA', 'policy': 'Sticky hash'})
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
    assert same_64 in (content_a, content_b)

    # across many distinct /64s both records are reachable
    seen = {lookup_with_ip(f'2001:db8:a:{block:x}::1') for block in range(1, 60)}
    assert seen == {content_a, content_b}


# sticky-hash: a zero mask collapses every client to a single record

def test_sticky_hash_zero_mask_collapses_to_one(
        w2ui: W2UIClient, dns: DNSClient, base_record: dict[str, Any], cleanup: list[tuple[str, int]]) -> None:
    """A sticky-hash policy with ipv4_mask=0 masks every client to one network, so all clients share one record.

    This also exercises routing-policy CRUD: the custom-parameter policy is created via the admin API first.
    """
    policy_name = 'Sticky single'
    r = w2ui.save('routings', policy=policy_name, policy_json='{"type": "sticky-hash", "ipv4_mask": 0}')
    assert r.json()['status'] == 'success'
    recid_policy = w2ui.find_recid('routings', policy=policy_name)
    assert recid_policy is not None
    cleanup.append(('routings', recid_policy))

    name = 'sticky-wide-test'
    fqdn = f'{name}.example.com'
    content_lo, content_hi = '192.0.2.130', '192.0.2.131'

    for content in (content_lo, content_hi):
        r = w2ui.save('records', name=name, content=content, **{**base_record, 'policy': policy_name})
        assert r.json()['status'] == 'success'
        recid = w2ui.find_recid('records', name=name, content=content)
        assert recid is not None
        cleanup.append(('records', recid))

    # wildly different clients all collapse to the same single record (the deterministic HRW winner of the zero net)
    answers = set()
    for client in ('203.0.113.5', '8.8.8.8', '10.0.0.1', '198.51.100.200'):
        result = dns.lookup(fqdn, 'A', real_remote=f'{client}/32')
        assert len(result) == 1, f'{client}: {result}'
        answers.add(result[0]['content'])
    assert len(answers) == 1
    assert answers <= {content_lo, content_hi}


# weighted-random routing: a single proportional answer biased by weight

def test_weighted_random_single_answer(
        w2ui: W2UIClient, dns: DNSClient, base_record: dict[str, Any], cleanup: list[tuple[str, int]]) -> None:
    """The weighted-random policy returns one record per query, biased by weight.

    With the default max_answers=1 every query answers exactly one valid record (the wiring regression guard).
    The heavily-weighted record (weight=100 vs weight=1) dominates the draw across many queries; asserting only
    that it appears and forms the majority keeps the test non-flaky despite the randomness.
    """
    name = 'weighted-test'
    fqdn = f'{name}.example.com'
    content_light, content_heavy = '192.0.2.140', '192.0.2.141'

    r = w2ui.save('records', name=name, content=content_light,
                  **{**base_record, 'policy': 'Weighted random', 'weight': 1})
    assert r.json()['status'] == 'success'
    recid_light = w2ui.find_recid('records', name=name, content=content_light)
    assert recid_light is not None
    cleanup.append(('records', recid_light))

    r = w2ui.save('records', name=name, content=content_heavy,
                  **{**base_record, 'policy': 'Weighted random', 'weight': 100})
    assert r.json()['status'] == 'success'
    recid_heavy = w2ui.find_recid('records', name=name, content=content_heavy)
    assert recid_heavy is not None
    cleanup.append(('records', recid_heavy))

    answers = []
    for _ in range(100):
        result = dns.lookup(fqdn, 'A')
        assert len(result) == 1, result  # max_answers=1: always a single proportional pick
        answers.append(result[0]['content'])

    assert set(answers) <= {content_light, content_heavy}
    assert answers.count(content_heavy) > len(answers) // 2  # weight=100 vs 1 dominates the draw


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
