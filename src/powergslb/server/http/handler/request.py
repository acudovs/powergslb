"""Request routing base class, body reading, and response writing."""

import abc
import logging
from http.server import SimpleHTTPRequestHandler
from typing import Any, ClassVar
from urllib.parse import urlsplit, unquote

import netaddr

import powergslb.monitor
from powergslb.database import Database
from powergslb.monitor.status import StatusRegistry
from powergslb.version import VERSION

__all__ = ['HTTPRequestHandler']


class HTTPRequestHandler(SimpleHTTPRequestHandler, abc.ABC):
    """Shared plumbing for the role handlers: per-request state, body reading, and response writing.

    One handler class serves one role on one port; the request path is owned by 'route' and a subclass
    implements '_handle_route()'. Each client connection gets one database connection, shared by its
    keep-alive requests and bounded by the idle timeout.

    :param database_config: mysql.connector connect kwargs.
    :param status_registry: Shared health status registry.
    :param timeout: Idle keep-alive timeout in seconds; bounds how long the handler holds its database connection.
    """
    protocol_version = 'HTTP/1.1'
    server_version = f'PowerGSLB/{VERSION}'
    rbufsize = -1
    wbufsize = -1
    max_body_size = 1048576
    route: ClassVar[str]

    # Per-role Cache-Control for dynamic responses.
    _cache_control: ClassVar[str | None] = None

    # Header names (lowercased) whose values carry credentials and are masked in the debug header dump.
    sensitive_headers: ClassVar[frozenset[str]] = frozenset({
        'authorization', 'proxy-authorization', 'cookie', 'set-cookie',
    })

    # Placeholder shown in place of a sensitive value in a log.
    _mask: ClassVar[str] = '*****'

    def __init__(self,
                 *args: Any,
                 database_config: dict[str, Any],
                 status_registry: StatusRegistry,
                 timeout: float,
                 **kwargs: Any) -> None:
        self.body: bytes | None = None
        self.close_connection: bool = False
        self.database: Database = None  # type: ignore[assignment]  # set per request by handle()
        self.database_config: dict[str, Any] = database_config
        self.dirs: list[str] = []
        self.path: str = ''
        self.remote_ip: netaddr.IPAddress = None  # type: ignore[assignment]  # set per request by _set_remote_ip()
        self.query: Any = None
        self.status_registry: StatusRegistry = status_registry
        self.timeout: float = timeout  # type: ignore[misc]
        super().__init__(*args, **kwargs)

    @abc.abstractmethod
    def _handle_route(self) -> None:
        """Serve the request once routing has matched this handler's 'route'."""

    def _handle_request(self) -> None:
        """Set the per-request client data, split the URL, and dispatch; an off-route path gets a 404."""
        self._set_remote_ip()
        self._urlsplit()

        if self.dirs and self.dirs[0] == self.route:
            self._handle_route()
        else:
            self.send_error(404)

    def _read_body(self) -> None:
        """Read the request body into self.body.

        :raises ValueError: When the Content-Length header is not an int within 0..max_body_size.
        """
        content_length = self.headers.get('Content-Length', 0)
        try:
            content_length = int(content_length)
            if not 0 <= content_length <= self.max_body_size:
                raise ValueError('out of range')
        except ValueError as e:
            raise ValueError(f"'Content-Length' header invalid: '{content_length}'") from e
        self.body = self.rfile.read(content_length)

    def _encode_body(self, content_bytes: bytes) -> tuple[bytes, str | None]:
        """Return the body to send and its Content-Encoding, or None for identity.

        :param content_bytes: The identity response body.
        :returns: The body and its Content-Encoding token, or None for identity.
        """
        return content_bytes, None

    def _send_content(self, content: str, code: int = 200, debug: bool = True) -> None:
        """Send a JSON response.

        :param content: The JSON text to send.
        :param code: HTTP status code of the response.
        :param debug: When true, log the content at DEBUG.
        """
        content_bytes = content.encode('utf-8')
        body, encoding = self._encode_body(content_bytes)
        self.send_response(code)
        if self._cache_control is not None:
            self.send_header('Cache-Control', self._cache_control)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        if encoding is not None:
            self.send_header('Content-Encoding', encoding)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        if debug:
            logging.debug('%s', content)

    def _set_remote_ip(self) -> None:
        """Set the client IP to the TCP peer."""
        self.remote_ip = netaddr.IPAddress(self.address_string())

    def _urlsplit(self) -> None:
        """Split self.path into the path segments (self.dirs) and the query string (self.query).

        The segments are percent-decoded; self.path and self.query stay percent-encoded.
        """
        path, self.query = urlsplit(self.path)[2:4]
        self.dirs = unquote(path).split('/')[1:]

    def do_GET(self) -> None:
        """Dispatch a GET, clearing any body left over from a prior request on this keep-alive connection."""
        self.body = None
        self._handle_request()

    def do_HEAD(self) -> None:
        """Dispatch a HEAD, clearing any body left over from a prior request on this keep-alive connection."""
        self.body = None
        self._handle_request()

    def do_POST(self) -> None:  # pylint: disable=invalid-name
        """Read the size-capped request body, then dispatch.

        Draining the body keeps the keep-alive connection in sync even when a handler responds before
        consuming it (matches nginx/Apache).
        """
        try:
            self._read_body()
        except ValueError as e:
            logging.error('request body invalid: %s', e)
            self.send_error(400)
            return
        self._handle_request()

    def _client_ip(self) -> str:
        """Client address: remote_ip when set, else the TCP peer.

        :returns: The client address.
        """
        return str(self.remote_ip) if self.remote_ip is not None else self.address_string()

    def _log(self, level: int, format: str, *args: Any) -> None:  # pylint: disable=redefined-builtin
        """Route the stdlib log to logging at the given level instead of a raw stderr write.

        Keeps a client address and the control-character escaping (guards against log injection via the request line).

        :param level: logging level for the log line (e.g. INFO or ERROR).
        :param format: printf-style format string passed by the stdlib.
        :param args: Format arguments.
        """
        control_chars = self._control_char_table  # type: ignore[attr-defined]
        logging.log(level, '%s %s', self._client_ip(), (format % args).translate(control_chars))

    def log_message(self, format: str, *args: Any) -> None:  # pylint: disable=redefined-builtin
        """Route the stdlib access log to logging at INFO.

        When the request headers are available, dumps them at DEBUG with sensitive values masked.

        :param format: printf-style format string passed by the stdlib.
        :param args: Format arguments.
        """
        self._log(logging.INFO, format, *args)
        if logging.getLogger().isEnabledFor(logging.DEBUG) and getattr(self, 'headers', None):
            headers = {name: self._mask if name.lower() in self.sensitive_headers else value
                       for name, value in self.headers.items()}
            logging.debug('request headers from %s: %s', self._client_ip(), headers)

    def log_error(self, format: str, *args: Any) -> None:  # pylint: disable=redefined-builtin
        """Route the stdlib error log to logging at ERROR.

        :param format: printf-style format string passed by the stdlib.
        :param args: Format arguments.
        """
        self._log(logging.ERROR, format, *args)

    def handle(self) -> None:
        """Serve requests on the connection, holding one database connection open for its lifetime.

        A vanished client (connection reset, timeout) is expected and logged at debug. Any other error is
        raised while building the response, before bytes are sent, so a 500 is safe; the connection is then
        closed so a desynced keep-alive socket is not reused.
        """
        try:
            with powergslb.database.Database(**self.database_config) as self.database:
                while not self.close_connection:
                    self.handle_one_request()
        except (BrokenPipeError, ConnectionError, TimeoutError) as e:
            logging.debug('connection closed: %s: %s', type(e).__name__, e)
        except Exception as e:  # pylint: disable=broad-exception-caught
            logging.error('%s: %s', type(e).__name__, e)
            self.close_connection = True
            # send_error needs a parsed request; self.command is unset until then.
            if getattr(self, 'command', None):
                try:
                    self.send_error(500)
                except OSError as send_error_exc:
                    logging.debug('send_error failed: %s', send_error_exc)
