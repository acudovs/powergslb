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
    :param expected_status: Comma-separated codes and inclusive ranges, e.g. '101,200-204,300-308'.
    :param body_match: Regex searched in the first 'body_chunk' bytes of the body; GET-only; empty disables the match.
    :param tls_verify: Verify the server certificate; False disables TLS verification.
    :param host: HTTP Host header override; the TCP connection still goes to the URL's host.
    """
    name = 'http'
    body_chunk: ClassVar[int] = 65536
    user_agent: ClassVar[str] = f'PowerGSLB/{VERSION}'

    url: str
    method: str = 'GET'
    expected_status: str = '200-399'
    body_match: Regex = ''
    tls_verify: bool = True
    host: str = ''

    def __post_init__(self) -> None:
        """Validate the URL, method and match options on top of the base field validation; pre-parse the status spec.

        :raises ValueError: When 'url' is not an http(s) URL, 'method' is not GET or HEAD, or 'body_match' is
            combined with HEAD.
        """
        super().__post_init__()
        parts = urlsplit(self.url)
        if parts.scheme not in ('http', 'https') or not parts.netloc:
            raise ValueError("check parameter 'url' invalid")
        self._expected_statuses: frozenset[int] = self._parse_status_spec(self.expected_status)
        if self.method not in ('GET', 'HEAD'):
            raise ValueError("check parameter 'method' unsupported")
        if self.method == 'HEAD' and self.body_match:
            raise ValueError("check parameter 'body_match' unsupported for HEAD requests")

    @staticmethod
    def _parse_status_spec(spec: str) -> frozenset[int]:
        """Parse an 'expected_status' spec into the set of accepted status codes.

        Each comma-separated token is a single code 'N' or an inclusive range 'LOW-HIGH'.
        Every code must be in 100..599 and each range must have LOW <= HIGH.

        :param spec: Comma-separated codes and inclusive ranges, e.g. '101,200-204,300-308'.
        :returns: The frozenset of accepted status codes.
        :raises ValueError: When a token is empty, non-numeric, malformed, or out of range.
        """
        statuses: set[int] = set()
        for token in spec.split(','):
            low, sep, high = token.strip().partition('-')
            try:
                bounds = range(int(low), int(high if sep else low) + 1)
            except ValueError as e:
                raise ValueError("check parameter 'expected_status' invalid") from e
            if not bounds or bounds.start < 100 or bounds.stop - 1 > 599:
                raise ValueError("check parameter 'expected_status' invalid")
            statuses.update(bounds)
        return frozenset(statuses)

    def execute(self) -> bool:
        """Issue one HTTP request to 'url' and evaluate the response.

        :returns: True when the request completed in time with 'expected_status' and its body matches 'body_match'.
        """
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

        if status not in self._expected_statuses:
            return False

        if not self.body_match:
            return True

        return bool(re.search(self.body_match, body.decode('utf-8', errors='replace')))

    def _read_body(self, response: HTTPResponse, deadline: float) -> tuple[bytes, bool]:
        """Read up to 'body_chunk' bytes of a GET body, stopping once the deadline passes.

        Uses read1() so each iteration performs at most one socket read (bounded by the connection's
        socket timeout) before the deadline is re-checked, which caps the total read time even when a
        server trickles the body. Bytes past 'body_chunk' are left unread.

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
