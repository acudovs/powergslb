"""Request routing base class, body reading, and response writing."""

import abc
import logging
from http.server import SimpleHTTPRequestHandler
from typing import Any, ClassVar
from urllib.parse import urlsplit, unquote

import powergslb.monitor
from powergslb.database import Database
from powergslb.monitor.status import StatusRegistry
from powergslb.system.geoip import GeoIPReader
from powergslb.version import VERSION

__all__ = ['HTTPRequestHandler']


class HTTPRequestHandler(SimpleHTTPRequestHandler, abc.ABC):
    """Shared plumbing for the role handlers: per-request state, body reading, and response writing.

    One handler class serves one role on one port; the mounted segment is owned by 'route' and a subclass
    implements '_handle_route()'. Each client connection gets one database connection, shared by its
    keep-alive requests and bounded by the idle timeout.

    :param database_config: mysql.connector connect kwargs.
    :param geoip_reader: GeoIP reader for lookup and validation.
    :param status_registry: Shared health status registry.
    :param timeout: Idle keep-alive timeout in seconds; bounds how long the handler holds its database connection.
    """
    protocol_version = 'HTTP/1.1'
    server_version = f'PowerGSLB/{VERSION}'
    rbufsize = -1
    wbufsize = -1
    max_body_size = 1048576
    route: ClassVar[str]

    def __init__(self,
                 *args: Any,
                 database_config: dict[str, Any],
                 geoip_reader: GeoIPReader,
                 status_registry: StatusRegistry,
                 timeout: float,
                 **kwargs: Any) -> None:
        self.body: bytes | None = None
        self.close_connection: bool = False
        self.database: Database = None  # type: ignore[assignment]
        self.database_config: dict[str, Any] = database_config
        self.dirs: list[str] = []
        self.path: str = ''
        self.remote_ip: str | None = None
        self.query: Any = None
        self.geoip_reader: GeoIPReader = geoip_reader
        self.status_registry: StatusRegistry = status_registry
        self.timeout: float = timeout  # type: ignore[misc]
        super().__init__(*args, **kwargs)

    @abc.abstractmethod
    def _handle_route(self) -> None:
        """Serve the request once routing has matched this handler's 'route'."""

    def _handle_request(self) -> None:
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

    def _send_content(self, content: str, code: int = 200, debug: bool = True) -> None:
        content_bytes = content.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(content_bytes)))
        self.end_headers()
        self.wfile.write(content_bytes)
        if debug:
            logging.debug('%s', content)

    def _set_remote_ip(self) -> None:
        """Set the client IP to the TCP peer."""
        self.remote_ip = self.client_address[0]

    def _urlsplit(self) -> None:
        # self.path and self.query stay percent-encoded.
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

        Draining the body keeps the keep-alive connection in sync even when the admin path answers 401
        before reaching the body (matches nginx/Apache).
        """
        try:
            self._read_body()
        except ValueError as e:
            logging.error('request body invalid: %s', e)
            self.send_error(400)
            return
        self._handle_request()

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
