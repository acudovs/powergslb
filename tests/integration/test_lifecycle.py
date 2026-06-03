# pylint: disable=missing-function-docstring

"""Graceful-shutdown integration test, exercised against the real systemd unit inside the container.

Unlike the rest of
the suite (pure HTTP/DNS clients), this drives `systemctl`, so it is skipped unless POWERGSLB_CONTAINER names a
docker container to control. It must run serially - it stops the shared service.
"""

import os
import subprocess
import time
from collections.abc import Iterator

import pytest
import requests

CONTAINER = os.environ.get('POWERGSLB_CONTAINER', '')

pytestmark = pytest.mark.skipif(
    not CONTAINER, reason='POWERGSLB_CONTAINER not set; the lifecycle test needs docker/systemd control')


def _systemctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(['docker', 'exec', CONTAINER, 'systemctl', *args],
                          capture_output=True, text=True, check=False)


def _show(prop: str) -> str:
    """Return a single systemd unit property value (systemctl show -p PROP --value)."""
    return _systemctl('show', 'powergslb', '-p', prop, '--value').stdout.strip()


def _journal() -> str:
    return subprocess.run(['docker', 'exec', CONTAINER, 'journalctl', '-u', 'powergslb', '--no-pager'],
                          capture_output=True, text=True, check=False).stdout


def _serving(base_url: str) -> bool:
    try:
        return requests.get(f'{base_url}/dns/lookup/example.com./SOA', timeout=3).status_code == 200
    except requests.RequestException:
        return False


def _wait_serving(base_url: str, timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _serving(base_url):
            return
        time.sleep(1)
    pytest.fail(f'service did not resume serving at {base_url} within {timeout}s')


@pytest.fixture
def ensure_running(base_url: str) -> Iterator[None]:
    """Always leave the shared service up and serving, even if the test fails mid-stop."""
    yield
    _systemctl('start', 'powergslb')
    _wait_serving(base_url)


@pytest.mark.usefixtures('ensure_running')
def test_graceful_restart(base_url: str) -> None:
    assert _serving(base_url)  # serving before the stop

    stop = _systemctl('stop', 'powergslb')
    assert stop.returncode == 0, stop.stderr

    # systemd's own view of the stop: cleanly exited, no SIGKILL escalation, no timeout
    assert _show('ActiveState') == 'inactive'
    assert _show('Result') == 'success'  # 'timeout'/'signal' would mean it was killed
    assert _show('ExecMainStatus') == '0'  # exit 0 -> Restart=on-failure will not fire
    # the Python graceful path actually ran, rather than systemd hard-killing the process
    assert 'received SIGTERM, exiting' in _journal()

    # restart: the released listen sockets must rebind (allow_reuse_address) and serve again
    start = _systemctl('start', 'powergslb')
    assert start.returncode == 0, start.stderr
    _wait_serving(base_url)
