# pylint: disable=missing-function-docstring

"""Tests for Masked: a bind value that reprs as a mask but carries its real value."""

from powergslb.database.mysql.masked import Masked


def test_repr_is_a_mask() -> None:
    assert repr(Masked('$6$salt$hash')) == "'*****'"


def test_value_carries_the_real_bind_value() -> None:
    assert Masked('$6$salt$hash').value == '$6$salt$hash'


def test_masks_inside_a_formatted_tuple() -> None:
    # _cursor logs the parameter tuple with %s, so str() of the tuple hides the secret via its repr
    params = ('bob', Masked('$6$salt$hash'))
    assert str(params) == "('bob', '*****')"


def test_masks_when_stringified_directly() -> None:
    # no __str__, so str()/f-string/format() all fall back to __repr__; a direct log never exposes the value
    masked = Masked('$6$salt$hash')
    assert str(masked) == "'*****'"
    assert f'{masked}' == "'*****'"
    assert format(masked) == "'*****'"
