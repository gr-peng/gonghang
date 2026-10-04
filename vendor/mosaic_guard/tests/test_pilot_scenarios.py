from __future__ import annotations

from scns_guard.dataset import authorization_equivalent
from scns_guard.lineage import LineageAuthority
from scns_guard.pilot import build_source_conflict_pilot


def test_source_conflict_pilot_is_one_group_with_required_controls() -> None:
    authority = LineageAuthority({"mosaic-runtime": b"prototype-lineage-secret"})
    scenarios = build_source_conflict_pilot(authority, group_id="pilot-group")
    assert {scenario.group_id for scenario in scenarios} == {"pilot-group"}
    assert {scenario.split for scenario in scenarios} == {"train"}
    assert {scenario.metadata["variant"] for scenario in scenarios} >= {
        "clean_user",
        "direct_rag_after",
        "laundered_summary",
        "opaque_rag",
    }

    by_variant = {scenario.metadata["variant"]: scenario for scenario in scenarios}
    assert authorization_equivalent(
        by_variant["clean_user"],
        by_variant["benign_irrelevant_after"],
    )
    assert authorization_equivalent(
        by_variant["benign_irrelevant_after"],
        by_variant["benign_irrelevant_before"],
    )

    laundered = by_variant["laundered_summary"]
    catalog = laundered.trace.source_catalog()
    summary = next(
        message.source
        for message in laundered.trace.messages
        if message.source.transformation == "summary"
    )
    assert any(catalog[parent].kind.value == "rag" for parent in summary.parent_source_ids)
    assert authority.verify(summary, catalog=catalog)
