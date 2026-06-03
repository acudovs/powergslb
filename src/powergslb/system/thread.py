"""Service thread contract."""

from typing import Protocol, runtime_checkable

__all__ = ['ServiceThread']


@runtime_checkable
class ServiceThread(Protocol):
    """The contract SystemService requires of a managed thread: name it, start it, poll it, stop it."""
    name: str

    def start(self) -> None:
        """Start the thread."""

    def is_alive(self) -> bool:
        """Return True while the thread is running."""

    def shutdown(self, timeout: float = 0) -> None:
        """Signal the thread to stop and wait up to timeout seconds for it to actually stop."""
