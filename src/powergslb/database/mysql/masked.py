"""A statement parameter that masks itself while still binding its real value."""

import dataclasses
from typing import Any, ClassVar

__all__ = ['Masked']


@dataclasses.dataclass(frozen=True)
class Masked:
    """A bind value whose repr is a mask.

    :param value: The real bind value.
    """
    mask: ClassVar[str] = '*****'

    value: Any

    def __repr__(self) -> str:
        return repr(self.mask)
