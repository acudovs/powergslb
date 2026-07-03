"""none health check: a registered type that is never run."""

from dataclasses import dataclass

from powergslb.monitor.check.base import Check

__all__ = ['NoCheck']


@dataclass
class NoCheck(Check):
    """The "none" monitor type: a registered check that is never run.

    Sets the 'skip' flag; 'execute()' returns True (always healthy) if ever run.
    """
    name = 'none'
    skip = True

    def execute(self) -> bool:
        """Report the target healthy unconditionally.

        :returns: Always True.
        """
        return True
