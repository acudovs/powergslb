# pylint: disable=missing-function-docstring

"""Tests for the version constant."""

import importlib.resources
import re

from powergslb.version import VERSION


def test_version_is_semver_string() -> None:
    assert isinstance(VERSION, str)
    assert re.fullmatch(r'\d+\.\d+\.\d+', VERSION)


def test_admin_js_matches_version() -> None:
    admin = importlib.resources.files('powergslb.resources') / 'admin'
    index = (admin / 'index.html').read_text()
    assert f'src/powergslb-{VERSION}.js' in index
    assert (admin / 'src' / f'powergslb-{VERSION}.js').is_file()
