"""In-memory health status registry."""

__all__ = ['StatusRegistry', 'StatusWriter']


class StatusWriter:
    """Write access to a single content id in the StatusRegistry."""

    def __init__(self, registry: 'StatusRegistry', content_id: int) -> None:
        self._registry = registry
        self.content_id = content_id

    def set_down(self) -> None:
        """Mark the content as down."""
        self._registry.add(self.content_id)

    def set_up(self) -> None:
        """Mark the content as up."""
        self._registry.remove(self.content_id)

    def is_down(self) -> bool:
        """Return True if the content is down."""
        return self._registry.is_down(self.content_id)


class StatusRegistry:
    """Tracks the content ids that are currently down."""

    def __init__(self) -> None:
        # A plain set is thread-safe for this usage under CPython (atomic add/remove/in on ints).
        self._status: set[int] = set()

    def add(self, content_id: int) -> None:
        """Add a content id to the status set."""
        self._status.add(content_id)

    def remove(self, content_id: int) -> None:
        """Remove a content id from the status set."""
        self._status.discard(content_id)

    def is_down(self, content_id: int) -> bool:
        """Return True if the content id is in the status set."""
        return content_id in self._status

    def get_writer(self, content_id: int) -> StatusWriter:
        """Return a StatusWriter for the given content id."""
        return StatusWriter(self, content_id)

    def retain(self, valid_ids: set[int]) -> set[int]:
        """Drop any content ids not in valid_ids; return the ids that were removed."""
        stale = self._status - valid_ids
        self._status &= valid_ids
        return stale
