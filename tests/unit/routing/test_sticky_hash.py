# pylint: disable=missing-function-docstring

"""Tests for the sticky-hash routing policy.

Covers the masked-network serialization, the salt-free byte-canonical stable hash (a golden blake2b literal plus a
cross-process check that it does not use the PYTHONHASHSEED-salted built-in hash()), and the rendezvous (HRW)
_sticky_pick: deterministic, order-independent, and bounded-divergence when a record drops.
"""

import os
import subprocess
import sys
from typing import Any

import netaddr

import powergslb
from powergslb.client import ClientContext
from powergslb.routing.sticky_hash import StickyHash, _masked_network, _stable_hash, _sticky_pick


def _ip(address: str) -> netaddr.IPAddress:
    return netaddr.IPAddress(address)


# blake2b(len-prefixed (masked /24 network of 192.0.2.7) + 'a'), digest_size=16, big-endian int. A literal so a
# future encoding change that would break cross-node identity is caught here.
GOLDEN_A = 225624485536309302887617286076140071008


def _record(content: str, weight: int = 0) -> dict[str, Any]:
    return {'id': 0, 'content': content, 'weight': weight, 'qname': 'example.com'}


# _masked_network

def test_masked_network_ipv4_zeroes_host_bits() -> None:
    assert _masked_network(_ip('192.0.2.7'), 24, 64) == bytes.fromhex('c0000200')  # 192.0.2.0
    assert _masked_network(_ip('192.0.2.250'), 24, 64) == bytes.fromhex('c0000200')  # same /24


def test_masked_network_ipv6_zeroes_host_bits() -> None:
    assert _masked_network(_ip('2001:db8::dead:beef'), 24, 64) == bytes.fromhex('20010db8' + '0' * 24)


def test_masked_network_family_chosen_prefix() -> None:
    # An IPv4 /32 and an IPv6 /128 keep the full address; the widths (4 vs 16 bytes) are family-distinguishing.
    assert _masked_network(_ip('192.0.2.7'), 32, 64) == bytes.fromhex('c0000207')
    assert len(_masked_network(_ip('2001:db8::1'), 24, 128)) == 16


# _stable_hash: salt-free, byte-canonical, node-independent

def test_stable_hash_matches_golden_literal() -> None:
    assert _stable_hash(_masked_network(_ip('192.0.2.7'), 24, 64), 'a') == GOLDEN_A


def test_stable_hash_distinct_per_content() -> None:
    net = _masked_network(_ip('192.0.2.7'), 24, 64)
    assert _stable_hash(net, 'a') != _stable_hash(net, 'b')


def test_stable_hash_is_salt_free_across_processes() -> None:
    # The only cross-process variable for a pure function is PYTHONHASHSEED; the salted built-in hash() would differ
    # node to node. Spawn two processes with different seeds and assert identical output (and the golden literal).
    code = (
        'import netaddr;'
        'from powergslb.routing.sticky_hash import _masked_network, _stable_hash;'
        "print(_stable_hash(_masked_network(netaddr.IPAddress('192.0.2.7'), 24, 64), 'a'))"
    )
    # Wipe the env to control PYTHONHASHSEED, but keep powergslb importable: point PYTHONPATH at the package root,
    # which works whether the package is installed in site-packages or only on the src path.
    package_root = os.path.dirname(os.path.dirname(powergslb.__file__))
    outputs = []
    for seed in ('0', '1'):
        result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, check=True,
                                env={'PYTHONHASHSEED': seed, 'PATH': '', 'PYTHONPATH': package_root})
        outputs.append(result.stdout.strip())
    assert outputs[0] == outputs[1] == str(GOLDEN_A)


# _sticky_pick: rendezvous hashing

def test_sticky_pick_is_deterministic() -> None:
    net = _masked_network(_ip('192.0.2.7'), 24, 64)
    records = [_record('a'), _record('b'), _record('c')]
    chosen = _sticky_pick(records, net, 1)
    assert len(chosen) == 1 and chosen[0] in records
    assert _sticky_pick(records, net, 1)[0] is chosen[0]


def test_sticky_pick_is_order_independent() -> None:
    net = _masked_network(_ip('192.0.2.7'), 24, 64)
    ordered = [_record('a'), _record('b'), _record('c')]
    shuffled = [_record('c'), _record('a'), _record('b')]
    assert _sticky_pick(ordered, net, 1)[0]['content'] == _sticky_pick(shuffled, net, 1)[0]['content']


def test_sticky_pick_caps_at_count_ranked_by_key() -> None:
    net = _masked_network(_ip('192.0.2.7'), 24, 64)
    records = [_record(c) for c in 'abcde']
    top = _sticky_pick(records, net, 3)
    assert len(top) == 3
    # the top 3 are exactly the highest-key records, in descending key order
    ranked = sorted(records, key=lambda r: (_stable_hash(net, r['content']), r['content']), reverse=True)
    assert [r['content'] for r in top] == [r['content'] for r in ranked[:3]]


def test_sticky_pick_count_exceeding_size_returns_all() -> None:
    net = _masked_network(_ip('192.0.2.7'), 24, 64)
    records = [_record('a'), _record('b')]
    assert len(_sticky_pick(records, net, 5)) == 2


def test_sticky_pick_bounded_divergence_when_record_drops() -> None:
    # Dropping one record must remap only the clients whose winner was that record; every other client is unchanged.
    full = [_record(f'r{i}') for i in range(10)]
    victim = 'r3'
    reduced = [r for r in full if r['content'] != victim]

    moved = 0
    for octet in range(256):
        net = _masked_network(_ip(f'10.0.{octet}.1'), 24, 64)  # vary the /24 so the network changes per client
        before = _sticky_pick(full, net, 1)[0]['content']
        after = _sticky_pick(reduced, net, 1)[0]['content']
        if before == victim:
            assert after != victim  # the dropped record cannot be chosen anymore
            moved += 1
        else:
            assert after == before  # unaffected clients keep their record
    assert moved > 0  # the victim was some clients' winner


# select

def test_default_max_answers_is_one() -> None:
    assert StickyHash().max_answers == 1


def test_select_returns_one_record_by_default() -> None:
    records = [_record('a'), _record('b'), _record('c')]
    result = StickyHash().select(records, ClientContext(netaddr.IPNetwork('192.0.2.7')))
    assert len(result) == 1 and result[0] in records


def test_select_empty_returns_empty() -> None:
    assert not StickyHash().select([], ClientContext(netaddr.IPNetwork('192.0.2.7')))


def test_select_same_client_network_is_sticky() -> None:
    records = [_record('a'), _record('b'), _record('c')]
    policy = StickyHash()
    # two clients in the same /24 collapse to the same answer
    first = policy.select(records, ClientContext(netaddr.IPNetwork('192.0.2.7')))[0]['content']
    second = policy.select(records, ClientContext(netaddr.IPNetwork('192.0.2.200')))[0]['content']
    assert first == second


def test_select_max_answers_returns_top_n_sticky() -> None:
    records = [_record(c) for c in 'abcde']
    policy = StickyHash(max_answers=3)
    result = policy.select(records, ClientContext(netaddr.IPNetwork('192.0.2.7')))
    assert len(result) == 3
    # same client network returns the identical set in the identical order
    again = policy.select(records, ClientContext(netaddr.IPNetwork('192.0.2.200')))
    assert [r['content'] for r in result] == [r['content'] for r in again]


def test_select_answers_only_highest_weight_tier() -> None:
    records = [_record('primary-a', 10), _record('primary-b', 10), _record('backup', 1)]
    policy = StickyHash()
    # vary the /24 so the winner spans the tier; the lower tier must never be chosen
    for octet in range(256):
        chosen = policy.select(records, ClientContext(netaddr.IPNetwork(f'10.0.{octet}.1')))[0]['content']
        assert chosen in ('primary-a', 'primary-b')


# network_prefix (the ECS scope granularity: the family prefix the answer is sticky within)

def test_network_prefix_returns_family_prefix() -> None:
    policy = StickyHash(ipv4_prefix=16, ipv6_prefix=48)
    assert policy.network_prefix(ClientContext(netaddr.IPNetwork('192.0.2.7'))) == 16
    assert policy.network_prefix(ClientContext(netaddr.IPNetwork('2001:db8::1'))) == 48


def test_network_prefix_defaults() -> None:
    policy = StickyHash()
    assert policy.network_prefix(ClientContext(netaddr.IPNetwork('192.0.2.7'))) == 24
    assert policy.network_prefix(ClientContext(netaddr.IPNetwork('2001:db8::1'))) == 64
