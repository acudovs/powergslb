# pylint: disable=missing-function-docstring, protected-access

"""Tests for the in-tree build backend's admin-asset pre-compression (build_backend/backend.py).

The backend lives outside src/ (build-only tooling, not covered by --source=src), so it is imported by name
via importlib rather than a static 'import backend' that pylint could not resolve at lint time. The
compression helper is driven against a temp asset tree by monkeypatching the module's _ASSET_ROOT.
"""

import gzip
import importlib
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import brotli
import pytest


@pytest.fixture(name='backend')
def _backend(pytestconfig: pytest.Config) -> Iterator[Any]:
    root = pytestconfig.rootpath / 'build_backend'
    sys.path.insert(0, str(root))
    try:
        yield importlib.import_module('backend')
    finally:
        sys.path.remove(str(root))


def test_compress_produces_roundtrip_siblings(backend: Any, tmp_path: Path, monkeypatch: Any) -> None:
    # A multi-dot name (app-1.2.3.min.js) proves the sibling naming appends the suffix, not with_suffix.
    body = b'console.log(1);\n' * 512
    (tmp_path / 'app-1.2.3.min.js').write_bytes(body)
    monkeypatch.setattr(backend, '_ASSET_ROOT', tmp_path)

    backend._compress_admin_assets()

    gz = tmp_path / 'app-1.2.3.min.js.gz'
    br = tmp_path / 'app-1.2.3.min.js.br'
    assert gz.is_file() and br.is_file()
    assert gzip.decompress(gz.read_bytes()) == body
    assert brotli.decompress(br.read_bytes()) == body


def test_compress_skips_non_whitelisted_extension(backend: Any, tmp_path: Path, monkeypatch: Any) -> None:
    (tmp_path / 'logo.png').write_bytes(b'\x89PNG' + b'\x00' * 4096)
    monkeypatch.setattr(backend, '_ASSET_ROOT', tmp_path)

    backend._compress_admin_assets()

    assert not (tmp_path / 'logo.png.gz').exists()
    assert not (tmp_path / 'logo.png.br').exists()


def test_write_if_smaller_keeps_shrinking_sibling(backend: Any, tmp_path: Path) -> None:
    sibling = tmp_path / 'app.js.gz'
    backend._write_if_smaller(sibling, b'small', b'the-larger-original')
    assert sibling.read_bytes() == b'small'


def test_write_if_smaller_skips_when_not_smaller(backend: Any, tmp_path: Path) -> None:
    sibling = tmp_path / 'app.js.br'
    backend._write_if_smaller(sibling, b'not-smaller-at-all', b'tiny')
    assert not sibling.exists()


def test_write_if_smaller_drops_stale_sibling(backend: Any, tmp_path: Path) -> None:
    # A sibling that helped in an earlier build but no longer shrinks the asset is removed.
    sibling = tmp_path / 'app.js.br'
    sibling.write_bytes(b'stale-precompressed')
    backend._write_if_smaller(sibling, b'no-longer-smaller', b'x')
    assert not sibling.exists()
