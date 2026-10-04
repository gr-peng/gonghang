"""Strict value semantics for deterministic policy evaluation.

Malformed values remain Unknown under negation; they must not become False.
Signature verification deliberately remains independent of this type contract.
"""
from __future__ import annotations

from typing import Any
import math

from .money import is_minor_unit_name, validate_minor_units


def fact_value_valid(predicate: str, value: Any) -> bool:
    if predicate in {'authenticated', 'account_active', 'recipient_allowed'}:
        return type(value) is bool
    if predicate in {'owned_accounts', 'blocked_recipients'}:
        return type(value) in (list, tuple) and all(type(v) is str and bool(v.strip()) for v in value)
    if is_minor_unit_name(predicate):
        try:
            validate_minor_units(value, field_name=predicate, allow_zero=True)
        except (ValueError, TypeError):
            return False
    return value is not None


def strict_equal(left: Any, right: Any) -> bool | None:
    if left is None or right is None or type(left) is not type(right):
        return None
    if type(left) in (list, tuple):
        if len(left) != len(right):
            return False
        comparisons = [strict_equal(a, b) for a, b in zip(left, right)]
        return None if None in comparisons else all(comparisons)
    if type(left) is dict:
        if left.keys() != right.keys():
            return False
        comparisons = [strict_equal(left[k], right[k]) for k in left]
        return None if None in comparisons else all(comparisons)
    if type(left) not in (str, bool, int, float):
        return None
    if type(left) is float and not (math.isfinite(left) and math.isfinite(right)):
        return None
    return left == right


def strict_contains(collection: Any, item: Any) -> bool | None:
    # Strings and dictionaries are not permission collections. Reject mixed
    # element types even if an earlier element happens to match.
    if type(collection) not in (list, tuple, set, frozenset) or item is None:
        return None
    comparisons = [strict_equal(value, item) for value in collection]
    return None if None in comparisons else any(comparisons)


def latest_fact_value(predicate: str, rows: Any) -> Any:
    """Return the newest unambiguous well-typed value, or None (Unknown).

    A timestamp is not a sequence number. Conflicting simultaneous facts must
    not be ordered by attacker-selected or randomly generated identifiers.
    """
    if not rows:
        return None
    latest = max(fact.issued_at for fact in rows)
    current = [fact.value for fact in rows if fact.issued_at == latest]
    if any(not fact_value_valid(predicate, value) for value in current):
        return None
    if any(strict_equal(current[0], value) is not True for value in current[1:]):
        return None
    return current[0]
