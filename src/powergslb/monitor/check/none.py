"""none health check: a registered type that is never run."""

from dataclasses import dataclass

from powergslb.monitor.check.base import Check

__all__ = ['NoCheck']


@dataclass
class NoCheck(Check):
    """The "none" type: a registered check that marks "No check".

    MonitorManager honors 'skip' and never threads it, so 'execute()' is never called.
    """
    name = 'none'
    skip = True

    def execute(self) -> bool:
        return True
