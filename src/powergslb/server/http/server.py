"""HTTP server lifecycle: socket binding, TLS setup, serve loop, shutdown."""

import functools
import importlib.resources
import logging
import ssl
import threading
from http.server import HTTPServer
from socketserver import ThreadingMixIn
from typing import Any

from powergslb.monitor.status import StatusRegistry
from powergslb.server.http.handler import HTTPRequestHandler

__all__ = ['HTTPServerManager']


class HTTPServerManager(threading.Thread):
    """Binds the listen socket, sets up TLS, and owns the threading HTTP server.

    :param server_config: The [server] or [admin] config section (address, port, TLS material, root, timeout).
    :param database_config: mysql.connector connect kwargs.
    :param status_registry: Shared health status registry.
    :param handler: The request handler class this port serves; selects the DNS or admin surface.
    :raises ValueError: When TLS is enabled without a certificate.
    """

    def __init__(self,
                 server_config: dict[str, Any],
                 database_config: dict[str, Any],
                 status_registry: StatusRegistry,
                 handler: type[HTTPRequestHandler],
                 **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.daemon = True
        self.address: str = server_config['address']
        self.port: int = server_config['port']
        self.ssl: bool = server_config.get('ssl', False)
        self.cert: str | None = server_config.get('cert')
        self.key: str | None = server_config.get('key')
        if self.ssl and not self.cert:
            raise ValueError('TLS is enabled but no certificate is configured')
        self.ciphers: str | None = server_config.get('ciphers')
        self.root: str = server_config.get('root') or _default_root()
        self.keep_alive_timeout: float = server_config.get('keep_alive_timeout', 300)
        self._database_config = database_config
        self._status_registry = status_registry
        self._handler = handler
        self._lock = threading.Lock()
        self._stopping = False
        self._server: HTTPServer | None = None

    def run(self) -> None:
        """Bind the socket, wrap it in TLS when configured, and serve until shutdown() stops the server."""
        address = (self.address, self.port)
        handler = functools.partial(
            self._handler,
            directory=self.root,
            database_config=self._database_config,
            status_registry=self._status_registry,
            timeout=self.keep_alive_timeout)
        server = _ThreadingHTTPServer(address, handler)  # binds the socket
        server.daemon_threads = True
        with server:  # server_close() on exit
            if self.ssl:
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                context.load_cert_chain(certfile=self.cert, keyfile=self.key)  # type: ignore[arg-type]
                if self.ciphers:
                    context.set_ciphers(self.ciphers)
                context.options |= ssl.OP_CIPHER_SERVER_PREFERENCE
                server.socket = context.wrap_socket(server.socket, server_side=True)
            logging.info('listening on %s:%s', self.address, self.port)
            with self._lock:
                if self._stopping:  # shutdown() arrived before we started serving
                    return
                self._server = server
            server.serve_forever()

    def shutdown(self, timeout: float = 0) -> None:
        """Signal the server to stop serving and wait up to timeout seconds for the thread to stop.

        :param timeout: Seconds to wait for the server thread to exit; 0 waits indefinitely.
        """
        with self._lock:
            self._stopping = True
            server = self._server
        if server is not None:
            server.shutdown()
        self.join(timeout)


class _ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    """Threading HTTP server; handles each request in a new thread."""


def _default_root() -> str:
    """Return the document root for the static admin assets bundled in the wheel.

    Resolves the on-disk path of the powergslb.resources package. The admin UI is served from its admin/
    subdirectory, so this returns the package directory itself (the parent of admin/). Assumes a normally
    installed wheel, where importlib.resources yields a real filesystem path; PowerGSLB is not run from a zipapp.

    :returns: The filesystem path of the powergslb.resources package.
    """
    return str(importlib.resources.files('powergslb.resources'))
