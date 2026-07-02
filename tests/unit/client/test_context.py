# pylint: disable=missing-function-docstring

"""Tests for ClientContext: the mutable per-request client carrier (client network plus lazily-filled geo)."""

import netaddr

from powergslb.client import ClientContext, ClientGeo


def test_carries_remote_network() -> None:
    context = ClientContext(netaddr.IPNetwork('198.51.100.0/24'))
    assert context.remote.ip == netaddr.IPAddress('198.51.100.0')
    assert context.remote.prefixlen == 24


def test_geo_starts_none_and_is_assignable() -> None:
    context = ClientContext(netaddr.IPNetwork('198.51.100.9'))
    assert context.geo is None
    context.geo = ClientGeo('DE', 'EU')
    assert context.geo == ClientGeo('DE', 'EU')
