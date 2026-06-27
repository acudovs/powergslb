"""Per-request client data: the client IP and its geolocation."""

from dataclasses import dataclass

import netaddr

from powergslb.client.geo import ClientGeo

__all__ = ['ClientContext']


@dataclass
class ClientContext:
    """Mutable per-request client data: the client IP and its geolocation.

    :param remote_ip: The client IP (IPv4 or IPv6).
    :param geo: The client's geolocation, or None until it is resolved.
    """
    remote_ip: netaddr.IPAddress
    geo: ClientGeo | None = None
