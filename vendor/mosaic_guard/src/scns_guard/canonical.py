from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel


def canonical_data(value: Any) -> Any:
    """Convert supported values into a deterministic JSON-compatible tree.

    Pydantic's JSON mode serializes sets and frozensets as lists before a JSON
    encoder can see that they were unordered. That can make security hashes
    depend on incidental set iteration order. Canonicalization therefore starts
    from Python-mode model data and recursively sorts every unordered
    collection before encoding.
    """

    if isinstance(value, BaseModel):
        return canonical_data(value.model_dump(mode="python", exclude_none=True))
    if isinstance(value, Enum):
        return canonical_data(value.value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, (str, int, float, bool)) and key is not None:
                raise TypeError(f"Unsupported canonical JSON key type: {type(key)!r}")
            normalized_key = str(key)
            if normalized_key in normalized:
                raise ValueError(
                    f"Canonical JSON key collision after string conversion: {normalized_key!r}"
                )
            normalized[normalized_key] = canonical_data(item)
        return normalized
    if isinstance(value, list | tuple):
        return [canonical_data(item) for item in value]
    if isinstance(value, set | frozenset):
        items = [canonical_data(item) for item in value]
        return sorted(
            items,
            key=lambda item: json.dumps(
                item,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
    if value is None or isinstance(value, str | int | float | bool):
        return value
    raise TypeError(f"Unsupported canonical JSON type: {type(value)!r}")


def canonical_json(value: Any) -> str:
    return json.dumps(
        canonical_data(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def sha256_hex(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
