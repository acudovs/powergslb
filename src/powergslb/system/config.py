"""TOML configuration with environment overrides."""

import os
import tomllib
from typing import Any

__all__ = ['Config', 'coerce_env']


def coerce_env(value: Any, current: Any, name: str = '') -> Any:
    """Coerce a raw string value to the type of the existing typed value.

    An already-typed value (e.g. from TOML) and a string matching the current type are returned unchanged; a None
    current has no type to coerce to, so the raw string stays a string.

    :param value: Raw value, usually a POWERGSLB_* environment override string.
    :param current: Existing typed value supplying the target type.
    :param name: Option label used in the error message.
    :returns: The coerced value, or unchanged value when no coercion applies.
    :raises ValueError: When the string cannot be coerced.
    """
    if isinstance(value, str) and not isinstance(current, str):
        if isinstance(current, bool):
            return value.strip().lower() in ('1', 'true', 'yes', 'on')
        if current is not None:
            try:
                return type(current)(value)  # int / float / ... from the string
            except (ValueError, TypeError) as e:
                label = f'{name} value' if name else 'value'
                raise ValueError(f'{label} {value!r} is not a valid {type(current).__name__}') from e
    return value


class Config:
    """TOML configuration with POWERGSLB_<SECTION>_<OPTION> environment overrides.

    TOML is natively typed, so no value coercion is needed: ports and timeouts are ints, ssl is a bool, and strings
    (quoted in TOML) stay strings, so a numeric-looking password is never turned into an int. Environment overrides
    are coerced to the configured value's type, so pass-through options (e.g. mysql.connector kwargs) keep their types.

    :param files: Path or list of paths to TOML files; later files override earlier ones per option.
    """

    def __init__(self, files: str | list[str]) -> None:
        self._data: dict[str, dict[str, Any]] = {}
        paths = [files] if isinstance(files, str) else list(files)
        for path in paths:
            with open(path, 'rb') as stream:
                for section, options in tomllib.load(stream).items():
                    self._data.setdefault(section, {}).update(options)

    def get(self, section: str, option: str, default: Any = None) -> Any:
        """Return one typed value, honoring an environment override.

        When the option is not in the file, default supplies both the value and the type to coerce to.
        """
        value = self._data.get(section, {}).get(option, default)
        env_key = f'POWERGSLB_{section}_{option}'.upper()
        if env_key in os.environ:
            value = coerce_env(os.environ[env_key], value, env_key)
        return value

    def items(self, section: str) -> dict[str, Any]:
        """Return a whole section as a dict, with environment overrides applied.

        POWERGSLB_<SECTION>_* environment keys are included, so an override can add an option the section
        does not define.
        """
        options = set(self._data.get(section, {}))
        prefix = f'POWERGSLB_{section}_'.upper()
        options.update(env_key[len(prefix):].lower() for env_key in os.environ if env_key.startswith(prefix))
        return _Section({option: self.get(section, option) for option in options}, self, section)


class _Section(dict[str, Any]):
    """A config section whose get() and pop() resolves through Config, so an env override coerces to default's type."""

    def __init__(self, data: dict[str, Any], config: Config, section: str) -> None:
        super().__init__(data)
        self._config = config
        self._section = section

    def get(self, option: str, default: Any = None) -> Any:  # type: ignore[override]
        """Return the option, coercing a POWERGSLB_<SECTION>_<OPTION> override to default's type."""
        return self._config.get(self._section, option, default)

    def pop(self, option: str, default: Any = None) -> Any:  # type: ignore[override]
        """Remove the option and return it, coercing a POWERGSLB_<SECTION>_<OPTION> override to default's type."""
        value = self._config.get(self._section, option, default)
        super().pop(option, default)
        return value
