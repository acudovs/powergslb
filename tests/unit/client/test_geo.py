# pylint: disable=missing-function-docstring

"""Tests for ClientGeo: the immutable resolved-geo value object."""

from powergslb.client import ClientGeo


def test_defaults_to_unknown() -> None:
    geo = ClientGeo()
    assert geo.country is None
    assert geo.continent is None


def test_carries_country_and_continent() -> None:
    geo = ClientGeo('US', 'NA')
    assert geo.country == 'US'
    assert geo.continent == 'NA'


def test_equality() -> None:
    assert ClientGeo('DE', 'EU') == ClientGeo('DE', 'EU')
    assert ClientGeo('DE', 'EU') != ClientGeo('FR', 'EU')
    assert ClientGeo() == ClientGeo()
