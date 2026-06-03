# pylint: disable=missing-function-docstring

"""Tests for StatusRegistry and StatusWriter (the health status set)."""

from powergslb.monitor.status import StatusRegistry


def test_status_registry_initial_state() -> None:
    registry = StatusRegistry()
    assert not registry.is_down(42)


def test_status_registry_add_remove() -> None:
    registry = StatusRegistry()
    registry.add(42)
    assert registry.is_down(42)
    registry.remove(42)
    assert not registry.is_down(42)


def test_status_registry_retain() -> None:
    registry = StatusRegistry()
    registry.add(1)
    registry.add(2)
    stale = registry.retain({2, 3})
    assert stale == {1}  # the dropped ids are returned
    assert not registry.is_down(1)
    assert registry.is_down(2)


def test_status_writer() -> None:
    registry = StatusRegistry()
    writer = registry.get_writer(42)
    assert writer.content_id == 42
    assert not writer.is_down()

    writer.set_down()
    assert writer.is_down()
    assert registry.is_down(42)

    writer.set_up()
    assert not writer.is_down()
    assert not registry.is_down(42)
