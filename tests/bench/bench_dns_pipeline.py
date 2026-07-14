"""Micro-benchmark for the DNS handler post-fetch pipeline (CPU only, no SQL).

Feeds record-row dicts (as gslb_records returns) straight into the handler's _get_lookup post-fetch stages:
_select_records (view filter), _live (health), the routing policy, and _scope_prefix. Profiles the pure-Python cost.

Run:
    .venv/bin/python tests/bench/bench_dns_pipeline.py            # timing summary
    .venv/bin/python tests/bench/bench_dns_pipeline.py --profile  # cProfile top functions
"""

import argparse
import cProfile
import pstats
import time
from collections import defaultdict
from typing import Any

import netaddr

from powergslb.client import ClientContext
from powergslb.monitor.status import StatusRegistry
from powergslb.server.http.handler.powerdns import PowerDNSRequestHandler

PUBLIC = '0.0.0.0/0 ::/0'
PRIVATE = '10.0.0.0/8 172.16.0.0/12 192.168.0.0/16'
RR = '{"type": "round-robin"}'


def _rec(qname: str, qtype: str, content: str, rid: int, rule: str = PUBLIC,
         policy: str = RR, ttl: int = 300, weight: int = 0) -> dict[str, Any]:
    return {'qname': qname, 'qtype': qtype, 'content': content, 'ttl': ttl,
            'policy_json': policy, 'weight': weight, 'id': rid, 'rule': rule}


def apex_any_bundle(zone: str = 'gen05000.test') -> list[dict[str, Any]]:
    """The apex-ANY rrset bundle for a seed-shaped zone: SOA, NS, A, AAAA, MX, TXT, HTTPS, CAA."""
    rid = iter(range(1, 1000))
    rows: list[dict[str, Any]] = [
        _rec(zone, 'SOA', f'ns1.{zone}. hostmaster.{zone}. 2016010101 21600 3600 1209600 300',
             next(rid), ttl=86400)]
    for i in range(1, 5):
        rows.append(_rec(zone, 'NS', f'ns{i}.{zone}', next(rid), ttl=3600))
    for i in range(1, 5):
        rows.append(_rec(zone, 'A', f'192.0.2.10{i}', next(rid)))
    for i in range(1, 5):
        rows.append(_rec(zone, 'AAAA', f'2001:db8::10{i}', next(rid)))
    for pref, host in ((10, 'mail1'), (20, 'mail2'), (30, 'mail3')):
        rows.append(_rec(zone, 'MX', f'{pref} {host}.{zone}', next(rid), ttl=3600))
    rows.append(_rec(zone, 'TXT', 'v=spf1 ip4:192.0.2.0/24 2001:db8::/32 ~all', next(rid), ttl=3600))
    rows.append(_rec(zone, 'HTTPS', '1 . alpn="h2,h3"', next(rid), ttl=3600))
    rows.append(_rec(zone, 'CAA', f'0 issue "ca.{zone}"', next(rid), ttl=3600))
    return rows


def split_bundle(zone: str = 'split.gen05000.test') -> list[dict[str, Any]]:
    """A view-differentiated A rrset: a Private record plus a Public catch-all (exercises the tier split)."""
    return [_rec(zone, 'A', '10.9.9.9', 9001, rule=PRIVATE),
            _rec(zone, 'A', '192.0.2.99', 9002, rule=PUBLIC)]


def make_handler() -> PowerDNSRequestHandler:
    """Build a handler with only the attributes the post-fetch pipeline reads (no HTTP server, no socket)."""
    handler = object.__new__(PowerDNSRequestHandler)
    handler.status_registry = StatusRegistry()
    return handler


def run_lookup(handler: PowerDNSRequestHandler, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Replicate _get_lookup's post-fetch body (everything after gslb_records)."""
    all_records: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        all_records[record['qtype']].append(record)
    selected = handler._select_records(all_records)  # pylint: disable=protected-access
    result: list[dict[str, Any]] = []
    for qtype, group in selected.items():
        scope_prefix = handler._scope_prefix(all_records[qtype])  # pylint: disable=protected-access
        result.extend({'qname': r['qname'], 'qtype': r['qtype'], 'content': r['content'],
                       'ttl': r['ttl'], 'scopeMask': scope_prefix} for r in group)
    return result


# Varied client contexts: ECS subnets in different families, plus an ECS opt-out (host /32).
CONTEXTS = [
    ClientContext(netaddr.IPNetwork('192.0.2.0/24')),
    ClientContext(netaddr.IPNetwork('10.1.2.0/24')),
    ClientContext(netaddr.IPNetwork('2001:db8:1::/48')),
    ClientContext(netaddr.IPNetwork('203.0.113.7/32')),
]


def workload(handler: PowerDNSRequestHandler, iterations: int) -> None:
    """Run the apex-ANY and view-split lookups per iteration, cycling the client context across families."""
    apex = apex_any_bundle()
    split = split_bundle()
    for i in range(iterations):
        handler.context = CONTEXTS[i % len(CONTEXTS)]
        run_lookup(handler, apex)
        run_lookup(handler, split)


def main() -> None:
    """Parse args, warm the resolve caches, then print either a timing summary or a cProfile breakdown."""
    parser = argparse.ArgumentParser()
    parser.add_argument('--profile', action='store_true')
    parser.add_argument('--iterations', type=int, default=200000)
    args = parser.parse_args()

    handler = make_handler()
    workload(handler, 2000)  # warm the resolve caches

    if args.profile:
        profiler = cProfile.Profile()
        profiler.enable()
        workload(handler, args.iterations)
        profiler.disable()
        stats = pstats.Stats(profiler)
        stats.sort_stats('tottime')
        print(f'=== {args.iterations} iterations (each = 1 apex-ANY + 1 split lookup) ===')
        stats.print_stats(20)
        stats.sort_stats('cumulative').print_stats(15)
    else:
        start = time.perf_counter()
        workload(handler, args.iterations)
        elapsed = time.perf_counter() - start
        per = elapsed / args.iterations * 1e6
        print(f'{args.iterations} iterations in {elapsed:.3f}s = {per:.2f} us/iter '
              f'({2 * args.iterations / elapsed:,.0f} lookups/s)')


if __name__ == '__main__':
    main()
