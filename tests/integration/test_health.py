# pylint: disable=missing-function-docstring

"""Health status reporting tests (static, no waiting).

Reads the status table through the admin API on a freshly seeded container where every record is healthy: confirms all
records report On with the expected fields, that 'No check' records never go down, and that the status table row count
matches the records table. Active fall/rise lifecycle behaviour is covered in test_monitor_health.py.
"""

from .conftest import DNSClient, W2UIClient


def test_all_records_up_and_fields_present(w2ui: W2UIClient) -> None:
    response = w2ui.request('get-records', 'status')
    assert response.status_code == 200
    data = response.json()
    assert data['status'] == 'success'
    assert data['total'] > 0
    records = data['records']
    assert all({'domain', 'name', 'content', 'monitor', 'status', 'view'}.issubset(r.keys())
               for r in records)
    assert all(r['status'] == 'On' for r in records)


def test_no_check_monitor_never_marks_down(w2ui: W2UIClient, dns: DNSClient) -> None:
    no_check = [r for r in w2ui.records('status') if r['monitor'] == 'No check']
    assert len(no_check) > 0
    assert all(r['status'] == 'On' for r in no_check)

    result = dns.lookup('example.com')
    assert isinstance(result, list)
    assert len(result) > 0


def test_status_count_matches_records(w2ui: W2UIClient) -> None:
    status = w2ui.request('get-records', 'status').json()
    records = w2ui.request('get-records', 'records').json()
    assert status['total'] == records['total']
