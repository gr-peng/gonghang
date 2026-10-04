from __future__ import annotations

from typing import Any


MAX_MINOR_UNITS = 2**63 - 1


def validate_minor_units(
    value: Any,
    *,
    field_name: str,
    allow_zero: bool,
) -> int:
    """Require exact signed-64-bit integer minor units.

    ``bool`` is rejected even though it is an ``int`` subclass. Floats and
    ``Decimal`` values are rejected rather than rounded so that parsing cannot
    silently change a financial effect or its canonical digest.
    """

    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be integer minor units")
    minimum = 0 if allow_zero else 1
    if value < minimum:
        relation = "non-negative" if allow_zero else "positive"
        raise ValueError(f"{field_name} must be {relation}")
    if value > MAX_MINOR_UNITS:
        raise ValueError(f"{field_name} exceeds signed 64-bit minor-unit range")
    return value


def is_minor_unit_name(name: str) -> bool:
    return name == "amount_minor" or name.endswith("_minor")


def checked_add_minor_units(left: int, right: int, *, field_name: str) -> int:
    left = validate_minor_units(left, field_name=field_name, allow_zero=True)
    right = validate_minor_units(right, field_name=field_name, allow_zero=True)
    if left > MAX_MINOR_UNITS - right:
        raise OverflowError(f"{field_name} exceeds signed 64-bit minor-unit range")
    return left + right
