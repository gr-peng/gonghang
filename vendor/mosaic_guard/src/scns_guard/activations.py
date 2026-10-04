from __future__ import annotations

from collections.abc import Sequence

from .adapters.openai_compatible import SourceSpan
from .canonical import sha256_hex


def validate_source_token_spans(
    spans: Sequence[SourceSpan],
    *,
    token_ids: Sequence[int],
    expected_token_ids_sha256: str,
) -> tuple[SourceSpan, ...]:
    materialized_tokens = tuple(int(token_id) for token_id in token_ids)
    if sha256_hex(materialized_tokens) != expected_token_ids_sha256:
        raise ValueError("local prompt token IDs do not match the recorded server token IDs")
    validated: list[SourceSpan] = []
    for span in spans:
        if span.token_start is None or span.token_end is None:
            raise ValueError(f"unresolved token span for source {span.source_id!r}")
        if not 0 <= span.token_start < span.token_end <= len(materialized_tokens):
            raise ValueError(f"token span for source {span.source_id!r} is outside prompt")
        validated.append(span)
    return tuple(validated)


__all__ = ["validate_source_token_spans"]
