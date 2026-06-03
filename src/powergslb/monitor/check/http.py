"""http health check."""

import logging
import re
import ssl
import time
from dataclasses import dataclass
from http.client import HTTPConnection, HTTPResponse, HTTPSConnection
from typing import ClassVar
from urllib.parse import urlsplit, urlunsplit

from powergslb.monitor.check.base import Check, Regex
from powergslb.version import VERSION

__all__ = ['HttpCheck']


@dataclass
class HttpCheck(Check):
    """Request a URL; healthy when the response status and, optionally, the body match.

    Redirects are never followed: a 3xx surfaces as its own status (use 'expected_status' to match it exactly).

    :param url: Target http:// or https:// URL.
    :param method: 'GET' or 'HEAD'.
    :param expected_status: 0 accepts the success range (200 <= status < 400); any other value requires an exact match.
    :param body_match: Regex searched in the first 'body_chunk' bytes of the body; GET-only; empty disables the match.
    :param tls_verify: Verify the server certificate; False disables TLS verification.
    :param host: HTTP Host header override; the TCP connection still goes to the URL's host.
    """
    name = 'http'
    body_chunk: ClassVar[int] = 65536
    user_agent: ClassVar[str] = f'PowerGSLB/{VERSION}'

    url: str
    method: str = 'GET'
    expected_status: int = 0
    body_match: Regex = ''
    tls_verify: bool = True
    host: str = ''

    def __post_init__(self) -> None:
        super().__post_init__()
        parts = urlsplit(self.url)
        if parts.scheme not in ('http', 'https') or not parts.netloc:
            raise ValueError("check parameter 'url' invalid")
        if self.expected_status != 0 and not 100 <= self.expected_status <= 599:
            raise ValueError("check parameter 'expected_status' invalid")
        if self.method not in ('GET', 'HEAD'):
            raise ValueError("check parameter 'method' unsupported")
        if self.method == 'HEAD' and self.body_match:
            raise ValueError("check parameter 'body_match' unsupported for HEAD requests")

    def execute(self) -> bool:
        deadline = time.monotonic() + self.timeout
        parts = urlsplit(self.url)
        hostname = parts.hostname
        assert hostname is not None

        if parts.scheme == 'https':
            context = ssl.create_default_context()
            if not self.tls_verify:
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
            connection: HTTPConnection = HTTPSConnection(hostname, parts.port, context=context, timeout=self.timeout)
        else:
            connection = HTTPConnection(hostname, parts.port, timeout=self.timeout)

        headers = {'User-Agent': self.user_agent}
        if self.host:
            headers['Host'] = self.host

        target = urlunsplit(('', '', parts.path or '/', parts.query, ''))
        try:
            connection.request(self.method, target, headers=headers)
            response = connection.getresponse()
            status = response.status
            body, timed_out = self._read_body(response, deadline)
        finally:
            connection.close()

        if timed_out:
            logging.error('HTTP request timed out after %s seconds', self.timeout)
            return False

        status_ok = status == self.expected_status if self.expected_status else 200 <= status < 400
        if not status_ok:
            return False
        if not self.body_match:
            return True
        return bool(re.search(self.body_match, body.decode('utf-8', errors='replace')))

    def _read_body(self, response: HTTPResponse, deadline: float) -> tuple[bytes, bool]:
        """Read up to 'body_chunk' bytes of a GET body, stopping once the deadline passes.

        Uses read1() so each iteration performs at most one socket read (bounded by the connection's
        socket timeout) before the deadline is re-checked, which caps the total read time even when a
        server trickles the body. Larger bodies are not drained; the connection is closed by the caller.

        :param response: The response object to read the body from.
        :param deadline: Absolute time.monotonic() value the read must finish by.
        :returns: The bytes read so far and a flag that is True when the deadline fired before EOF.
        """
        if self.method != 'GET':
            return b'', False

        body = b''
        while len(body) < self.body_chunk:
            if time.monotonic() >= deadline:
                return body, True
            try:
                chunk = response.read1(self.body_chunk - len(body))
            except TimeoutError:
                return body, True
            if not chunk:
                break
            body += chunk

        return body, False
