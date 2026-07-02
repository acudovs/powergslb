"""sticky-hash routing policy."""

import hashlib
from dataclasses import dataclass
from typing import Any

import netaddr

from powergslb.client import ClientContext
from powergslb.routing.base import IPv4Prefix, IPv6Prefix, Positive, RoutingPolicy

__all__ = ['StickyHash']


def _masked_network(remote_ip: netaddr.IPAddress, ipv4_prefix: int, ipv6_prefix: int) -> bytes:
    """Mask the client IP to its network prefix, zeroing the host bits, as fixed-width big-endian bytes.

    The prefix is chosen by address family (ipv4_prefix for IPv4, ipv6_prefix for IPv6). The fixed width (4 bytes for
    IPv4, 16 for IPv6) is itself family-distinguishing and a byte-identical, node-independent serialization.

    :param remote_ip: The client IP address (IPv4 or IPv6).
    :param ipv4_prefix: IPv4 prefix length.
    :param ipv6_prefix: IPv6 prefix length.
    :returns: The network address as big-endian bytes.
    """
    width, prefix = (32, ipv4_prefix) if remote_ip.version == 4 else (128, ipv6_prefix)
    host_bits = width - prefix
    network = (int(remote_ip.value) >> host_bits) << host_bits  # zero the host bits
    return network.to_bytes(width // 8, 'big')


def _stable_hash(network: bytes, content: str) -> int:
    """Hash (network, content) to an int with a salt-free, byte-canonical, node-independent digest.

    Uses blake2b (never the PYTHONHASHSEED-salted built-in hash()) over a length-prefixed buffer so two distinct
    (network, content) pairs cannot realias into the same bytes, giving every node the same value for the same input.

    :param network: The masked client network from _masked_network.
    :param content: The record content (answer).
    :returns: The 128-bit digest as an int.
    """
    content_bytes = content.encode('utf-8')
    buffer = (len(network).to_bytes(2, 'big') + network +
              len(content_bytes).to_bytes(4, 'big') + content_bytes)
    return int.from_bytes(hashlib.blake2b(buffer, digest_size=16).digest(), 'big')


def _sticky_pick(candidates: list[dict[str, Any]], network: bytes, count: int) -> list[dict[str, Any]]:
    """Pick the top 'count' rendezvous-hashing (HRW) winners for a client network, highest key first.

    Records are ranked by (_stable_hash(network, content), content) descending and the top 'count' are returned. The
    total order on (hash, content) makes the ranking order-independent, so it does not depend on DB row order across
    nodes. Dropping a record only reshuffles entries ranked below it.

    :param candidates: Records to choose from.
    :param network: The masked client network from _masked_network.
    :param count: Maximum number of records to return.
    :returns: Up to 'count' chosen records, highest HRW key first.
    """
    ranked = sorted(candidates,
                    key=lambda record: (_stable_hash(network, record['content']), record['content']),
                    reverse=True)
    return ranked[:count]


@dataclass(frozen=True, kw_only=True)
class StickyHash(RoutingPolicy):
    """Answer up to 'max_answers' records from the highest weight tier, sticky per client network via rendezvous (HRW)
    hashing.

    'weight' is read as a tier: only the highest-weight group of candidates is eligible. Within it, the client IP is
    masked to its network prefix (ipv4_prefix / ipv6_prefix, family-chosen) and records are ranked by the stable hash of
    (network, content); the top 'max_answers' win. A change to the eligible set remaps only ~max_answers/N clients
    (~1/N at the default max_answers=1), not nearly all as a modulo scheme would. Stickiness is stable per client
    network given the same eligible set and prefixes.

    :param max_answers: Maximum records returned from the winning tier (default 1).
    :param ipv4_prefix: IPv4 prefix length the client is masked to.
    :param ipv6_prefix: IPv6 prefix length the client is masked to.
    """
    name = 'sticky-hash'

    max_answers: Positive = 1
    ipv4_prefix: IPv4Prefix = 24
    ipv6_prefix: IPv6Prefix = 64

    def select(self, candidates: list[dict[str, Any]], context: ClientContext) -> list[dict[str, Any]]:
        tier = self.highest_tier(candidates)
        if not tier:
            return []

        network = _masked_network(context.remote_ip, self.ipv4_prefix, self.ipv6_prefix)
        return _sticky_pick(tier, network, self.max_answers)
