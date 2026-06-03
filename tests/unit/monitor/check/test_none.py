# pylint: disable=missing-function-docstring

"""Tests for NoCheck, the skipped "no monitoring" type."""

from powergslb.monitor.check.none import NoCheck


def test_skip_is_set() -> None:
    # MonitorManager honours skip and never threads a NoCheck.
    assert NoCheck.skip is True


def test_takes_no_params_and_uses_base_defaults() -> None:
    check = NoCheck()
    assert (check.interval, check.timeout, check.fall, check.rise) == (3, 1, 3, 5)


def test_execute_is_healthy() -> None:
    # execute() is never reached in production (the check is skipped) but always reports healthy.
    assert NoCheck().execute() is True
