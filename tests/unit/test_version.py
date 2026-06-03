# pylint: disable=missing-function-docstring

"""Tests for the version constant."""

import re

from powergslb.version import VERSION


def test_version_is_semver_string() -> None:
    assert isinstance(VERSION, str)
    assert re.fullmatch(r'\d+\.\d+\.\d+', VERSION)
