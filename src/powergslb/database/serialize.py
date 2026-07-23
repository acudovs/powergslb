"""JSON encoding of database row values."""

import datetime
from typing import Any

__all__ = ['json_default']


def json_default(value: Any) -> str:
    """Serialize a datetime value; reject any other unexpected type.

    :param value: The value json.dumps could not serialize itself.
    :returns: The datetime as a space-separated ISO string (YYYY-MM-DD HH:MM:SS).
    :raises TypeError: When the value is not a datetime.
    """
    if isinstance(value, datetime.datetime):
        return value.isoformat(sep=' ')
    raise TypeError(f'Object of type {type(value).__name__} is not JSON serializable')
