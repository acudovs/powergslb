"""RoutingPolicy base class, validated field types, the subclass registry, and the cached resolver."""

import abc
import dataclasses
import functools
import json
from collections import defaultdict
from dataclasses import MISSING, dataclass
from typing import Annotated, Any, ClassVar, get_args, get_origin, get_type_hints

from powergslb.client import ClientContext

__all__ = ['IPv4Prefix', 'IPv6Prefix', 'Positive', 'RoutingPolicy']


def _validate_ipv4_prefix(value: int) -> None:
    """Reject an IPv4 prefix length outside the 0..32 range."""
    if not 0 <= value <= 32:
        raise ValueError('out of range')


def _validate_ipv6_prefix(value: int) -> None:
    """Reject an IPv6 prefix length outside the 0..128 range."""
    if not 0 <= value <= 128:
        raise ValueError('out of range')


def _validate_positive(value: int) -> None:
    """Reject a non-positive count (max_answers must be >= 1)."""
    if value < 1:
        raise ValueError('not positive')


# Reusable field types: the policy validates any field annotated with one by running its callable metadata.
IPv4Prefix = Annotated[int, _validate_ipv4_prefix]
IPv6Prefix = Annotated[int, _validate_ipv6_prefix]
Positive = Annotated[int, _validate_positive]


@dataclass(frozen=True, kw_only=True)
class RoutingPolicy(abc.ABC):
    """Base routing policy: shared validation and the subclass registry.

    Concrete subclasses set a 'name' class attribute (the policy 'type' token), declare their own parameter fields,
    and implement 'select()'. Each subclass registers itself by name, so 'create()' can build the right policy from
    parsed policy JSON. The dataclass is frozen so one instance is safe to share across the threaded HTTP server and
    to cache by 'resolve()'.
    """
    _registry: ClassVar[dict[str, type['RoutingPolicy']]] = {}
    name: ClassVar[str]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Register the subclass in the type registry under its name token; reject duplicate names."""
        super().__init_subclass__(**kwargs)
        if cls.name in RoutingPolicy._registry:
            raise ValueError(f"duplicate routing policy type '{cls.name}'")
        RoutingPolicy._registry[cls.name] = cls

    def __post_init__(self) -> None:
        """Validate every field against its annotation and Annotated metadata.

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
            # bool is a subclass of int, so reject it explicitly for int fields (e.g. max_answers=True).
            if not isinstance(value, check_type) or (check_type is int and isinstance(value, bool)):
                raise ValueError(f"routing policy parameter '{field.name}' invalid")
            for validate in validators:
                if not callable(validate):
                    continue  # non-callable Annotated metadata (docs/markers) is not a validator
                try:
                    validate(value)
                except Exception as e:
                    raise ValueError(f"routing policy parameter '{field.name}' invalid") from e

    @classmethod
    def create(cls, policy_spec: dict[str, Any]) -> 'RoutingPolicy':
        """Build a RoutingPolicy from a policy spec.

        :param policy_spec: Parsed policy JSON; 'type' picks the subclass, the rest are its field values.
        :returns: The constructed policy.
        :raises ValueError: When the type is unknown or parameters are missing, unexpected, or invalid.
        """
        policy_type = policy_spec.get('type')
        if not isinstance(policy_type, str):
            raise ValueError("routing policy parameter 'type' invalid")

        subclass = cls._registry.get(policy_type)
        if subclass is None:
            raise ValueError(f"unknown routing policy type '{policy_type}'")

        params = {key: value for key, value in policy_spec.items() if key != 'type'}
        all_fields = {field.name for field in dataclasses.fields(subclass)}
        required = {field.name for field in dataclasses.fields(subclass)
                    if field.default is MISSING and field.default_factory is MISSING}
        missing = required - params.keys()
        if missing:
            raise ValueError(f"missing routing policy parameters: {', '.join(sorted(missing))}")
        unexpected = params.keys() - all_fields
        if unexpected:
            raise ValueError(f"unexpected routing policy parameters: {', '.join(sorted(unexpected))}")

        return subclass(**params)  # type: ignore[abstract]

    @staticmethod
    @functools.lru_cache(maxsize=128)
    def resolve(policy_json: str) -> 'RoutingPolicy':
        """Build (and cache) the RoutingPolicy for a raw policy_json string.

        Distinct policies are few, so the bounded cache effectively never evicts; the frozen policy instances are
        pure value objects and safe to share. Keying on the raw string caches before the parse-and-validate work;
        invalid JSON still raises (lru_cache does not cache exceptions).

        :param policy_json: The rrset's raw policy_json column value.
        :returns: The shared RoutingPolicy instance for that JSON.
        :raises ValueError: When the JSON is malformed, is not a JSON object, or describes an invalid policy.
        """
        policy_spec = json.loads(policy_json)
        if not isinstance(policy_spec, dict):
            raise ValueError('policy_json must be a JSON object')
        return RoutingPolicy.create(policy_spec)

    @staticmethod
    def highest_tier(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Group candidates by integer 'weight' and return the highest-weight group.

        :param candidates: The candidate records, each carrying an integer 'weight'.
        :returns: The records in the highest-weight tier, or an empty list when there are no candidates.
        """
        if not candidates:
            return []
        tiers: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for candidate in candidates:
            tiers[candidate['weight']].append(candidate)
        return tiers[max(tiers)]

    @abc.abstractmethod
    def select(self, candidates: list[dict[str, Any]], context: ClientContext) -> list[dict[str, Any]]:
        """Choose the records to answer with from the candidate records.

        :param candidates: The candidate records to choose among.
        :param context: Per-request client data the policy may read (e.g. the client IP for stickiness).
        :returns: The selected records; an empty input yields an empty result.
        """
