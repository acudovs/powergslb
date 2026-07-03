"""In-memory health status registry."""

__all__ = ['StatusRegistry', 'StatusWriter']


class StatusWriter:
    """Write access to a single content id in the StatusRegistry.

    :param registry: The registry the writes go to.
    :param content_id: The content id this writer owns.
    """

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
        """Return True if the content is down.

        :returns: True when the content is marked down.
        """
        return self._registry.is_down(self.content_id)


class StatusRegistry:
    """Tracks the content ids that are currently down."""

    def __init__(self) -> None:
        # A plain set is thread-safe for this usage under CPython (atomic add/remove/in on ints).
        self._status: set[int] = set()

    def add(self, content_id: int) -> None:
        """Add a content id to the status set.

        :param content_id: The content id to mark down.
        """
        self._status.add(content_id)

    def remove(self, content_id: int) -> None:
        """Remove a content id from the status set.

        :param content_id: The content id to mark up.
        """
        self._status.discard(content_id)

    def is_down(self, content_id: int) -> bool:
        """Return True if the content id is in the status set.

        :param content_id: The content id to test.
        :returns: True when the content id is marked down.
        """
        return content_id in self._status

    def get_writer(self, content_id: int) -> StatusWriter:
        """Return a StatusWriter for the given content id.

        :param content_id: The content id the writer owns.
        :returns: The writer bound to this registry and content id.
        """
        return StatusWriter(self, content_id)

    def retain(self, valid_ids: set[int]) -> set[int]:
        """Drop any content ids not in valid_ids; return the ids that were removed.

        :param valid_ids: The content ids allowed to stay.
        :returns: The stale ids that were dropped.
        """
        stale = self._status - valid_ids
        self._status &= valid_ids
        return stale
