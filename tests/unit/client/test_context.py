# pylint: disable=missing-function-docstring

"""Tests for ClientContext: the mutable per-request client carrier (pre-parsed IP plus lazily-filled geo)."""

import netaddr

from powergslb.client import ClientContext, ClientGeo


def test_carries_remote_ip() -> None:
    context = ClientContext(netaddr.IPAddress('198.51.100.9'))
    assert context.remote_ip == netaddr.IPAddress('198.51.100.9')


def test_geo_starts_none_and_is_assignable() -> None:
    context = ClientContext(netaddr.IPAddress('198.51.100.9'))
    assert context.geo is None
    context.geo = ClientGeo('DE', 'EU')
    assert context.geo == ClientGeo('DE', 'EU')
