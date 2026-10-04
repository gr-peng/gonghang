from __future__ import annotations

import pytest

from scns_guard.llm_adapter import StructuredLLMAdapter

from .helpers import make_trace


def test_llm_cannot_self_assert_trusted_provenance() -> None:
    trace = make_trace(trace_id="llm-adapter")

    def generator(messages, seed):
        del messages, seed
        return '{"action_type":"transfer","params":{"from_account":"acct-user","to_account":"acct-alice","amount_minor":1000},"write_action":true}'

    action = StructuredLLMAdapter(generator).propose(
        trace, disabled_source_ids=frozenset(), seed=0
    )
    assert action is not None
    assert all(not binding.observed_provenance_complete for binding in action.argument_bindings)


def test_extra_model_provenance_claim_is_rejected() -> None:
    trace = make_trace(trace_id="llm-extra")

    def generator(messages, seed):
        del messages, seed
        return '{"action_type":"transfer","params":{},"write_action":true,"argument_bindings":[]}'

    with pytest.raises(Exception):
        StructuredLLMAdapter(generator).propose(
            trace, disabled_source_ids=frozenset(), seed=0
        )


def test_llm_adapter_rejects_duplicate_keys_and_nonfinite_json() -> None:
    from scns_guard.llm_adapter import StructuredLLMAdapter

    trace = make_trace(trace_id="strict-json")
    duplicate = StructuredLLMAdapter(
        lambda messages, seed: (
            '{"action_type":"transfer","action_type":"read_balance",'
            '"params":{},"write_action":true}'
        )
    )
    with pytest.raises(ValueError, match="strict JSON object"):
        duplicate.propose(trace, disabled_source_ids=frozenset(), seed=0)

    nonfinite = StructuredLLMAdapter(
        lambda messages, seed: (
            '{"action_type":"transfer","params":{"amount_minor":NaN},'
            '"write_action":true}'
        )
    )
    with pytest.raises(ValueError, match="strict JSON object"):
        nonfinite.propose(trace, disabled_source_ids=frozenset(), seed=0)
