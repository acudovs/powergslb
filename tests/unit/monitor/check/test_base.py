# pylint: disable=missing-function-docstring, protected-access

"""Tests for the Check base class.

The type registry (__init_subclass__), create() building and validating a check from parsed monitor JSON,
__post_init__ type-checking and the timeout/interval clamp, and configure() applying application-wide ClassVar
options from config.
"""

from dataclasses import dataclass
from typing import Annotated, Any

import pytest

from powergslb.monitor.check.base import Check
from powergslb.monitor.check.http import HttpCheck
from powergslb.monitor.check.icmp import IcmpCheck
from powergslb.monitor.check.none import NoCheck
from powergslb.monitor.check.tcp import TcpCheck


def _tcp(**overrides: Any) -> dict[str, Any]:
    check_spec: dict[str, Any] = {'type': 'tcp', 'ip': '192.0.2.1', 'port': 80,
                                  'interval': 10, 'timeout': 1, 'fall': 2, 'rise': 2}
    check_spec.update(overrides)
    return check_spec


# registry

def test_builtin_types_are_registered() -> None:
    assert {'exec', 'http', 'icmp', 'tcp', 'tls'} <= set(Check._registry)


def test_duplicate_type_name_raises() -> None:
    with pytest.raises(ValueError, match='duplicate check type'):
        class _Dup(Check):  # the 'tcp' token is already registered
            name = 'tcp'

            def execute(self) -> bool:
                return True


# create: success

def test_create_returns_typed_check() -> None:
    check = Check.create(_tcp())
    assert isinstance(check, TcpCheck)
    assert check.ip == '192.0.2.1' and check.port == 80


def test_create_none_type_returns_skipped_check() -> None:
    # "No monitoring" is the registered 'none' type: a real check that MonitorManager never threads (skip is True),
    # buildable with no params of its own.
    check = Check.create({'type': 'none'})
    assert isinstance(check, NoCheck)
    assert check.skip is True


def test_create_omits_base_timing_uses_defaults() -> None:
    # interval/timeout/fall/rise carry base defaults, so a monitor JSON may omit them.
    check = Check.create({'type': 'tcp', 'ip': '192.0.2.1', 'port': 80})
    assert (check.interval, check.timeout, check.fall, check.rise) == (3, 1, 3, 5)


# create: failures

def test_create_missing_type_key_raises() -> None:
    with pytest.raises(ValueError, match="check parameter 'type' invalid"):
        Check.create({'ip': '192.0.2.1'})


def test_create_empty_type_raises() -> None:
    with pytest.raises(ValueError, match="unknown check type ''"):
        Check.create({'type': ''})


def test_create_unknown_type_raises() -> None:
    with pytest.raises(ValueError, match='unknown check type'):
        Check.create({'type': 'smtp', 'interval': 1, 'timeout': 1, 'fall': 1, 'rise': 1})


@pytest.mark.parametrize('monitor_type', [['icmp'], {'icmp': True}, 123, [], 0])
def test_create_non_string_type_raises(monitor_type: Any) -> None:
    # A non-string 'type' is a bad spec and must raise ValueError, never TypeError: callers catch ValueError
    # only, and an escaping TypeError kills the monitor thread (service exit, systemd restart loop).
    with pytest.raises(ValueError, match="check parameter 'type' invalid"):
        Check.create(_tcp(type=monitor_type))


def test_create_missing_params_raises() -> None:
    with pytest.raises(ValueError, match='missing check parameters'):
        Check.create({'type': 'tcp', 'ip': '192.0.2.1'})


def test_create_unexpected_params_raises() -> None:
    with pytest.raises(ValueError, match='unexpected check parameters'):
        Check.create(_tcp(extra='nope'))


def test_create_omits_optional_param_uses_default() -> None:
    check = Check.create({'type': 'http', 'url': 'http://h/',
                          'interval': 10, 'timeout': 1, 'fall': 2, 'rise': 2})
    assert check is not None
    assert check.method == 'GET'  # type: ignore[attr-defined]


def test_create_optional_param_overrides_default() -> None:
    check = Check.create({'type': 'http', 'url': 'http://h/', 'method': 'HEAD',
                          'interval': 10, 'timeout': 1, 'fall': 2, 'rise': 2})
    assert check is not None
    assert check.method == 'HEAD'  # type: ignore[attr-defined]


def test_create_wrong_param_type_raises() -> None:
    with pytest.raises(ValueError, match="check parameter 'port' invalid"):
        Check.create(_tcp(port='eighty'))


def test_create_bool_for_int_param_raises() -> None:
    # bool is a subclass of int, so port=True must be rejected, not silently accepted as 1.
    with pytest.raises(ValueError, match="check parameter 'port' invalid"):
        Check.create(_tcp(port=True))


@pytest.mark.parametrize('port', [0, -1, 65536, 99999])
def test_create_out_of_range_port_raises(port: int) -> None:
    # The base validates a Port field beyond the bare int type-check: out-of-range values are config errors.
    with pytest.raises(ValueError, match="check parameter 'port' invalid"):
        Check.create(_tcp(port=port))


def test_create_in_range_port_accepted() -> None:
    check = Check.create(_tcp(port=65535))
    assert isinstance(check, TcpCheck)
    assert check.port == 65535


@pytest.mark.parametrize('ip', ['', 'not-an-ip', '999.0.2.1', '192.0.2.1/24'])
def test_create_invalid_ip_raises(ip: str) -> None:
    # An IPAddress field rejects anything netaddr.IPAddress cannot parse (IPv4 or IPv6).
    with pytest.raises(ValueError, match="check parameter 'ip' invalid"):
        Check.create(_tcp(ip=ip))


def test_create_ipv6_address_accepted() -> None:
    check = Check.create(_tcp(ip='2001:db8::1'))
    assert isinstance(check, TcpCheck)
    assert check.ip == '2001:db8::1'


# __post_init__ positivity

@pytest.mark.parametrize('field', ['interval', 'timeout', 'fall', 'rise'])
@pytest.mark.parametrize('value', [0, -1])
def test_non_positive_base_field_rejected(field: str, value: int) -> None:
    # interval <= 0 busy-loops the check thread; the rest degenerate the timeout/debounce. All must be >= 1.
    with pytest.raises(ValueError, match=f"check parameter '{field}' invalid"):
        Check.create(_tcp(**{field: value}))


def test_non_callable_metadata_is_skipped() -> None:
    # Non-callable Annotated metadata (docs/markers) must be ignored, not invoked as a validator.
    @dataclass
    class _Doc(Check):
        name = '_doc'

        value: Annotated[int, 'documentation']

        def execute(self) -> bool:
            return True

    try:
        check = _Doc(interval=10, timeout=1, fall=2, rise=2, value=5)
        assert check.value == 5
    finally:
        Check._registry.pop('_doc', None)


def test_subscripted_generic_field_validates_against_origin() -> None:
    # A subscripted generic hint (list[str]) validates against its origin (list), exactly like a bare 'list'.
    # Element types are the subclass's concern, not the base type-check.
    @dataclass
    class _Gen(Check):
        name = '_gen'

        items: list[str]

        def execute(self) -> bool:
            return True

    try:
        check = _Gen(interval=10, timeout=1, fall=2, rise=2, items=['a', 'b'])
        assert check.items == ['a', 'b']
        # only the origin is enforced: a non-list is rejected, but non-string elements are not
        assert _Gen(interval=10, timeout=1, fall=2, rise=2, items=[1, 2]).items == [1, 2]  # type: ignore[list-item]
        with pytest.raises(ValueError, match="check parameter 'items' invalid"):
            _Gen(interval=10, timeout=1, fall=2, rise=2, items='nope')  # type: ignore[arg-type]
    finally:
        Check._registry.pop('_gen', None)


# __post_init__ clamp

def test_timeout_clamped_to_interval() -> None:
    check = Check.create(_tcp(timeout=99, interval=10))
    assert check is not None
    assert check.timeout == 10


def test_timeout_clamp_warning_names_the_check(caplog: pytest.LogCaptureFixture) -> None:
    # The clamp warning must identify which check tripped it, so an operator can find the offending monitor.
    with caplog.at_level('WARNING'):
        Check.create(_tcp(timeout=99, interval=10))
    assert 'tcp' in caplog.text and "timeout" in caplog.text and "interval" in caplog.text


def test_timeout_not_clamped_when_within_interval() -> None:
    check = Check.create(_tcp(timeout=1, interval=10))
    assert check is not None
    assert check.timeout == 1


# configure / _config_options

def test_config_options_discovers_own_classvars() -> None:
    assert IcmpCheck._config_options() == {'privileged'}
    assert HttpCheck._config_options() == {'body_chunk', 'user_agent'}


def test_config_options_excludes_framework_and_private() -> None:
    # 'name' (a framework ClassVar inherited from Check) and '_registry' (private) are never tunable; TcpCheck
    # declares no application-wide options of its own.
    assert TcpCheck._config_options() == set()


def test_configure_sets_matching_classvar(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(IcmpCheck, 'privileged', True)
    monkeypatch.setattr(HttpCheck, 'body_chunk', 65536)
    Check.configure({'icmp_privileged': False, 'http_body_chunk': 1024})
    assert IcmpCheck.privileged is False
    assert HttpCheck.body_chunk == 1024


def test_configure_absent_key_keeps_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(IcmpCheck, 'privileged', True)
    Check.configure({'update_interval': 60})  # not a <type>_<option> key, so nothing matches
    assert IcmpCheck.privileged is True


def test_configure_coerces_env_string_to_default_type(monkeypatch: pytest.MonkeyPatch) -> None:
    # An env-only override arrives as a raw string (Config.items cannot type it without a TOML value).
    monkeypatch.setattr(IcmpCheck, 'privileged', True)
    monkeypatch.setattr(HttpCheck, 'body_chunk', 65536)
    Check.configure({'icmp_privileged': 'false', 'http_body_chunk': '2048'})
    assert IcmpCheck.privileged is False
    assert HttpCheck.body_chunk == 2048


def test_configure_invalid_value_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(HttpCheck, 'body_chunk', 65536)
    with pytest.raises(ValueError, match=r'http_body_chunk value .* is not a valid int'):
        Check.configure({'http_body_chunk': 'abc'})
    assert HttpCheck.body_chunk == 65536  # rejected before any ClassVar is mutated


def test_configure_unknown_option_warns(caplog: pytest.LogCaptureFixture) -> None:
    # An option matching no <type>_<option> of any registered check is almost certainly a typo; warn about it.
    with caplog.at_level('WARNING'):
        Check.configure({'icmp_privileged': True, 'bogus_option': 1})
    assert 'bogus_option' in caplog.text
    assert 'icmp_privileged' not in caplog.text  # a matched option is not reported as unknown
