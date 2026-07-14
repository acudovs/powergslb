# pylint: disable=missing-function-docstring

"""Tests for ClientContext: the mutable per-request client carrier (client address, source prefix, geo).

Constructed from a client network, which it decomposes into the address and its source prefix length; the
netaddr.IPNetwork is consumed, not retained, so ip and prefixlen always come from one network.
"""

import netaddr

from powergslb.client import ClientContext, ClientGeo


def test_decomposes_network_into_address_and_prefix() -> None:
    context = ClientContext(netaddr.IPNetwork('198.51.100.0/24'))
    assert context.ip == netaddr.IPAddress('198.51.100.0')
    assert context.prefixlen == 24


def test_geo_starts_none_and_is_assignable() -> None:
    context = ClientContext(netaddr.IPNetwork('198.51.100.9'))
    assert context.geo is None
    context.geo = ClientGeo('DE', 'EU')
    assert context.geo == ClientGeo('DE', 'EU')


def test_equality_covers_address_prefix_and_geo() -> None:
    one = ClientContext(netaddr.IPNetwork('192.0.2.0/24'))
    two = ClientContext(netaddr.IPNetwork('192.0.2.0/24'))
    assert one == two
    assert one != ClientContext(netaddr.IPNetwork('192.0.2.0/32'))  # same address, different source prefix
