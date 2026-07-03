"""tcp health check."""

import socket
from dataclasses import dataclass

from powergslb.monitor.check.base import Check, IPAddress, Port

__all__ = ['TcpCheck']


@dataclass
class TcpCheck(Check):
    """Open a TCP connection; healthy when it connects.

    :param ip: Target IP address.
    :param port: Target TCP port.
    """
    name = 'tcp'

    ip: IPAddress
    port: Port

    def execute(self) -> bool:
        """Open one TCP connection to the target.

        :returns: True when the connection is established within the timeout.
        """
        with socket.create_connection((self.ip, self.port), self.timeout):
            return True
