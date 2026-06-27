"""Monitor type tests.

Verifies that each of the five monitor types (icmp, tcp, http, tls, exec) can be created, retrieved, and deleted via
the admin API with valid parameters matching the schema enforced by the per-type Check dataclasses (MonitorManager
parses the monitor JSON and Check.create validates it).
"""

import json

from .conftest import W2UIClient

_MONITORS = {
    'icmp': {
        'type': 'icmp', 'ip': '192.0.2.1',
        'interval': 10, 'timeout': 3, 'fall': 3, 'rise': 5,
    },
    'tcp': {
        'type': 'tcp', 'ip': '192.0.2.1', 'port': 8080,
        'interval': 10, 'timeout': 3, 'fall': 3, 'rise': 5,
    },
    'http': {
        'type': 'http', 'url': 'http://192.0.2.1:8080/health',
        'interval': 10, 'timeout': 3, 'fall': 3, 'rise': 5,
    },
    'tls': {
        'type': 'tls', 'ip': '192.0.2.1', 'port': 443,
        'interval': 10, 'timeout': 3, 'fall': 3, 'rise': 5,
    },
    'exec': {
        'type': 'exec', 'args': ['/usr/local/bin/check.sh', '192.0.2.1'],
        'interval': 10, 'timeout': 3, 'fall': 3, 'rise': 5,
    },
}


def test_all_monitor_types_crud(w2ui: W2UIClient, cleanup: list[tuple[str, int]]) -> None:
    """Each of the five check types (icmp, tcp, http, tls, exec) round-trips through the admin CRUD API.

    For each type:
    - create the monitor with the required JSON fields
    - verify it appears in the monitors list
    - retrieve the single record and verify all fields
    - delete and verify it is gone
    """
    for check_type, spec in _MONITORS.items():
        name = f'Test {check_type.upper()} Monitor'

        r = w2ui.save('monitors', monitor=name, monitor_json=json.dumps(spec))
        assert r.json()['status'] == 'success', f'{check_type}: save failed: {r.json()}'

        recid = w2ui.find_recid('monitors', monitor=name)
        assert recid is not None, f'{check_type}: not found after create'
        cleanup.append(('monitors', recid))

        record = w2ui.record('monitors', recid)
        assert record['monitor'] == name
        stored = json.loads(record['monitor_json'])
        assert stored['type'] == check_type
        assert set(stored.keys()) == set(spec.keys()), f'{check_type}: JSON keys mismatch'

        w2ui.delete('monitors', recid)
        assert w2ui.find_recid('monitors', monitor=name) is None, f'{check_type}: still present after delete'
