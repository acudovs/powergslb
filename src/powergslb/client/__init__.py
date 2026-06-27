"""Shared per-request client data: the client IP and its resolved geolocation."""

from powergslb.client.context import ClientContext
from powergslb.client.geo import ClientGeo

__all__ = ['ClientContext', 'ClientGeo']
