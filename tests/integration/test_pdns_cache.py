# pylint: disable=redefined-outer-name

"""PowerDNS cache behavior on the dig path, exercised with the cache OFF and ON.

Unlike the other DNS suites, which mostly call the PowerGSLB backend directly on :8080 (a path PowerDNS's cache does
NOT sit in front of), these query PowerDNS on :53 with dig, so the packet/query cache is in the loop. Each of the
three answer-shaping axes - view (per-subnet/ECS), health (the down-set), and routing policy (the per-query pick) -
is asserted under both cache states, so the on/off behavioral difference is pinned, not just the cached path.

The suite drives `pdns_control` and rewrites `/etc/pdns/pdns.conf` through `docker exec`, so it is skipped unless
POWERGSLB_CONTAINER names a container. The `cache_state` fixture is parametrized over ('off', 'on'): it sets the
three cache TTLs to 0 or a known positive value, restarts pdns, and restores the original config afterward. Assertions
on the ON state use `pdns_control purge` rather than TTL expiry, so they are deterministic at any positive TTL.

Timing note: run-integration.sh starts the container with POWERGSLB_MONITOR_UPDATE_INTERVAL=2, so a monitor-driven
health flip is visible on the backend within a few seconds (see _FAIL_WAIT).
"""

import json
import os
import subprocess
import time
from collections.abc import Iterator
from typing import Any

import pytest

from .conftest import DNSClient, W2UIClient

CONTAINER = os.environ.get('POWERGSLB_CONTAINER', '')

pytestmark = pytest.mark.skipif(
    not CONTAINER, reason='POWERGSLB_CONTAINER not set; the pdns-cache tests need docker/pdns_control')

_CONF = '/etc/pdns/pdns.conf'
_CACHE_KEYS = ('cache-ttl', 'query-cache-ttl', 'negquery-cache-ttl')
_ON_TTL = 30  # a positive TTL for the ON state; assertions purge rather than wait it out
_FAIL_WAIT = 9  # seconds for a monitor at interval=1, fall=2 to mark a record down (mirrors test_monitor_health)


# --- container / pdns control ----------------------------------------------------------------------------------------


def _exec(*args: str) -> str:
    """Run a command in the container and return its stripped stdout, raising on a non-zero exit."""
    proc = subprocess.run(['docker', 'exec', CONTAINER, *args], capture_output=True, text=True, timeout=30, check=True)
    return proc.stdout.strip()


def _counter(name: str) -> int:
    """Return a PowerDNS statistic (e.g. packetcache-hit) via pdns_control show."""
    return int(_exec('pdns_control', 'show', name))


def _purge(name: str) -> None:
    """Drop every cache entry for a name (suffix match, as pdns_control expects)."""
    _exec('pdns_control', 'purge', f'{name}$')


def _set_cache_ttls(value: int) -> None:
    """Rewrite the three cache TTLs in pdns.conf to value and restart pdns, waiting until it answers again."""
    script = '; '.join(f"sed -i -E 's/^{key}=.*/{key}={value}/' {_CONF}" for key in _CACHE_KEYS)
    _exec('sh', '-c', script)
    subprocess.run(['docker', 'exec', CONTAINER, 'systemctl', 'restart', 'pdns'],
                   capture_output=True, text=True, timeout=30, check=False)  # notify may time out; poll readiness below
    for _ in range(30):
        probe = subprocess.run(['docker', 'exec', CONTAINER, 'pdns_control', 'show', 'uptime'],
                               capture_output=True, text=True, timeout=10, check=False)
        if probe.returncode == 0 and probe.stdout.strip().isdigit():
            return
        time.sleep(0.5)
    raise RuntimeError('pdns did not come back after a cache-ttl change')


@pytest.fixture(scope='module')
def _original_ttl() -> int:
    """The cache-ttl the container shipped with, captured once so every state restores to it."""
    for line in _exec('grep', '-E', '^cache-ttl=', _CONF).splitlines():
        return int(line.split('=', 1)[1])
    raise RuntimeError('cache-ttl not found in pdns.conf')


@pytest.fixture(params=['off', 'on'])
def cache_state(request: pytest.FixtureRequest, _original_ttl: int) -> Iterator[str]:
    """Put pdns in the requested cache state ('off' -> TTL 0, 'on' -> a known positive TTL), then restore the ship TTL.

    :param request: The pytest request carrying the 'off'/'on' param.
    :param _original_ttl: The shipped cache-ttl, restored on teardown.
    """
    state: str = request.param
    _set_cache_ttls(0 if state == 'off' else _ON_TTL)
    try:
        yield state
    finally:
        _set_cache_ttls(_original_ttl)


# --- record/monitor helpers (thin wrappers over the admin API) -------------------------------------------------------


def _save_record(w2ui: W2UIClient, base_record: dict[str, Any], cleanup: list[tuple[str, int]],
                 name: str, content: str, **overrides: Any) -> int:
    """Create one record via the admin API, register it for teardown, and return its recid."""
    response = w2ui.save('records', name=name, content=content, **{**base_record, **overrides})
    assert response.json()['status'] == 'success', response.json()
    recid = w2ui.find_recid('records', name=name, content=content)
    assert recid is not None
    cleanup.append(('records', recid))
    return recid


def _make_monitor(w2ui: W2UIClient, cleanup: list[tuple[str, int]], name: str, spec: dict[str, Any]) -> None:
    """Create a monitor from a spec dict and register it for teardown."""
    w2ui.save('monitors', monitor=name, monitor_json=json.dumps(spec))
    recid = w2ui.find_recid('monitors', monitor=name)
    assert recid is not None, f'monitor {name} not created'
    cleanup.append(('monitors', recid))


def _dig_contents(dns_addr: str, name: str, qtype: str = 'A', extra: tuple[str, ...] = ()) -> set[str]:
    """Return the +short answer lines for name/qtype from PowerDNS on :53 as a set.

    :param extra: Extra dig flags, e.g. an ('+subnet=10.1.2.0/24',) ECS option.
    """
    proc = subprocess.run(['dig', f'@{dns_addr}', name, qtype, '+short', '+time=2', '+tries=1', *extra],
                          capture_output=True, text=True, timeout=10, check=True)
    return {line for line in proc.stdout.strip().splitlines() if line}


# --- view axis -------------------------------------------------------------------------------------------------------


def test_view_axis_isolation_ecs_bypass_and_global_caching(
        w2ui: W2UIClient, dns_addr: str, base_record: dict[str, Any],
        cleanup: list[tuple[str, int]], cache_state: str) -> None:
    """Per-subnet isolation holds under both states; ECS answers bypass the cache; only the global answer is cached.

    A view-differentiated name (Public + Private) never hands the Private subnet's tier to a Public client - the
    invariant, both states. PowerDNS does not cache a scope-nonzero (per-subnet) answer, so an ECS repeat always
    reaches the backend under either state: caching can never stale a per-subnet answer. The scope-0 (match-all)
    answer is the one the cache governs - a repeat re-queries the backend when OFF and is served from cache when ON.
    """
    diff_fqdn = 'cache-view.example.com'
    _save_record(w2ui, base_record, cleanup, 'cache-view', '192.0.2.10', view='Public')
    _save_record(w2ui, base_record, cleanup, 'cache-view', '192.0.2.11', view='Private')
    global_fqdn = 'cache-global.example.com'  # match-all: every answer is globally cacheable (scope 0)
    _save_record(w2ui, base_record, cleanup, 'cache-global', '192.0.2.12', view='Public')
    _purge('cache-view.example.com')
    _purge('cache-global.example.com')

    # Invariant under both states: each subnet sees only its own tier.
    assert _dig_contents(dns_addr, diff_fqdn, 'A', ('+subnet=10.1.2.0/24',)) == {'192.0.2.11'}
    assert _dig_contents(dns_addr, diff_fqdn, 'A', ('+subnet=198.51.100.0/24',)) == {'192.0.2.10'}

    # A scope-nonzero (per-subnet) answer is never cached: a primed ECS query still reaches the backend on repeat,
    # under both states. backend-queries is the layer-agnostic signal (no backend call == served from cache).
    _dig_contents(dns_addr, diff_fqdn, 'A', ('+subnet=10.1.2.0/24',))  # prime
    ecs_before = _counter('backend-queries')
    _dig_contents(dns_addr, diff_fqdn, 'A', ('+subnet=10.1.2.0/24',))  # repeat
    assert _counter('backend-queries') > ecs_before, 'a per-subnet ECS answer is recomputed every query, never cached'

    # The scope-0 (match-all) answer is the one the cache governs.
    _dig_contents(dns_addr, global_fqdn, 'A', ('+subnet=10.1.2.0/24',))  # prime (scope 0)
    global_before = _counter('backend-queries')
    assert _dig_contents(dns_addr, global_fqdn, 'A', ('+subnet=10.1.2.0/24',)) == {'192.0.2.12'}  # repeat
    if cache_state == 'on':
        assert _counter('backend-queries') == global_before, 'a scope-0 answer must be served from cache when on'
    else:
        assert _counter('backend-queries') > global_before, 'every query must reach the backend when caching is off'


def test_view_axis_public_fallback_never_globally_negative(
        w2ui: W2UIClient, dns_addr: str, base_record: dict[str, Any],
        cleanup: list[tuple[str, int]], cache_state: str) -> None:
    """A Public fallback keeps a view-restricted name from ever caching a global (scope-0) negative.

    An out-of-view client of a name that also has a Public record gets that fallback at the source prefix scope, so
    it is cached per subnet, not globally - it can never mask the Private record from an in-view client. This is the
    operator contract for limitation #9, and it must hold under both cache states.
    """
    fqdn = 'cache-fallback.example.com'
    _save_record(w2ui, base_record, cleanup, 'cache-fallback', '192.0.2.20', view='Public')
    _save_record(w2ui, base_record, cleanup, 'cache-fallback', '192.0.2.21', view='Private')
    _purge('cache-fallback.example.com')

    # Out-of-view client: gets the Public fallback, not an empty answer.
    assert _dig_contents(dns_addr, fqdn, 'A', ('+subnet=203.0.113.0/24',)) == {'192.0.2.20'}, f'cache {cache_state}'
    # In-view client is unaffected: the fallback answer was never a global negative.
    assert _dig_contents(dns_addr, fqdn, 'A', ('+subnet=10.1.2.0/24',)) == {'192.0.2.21'}, f'cache {cache_state}'


# --- health axis -----------------------------------------------------------------------------------------------------


def test_health_axis_down_masked_until_purge_when_cached(
        w2ui: W2UIClient, dns: DNSClient, dns_addr: str, base_record: dict[str, Any],
        cleanup: list[tuple[str, int]], cache_state: str) -> None:
    """A record that goes down is dropped from the dig answer immediately with the cache OFF, but stays until purge ON.

    A monitored record (failing exec) plus an always-up sibling: once the monitor marks the record down the backend
    (:8080, uncached) drops it at once. On the dig path (:53) the down record is still served from a packet cache
    primed while the record was up, until the entry is purged - the health-staleness quirk. With the cache OFF the
    dig path tracks the backend with no purge needed.
    """
    fqdn = 'cache-health.example.com'
    _make_monitor(w2ui, cleanup, 'Cache Health Fail',
                  {'type': 'exec', 'args': ['/bin/false'], 'interval': 1, 'timeout': 1, 'fall': 2, 'rise': 2})
    _save_record(w2ui, base_record, cleanup, 'cache-health', '192.0.2.30', monitor='Cache Health Fail')
    # always-up sibling
    _save_record(w2ui, base_record, cleanup, 'cache-health', '192.0.2.31', monitor='No check')
    _purge('cache-health.example.com')

    # Prime the dig cache while both records are still live.
    assert _dig_contents(dns_addr, fqdn) == {'192.0.2.30', '192.0.2.31'}

    time.sleep(_FAIL_WAIT)
    # The backend (uncached) has dropped the down record.
    assert {r['content'] for r in dns.lookup(fqdn)} == {'192.0.2.31'}

    dig_after_down = _dig_contents(dns_addr, fqdn)
    if cache_state == 'on':
        assert dig_after_down == {'192.0.2.30', '192.0.2.31'}, 'a cached answer must mask the health change'
        _purge('cache-health.example.com')
        assert _dig_contents(dns_addr, fqdn) == {'192.0.2.31'}, 'purge must reveal the health change'
    else:
        assert dig_after_down == {'192.0.2.31'}, 'with caching off the dig path must track health immediately'


# --- routing-policy axis ---------------------------------------------------------------------------------------------


def test_routing_axis_weighted_random_frozen_when_cached(
        w2ui: W2UIClient, dns_addr: str, base_record: dict[str, Any],
        cleanup: list[tuple[str, int]], cache_state: str) -> None:
    """Weighted-random re-draws every query with the cache OFF, but is frozen to one pick per entry when cached.

    Two equal-weight records under weighted-random (max_answers=1) return one answer per query. With the cache OFF
    the pick varies across queries, so many digs surface both records; with the cache ON one draw is frozen in the
    packet cache, so every dig within the entry's life returns the identical record. Purging restores the draw.
    """
    fqdn = 'cache-wr.example.com'
    both = {'192.0.2.40', '192.0.2.41'}
    for content in sorted(both):
        _save_record(w2ui, base_record, cleanup, 'cache-wr', content, policy='Weighted random', weight=1)
    _purge('cache-wr.example.com')

    seen = set()
    for _ in range(30):
        seen |= _dig_contents(dns_addr, fqdn)

    if cache_state == 'on':
        assert len(seen) == 1, f'a cached weighted-random answer must be frozen to one record, saw {seen}'
    else:
        assert seen == both, f'an uncached weighted-random answer must vary across queries, saw {seen}'


def test_routing_axis_sticky_hash_is_cache_transparent(
        w2ui: W2UIClient, dns_addr: str, base_record: dict[str, Any],
        cleanup: list[tuple[str, int]], cache_state: str) -> None:
    """Sticky-hash is deterministic per client network, so the answer is identical with the cache OFF and ON.

    Rendezvous hashing pins a client network to one record. Its answer is per-subnet (scope-nonzero), so PowerDNS
    recomputes it every query anyway (the backend is hit every dig, asserted below), and even were it cached the
    result would match a fresh computation. Either way the same client subnet gets the same single record under both
    states - caching adds no distortion (the counterpoint to weighted-random above).
    """
    fqdn = 'cache-sticky.example.com'
    contents = {'192.0.2.50', '192.0.2.51', '192.0.2.52'}
    for content in sorted(contents):
        _save_record(w2ui, base_record, cleanup, 'cache-sticky', content, policy='Sticky hash')
    _purge('cache-sticky.example.com')

    _dig_contents(dns_addr, fqdn, 'A', ('+subnet=198.51.100.0/24',))  # prime
    before = _counter('backend-queries')
    picks = {_dig_contents(dns_addr, fqdn, 'A', ('+subnet=198.51.100.0/24',)).pop() for _ in range(10)}
    assert len(picks) == 1, f'sticky-hash must return one record per network (cache {cache_state}): {picks}'
    assert picks <= contents
    assert _counter('backend-queries') > before, 'a scope-nonzero sticky-hash answer is recomputed every query'
