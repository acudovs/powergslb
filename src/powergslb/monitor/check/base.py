"""Check base class, validated field types, and the subclass registry."""

import abc
import dataclasses
import logging
import re
from dataclasses import MISSING, dataclass
from typing import Annotated, Any, ClassVar, get_args, get_origin, get_type_hints

import netaddr

from powergslb.system.config import coerce_env

__all__ = ['Check', 'IPAddress', 'Port', 'Positive', 'Regex']


def _validate_port(value: int) -> None:
    """Reject a TCP/UDP port outside the 1..65535 range.

    :param value: The port number to validate.
    :raises ValueError: When the value is out of range.
    """
    if not 1 <= value <= 65535:
        raise ValueError('out of range')


def _validate_positive(value: int) -> None:
    """Reject a non-positive count/duration (interval, timeout, fall, rise must be >= 1).

    :param value: The count or duration to validate.
    :raises ValueError: When the value is not positive.
    """
    if value < 1:
        raise ValueError('not positive')


# Reusable field types: the Check validates any field annotated with one by running its callable metadata.
IPAddress = Annotated[str, netaddr.IPAddress]
Port = Annotated[int, _validate_port]
Positive = Annotated[int, _validate_positive]
Regex = Annotated[str, re.compile]


@dataclass(kw_only=True)
class Check(abc.ABC):
    """Base health check: shared parameters, validation, and the subclass registry.

    Concrete subclasses set a 'name' class attribute (the monitor 'type' token), declare their own parameter fields,
    and implement 'execute()'. Each subclass registers itself by name, so 'create()' can build the right check from
    parsed monitor JSON.

    :param interval: Seconds between check runs.
    :param timeout: Per-run time budget in seconds; capped at an interval.
    :param fall: Consecutive failures before the content is marked down.
    :param rise: Consecutive successes before the content is marked up.
    """
    _registry: ClassVar[dict[str, type['Check']]] = {}
    name: ClassVar[str]
    skip: ClassVar[bool] = False

    interval: Positive = 3
    timeout: Positive = 1
    fall: Positive = 3
    rise: Positive = 5

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Register the subclass in the type registry under its name token; reject duplicate names.

        :raises ValueError: When another subclass already registered the name.
        """
        super().__init_subclass__(**kwargs)
        if cls.name in Check._registry:
            raise ValueError(f"duplicate check type '{cls.name}'")
        Check._registry[cls.name] = cls

    @classmethod
    def _config_options(cls) -> set[str]:
        """Return the names of this subclass's own, non-private ClassVar attributes (the operator-tunable options).

        :returns: The tunable option names.
        """
        hints = get_type_hints(cls)
        return {name for name in vars(cls).get('__annotations__', {})
                if not name.startswith('_') and get_origin(hints.get(name)) is ClassVar}

    @classmethod
    def configure(cls, options: dict[str, Any]) -> None:
        """Apply <type>_<option> values from config to the matching Check subclass ClassVars.

        A ClassVar keeps its in-code default when its key is absent; a present value is coerced to the default's type.
        An option that matches no <type>_<option> of any registered check is logged as a warning (likely a typo).

        :param options: The [monitor] config options.
        """
        consumed: set[str] = set()
        for type_name, subclass in cls._registry.items():
            for option in subclass._config_options():  # pylint: disable=protected-access
                key = f'{type_name}_{option}'
                if key in options:
                    setattr(subclass, option, coerce_env(options[key], getattr(subclass, option), key))
                    consumed.add(key)

        for key in options.keys() - consumed:
            logging.warning("unknown [monitor] option '%s'", key)

    def __post_init__(self) -> None:
        """Validate every field against its annotation and Annotated metadata; cap timeout at an interval.

        :raises ValueError: When a field value has the wrong type or fails a validator.
        """
        type_hints = get_type_hints(type(self), include_extras=True)
        for field in dataclasses.fields(self):
            value = getattr(self, field.name)
            hint = type_hints[field.name]
            validators = getattr(hint, '__metadata__', ())
            expected = get_args(hint)[0] if validators else hint
            # A subscript generic (list[str]) checks against its origin (list); a bare type checks against itself.
            check_type = get_origin(expected) or expected
            # bool is a subclass of int, so reject it explicitly for int fields (e.g. port=True).
            if not isinstance(value, check_type) or (check_type is int and isinstance(value, bool)):
                raise ValueError(f"check parameter '{field.name}' invalid")
            for validate in validators:
                if not callable(validate):
                    continue  # non-callable Annotated metadata (docs/markers) is not a validator
                try:
                    validate(value)
                except Exception as e:
                    raise ValueError(f"check parameter '{field.name}' invalid") from e
        if self.timeout > self.interval:
            logging.warning("%s check timeout %s is greater than interval %s: capped to interval",
                            self.name, self.timeout, self.interval)
            self.timeout = self.interval

    @classmethod
    def create(cls, check_spec: dict[str, Any]) -> 'Check':
        """Build a Check from a check spec.

        :param check_spec: Parsed monitor JSON; 'type' picks the subclass, the rest are its field values.
        :returns: The constructed check.
        :raises ValueError: When the type is unknown or parameters are missing, unexpected, or invalid.
        """
        monitor_type = check_spec.get('type')
        if not isinstance(monitor_type, str):
            raise ValueError("check parameter 'type' invalid")

        subclass = cls._registry.get(monitor_type)
        if subclass is None:
            raise ValueError(f"unknown check type '{monitor_type}'")

        params = {key: value for key, value in check_spec.items() if key != 'type'}
        all_fields = {field.name for field in dataclasses.fields(subclass)}
        required = {field.name for field in dataclasses.fields(subclass)
                    if field.default is MISSING and field.default_factory is MISSING}
        missing = required - params.keys()
        if missing:
            raise ValueError(f"missing check parameters: {', '.join(sorted(missing))}")
        unexpected = params.keys() - all_fields
        if unexpected:
            raise ValueError(f"unexpected check parameters: {', '.join(sorted(unexpected))}")

        return subclass(**params)  # type: ignore[abstract]

    @abc.abstractmethod
    def execute(self) -> bool:
        """Run the check once; return True when the target is healthy.

        Subclasses MUST never block indefinitely and return within roughly self.timeout seconds.

        :returns: True when the target is healthy.
        """
