"""The client's resolved geolocation."""

from dataclasses import dataclass

__all__ = ['ClientGeo']


@dataclass(frozen=True)
class ClientGeo:
    """The client's resolved geolocation.

    :param country: ISO 3166-1 country code, or None when unknown.
    :param continent: Continent code, or None when unknown.
    """
    country: str | None = None
    continent: str | None = None
