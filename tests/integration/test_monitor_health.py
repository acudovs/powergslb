# pylint: disable=missing-function-docstring

"""Health check monitor tests.

The container must be started with a short monitor update interval so MonitorManager picks up new checks quickly.
run-integration.sh sets:
  -e POWERGSLB_MONITOR_UPDATE_INTERVAL=2

Monitor parameters used throughout: interval=1, fall=2, rise=2.
  Fail path timing: 2s pickup + thread refresh + 2*1s fall + buffer = ~7s  (_FAIL_WAIT=9)
  Rise path timing: 2s pickup + thread refresh + buffer                    (_RISE_WAIT=6)
"""

import json
import time
from typing import Any

import pytest

from .conftest import DNSClient, W2UIClient

_FAIL_WAIT = 9  # seconds: MonitorManager pickup + fall=2 at interval=1
_RISE_WAIT = 6  # seconds: MonitorManager pickup after monitor switch

_T = {'interval': 1, 'timeout': 1, 'fall': 2, 'rise': 2}
# *_fail target a closed port / unreachable IP; *_pass target services that are up inside
# the container (loopback ping, powergslb's own :8080) so the success paths are exercised too.
_SPECS = {
    'exec_fail': {**_T, 'type': 'exec', 'args': ['/bin/false']},
    'exec_pass': {**_T, 'type': 'exec', 'args': ['/bin/true']},
    'tcp_fail': {**_T, 'type': 'tcp', 'ip': '127.0.0.1', 'port': 19999},
    'tcp_pass': {**_T, 'type': 'tcp', 'ip': '127.0.0.1', 'port': 8080},
    'icmp_fail': {**_T, 'type': 'icmp', 'ip': '192.0.2.254'},
    'icmp_pass': {**_T, 'type': 'icmp', 'ip': '127.0.0.1'},
    'http_fail': {**_T, 'type': 'http', 'url': 'http://127.0.0.1:19999/health'},
    'http_pass': {**_T, 'type': 'http', 'url': 'http://127.0.0.1:8080/dns/lookup/example.com./SOA'},
}
_NAMES = {k: f'Health Test {k.replace("_", " ").title()}' for k in _SPECS}

# unique IPs for test records - not present in seed data
_IPS = {'exec_fail': '192.0.2.211', 'exec_pass': '192.0.2.212',
        'tcp_fail': '192.0.2.213', 'icmp_fail': '192.0.2.214', 'http_fail': '192.0.2.215',
        'icmp_pass': '192.0.2.216', 'tcp_pass': '192.0.2.217', 'http_pass': '192.0.2.218'}

# An always-up (No check) record co-located with a monitored one. Under "all down = all up" a sole down record is
# served as a last resort, so a live sibling is needed for the health filter to actually drop the down record.
_SIBLING = '192.0.2.250'

# Targets for the optional-field checks live inside the container: the DNS backend on :8080 (200 + a JSON body
# containing 'hostmaster' for the example.com SOA lookup; 404 for any path that is neither /dns nor /admin) and the
# admin HTTPS interface on :443 (self-signed cert; 401 without credentials).
_SOA_URL = 'http://127.0.0.1:8080/dns/lookup/example.com./SOA'
_NOTFOUND_URL = 'http://127.0.0.1:8080/nope'
_ADMIN_URL = 'https://127.0.0.1:443/admin/'


# helpers

def _name(key: str) -> str:
    """Relative record name (stored on the rrset) for a health-check test record."""
    return f'hc-{key.replace("_", "-")}'


def _fqdn(key: str) -> str:
    """The FQDN to query the DNS backend with (the relative name plus the example.com zone)."""
    return f'{_name(key)}.example.com'


def _make_monitor(w2ui: W2UIClient, name: str, monitor_json: str) -> int:
    w2ui.save('monitors', monitor=name, monitor_json=monitor_json)
    recid = w2ui.find_recid('monitors', monitor=name)
    assert recid is not None, f'monitor {name} not created'
    return recid


def _make_record(
        w2ui: W2UIClient, base_record: dict[str, Any], name: str, content: str, monitor: str, **overrides: Any) -> int:
    w2ui.save('records', **{**base_record, 'name': name, 'content': content,
                            'monitor': monitor, **overrides})
    recid = w2ui.find_recid('records', name=name, content=content)
    assert recid is not None, f'record {name}/{content} not created'
    return recid


def _status_of(w2ui: W2UIClient, content: str) -> dict[str, Any] | None:
    return next((r for r in w2ui.records('status') if r['content'] == content), None)


def _create_monitor(w2ui: W2UIClient, key: str) -> int:
    return _make_monitor(w2ui, _NAMES[key], json.dumps(_SPECS[key]))


def _create_record(w2ui: W2UIClient, base_record: dict[str, Any], key: str) -> int:
    return _make_record(w2ui, base_record, _name(key), _IPS[key], _NAMES[key])


def _add_up_sibling(w2ui: W2UIClient, base_record: dict[str, Any], name: str,
                    cleanup: list[tuple[str, int]]) -> None:
    """Add an always-up (No check) sibling at the same name so a down record is dropped, not all-up served."""
    cleanup.append(('records', _make_record(w2ui, base_record, name, _SIBLING, 'No check')))


# exec monitor: full fall-and-rise lifecycle

def test_exec_fail_marks_down_and_no_check_recovers(
        w2ui: W2UIClient, dns: DNSClient, base_record: dict[str, Any], cleanup: list[tuple[str, int]]) -> None:
    """/bin/false marks a record Off after the fall threshold.

    Switching to No check restores On within one MonitorManager cycle.
    """
    key = 'exec_fail'
    name = _name(key)
    cleanup.append(('monitors', _create_monitor(w2ui, key)))
    rec_recid = _create_record(w2ui, base_record, key)
    cleanup.append(('records', rec_recid))
    _add_up_sibling(w2ui, base_record, name, cleanup)  # live sibling so the down record is dropped, not all-up served

    time.sleep(_FAIL_WAIT)

    st = _status_of(w2ui, _IPS[key])
    assert st is not None, 'record not found in status'
    assert st['status'] == 'Off', f'expected Off, got {st["status"]}'
    assert st['style'] == 'color: red'
    # the down record is dropped from the live answer; only the healthy sibling is served
    assert {r['content'] for r in dns.lookup(_fqdn(key))} == {_SIBLING}

    w2ui.save('records', recid=rec_recid,
              **{**base_record, 'name': name, 'content': _IPS[key], 'monitor': 'No check'})
    time.sleep(_RISE_WAIT)

    st = _status_of(w2ui, _IPS[key])
    assert st is not None
    assert st['status'] == 'On', f'expected On after recovery, got {st["status"]}'
    assert st['style'] == 'color: green'
    # recovered: both the record and its sibling are served
    assert {r['content'] for r in dns.lookup(_fqdn(key))} == {_IPS[key], _SIBLING}


# exec monitor: /bin/true keeps record On

def test_exec_pass_keeps_record_on(
        w2ui: W2UIClient, dns: DNSClient, base_record: dict[str, Any], cleanup: list[tuple[str, int]]) -> None:
    key = 'exec_pass'
    cleanup.append(('monitors', _create_monitor(w2ui, key)))
    cleanup.append(('records', _create_record(w2ui, base_record, key)))

    time.sleep(_FAIL_WAIT)

    st = _status_of(w2ui, _IPS[key])
    assert st is not None
    assert st['status'] == 'On'
    result = dns.lookup(_fqdn(key))
    assert len(result) == 1 and result[0]['content'] == _IPS[key]


# tcp / icmp / http monitors against a closed port or unreachable IP mark the record down

@pytest.mark.parametrize('key', ['tcp_fail', 'icmp_fail', 'http_fail'])
def test_monitor_type_marks_down(
        w2ui: W2UIClient, dns: DNSClient, base_record: dict[str, Any],
        cleanup: list[tuple[str, int]], key: str) -> None:
    cleanup.append(('monitors', _create_monitor(w2ui, key)))
    cleanup.append(('records', _create_record(w2ui, base_record, key)))
    _add_up_sibling(w2ui, base_record, _name(key), cleanup)

    time.sleep(_FAIL_WAIT)

    st = _status_of(w2ui, _IPS[key])
    assert st is not None and st['status'] == 'Off', f'{key}: {st}'
    # the down record is dropped from the live answer; only the healthy sibling remains
    contents = {r['content'] for r in dns.lookup(_fqdn(key))}
    assert _IPS[key] not in contents and contents == {_SIBLING}


# icmp/tcp/http monitors against reachable targets keep their records On, confirming raw ICMP
# sockets (CAP_NET_RAW) and outbound TCP/HTTP work under the systemd sandbox. Batched into one wait.

def test_reachable_monitors_stay_up(
        w2ui: W2UIClient, dns: DNSClient, base_record: dict[str, Any], cleanup: list[tuple[str, int]]) -> None:
    keys = ['icmp_pass', 'tcp_pass', 'http_pass']
    for key in keys:
        cleanup.append(('monitors', _create_monitor(w2ui, key)))
        cleanup.append(('records', _create_record(w2ui, base_record, key)))

    time.sleep(_FAIL_WAIT)

    for key in keys:
        st = _status_of(w2ui, _IPS[key])
        assert st is not None and st['status'] == 'On', f'{key} should stay On: {st}'
        result = dns.lookup(_fqdn(key))
        assert len(result) == 1 and result[0]['content'] == _IPS[key], f'{key}: {result}'


# MonitorManager resilience to malformed monitors + ${content} interpolation

def test_admin_rejects_bad_configs_and_interpolation_runs(
        w2ui: W2UIClient, dns: DNSClient, base_record: dict[str, Any], cleanup: list[tuple[str, int]]) -> None:
    """The admin validates a monitor before saving it, so a malformed config never reaches the monitor loop.

    Three broken monitors - an unknown ${host} token (left literal, so an invalid IP), a missing parameter, and a
    non-mapping JSON body - are each rejected at save and never created. A valid monitor that uses ${content}
    against a closed port still marks its record Off, proving token substitution works end to end and the monitor
    loop is running.
    """
    # (monitor name, raw monitor_json) - each rejected at save time, so no row is created
    broken = [
        ('HC Bad Interp', json.dumps({**_T, 'type': 'tcp', 'ip': '${host}', 'port': 80})),
        ('HC Missing Param', json.dumps({**_T, 'type': 'tcp', 'ip': '127.0.0.1'})),  # tcp requires 'port'
        ('HC Non Mapping', '[1, 2, 3]'),
    ]
    for mon_name, mon_json in broken:
        assert w2ui.save('monitors', monitor=mon_name, monitor_json=mon_json).json()['status'] == 'error'
        assert w2ui.find_recid('monitors', monitor=mon_name) is None, f'{mon_name} should not be created'

    # valid interpolating monitor -> record must go Off
    good_content = '127.0.0.1'
    cleanup.append(('monitors', _make_monitor(
        w2ui, 'HC Interp TCP',
        json.dumps({**_T, 'type': 'tcp', 'ip': '${content}', 'port': 19999}))))
    cleanup.append(('records', _make_record(w2ui, base_record, 'hc-interp',
                                            good_content, 'HC Interp TCP')))
    _add_up_sibling(w2ui, base_record, 'hc-interp', cleanup)

    time.sleep(_FAIL_WAIT)

    # interpolation worked and the thread is alive: this record is Off
    st = _status_of(w2ui, good_content)
    assert st is not None and st['status'] == 'Off', f'interp record: {st}'
    # the down record is dropped from the live answer; only the healthy sibling remains
    contents = {r['content'] for r in dns.lookup('hc-interp.example.com')}
    assert good_content not in contents and contents == {_SIBLING}


# literal '%' in monitor_json no longer drops the monitor (was silently skipped under %-formatting)

def test_monitor_with_literal_percent_runs(
        w2ui: W2UIClient, dns: DNSClient, base_record: dict[str, Any], cleanup: list[tuple[str, int]]) -> None:
    """A monitor whose value carries a literal '%' parses, runs, and marks the record Off.

    The monitor is a failing exec ending in '# 100%'. A %-formatting parser would raise during interpolation and
    silently drop the check, leaving the record permanently On regardless of health.
    """
    content = '192.0.2.234'
    cleanup.append(('monitors', _make_monitor(
        w2ui, 'HC Literal Percent',
        json.dumps({**_T, 'type': 'exec', 'args': ['/bin/sh', '-c', 'exit 1 # 100%']}))))
    cleanup.append(('records', _make_record(w2ui, base_record, 'hc-literal-pct',
                                            content, 'HC Literal Percent')))
    _add_up_sibling(w2ui, base_record, 'hc-literal-pct', cleanup)

    time.sleep(_FAIL_WAIT)

    st = _status_of(w2ui, content)
    assert st is not None and st['status'] == 'Off', f'literal-% monitor should run and mark Off: {st}'
    # the down record is dropped from the live answer; only the healthy sibling remains
    contents = {r['content'] for r in dns.lookup('hc-literal-pct.example.com')}
    assert content not in contents and contents == {_SIBLING}


# real health-driven 'all down = all up': every record down -> the highest-weight tier is served as last resort

def test_real_health_all_down_keeps_all(
        w2ui: W2UIClient, dns: DNSClient, base_record: dict[str, Any], cleanup: list[tuple[str, int]]) -> None:
    """When every record for a name is down, the routing policy keeps them all and picks the highest-weight tier.

    The 'all down = all up' rule replaces the old fallback flag: with no live record the down records are
    reactivated and round-robin answers the highest-weight tier (the primary), so DNS never fails entirely.
    """
    name = 'hc-alldown'
    fqdn = f'{name}.example.com'
    lo_c, mid_c, hi_c = '192.0.2.240', '192.0.2.241', '192.0.2.242'

    cleanup.append(('monitors', _make_monitor(
        w2ui, 'HC AllDown Fail', json.dumps({**_T, 'type': 'exec', 'args': ['/bin/false']}))))
    cleanup.append(('records', _make_record(w2ui, base_record, name, lo_c, 'HC AllDown Fail', weight=0)))
    cleanup.append(('records', _make_record(w2ui, base_record, name, mid_c, 'HC AllDown Fail', weight=5)))
    cleanup.append(('records', _make_record(w2ui, base_record, name, hi_c, 'HC AllDown Fail', weight=10)))

    time.sleep(_FAIL_WAIT)

    # all three records are down
    for content in (lo_c, mid_c, hi_c):
        st = _status_of(w2ui, content)
        assert st is not None and st['status'] == 'Off', f'{content}: {st}'

    # live set empty -> keep-all -> only the highest-weight tier is answered
    result = dns.lookup(fqdn)
    assert len(result) == 1, result
    assert result[0]['content'] == hi_c
    contents = {r['content'] for r in result}
    assert lo_c not in contents and mid_c not in contents


# optional check fields drive the verdict end to end (admin -> DB -> MonitorManager -> Check -> status set).
# Field semantics are covered by the unit tests; these assert that a field set via the admin API survives the
# JSON round-trip and flips the verdict in both directions.

def _run_field_cases(
        w2ui: W2UIClient, base_record: dict[str, Any], cleanup: list[tuple[str, int]],
        cases: dict[str, tuple[dict[str, Any], str, str]]) -> dict[str, dict[str, Any] | None]:
    """Create a monitor + record for every case, wait one fall cycle, and return each status row keyed by case id.

    Each case value is (monitor spec, record content/IP, expected status).
    """
    for key, (spec, content, _expected) in cases.items():
        name = f'HC Field {key}'
        record_name = f'field-{key.replace("_", "-")}.example.com'
        cleanup.append(('monitors', _make_monitor(w2ui, name, json.dumps(spec))))
        cleanup.append(('records', _make_record(w2ui, base_record, record_name, content, name)))

    time.sleep(_FAIL_WAIT)

    return {key: _status_of(w2ui, content) for key, (_spec, content, _expected) in cases.items()}


def test_http_optional_fields(
        w2ui: W2UIClient, base_record: dict[str, Any], cleanup: list[tuple[str, int]]) -> None:
    """HttpCheck optional fields each flip the verdict end to end.

      body_match      - regex against the GET body (a SOA token matches; a bogus token misses -> Off)
      expected_status - status spec match (a 404 path matches "404"; the 200 SOA lookup mismatches "404" -> Off)
      tls_verify      - False trusts the self-signed admin cert and matches its 401; True fails verification -> Off
    """
    cases: dict[str, tuple[dict[str, Any], str, str]] = {
        'http_body_pass':
            ({**_T, 'type': 'http', 'url': _SOA_URL, 'body_match': 'hostmaster'}, '192.0.2.50', 'On'),
        'http_body_fail':
            ({**_T, 'type': 'http', 'url': _SOA_URL, 'body_match': 'no-such-token'}, '192.0.2.51', 'Off'),
        'http_status_pass':
            ({**_T, 'type': 'http', 'url': _NOTFOUND_URL, 'expected_status': '404'}, '192.0.2.52', 'On'),
        'http_status_fail':
            ({**_T, 'type': 'http', 'url': _SOA_URL, 'expected_status': '404'}, '192.0.2.53', 'Off'),
        'http_tls_noverify_pass':
            ({**_T, 'type': 'http', 'url': _ADMIN_URL, 'tls_verify': False, 'expected_status': '401'},
             '192.0.2.54', 'On'),
        'http_tls_verify_fail':
            ({**_T, 'type': 'http', 'url': _ADMIN_URL, 'tls_verify': True, 'expected_status': '401'},
             '192.0.2.55', 'Off'),
    }

    statuses = _run_field_cases(w2ui, base_record, cleanup, cases)

    for key, (_spec, _content, expected) in cases.items():
        st = statuses[key]
        assert st is not None and st['status'] == expected, f'{key}: {st}'


def test_tls_optional_fields(
        w2ui: W2UIClient, base_record: dict[str, Any], cleanup: list[tuple[str, int]]) -> None:
    """TlsCheck drives the verdict end to end against the in-container :443 and :8080 endpoints.

      tls_verify False - trusts the self-signed admin cert; the handshake completes -> On
      tls_verify True  - the self-signed admin cert is untrusted; verification fails -> Off
      closed port      - connection refused -> Off
      plaintext :8080  - the port speaks HTTP, not TLS, so the handshake fails -> Off (stronger than tcp_pass)
      host/SNI         - survives the JSON round-trip without breaking the handshake (verify off) -> On
    """
    cases: dict[str, tuple[dict[str, Any], str, str]] = {
        'tls_noverify_pass':
            ({**_T, 'type': 'tls', 'ip': '127.0.0.1', 'port': 443, 'tls_verify': False}, '192.0.2.70', 'On'),
        'tls_verify_fail':
            ({**_T, 'type': 'tls', 'ip': '127.0.0.1', 'port': 443, 'tls_verify': True}, '192.0.2.71', 'Off'),
        'tls_closed_fail':
            ({**_T, 'type': 'tls', 'ip': '127.0.0.1', 'port': 19999}, '192.0.2.72', 'Off'),
        'tls_plaintext_fail':
            ({**_T, 'type': 'tls', 'ip': '127.0.0.1', 'port': 8080, 'tls_verify': False}, '192.0.2.73', 'Off'),
        'tls_host_noverify_pass':
            ({**_T, 'type': 'tls', 'ip': '127.0.0.1', 'port': 443, 'tls_verify': False, 'host': 'powergslb'},
             '192.0.2.74', 'On'),
    }

    statuses = _run_field_cases(w2ui, base_record, cleanup, cases)

    for key, (_spec, _content, expected) in cases.items():
        st = statuses[key]
        assert st is not None and st['status'] == expected, f'{key}: {st}'


def test_exec_optional_fields(
        w2ui: W2UIClient, base_record: dict[str, Any], cleanup: list[tuple[str, int]]) -> None:
    """ExecCheck optional fields each flip the verdict end to end.

      expected_code  - a non-zero exit (3) is healthy when matched; the default 0 makes the same command Off
      output_match   - regex against captured output (matches the echoed token; a bogus token misses -> Off)
      redirect_error - True folds stderr into stdout so output_match sees it; False hides it -> Off
    """
    cases: dict[str, tuple[dict[str, Any], str, str]] = {
        'exec_code_pass':
            ({**_T, 'type': 'exec', 'args': ['/bin/sh', '-c', 'exit 3'], 'expected_code': 3}, '192.0.2.60', 'On'),
        'exec_code_fail':
            ({**_T, 'type': 'exec', 'args': ['/bin/sh', '-c', 'exit 3']}, '192.0.2.61', 'Off'),
        'exec_output_pass':
            ({**_T, 'type': 'exec', 'args': ['/bin/echo', 'healthy'], 'output_match': 'healthy'},
             '192.0.2.62', 'On'),
        'exec_output_fail':
            ({**_T, 'type': 'exec', 'args': ['/bin/echo', 'healthy'], 'output_match': 'no-such-token'},
             '192.0.2.63', 'Off'),
        'exec_stderr_redirect_pass':
            ({**_T, 'type': 'exec', 'args': ['/bin/sh', '-c', 'echo oops 1>&2'],
              'output_match': 'oops', 'redirect_error': True}, '192.0.2.64', 'On'),
        'exec_stderr_no_redirect_fail':
            ({**_T, 'type': 'exec', 'args': ['/bin/sh', '-c', 'echo oops 1>&2'],
              'output_match': 'oops', 'redirect_error': False}, '192.0.2.65', 'Off'),
    }

    statuses = _run_field_cases(w2ui, base_record, cleanup, cases)

    for key, (_spec, _content, expected) in cases.items():
        st = statuses[key]
        assert st is not None and st['status'] == expected, f'{key}: {st}'
