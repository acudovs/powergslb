"""Per-request client data: the client IP and its geolocation."""

from dataclasses import InitVar, dataclass, field

import netaddr

from powergslb.client.geo import ClientGeo

__all__ = ['ClientContext']


@dataclass
class ClientContext:
    """Mutable per-request client data: the client address, its ECS source prefix, and its geolocation.

    :param remote: The client network (IPv4 or IPv6); decomposed into ip and prefixlen.
    :param geo: The client's geolocation, or None until it is resolved.
    """
    remote: InitVar[netaddr.IPNetwork]
    geo: ClientGeo | None = None
    ip: netaddr.IPAddress = field(init=False)
    prefixlen: int = field(init=False)

    def __post_init__(self, remote: netaddr.IPNetwork) -> None:
        """Decompose the client network into the client address and its source prefix length.

        :param remote: The client network to split into ip and prefixlen.
        """
        self.ip = remote.ip
        self.prefixlen = remote.prefixlen
