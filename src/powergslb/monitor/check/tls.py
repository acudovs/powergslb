"""tls health check."""

import socket
import ssl
from dataclasses import dataclass

from powergslb.monitor.check.base import Check, IPAddress, Port

__all__ = ['TlsCheck']


@dataclass
class TlsCheck(Check):
    """Open a TCP connection and complete a TLS handshake; healthy when the handshake succeeds.

    :param ip: Target IP address.
    :param port: Target TCP port.
    :param tls_verify: Verify the server certificate; False disables TLS verification.
    :param host: SNI server name and the name verified against the certificate; empty falls back to 'ip'.
    """
    name = 'tls'

    ip: IPAddress
    port: Port
    tls_verify: bool = True
    host: str = ''

    def execute(self) -> bool:
        context = ssl.create_default_context()
        if not self.tls_verify:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        server_hostname = self.host or self.ip
        with socket.create_connection((self.ip, self.port), self.timeout) as sock:
            with context.wrap_socket(sock, server_hostname=server_hostname):
                return True
