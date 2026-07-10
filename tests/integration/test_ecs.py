"""EDNS Client Subnet (ECS) tests.

Two layers exercise the ECS scope contract. The backend layer queries the PowerGSLB DNS backend directly (the dns
fixture, port 8080) and asserts the `scopeMask` each answer row carries, independent of PowerDNS. The end-to-end
layer runs `dig +subnet` against PowerDNS on port 53 and asserts the CLIENT-SUBNET option echoed on the wire, which
also depends on `edns-subnet-processing=yes`.

Scope is 0 when every record in the rrset has a view that matches all clients (a CIDR with prefix 0 in both families,
e.g. `0.0.0.0/0 ::/0`) and the routing policy is client-independent: the answer is the same for everyone, so it stays
globally cacheable. Otherwise, scope is the source prefix length, clamped by any client-network-keyed policy's mask, and
the answer is client-subnet specific. SOA, NS and DS are always scope 0.
"""

import re
import shutil
import subprocess
from typing import Any, NamedTuple

from .conftest import DNSClient, W2UIClient

_DIG = shutil.which('dig')

# The Public view rule. A record selected through it is view-independent and must carry scope 0.
_PUBLIC_VIEW = 'Public'


# --- helpers ---------------------------------------------------------------------------------------------------------


class Ecs(NamedTuple):
    """A parsed `CLIENT-SUBNET: address/source/scope` line from dig's OPT pseudosection."""
    address: str
    source: int
    scope: int


class DigResult(NamedTuple):
    """The fields a dig response we assert on: header status, answer contents, and the echoed ECS option (if any)."""
    status: str
    answers: list[str]
    ecs: Ecs | None


def _dig(dns_addr: str, name: str, qtype: str, *extra: str) -> DigResult:
    """Run dig (full output, no +short) and parse status, answers and the echoed ECS option."""
    proc = subprocess.run(
        [str(_DIG), f'@{dns_addr}', name, qtype, '+time=2', '+tries=1', *extra],
        capture_output=True, text=True, timeout=10, check=True)
    return _parse_dig(proc.stdout, qtype)


def _parse_dig(output: str, qtype: str) -> DigResult:
    """Extract header status, the answer-section contents for qtype, and any CLIENT-SUBNET option from dig output."""
    status_match = re.search(r'status:\s*(\w+)', output)
    status = status_match.group(1) if status_match else ''

    ecs: Ecs | None = None
    ecs_match = re.search(r'CLIENT-SUBNET:\s*(\S+)', output)
    if ecs_match:
        address, source, scope = ecs_match.group(1).rsplit('/', 2)
        ecs = Ecs(address, int(source), int(scope))

    answers: list[str] = []
    for line in output.splitlines():
        if line.startswith(';') or not line.strip():
            continue
        fields = line.split()
        if len(fields) >= 5 and fields[3] == qtype:
            answers.append(fields[-1])

    return DigResult(status, answers, ecs)


def _save_record(w2ui: W2UIClient, base_record: dict[str, Any], cleanup: list[tuple[str, int]],
                 name: str, content: str, **overrides: Any) -> int:
    """Create one record via the admin API, register it for teardown, and return its recid."""
    response = w2ui.save('records', name=name, content=content, **{**base_record, **overrides})
    assert response.json()['status'] == 'success', response.json()
    recid = w2ui.find_recid('records', name=name, content=content)
    assert recid is not None
    cleanup.append(('records', recid))
    return recid


def _scope(result: list[dict[str, Any]]) -> int:
    """Return the scopeMask shared by every answer row, asserting the field is present and consistent."""
    assert result, 'expected at least one answer row to carry a scopeMask'
    scopes = {row.get('scopeMask') for row in result}
    assert None not in scopes, f'every answer row must carry a scopeMask, got {result}'
    assert len(scopes) == 1, f'all rows in one response must share one scopeMask, got {scopes}'
    return scopes.pop()  # type: ignore[return-value]


# --- backend layer: scopeMask emission (the unit of implementation) --------------------------------------------------


def test_backend_emits_scope_mask_field(
        w2ui: W2UIClient, dns: DNSClient, base_record: dict[str, Any], cleanup: list[tuple[str, int]]) -> None:
    """Every answer row from the DNS backend carries a `scopeMask` field (closes the Phase B gap)."""
    fqdn = 'ecs-field.example.com'
    _save_record(w2ui, base_record, cleanup, 'ecs-field', '192.0.2.210')

    result = dns.lookup(fqdn, 'A', real_remote='192.0.2.0/24')
    assert result, result
    assert all('scopeMask' in row for row in result), result


def test_backend_public_view_scope_zero(
        w2ui: W2UIClient, dns: DNSClient, base_record: dict[str, Any], cleanup: list[tuple[str, int]]) -> None:
    """A Public (match-all) answer carries scope 0 even when the client sent a /24 source prefix.

    Scope reflects the answer's view, not the source prefix: a view-independent answer stays globally cacheable.
    """
    fqdn = 'ecs-public.example.com'
    _save_record(w2ui, base_record, cleanup, 'ecs-public', '192.0.2.210', view=_PUBLIC_VIEW)

    result = dns.lookup(fqdn, 'A', real_remote='192.0.2.0/24')
    assert [row['content'] for row in result] == ['192.0.2.210']
    assert _scope(result) == 0


def test_backend_specific_view_scope_equals_source(
        w2ui: W2UIClient, dns: DNSClient, base_record: dict[str, Any], cleanup: list[tuple[str, int]]) -> None:
    """A CIDR-view (Private) answer carries scope = the source prefix length: the answer is subnet specific."""
    fqdn = 'ecs-private.example.com'
    _save_record(w2ui, base_record, cleanup, 'ecs-private', '192.0.2.211', view='Private')

    result = dns.lookup(fqdn, 'A', real_remote='10.1.2.0/24')
    assert [row['content'] for row in result] == ['192.0.2.211']
    assert _scope(result) == 24


def test_backend_specific_view_scope_ipv6_source(
        w2ui: W2UIClient, dns: DNSClient, base_record: dict[str, Any], cleanup: list[tuple[str, int]]) -> None:
    """An IPv6 CIDR-view answer carries scope = the IPv6 source prefix length (a /56 client subnet)."""
    response = w2ui.save('views', view='ECS IPv6', rule='2001:db8::/32')
    assert response.json()['status'] == 'success'
    view_recid = w2ui.find_recid('views', view='ECS IPv6')
    assert view_recid is not None
    cleanup.append(('views', view_recid))

    fqdn = 'ecs-v6.example.com'
    _save_record(w2ui, base_record, cleanup, 'ecs-v6', '2001:db8::1',
                 name_type='AAAA', view='ECS IPv6')

    result = dns.lookup(fqdn, 'AAAA', real_remote='2001:db8::/56')
    assert [row['content'] for row in result] == ['2001:db8::1']
    assert _scope(result) == 56


def test_backend_no_header_scope_zero(
        w2ui: W2UIClient, dns: DNSClient, base_record: dict[str, Any], cleanup: list[tuple[str, int]]) -> None:
    """With no X-Remotebackend-Real-Remote header the backend still answers and reports scope 0 (no crash)."""
    fqdn = 'ecs-nohdr.example.com'
    _save_record(w2ui, base_record, cleanup, 'ecs-nohdr', '192.0.2.212', view=_PUBLIC_VIEW)

    result = dns.lookup(fqdn, 'A')
    assert [row['content'] for row in result] == ['192.0.2.212']
    assert _scope(result) == 0


def test_backend_garbage_header_scope_zero(
        w2ui: W2UIClient, dns: DNSClient, base_record: dict[str, Any], cleanup: list[tuple[str, int]]) -> None:
    """A malformed real-remote header is tolerated: the backend falls back and reports scope 0."""
    fqdn = 'ecs-garbage.example.com'
    _save_record(w2ui, base_record, cleanup, 'ecs-garbage', '192.0.2.213', view=_PUBLIC_VIEW)

    result = dns.lookup(fqdn, 'A', real_remote='not-an-ip-network')
    assert [row['content'] for row in result] == ['192.0.2.213']
    assert _scope(result) == 0


def test_backend_opt_out_source_zero_scope_zero(
        w2ui: W2UIClient, dns: DNSClient, base_record: dict[str, Any], cleanup: list[tuple[str, int]]) -> None:
    """An ECS opt-out (source prefix 0, `0.0.0.0/0`) resolves to scope 0 with no special case (RFC 7871)."""
    fqdn = 'ecs-optout.example.com'
    _save_record(w2ui, base_record, cleanup, 'ecs-optout', '192.0.2.214', view=_PUBLIC_VIEW)

    result = dns.lookup(fqdn, 'A', real_remote='0.0.0.0/0')
    assert [row['content'] for row in result] == ['192.0.2.214']
    assert _scope(result) == 0


def test_backend_mixed_view_specific_client_scope_equals_source(
        w2ui: W2UIClient, dns: DNSClient, base_record: dict[str, Any], cleanup: list[tuple[str, int]]) -> None:
    """A client matching the narrower view gets only the specific record (the match-all catch-all is excluded)
    and scope is the source prefix: the answer set as a whole is subnet specific.
    """
    fqdn = 'ecs-mixed.example.com'
    _save_record(w2ui, base_record, cleanup, 'ecs-mixed', '192.0.2.215', view=_PUBLIC_VIEW)
    _save_record(w2ui, base_record, cleanup, 'ecs-mixed', '192.0.2.216', view='Private')

    result = dns.lookup(fqdn, 'A', real_remote='10.1.2.0/24')
    assert [row['content'] for row in result] == ['192.0.2.216']
    assert _scope(result) == 24


def test_backend_any_scopes_sibling_rrsets_independently(
        w2ui: W2UIClient, dns: DNSClient, base_record: dict[str, Any], cleanup: list[tuple[str, int]]) -> None:
    """PowerDNS sends every lookup as ANY, so one lookup returns the whole rrset bundle. A subnet-specific A
    rrset must not push its scope onto a sibling match-all AAAA rrset at the same name.
    """
    fqdn = 'ecs-anymix.example.com'
    _save_record(w2ui, base_record, cleanup, 'ecs-anymix', '192.0.2.230', view=_PUBLIC_VIEW)
    _save_record(w2ui, base_record, cleanup, 'ecs-anymix', '192.0.2.231', view='Private')
    _save_record(w2ui, base_record, cleanup, 'ecs-anymix', '2001:db8::30', name_type='AAAA', view=_PUBLIC_VIEW)

    result = dns.lookup(fqdn, 'ANY', real_remote='10.1.2.0/24')
    scope = {(row['qtype'], row['content']): row['scopeMask'] for row in result}
    assert ('A', '192.0.2.230') not in scope  # the matched specific view excludes the catch-all record
    assert scope[('A', '192.0.2.231')] == 24  # the A rrset as a whole is subnet specific
    assert scope[('AAAA', '2001:db8::30')] == 0  # the sibling AAAA rrset stays globally cacheable


def test_backend_mixed_view_public_client_scope_source(
        w2ui: W2UIClient, dns: DNSClient, base_record: dict[str, Any], cleanup: list[tuple[str, int]]) -> None:
    """A public client of a mixed name sees only the match-all record, but the rrset has a Private sibling, so the
    answer is subnet specific and carries scope = the source prefix, not 0.

    This is the cache-isolation invariant: a scope-0 answer would be cached globally and reused for private
    clients, withholding the Private record from them. Scope = source keeps the public and private answers in
    separate resolver cache entries.
    """
    fqdn = 'ecs-mixed2.example.com'
    _save_record(w2ui, base_record, cleanup, 'ecs-mixed2', '192.0.2.217', view=_PUBLIC_VIEW)
    _save_record(w2ui, base_record, cleanup, 'ecs-mixed2', '192.0.2.218', view='Private')

    result = dns.lookup(fqdn, 'A', real_remote='198.51.100.0/24')
    assert [row['content'] for row in result] == ['192.0.2.217']
    assert _scope(result) == 24


def test_backend_sticky_hash_match_all_scopes_source(
        w2ui: W2UIClient, dns: DNSClient, base_record: dict[str, Any], cleanup: list[tuple[str, int]]) -> None:
    """A match-all rrset under sticky-hash carries scope = source, not 0: the answer varies by client network.

    Every record is in the Public view, so the view stage alone would say scope 0 - but sticky-hash keys on the
    client network, so a global (scope 0) answer would let a resolver serve one network's sticky pick to all.
    """
    fqdn = 'ecs-sticky.example.com'
    _save_record(w2ui, base_record, cleanup, 'ecs-sticky', '192.0.2.240', view=_PUBLIC_VIEW, policy='Sticky hash')
    _save_record(w2ui, base_record, cleanup, 'ecs-sticky', '192.0.2.241', view=_PUBLIC_VIEW, policy='Sticky hash')

    result = dns.lookup(fqdn, 'A', real_remote='198.51.100.0/24')
    assert _scope(result) == 24  # min(default ipv4_prefix 24, source 24)


# --- end-to-end layer: ECS echoed on the wire by PowerDNS ------------------------------------------------------------


def test_dig_ecs_option_echoed(
        w2ui: W2UIClient, dns_addr: str, base_record: dict[str, Any], cleanup: list[tuple[str, int]]) -> None:
    """PowerDNS echoes a CLIENT-SUBNET option for an ECS query (requires edns-subnet-processing=yes)."""
    fqdn = 'ecs-echo.example.com'
    _save_record(w2ui, base_record, cleanup, 'ecs-echo', '192.0.2.220', view=_PUBLIC_VIEW)

    result = _dig(dns_addr, fqdn, 'A', '+subnet=192.0.2.0/24')
    assert result.ecs is not None, 'no CLIENT-SUBNET echoed - is edns-subnet-processing enabled?'


def test_dig_ecs_family_source_address_match_query(
        w2ui: W2UIClient, dns_addr: str, base_record: dict[str, Any], cleanup: list[tuple[str, int]]) -> None:
    """Google MUST: the echoed FAMILY, SOURCE PREFIX-LENGTH and ADDRESS match the query (here 192.0.2.0/24)."""
    fqdn = 'ecs-match.example.com'
    _save_record(w2ui, base_record, cleanup, 'ecs-match', '192.0.2.221', view=_PUBLIC_VIEW)

    result = _dig(dns_addr, fqdn, 'A', '+subnet=192.0.2.0/24')
    assert result.ecs is not None
    assert result.ecs.address == '192.0.2.0'
    assert result.ecs.source == 24


def test_dig_ecs_public_answer_scope_zero(
        w2ui: W2UIClient, dns_addr: str, base_record: dict[str, Any], cleanup: list[tuple[str, int]]) -> None:
    """A Public (match-all) answer is echoed with scope 0: globally cacheable by ECS-aware resolvers."""
    fqdn = 'ecs-univ-e2e.example.com'
    _save_record(w2ui, base_record, cleanup, 'ecs-univ-e2e', '192.0.2.222', view=_PUBLIC_VIEW)

    result = _dig(dns_addr, fqdn, 'A', '+subnet=192.0.2.0/24')
    assert result.ecs is not None
    assert result.ecs.scope == 0


def test_dig_ecs_subnet_specific_answer_scope_nonzero(
        w2ui: W2UIClient, dns_addr: str, base_record: dict[str, Any], cleanup: list[tuple[str, int]]) -> None:
    """The Phase C regression: a subnet-specific answer is echoed with a NON-zero scope, not `/0`.

    A name with a Public and a Private record, queried from a Private subnet, must return the Private record AND a
    scope equal to the source prefix (24). With the current build PowerDNS echoes `/0`, which would let a resolver
    cache one subnet's answer for everyone.
    """
    fqdn = 'ecs-specific.example.com'
    _save_record(w2ui, base_record, cleanup, 'ecs-specific', '192.0.2.223', view=_PUBLIC_VIEW)
    _save_record(w2ui, base_record, cleanup, 'ecs-specific', '192.0.2.224', view='Private')

    result = _dig(dns_addr, fqdn, 'A', '+subnet=10.1.2.0/24')
    assert '192.0.2.224' in result.answers, result
    assert result.ecs is not None
    assert result.ecs.scope == 24, f'subnet-specific answer must not be globally cacheable, got scope {result.ecs}'


def test_dig_ecs_distinct_subnets_isolated(
        w2ui: W2UIClient, dns_addr: str, base_record: dict[str, Any], cleanup: list[tuple[str, int]]) -> None:
    """Two different client subnets get their own answer, each keyed to its own /24 - the cache-isolation guarantee.

    Private subnet -> Private record present, scope 24. Public subnet -> Private record absent, also scope 24:
    the rrset is view-differentiated, so even the public answer must not be globally cacheable, or a resolver
    would reuse it for private clients and withhold the Private record. Both answers live under their own /24, so
    neither is reused for the other.
    """
    fqdn = 'ecs-isolate.example.com'
    _save_record(w2ui, base_record, cleanup, 'ecs-isolate', '192.0.2.225', view=_PUBLIC_VIEW)
    _save_record(w2ui, base_record, cleanup, 'ecs-isolate', '192.0.2.226', view='Private')

    private = _dig(dns_addr, fqdn, 'A', '+subnet=10.1.2.0/24')
    assert '192.0.2.226' in private.answers
    assert private.ecs is not None and private.ecs.scope == 24

    public = _dig(dns_addr, fqdn, 'A', '+subnet=198.51.100.0/24')
    assert '192.0.2.226' not in public.answers
    assert public.ecs is not None and public.ecs.scope == 24


def test_dig_ecs_ipv6_subnet_specific_scope(
        w2ui: W2UIClient, dns_addr: str, base_record: dict[str, Any], cleanup: list[tuple[str, int]]) -> None:
    """Google: IPv6 ECS scope must be returned. An IPv6 CIDR-view answer is echoed with the v6 source prefix."""
    response = w2ui.save('views', view='ECS IPv6 E2E', rule='2001:db8::/32')
    assert response.json()['status'] == 'success'
    view_recid = w2ui.find_recid('views', view='ECS IPv6 E2E')
    assert view_recid is not None
    cleanup.append(('views', view_recid))

    fqdn = 'ecs-v6-e2e.example.com'
    _save_record(w2ui, base_record, cleanup, 'ecs-v6-e2e', '2001:db8::2',
                 name_type='AAAA', view='ECS IPv6 E2E')

    result = _dig(dns_addr, fqdn, 'AAAA', '+subnet=2001:db8::/56')
    assert '2001:db8::2' in result.answers, result
    assert result.ecs is not None
    assert result.ecs.scope == 56


def test_dig_no_subnet_query_no_ecs_option(
        w2ui: W2UIClient, dns_addr: str, base_record: dict[str, Any], cleanup: list[tuple[str, int]]) -> None:
    """A query without an ECS option gets no CLIENT-SUBNET in the response: ECS is never added unsolicited."""
    fqdn = 'ecs-none.example.com'
    _save_record(w2ui, base_record, cleanup, 'ecs-none', '192.0.2.227', view=_PUBLIC_VIEW)

    result = _dig(dns_addr, fqdn, 'A')
    assert result.ecs is None, f'unexpected ECS option on a non-ECS query: {result.ecs}'


def test_dig_ecs_opt_out_scope_zero(
        w2ui: W2UIClient, dns_addr: str, base_record: dict[str, Any], cleanup: list[tuple[str, int]]) -> None:
    """An ECS opt-out (`+subnet=0.0.0.0/0`, the shape of Google's probes) is echoed at source 0 and scope 0.

    PowerDNS forwards the opt-out as `0.0.0.0/0`, so the view stage matches `0.0.0.0` and answers from the
    match-all views: the suitable-for-all answer RFC 7871 expects for a source-0 query.
    """
    fqdn = 'ecs-optout-e2e.example.com'
    _save_record(w2ui, base_record, cleanup, 'ecs-optout-e2e', '192.0.2.229', view=_PUBLIC_VIEW)

    result = _dig(dns_addr, fqdn, 'A', '+subnet=0.0.0.0/0')
    assert result.answers == ['192.0.2.229'], result
    assert result.ecs is not None, 'an opt-out ECS query must still get a matching ECS option'
    assert result.ecs.address == '0.0.0.0'
    assert result.ecs.source == 0
    assert result.ecs.scope == 0


def test_dig_apex_infra_not_contaminated_by_subnet_record(
        w2ui: W2UIClient, dns_addr: str, base_record: dict[str, Any], cleanup: list[tuple[str, int]]) -> None:
    """A narrower-view record at the apex must not over-scope the apex NS rrset.

    PowerDNS issues every backend lookup as ANY, so the apex A, NS and SOA come back in one bundle. Adding a
    Private A at the apex makes the A answer subnet specific (scope 24) but the NS rrset must stay scope 0, or a
    resolver would fragment (or, with a narrower scope, mis-share) the delegation per subnet.
    """
    _save_record(w2ui, base_record, cleanup, '@', '10.9.9.9', view='Private')

    ns = _dig(dns_addr, 'example.com', 'NS', '+subnet=10.1.2.0/24')
    assert ns.ecs is not None and ns.ecs.scope == 0, ns

    apex_a = _dig(dns_addr, 'example.com', 'A', '+subnet=10.1.2.0/24')
    assert '10.9.9.9' in apex_a.answers, apex_a
    assert apex_a.ecs is not None and apex_a.ecs.scope == 24, apex_a


# --- end-to-end layer: Google compliance for non-positive and global-scope responses --------------------------------


def test_dig_ecs_soa_scope_zero(dns_addr: str) -> None:
    """Google: SOA answers use global /0 scope (seed SOA is in the Public view, so scope resolves to 0)."""
    result = _dig(dns_addr, 'example.com', 'SOA', '+subnet=192.0.2.0/24')
    assert result.status == 'NOERROR'
    assert result.ecs is not None, 'SOA ECS query must get a matching ECS option'
    assert result.ecs.scope == 0


def test_dig_ecs_ns_scope_zero(dns_addr: str) -> None:
    """Google: NS/delegation answers use global /0 scope (seed NS records are in the Public view)."""
    result = _dig(dns_addr, 'example.com', 'NS', '+subnet=192.0.2.0/24')
    assert result.status == 'NOERROR'
    assert result.ecs is not None, 'NS ECS query must get a matching ECS option'
    assert result.ecs.scope == 0


def test_dig_ecs_nodata_scope_zero(
        w2ui: W2UIClient, dns_addr: str, base_record: dict[str, Any], cleanup: list[tuple[str, int]]) -> None:
    """Google SHOULD: a NODATA answer (existing name, no record of that qtype) carries global /0 scope."""
    fqdn = 'ecs-nodata.example.com'
    _save_record(w2ui, base_record, cleanup, 'ecs-nodata', '192.0.2.228', view=_PUBLIC_VIEW)

    # name exists with an A record but no AAAA -> NODATA
    result = _dig(dns_addr, fqdn, 'AAAA', '+subnet=192.0.2.0/24')
    assert result.status == 'NOERROR'
    assert result.answers == []
    assert result.ecs is not None, 'NODATA ECS query must get a matching ECS option'
    assert result.ecs.scope == 0


def test_dig_ecs_nxdomain_scope_zero(dns_addr: str) -> None:
    """Google SHOULD: an NXDOMAIN answer carries global /0 scope for better caching."""
    result = _dig(dns_addr, 'no.such.name.example.com', 'A', '+subnet=192.0.2.0/24')
    assert result.status == 'NXDOMAIN'
    assert result.ecs is not None, 'NXDOMAIN ECS query must get a matching ECS option'
    assert result.ecs.scope == 0
