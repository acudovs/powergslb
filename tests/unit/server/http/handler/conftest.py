"""Shared helpers for the HTTP request-handler unit tests.

FakeDatabase is the no-op context-manager stand-in for the real MySQL connection. Recorder + build_recorder
build a handler via __new__ (skipping the socket-opening __init__) with the response primitives and output
stream stubbed, so the writers and routing can be inspected without a socket.
"""

import io
from typing import Any, TypeVar

from powergslb.monitor.status import StatusRegistry
from powergslb.server.http.handler.request import HTTPRequestHandler


class FakeDatabase:
    """Context-manager stand-in for the real MySQL connection."""

    def __init__(self, **_kwargs: Any) -> None:
        pass

    def __enter__(self) -> 'FakeDatabase':
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None


class Recorder(HTTPRequestHandler):
    """Mixin declaring the lists a stubbed handler records its response primitives into."""
    responses_sent: list[int]
    headers_sent: list[tuple[str, str]]
    end_headers_called: int
    errors_sent: list[int]


RecorderT = TypeVar('RecorderT', bound=Recorder)


def build_recorder(handler_class: type[RecorderT], headers: dict[str, str] | None = None) -> RecorderT:
    """Build handler_class via __new__, stubbing the response primitives and streams for inspection."""
    handler = handler_class.__new__(handler_class)
    handler.headers = headers or {}  # type: ignore[assignment]
    handler.wfile = io.BytesIO()
    handler.responses_sent = []
    handler.headers_sent = []
    handler.end_headers_called = 0
    handler.errors_sent = []
    handler.status_registry = StatusRegistry()
    handler.send_response = handler.responses_sent.append  # type: ignore[assignment]
    handler.send_header = lambda k, v: handler.headers_sent.append((k, v))  # type: ignore[assignment]
    handler.end_headers = lambda: setattr(  # type: ignore[method-assign]
        handler, 'end_headers_called', handler.end_headers_called + 1)
    handler.send_error = lambda code, *a, **k: handler.errors_sent.append(code)  # type: ignore[method-assign]
    return handler
