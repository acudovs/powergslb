"""The authenticated user identity behind one request."""

from typing import NamedTuple

__all__ = ['UserContext']


class UserContext(NamedTuple):
    """The identity behind one admin request: the authenticated user and the address the request came from.

    :param id: The users row id.
    :param user: The login name.
    :param name: The display name.
    :param client_ip: The address the request came from.
    """
    id: int
    user: str
    name: str
    client_ip: str
