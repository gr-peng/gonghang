from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from .canonical import canonical_json, sha256_hex
from .causal import AgentTrace, SourceMessage
from .lineage import LineageAuthority
from .models import SourceRef


class Intervention(Protocol):
    intervention_id: str
    validity_assumptions: tuple[str, ...]

    def apply(self, trace: AgentTrace, source_id: str) -> AgentTrace: ...


def require_source(trace: AgentTrace, source_id: str) -> None:
    if source_id not in trace.source_catalog():
        raise ValueError(f"unknown source: {source_id}")


@dataclass(frozen=True)
class SourceAblation:
    intervention_id: str = "source_ablation"
    validity_assumptions: tuple[str, ...] = (
        "deletion removes descendants and may remove necessary evidence or change context length",
    )

    def apply(self, trace: AgentTrace, source_id: str) -> AgentTrace:
        require_source(trace, source_id)
        def keep(sid: str) -> bool:
            return not trace.source_depends_on(sid, frozenset({source_id}))
        return trace.model_copy(update={
            "messages": tuple(m for m in trace.messages if keep(m.source.source_id)),
            "lineage_messages": tuple(m for m in trace.lineage_messages if keep(m.source.source_id)),
            "lineage_sources": tuple(s for s in trace.lineage_sources if keep(s.source_id)),
            "metadata": {**trace.metadata, "intervention_id": self.intervention_id},
        })


@dataclass(frozen=True)
class SourceRelocation:
    position: Literal["first", "last"]
    intervention_id: str = "source_relocation"
    validity_assumptions: tuple[str, ...] = (
        "content, signatures, authority and ancestor closure are unchanged; position is changed",
    )

    def apply(self, trace: AgentTrace, source_id: str) -> AgentTrace:
        require_source(trace, source_id)
        if self.position not in ("first", "last"):
            raise ValueError("position must be first or last")
        selected = tuple(m for m in trace.messages
                         if trace.source_depends_on(m.source.source_id, frozenset({source_id})))
        if not selected:
            raise ValueError("source has no visible descendants to relocate")
        rest = tuple(m for m in trace.messages if m not in selected)
        messages = selected + rest if self.position == "first" else rest + selected
        return trace.model_copy(update={"messages": messages, "metadata": {
            **trace.metadata, "intervention_id": self.intervention_id,
            "visible_order_changed": messages != trace.messages,
        }})


TransformationRunner = Callable[[tuple[SourceMessage, ...]], tuple[str, dict[str, Any]]]


class TraceRewriter:
    """Reissue immutable nodes and recompute content-dependent descendants.

    Only a runtime authority can issue records. Model transformation runners
    supply text/payload only, never trust, signatures, or parent edges.
    """

    def __init__(self, authority: LineageAuthority,
                 transformations: Mapping[str, TransformationRunner]) -> None:
        self.authority = authority
        self.transformations = dict(transformations)

    def rewrite(self, trace: AgentTrace, source_id: str, *, operation: str,
                text: str | None = None, payload: dict[str, Any] | None = None,
                new_source_id: str | None = None) -> AgentTrace:
        require_source(trace, source_id)
        catalog = trace.source_catalog()
        contents: dict[str, SourceMessage] = {}
        for message in (*trace.messages, *trace.lineage_messages):
            sid = message.source.source_id
            if sid in contents and contents[sid] != message:
                raise ValueError(f"conflicting content for {sid}")
            if not self.authority.content_matches(message.source, {
                "text": message.text, "payload": message.payload,
            }):
                raise ValueError(f"source content does not verify: {sid}")
            contents[sid] = message
        for record in catalog.values():
            if not self.authority.verify(record, catalog=catalog):
                raise ValueError(f"source signature or closure does not verify: {record.source_id}")
        if source_id not in contents:
            raise ValueError("source content required for rewrite")
        if catalog[source_id].parent_source_ids:
            raise ValueError("content rewrites target root sources; edit its ancestor instead")
        if (text is None) != (payload is None):
            raise ValueError("text and payload must be replaced together")
        if new_source_id is not None and new_source_id in catalog:
            raise ValueError("source ID collision")
        if new_source_id is None and text is None:
            raise ValueError("rewrite requires content or a new source ID")
        root = contents[source_id]
        replacement = (root.text, root.payload) if text is None else (text, payload)
        content_changed = canonical_json(replacement) != canonical_json((root.text, root.payload))
        records: dict[str, SourceRef] = {}
        messages: dict[str, SourceMessage] = {}

        def rebuild(sid: str) -> SourceRef:
            if sid in records:
                return records[sid]
            old = catalog[sid]
            parents = tuple(rebuild(pid) for pid in old.parent_source_ids)
            changed_parents = any(p.source_id != pid for p, pid in zip(parents, old.parent_source_ids))
            if sid != source_id and not changed_parents:
                records[sid] = old
                if sid in contents:
                    messages[sid] = contents[sid]
                return old
            if sid not in contents:
                raise ValueError(f"descendant content missing: {sid}")
            old_message = contents[sid]
            body_text, body_payload = old_message.text, old_message.payload
            if sid == source_id:
                body_text, body_payload = replacement
            elif content_changed:
                runner = self.transformations.get(old.transformation or "")
                if runner is None:
                    raise ValueError(f"missing transformation runner: {old.transformation}")
                body_text, body_payload = runner(tuple(messages[pid] for pid in old.parent_source_ids))
            body = {"text": body_text, "payload": body_payload}
            fresh_id = (new_source_id if sid == source_id else None) or (
                f"{sid}~{sha256_hex([operation, body, [p.source_id for p in parents]])[:16]}"
            )
            if fresh_id in catalog or any(r.source_id == fresh_id for r in records.values()):
                raise ValueError("source ID collision during rewrite")
            assert old.producer_id is not None
            if parents:
                id_map = {key: value.source_id for key, value in records.items()}
                # Regenerated content has no certified field edges by default.
                edges = () if content_changed else tuple(edge.model_copy(update={
                    "parent_source_ids": tuple(id_map.get(pid, pid) for pid in edge.parent_source_ids),
                }) for edge in old.field_derivations)
                record = self.authority.derive(
                    source_id=fresh_id, kind=old.kind, content=body,
                    producer_id=old.producer_id, transformation=old.transformation or "",
                    parents=parents, field_derivations=edges, metadata=old.metadata,
                )
            else:
                record = self.authority.issue_root(
                    source_id=fresh_id, kind=old.kind, trust=old.trust,
                    content=body, producer_id=old.producer_id, metadata=old.metadata,
                )
            records[sid] = record
            messages[sid] = SourceMessage(source=record, text=body_text, payload=body_payload)
            return record

        for sid in catalog:
            rebuild(sid)
        return trace.model_copy(update={
            "messages": tuple(messages[m.source.source_id] for m in trace.messages),
            "lineage_messages": tuple(messages[m.source.source_id] for m in trace.lineage_messages),
            "lineage_sources": tuple(records[s.source_id] for s in trace.lineage_sources),
            "metadata": {**trace.metadata, "intervention_id": operation,
                         "source_id_map": {sid: r.source_id for sid, r in records.items()}},
        })


@dataclass(frozen=True)
class SourceRewrite:
    rewriter: TraceRewriter
    intervention_id: str
    text: str | None = None
    payload: dict[str, Any] | None = None
    new_source_id: str | None = None
    validity_assumptions: tuple[str, ...] = (
        "runtime reissues changed IDs and signatures without changing trust",
        "content changes rerun every affected transformation; identity-only edits do not",
        "replacement validity and authorized reference must be declared by the experiment",
    )

    def apply(self, trace: AgentTrace, source_id: str) -> AgentTrace:
        return self.rewriter.rewrite(trace, source_id, operation=self.intervention_id,
                                     text=self.text, payload=self.payload,
                                     new_source_id=self.new_source_id)
