# pylint: disable=missing-function-docstring

"""Tests for json_default: the space-separated datetime rendering, and the rejection of any other type."""

import datetime

import pytest

from powergslb.database.serialize import json_default


def test_datetime_renders_space_separated() -> None:
    assert json_default(datetime.datetime(2026, 7, 18, 12, 34, 56)) == '2026-07-18 12:34:56'


def test_midnight_keeps_its_zero_time() -> None:
    assert json_default(datetime.datetime(2026, 7, 18)) == '2026-07-18 00:00:00'


def test_unexpected_type_raises() -> None:
    with pytest.raises(TypeError, match='not JSON serializable'):
        json_default(object())
