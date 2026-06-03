# pylint: disable=missing-function-docstring, redefined-outer-name

"""Session fixtures and the W2UIClient/DNSClient helpers shared across the integration tests."""

import os
from collections.abc import Iterator
from typing import Any

import pytest
import requests

_AUTH = ('admin', 'admin')
_TIMEOUT = 15

# Default record fields shared by the record-creating tests. Individual tests
# override what they need with {**base_record, 'field': value}.
_BASE_RECORD = {
    'domain': 'example.com', 'name_type': 'A', 'ttl': 300, 'monitor': 'No check',
    'view': 'Public', 'disabled': 0, 'fallback': 0, 'weight': 0, 'persistence': 0,
}


class W2UIClient:
    """Thin wrapper over the admin /admin/w2ui API."""

    def __init__(self, url: str) -> None:
        self._url = f'{url}/admin/w2ui'

    def request(self, cmd: str, data: str, auth: Any = _AUTH, **params: Any) -> requests.Response:
        """Raw w2ui GET; escape hatch for custom auth or unusual params."""
        params['cmd'] = cmd
        params['data'] = data
        return requests.get(self._url, params=params, auth=auth, verify=False, timeout=_TIMEOUT)

    def records(self, data: str, **params: Any) -> list[dict[str, Any]]:
        return self.request('get-records', data, **params).json()['records']

    def record(self, data: str, recid: int) -> dict[str, Any]:
        return self.request('get-record', data, recid=recid).json()['record']

    def items(self, data: str, field: str) -> list[dict[str, Any]]:
        return self.request('get-items', data, field=field).json()['items']

    def save(self, data: str, recid: int = 0, **fields: Any) -> requests.Response:
        params = {f'record[{k}]': v for k, v in fields.items()}
        return self.request('save-record', data, recid=recid, **params)

    def post(self, cmd: str, data: str, **fields: Any) -> requests.Response:
        """w2ui POST: parameters travel in the form body, exercising the body-decode path (separate from GET)."""
        payload: dict[str, Any] = {'cmd': cmd, 'data': data}
        payload.update(fields)
        return requests.post(self._url, data=payload, auth=_AUTH, verify=False, timeout=_TIMEOUT)

    def save_post(self, data: str, recid: int = 0, **fields: Any) -> requests.Response:
        """save-record via the POST body (cf. save, which uses the GET query string)."""
        payload = {f'record[{k}]': v for k, v in fields.items()}
        return self.post('save-record', data, recid=recid, **payload)

    def delete(self, data: str, selected: Any) -> requests.Response:
        return self.request('delete-records', data, selected=selected)

    def find_recid(self, data: str, **match: Any) -> int | None:
        """Return the recid of the first record matching every match field, or None."""
        return next((r['recid'] for r in self.records(data)
                     if all(r.get(k) == v for k, v in match.items())), None)


class DNSClient:
    """Thin wrapper over the /dns/lookup HTTP backend."""

    def __init__(self, url: str) -> None:
        self._url = url

    def all_domains(self, include_disabled: str = 'false') -> list[dict[str, Any]]:
        """Call the getAllDomains zone-cache method and return its result list."""
        response = requests.get(f'{self._url}/dns/getAllDomains',
                                params={'includeDisabled': include_disabled}, timeout=10)
        return response.json()['result']

    def lookup(self, name: str, qtype: str = 'A', real_remote: str | None = None) -> list[dict[str, Any]]:
        headers = {'X-Remotebackend-Real-Remote': real_remote} if real_remote else None
        response = requests.get(f'{self._url}/dns/lookup/{name}./{qtype}',
                                headers=headers, timeout=10)
        return response.json()['result']


@pytest.fixture(scope='session')
def base_url() -> str:
    return os.environ.get('POWERGSLB_URL', 'http://127.0.0.1:8080')


@pytest.fixture(scope='session', autouse=True)
def require_container(base_url: str) -> None:
    try:
        requests.get(f'{base_url}/dns/lookup/example.com./SOA', timeout=3)
    except requests.ConnectionError:
        pytest.exit(f'Container not reachable at {base_url} - start it first', returncode=2)


@pytest.fixture(scope='session')
def admin_url() -> str:
    return os.environ.get('POWERGSLB_ADMIN_URL', 'https://127.0.0.1:443')


@pytest.fixture(scope='session')
def dns_addr() -> str:
    return os.environ.get('POWERGSLB_DIG_ADDR', '127.0.0.1')


@pytest.fixture(scope='session')
def w2ui(admin_url: str) -> W2UIClient:
    return W2UIClient(admin_url)


@pytest.fixture(scope='session')
def dns(base_url: str) -> DNSClient:
    return DNSClient(base_url)


@pytest.fixture
def base_record() -> dict[str, Any]:
    return dict(_BASE_RECORD)


@pytest.fixture
def cleanup(w2ui: W2UIClient) -> Iterator[list[tuple[str, int]]]:
    """Registry of (data, recid) rows to delete at teardown.

    Append to it instead of writing try/finally; teardown runs in reverse order (LIFO) so a record is removed before
    the view or monitor it references.
    """
    registry: list[tuple[str, int]] = []
    yield registry
    for data, recid in reversed(registry):
        if recid:
            w2ui.delete(data, recid)
