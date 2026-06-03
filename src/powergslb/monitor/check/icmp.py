"""icmp health check."""

from dataclasses import dataclass
from typing import ClassVar

from icmplib import ping

from powergslb.monitor.check.base import Check, IPAddress

__all__ = ['IcmpCheck']


@dataclass
class IcmpCheck(Check):
    """Ping a host; healthy when it replies.

    :param ip: Target IP address.
    """
    name = 'icmp'
    privileged: ClassVar[bool] = True

    ip: IPAddress

    def execute(self) -> bool:
        return ping(self.ip, count=1, timeout=self.timeout, privileged=self.privileged).is_alive
