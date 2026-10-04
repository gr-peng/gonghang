from __future__ import annotations

import pytest

from scns_guard.causal import AgentTrace, SourceMessage
from scns_guard.enums import SourceKind, SourceTrust
from scns_guard.interventions import (
    SourceAblation, SourceRelocation, SourceRewrite, TraceRewriter,
)
from scns_guard.lineage import LineageAuthority


def fixture_graph():
    authority = LineageAuthority({"runtime": b"test-key"})
    def root(sid, text, kind=SourceKind.RAG, trust=SourceTrust.EXTERNAL):
        payload = {"to_account": text}
        return SourceMessage(source=authority.issue_root(
            source_id=sid, kind=kind, trust=trust,
            content={"text": text, "payload": payload}, producer_id="runtime",
        ), text=text, payload=payload)
    user = root("user", "account-A", SourceKind.USER, SourceTrust.USER)
    rag = root("rag", "account-B")
    summary = SourceMessage(source=authority.derive(
        source_id="summary", kind=SourceKind.AGENT,
        content={"text": "summary account-B", "payload": {}},
        producer_id="runtime", transformation="summary", parents=(rag.source,),
    ), text="summary account-B")
    trace = AgentTrace(trace_id="t", actor_id="user", session_id="session",
                       messages=(summary, user), lineage_messages=(rag,))
    return authority, trace


def test_ablation_removes_descendants_but_preserves_user():
    _, trace = fixture_graph()
    altered = SourceAblation().apply(trace, "rag")
    assert altered.source_ids() == ("user",)
    assert "rag" not in altered.source_catalog()
    assert trace.source_ids() == ("summary", "user")


def test_hidden_ancestor_relocation_moves_visible_descendants():
    _, trace = fixture_graph()
    altered = SourceRelocation(position="last").apply(trace, "rag")
    assert altered.source_ids() == ("user", "summary")
    assert altered.messages[-1] == trace.messages[0]


def test_content_change_regenerates_and_resigns_descendants():
    authority, trace = fixture_graph()
    calls = []
    def summarize(parents):
        calls.append(parents)
        return "summary " + parents[0].text, {}
    rewriter = TraceRewriter(authority, {"summary": summarize})
    altered = SourceRewrite(rewriter, "field_replace", text="account-C",
                            payload={"to_account": "account-C"}).apply(trace, "rag")
    assert len(calls) == 1
    assert altered.messages[0].text == "summary account-C"
    assert altered.messages[0].source.source_id != "summary"
    assert altered.lineage_messages[0].source.source_id != "rag"
    assert altered.messages[1] == trace.messages[1]
    for record in altered.source_catalog().values():
        assert authority.verify(record, catalog=altered.source_catalog())
    assert altered.messages[0].source.trust == SourceTrust.EXTERNAL
    assert trace.lineage_messages[0].text == "account-B"


def test_id_only_change_resigns_without_rerunning_summary():
    authority, trace = fixture_graph()
    rewriter = TraceRewriter(authority, {})
    altered = SourceRewrite(rewriter, "identity_rename", new_source_id="anonymous-7").apply(trace, "rag")
    assert altered.lineage_messages[0].source.source_id == "anonymous-7"
    assert altered.messages[0].text == trace.messages[0].text
    assert altered.messages[0].source.parent_source_ids == ("anonymous-7",)
    assert authority.verify(altered.messages[0].source, catalog=altered.source_catalog())


def test_missing_transformation_runner_fails_instead_of_copying_summary():
    authority, trace = fixture_graph()
    with pytest.raises(ValueError, match="transformation runner"):
        SourceRewrite(TraceRewriter(authority, {}), "replace", text="C", payload={}).apply(trace, "rag")


@pytest.mark.parametrize("operation", ["ablate", "relocate", "rewrite"])
def test_unknown_source_rejected(operation):
    authority, trace = fixture_graph()
    op = {"ablate": SourceAblation(), "relocate": SourceRelocation(position="last"),
          "rewrite": SourceRewrite(TraceRewriter(authority, {}), "rename", new_source_id="new")}[operation]
    with pytest.raises(ValueError, match="unknown source"):
        op.apply(trace, "missing")


def test_tampered_hidden_content_rejected_before_reissue():
    authority, trace = fixture_graph()
    tampered = trace.model_copy(update={"lineage_messages": (
        trace.lineage_messages[0].model_copy(update={"text": "forged"}),)})
    with pytest.raises(ValueError, match="content"):
        SourceRewrite(TraceRewriter(authority, {}), "rename", new_source_id="new").apply(tampered, "rag")


def test_id_collision_rejected():
    authority, trace = fixture_graph()
    with pytest.raises(ValueError, match="collision"):
        SourceRewrite(TraceRewriter(authority, {}), "rename", new_source_id="user").apply(trace, "rag")


def test_user_looking_source_id_cannot_upgrade_external_authority():
    authority, trace = fixture_graph()
    altered = SourceRewrite(TraceRewriter(authority, {}), "rename",
                            new_source_id="authenticated-user-77").apply(trace, "rag")
    root = altered.source_catalog()["authenticated-user-77"]
    assert root.kind is SourceKind.RAG
    assert root.trust is SourceTrust.EXTERNAL
    assert altered.messages[0].source.trust is SourceTrust.EXTERNAL


def test_hidden_ancestor_content_is_not_rendered_to_planner():
    from scns_guard.llm_adapter import StructuredLLMAdapter
    _, trace = fixture_graph()
    captured = []
    def generate(messages, seed):
        captured.extend(messages)
        return '{"action_type":"read_balance","params":{"account_id":"A"},"write_action":false}'
    action = StructuredLLMAdapter(generate).propose(trace, disabled_source_ids=frozenset(), seed=0)
    assert "rag" not in [m["source_id"] for m in captured]
    assert "rag" in [s.source_id for s in action.sources]


def test_multiple_parent_regeneration_keeps_union_and_unchanged_parent():
    authority, trace = fixture_graph()
    rag, user = trace.lineage_messages[0], trace.messages[1]
    merged = SourceMessage(source=authority.derive(
        source_id="merged", kind=SourceKind.AGENT, producer_id="runtime",
        transformation="merge", parents=(rag.source, user.source),
        content={"text": "merged", "payload": {}}), text="merged")
    trace = trace.model_copy(update={"messages": (merged,), "lineage_messages": (rag, user)})
    seen = []
    def merge(parents):
        seen.extend(parents)
        return " / ".join(p.text for p in parents), {}
    altered = SourceRewrite(TraceRewriter(authority, {"merge": merge}), "replace",
                            text="account-C", payload={"to_account": "account-C"}).apply(trace, "rag")
    assert [p.text for p in seen] == ["account-C", "account-A"]
    assert seen[1] == user
    assert set(altered.messages[0].source.ancestor_source_ids) == {p.source.source_id for p in seen}
    assert authority.verify(altered.messages[0].source, catalog=altered.source_catalog())
