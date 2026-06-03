# pylint: disable=missing-function-docstring, redefined-outer-name

"""Tests for the TOML configuration.

Typed value access, whole-section items(), and POWERGSLB_<SECTION>_<OPTION> environment overrides coerced to the
configured type.
"""

from pathlib import Path

import pytest

from powergslb.system.config import Config

_CONFIG = """\
[database]
database = "powergslb"
user = "powergslb"
password = "12345"
host = "127.0.0.1"
port = 3306
connection_timeout = 1.5

[logging]
format = "%(levelname)s: %(threadName)s: %(message)s"
level = "DEBUG"

[monitor]
update_interval = 60

[admin]
address = "0.0.0.0"
port = 443
ssl = true
ciphers = "ECDHE-RSA-AES256-GCM-SHA384"
"""


@pytest.fixture
def config_file(tmp_path: Path) -> str:
    path = tmp_path / 'powergslb.toml'
    path.write_text(_CONFIG)
    return str(path)


@pytest.fixture
def config(config_file: str) -> Config:
    return Config(config_file)


# A second file overrides one option, adds an option to an existing section, and adds a new section.
_OVERRIDE = """\
[database]
host = "10.0.0.2"
charset = "utf8mb4"

[cache]
enabled = true
ttl = 30
"""


@pytest.fixture
def override_file(tmp_path: Path) -> str:
    path = tmp_path / 'override.toml'
    path.write_text(_OVERRIDE)
    return str(path)


@pytest.fixture
def merged_config(config_file: str, override_file: str) -> Config:
    return Config([config_file, override_file])


# typed value access (native TOML types, no coercion)

def test_get_int(config: Config) -> None:
    assert config.get('database', 'port') == 3306
    assert isinstance(config.get('database', 'port'), int)


def test_get_bool(config: Config) -> None:
    assert config.get('admin', 'ssl') is True


def test_get_plain_string(config: Config) -> None:
    assert config.get('logging', 'level') == 'DEBUG'
    assert config.get('admin', 'ciphers') == 'ECDHE-RSA-AES256-GCM-SHA384'


def test_get_format_string(config: Config) -> None:
    assert config.get('logging', 'format') == '%(levelname)s: %(threadName)s: %(message)s'


def test_get_numeric_password_stays_string(config: Config) -> None:
    # #4 fixed: a quoted TOML string is never coerced to int
    assert config.get('database', 'password') == '12345'
    assert isinstance(config.get('database', 'password'), str)


def test_get_missing_option_returns_none(config: Config) -> None:
    assert config.get('database', 'no_such_option') is None


# environment overrides (coerced to the configured value's type)

def test_env_override_string(config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('POWERGSLB_DATABASE_HOST', '10.0.0.1')
    assert config.get('database', 'host') == '10.0.0.1'


def test_env_override_int(config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('POWERGSLB_ADMIN_PORT', '9090')
    assert config.get('admin', 'port') == 9090
    assert isinstance(config.get('admin', 'port'), int)


def test_env_override_bool(config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('POWERGSLB_ADMIN_SSL', 'false')
    assert config.get('admin', 'ssl') is False
    monkeypatch.setenv('POWERGSLB_ADMIN_SSL', 'on')
    assert config.get('admin', 'ssl') is True


def test_env_override_float(config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('POWERGSLB_DATABASE_CONNECTION_TIMEOUT', '2.5')
    assert config.get('database', 'connection_timeout') == 2.5
    assert isinstance(config.get('database', 'connection_timeout'), float)


def test_env_password_override_stays_string(config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('POWERGSLB_DATABASE_PASSWORD', '99999')
    assert config.get('database', 'password') == '99999'
    assert isinstance(config.get('database', 'password'), str)


def test_env_override_invalid_value_raises(config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('POWERGSLB_ADMIN_PORT', 'abc')
    with pytest.raises(ValueError, match=r'POWERGSLB_ADMIN_PORT value .* is not a valid int'):
        config.get('admin', 'port')


# default parameter (get(section, option, default=...))

def test_default_returned_when_option_absent(config: Config) -> None:
    assert config.get('monitor', 'icmp_privileged', default=True) is True


def test_default_ignored_when_option_present(config: Config) -> None:
    # the file value wins over the default
    assert config.get('admin', 'ssl', default=False) is True
    assert config.get('admin', 'port', default=1) == 443


def test_default_none_matches_no_default(config: Config) -> None:
    assert config.get('monitor', 'icmp_privileged') is None
    assert config.get('monitor', 'icmp_privileged', default=None) is None


def test_env_override_coerced_to_default_type_when_option_absent(
        config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    # Regression: with the option absent from the file, the env override must be coerced to the
    # default's type. A bool default keeps a 'false' override a bool False (was the string 'false').
    monkeypatch.setenv('POWERGSLB_MONITOR_ICMP_PRIVILEGED', 'false')
    assert config.get('monitor', 'icmp_privileged', default=True) is False
    monkeypatch.setenv('POWERGSLB_MONITOR_ICMP_PRIVILEGED', 'true')
    assert config.get('monitor', 'icmp_privileged', default=True) is True


def test_env_override_int_default_when_option_absent(config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('POWERGSLB_MONITOR_RETRIES', '5')
    value = config.get('monitor', 'retries', default=0)
    assert value == 5
    assert isinstance(value, int)


def test_env_override_without_default_stays_string_when_option_absent(
        config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    # No default means no type to coerce to, so an env-only override stays the raw string.
    monkeypatch.setenv('POWERGSLB_MONITOR_ICMP_PRIVILEGED', 'false')
    assert config.get('monitor', 'icmp_privileged') == 'false'


# whole-section items()

def test_items_returns_full_section(config: Config) -> None:
    database = config.items('database')
    assert database['database'] == 'powergslb'
    assert database['port'] == 3306
    assert database['connection_timeout'] == 1.5
    assert set(database) == {'database', 'user', 'password', 'host', 'port', 'connection_timeout'}


def test_items_reflects_env_override(config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('POWERGSLB_ADMIN_PORT', '8443')
    assert config.items('admin')['port'] == 8443


def test_items_includes_env_only_option_absent_from_toml(config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    # An override for a key the TOML section does not define must still surface via items(), since the section
    # is splatted as **kwargs (e.g. into mysql.connector). unix_socket is absent from the test [database] section.
    assert 'unix_socket' not in config.items('database')
    monkeypatch.setenv('POWERGSLB_DATABASE_UNIX_SOCKET', '/var/lib/mysql/mysql.sock')
    assert config.items('database')['unix_socket'] == '/var/lib/mysql/mysql.sock'


def test_items_get_coerces_env_override_against_default_when_option_absent(
        config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    # Regression (finding 2): the server/admin consumers read typed scalars via section.get(option, default).
    # keep_alive_timeout is absent from the [admin] section, so an env-only override must coerce to the
    # default's type instead of staying the raw string (which would break socket.settimeout).
    monkeypatch.setenv('POWERGSLB_ADMIN_KEEP_ALIVE_TIMEOUT', '45')
    value = config.items('admin').get('keep_alive_timeout', 30)
    assert value == 45
    assert isinstance(value, int)


def test_items_get_coerces_env_override_to_bool_when_option_absent(
        config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    # ssl is absent from the [server] section; a 'false' override must become bool False, not the truthy
    # string 'false' (which would silently enable TLS).
    monkeypatch.setenv('POWERGSLB_SERVER_SSL', 'false')
    assert config.items('server').get('ssl', False) is False
    monkeypatch.setenv('POWERGSLB_SERVER_SSL', 'on')
    assert config.items('server').get('ssl', False) is True


def test_items_get_uses_default_when_option_absent_and_no_env(config: Config) -> None:
    # No file value and no env override: section.get returns the default unchanged.
    assert config.items('admin').get('keep_alive_timeout', 30) == 30


def test_items_pop_coerces_env_only_override_to_default_type(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Regression: when update_interval is absent from [monitor] and supplied only via the environment, pop must
    # coerce it to the default's int type instead of leaving the raw string. MonitorManager does
    # 'update_interval < 1', which would TypeError on a str.
    path = tmp_path / 'powergslb.toml'
    path.write_text('[monitor]\n')
    config = Config(str(path))
    monkeypatch.setenv('POWERGSLB_MONITOR_UPDATE_INTERVAL', '30')
    value = config.items('monitor').pop('update_interval', 60)
    assert value == 30
    assert isinstance(value, int)
    assert 'update_interval' in config.items('monitor')


def test_items_pop_coerces_env_override_against_present_option(
        config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    # The option is present in [monitor]; an env override is coerced to its type and the key is removed.
    monkeypatch.setenv('POWERGSLB_MONITOR_UPDATE_INTERVAL', '15')
    section = config.items('monitor')
    assert section.pop('update_interval', 60) == 15
    assert 'update_interval' not in section


def test_items_pop_uses_default_when_option_absent_and_no_env(config: Config) -> None:
    # An option absent from the section with no env override: section.pop returns the default unchanged.
    assert config.items('monitor').pop('absent_option', 60) == 60


# multiple config files (later file merges into earlier, per option)

def test_multi_file_overrides_existing_option(merged_config: Config) -> None:
    assert merged_config.get('database', 'host') == '10.0.0.2'


def test_multi_file_preserves_unoverridden_options(merged_config: Config) -> None:
    # options only present in the first file survive the merge
    assert merged_config.get('database', 'user') == 'powergslb'
    assert merged_config.get('database', 'port') == 3306
    assert merged_config.get('admin', 'ssl') is True


def test_multi_file_adds_option_to_existing_section(merged_config: Config) -> None:
    assert merged_config.get('database', 'charset') == 'utf8mb4'
    # the added option sits alongside the first file's options, with host overridden
    assert merged_config.items('database') == {
        'database': 'powergslb', 'user': 'powergslb', 'password': '12345', 'host': '10.0.0.2',
        'port': 3306, 'connection_timeout': 1.5, 'charset': 'utf8mb4',
    }


def test_multi_file_adds_new_section(merged_config: Config) -> None:
    assert merged_config.get('cache', 'enabled') is True
    assert merged_config.get('cache', 'ttl') == 30
    assert set(merged_config.items('cache')) == {'enabled', 'ttl'}


def test_multi_file_env_override_beats_both_files(merged_config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    # precedence: environment > later file > earlier file
    monkeypatch.setenv('POWERGSLB_DATABASE_HOST', '10.9.9.9')
    assert merged_config.get('database', 'host') == '10.9.9.9'
