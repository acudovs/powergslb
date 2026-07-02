"""The cached ViewRule value object: compile a view rule once, then match clients without re-parsing."""

import functools
from dataclasses import dataclass
from typing import Any, ClassVar

import netaddr

from powergslb.client import ClientContext
from powergslb.view.geoip import GeoIPReader

__all__ = ['ViewRule']


@dataclass(frozen=True)
class ViewRule:
    """A view rule compiled once per rule string and shared via resolve().

    Tokens split into pre-built CIDR networks and geo selectors at compile time, so the per-query matches() test
    does no parsing. The GeoIP backend is configured once via configure(); instances stay immutable value objects.

    :param cidrs: The rule's CIDR tokens, pre-built as networks.
    :param geos: The rule's geo selectors as (kind, value) pairs, e.g. ('country', 'DE').
    """
    _geoip: ClassVar[GeoIPReader | None] = None

    cidrs: tuple[netaddr.IPNetwork, ...]
    geos: tuple[tuple[str, str], ...]

    @classmethod
    def configure(cls, geoip_config: dict[str, Any]) -> None:
        """Open the process-wide GeoIP reader once at startup.

        :param geoip_config: The [geoip] config section passed to the GeoIPReader.
        """
        cls._geoip = GeoIPReader(geoip_config)

    @staticmethod
    @functools.lru_cache(maxsize=128)
    def resolve(rule: str) -> 'ViewRule':
        """Compile and cache the ViewRule for a raw rule string.

        Keying on the raw string caches before the parse work; an invalid token still raises (lru_cache does not
        cache exceptions).

        :param rule: The view's raw rule string of space-separated CIDR and geo tokens.
        :returns: The shared ViewRule for that string.
        :raises ValueError: When the rule is empty or has a malformed CIDR or geo token.
        """
        tokens = rule.split()
        if not tokens:
            raise ValueError('view rule must hold at least one token')

        cidrs: list[netaddr.IPNetwork] = []
        geos: list[tuple[str, str]] = []

        for token in tokens:
            geo = GeoIPReader.parse_geo_token(token)
            if geo is None:
                try:
                    cidrs.append(netaddr.IPNetwork(token))
                except netaddr.AddrFormatError as e:
                    raise ValueError(f'view rule CIDR invalid: {e}') from e
            else:
                geos.append(geo)

        return ViewRule(tuple(cidrs), tuple(geos))

    def matches(self, context: ClientContext) -> bool:
        """Return whether this rule matches the client, resolving the context geo on demand.

        Tests the pre-built CIDRs first by direct membership (so the cached IPNetwork objects are reused, not
        rebuilt); only when they miss and the rule has geo selectors does it resolve the client geo (once per
        request, and only when a reader is configured) and store it on the context.

        :param context: Per-request client data the policy may read.
        :returns: True when the client IP or its geo satisfies any token.
        """
        if any(context.remote.ip in cidr for cidr in self.cidrs):
            return True
        if self.geos:
            if context.geo is None and self._geoip:
                context.geo = self._geoip.lookup(context.remote.ip)
            if context.geo is not None:
                return any((kind == 'country' and value == context.geo.country) or
                           (kind == 'continent' and value == context.geo.continent) for kind, value in self.geos)
        return False

    @property
    def matches_all(self) -> bool:
        """Return whether this rule matches every client: a CIDR with prefix 0 in both the IPv4 and IPv6 families."""
        return {cidr.version for cidr in self.cidrs if cidr.prefixlen == 0} >= {4, 6}
