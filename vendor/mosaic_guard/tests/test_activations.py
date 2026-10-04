from __future__ import annotations

import pytest

from scns_guard.activations import validate_source_token_spans
from scns_guard.adapters.openai_compatible import SourceSpan
from scns_guard.canonical import sha256_hex


def test_source_activation_spans_require_exact_prompt_token_match() -> None:
    token_ids = (10, 20, 30, 40)
    spans = (
        SourceSpan(
            source_id="user",
            char_start=0,
            char_end=10,
            token_start=1,
            token_end=3,
        ),
    )
    assert validate_source_token_spans(
        spans,
        token_ids=token_ids,
        expected_token_ids_sha256=sha256_hex(token_ids),
    ) == spans

    with pytest.raises(ValueError, match="token IDs do not match"):
        validate_source_token_spans(
            spans,
            token_ids=(10, 20, 99, 40),
            expected_token_ids_sha256=sha256_hex(token_ids),
        )


def test_source_activation_spans_reject_missing_overlap_and_out_of_bounds() -> None:
    token_ids = (10, 20, 30)
    expected_hash = sha256_hex(token_ids)
    unresolved = SourceSpan(source_id="missing", char_start=0, char_end=1)
    with pytest.raises(ValueError, match="unresolved token span"):
        validate_source_token_spans(
            (unresolved,),
            token_ids=token_ids,
            expected_token_ids_sha256=expected_hash,
        )

    outside = SourceSpan(
        source_id="outside",
        char_start=0,
        char_end=1,
        token_start=2,
        token_end=4,
    )
    with pytest.raises(ValueError, match="outside prompt"):
        validate_source_token_spans(
            (outside,),
            token_ids=token_ids,
            expected_token_ids_sha256=expected_hash,
        )
