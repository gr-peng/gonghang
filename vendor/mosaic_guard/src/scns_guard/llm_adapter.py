from __future__ import annotations

import json
import math
from collections.abc import Callable, Sequence
from typing import Any, Protocol

from .causal import AgentTrace
from .enums import ArgumentRole
from .models import ActionProposal, ArgumentBinding, StrictModel




def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _reject_nonfinite_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number is forbidden: {value}")


class ModelActionOutput(StrictModel):
    action_type: str
    params: dict[str, Any]
    write_action: bool = True


def _finite_json_float(raw: str) -> float:
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError('JSON number outside finite float range')
    return value


def _check_json_budget(raw: str) -> None:
    if not isinstance(raw, str) or len(raw.encode('utf-8')) > 262_144:
        raise ValueError('JSON exceeds 256 KiB limit')
    depth = 0
    quoted = escaped = False
    for character in raw:
        if quoted:
            if escaped:
                escaped = False
            elif character == '\\':
                escaped = True
            elif character == '"':
                quoted = False
        elif character == '"':
            quoted = True
        elif character in '[{':
            depth += 1
            if depth > 64:
                raise ValueError('JSON nesting exceeds 64 levels')
        elif character in ']}':
            depth -= 1


def parse_model_json(raw: str) -> Any:
    try:
        _check_json_budget(raw)
        value = json.loads(raw, object_pairs_hook=_unique_json_object,
                           parse_constant=_reject_nonfinite_json_constant,
                           parse_float=_finite_json_float)
        if not isinstance(value, dict):
            raise ValueError('JSON root must be an object')
        # A byte limit is not a node-count limit. Bound post-parse traversal too,
        # including strings containing escaped unpaired Unicode surrogates.
        stack, visited = [value], 0
        while stack:
            item = stack.pop()
            visited += 1
            if visited > 32_768:
                raise ValueError('JSON tree exceeds node budget')
            if isinstance(item, dict):
                stack.extend(item.keys())
                stack.extend(item.values())
            elif isinstance(item, list):
                stack.extend(item)
            elif isinstance(item, str):
                item.encode('utf-8')
        return value
    except (ValueError, RecursionError) as exc:
        raise ValueError(
            'LLM adapter expected one strict JSON object with unique keys, finite numbers and bounded complexity'
        ) from exc


class JSONGenerator(Protocol):
    def __call__(self, messages: Sequence[dict[str, Any]], seed: int) -> str: ...


ProvenanceResolver = Callable[
    [ModelActionOutput, AgentTrace, tuple[str, ...]],
    tuple[ArgumentBinding, ...],
]


def fail_closed_provenance_resolver(
    output: ModelActionOutput,
    trace: AgentTrace,
    active_source_ids: tuple[str, ...],
) -> tuple[ArgumentBinding, ...]:
    """Default resolver deliberately marks provenance as incomplete.

    A model's own claim about where an argument came from is not trusted. A
    production runtime must replace this with parser/data-flow metadata or an
    audited provenance service.
    """

    del trace, active_source_ids
    return tuple(
        ArgumentBinding(
            field=field,
            source_ids=(),
            role=ArgumentRole.OTHER,
            observed_provenance_complete=False,
        )
        for field in output.params
    )


class StructuredLLMAdapter:
    """Minimal adapter for local vLLM/Transformers/API wrappers.

    The supplied generator must return exactly one JSON object with
    ``action_type``, ``params``, and optional ``write_action``. Source metadata
    is injected by the runtime and never accepted from model output.
    """

    def __init__(
        self,
        generator: JSONGenerator,
        *,
        provenance_resolver: ProvenanceResolver = fail_closed_provenance_resolver,
    ) -> None:
        self.generator = generator
        self.provenance_resolver = provenance_resolver

    def _render(
        self,
        trace: AgentTrace,
        disabled_source_ids: frozenset[str],
    ) -> tuple[list[dict[str, Any]], tuple[str, ...]]:
        active = tuple(
            message
            for message in trace.messages
            if not trace.source_depends_on(message.source.source_id, disabled_source_ids)
        )
        rendered = [
            {
                "source_id": message.source.source_id,
                "source_kind": message.source.kind.value,
                "trust": message.source.trust.name.lower(),
                "text": message.text,
                "payload": message.payload,
            }
            for message in active
        ]
        active_ids = tuple(message.source.source_id for message in active)
        return rendered, active_ids

    def _bind(
        self, output: ModelActionOutput, trace: AgentTrace, active_ids: tuple[str, ...],
    ) -> ActionProposal:
        bindings = self.provenance_resolver(output, trace, active_ids)
        active_lineage = trace.source_closure(active_ids)
        return ActionProposal(
            session_id=trace.session_id,
            actor_id=trace.actor_id,
            action_type=output.action_type,
            params=output.params,
            write_action=output.write_action,
            sources=active_lineage,
            argument_bindings=bindings,
        )

    def propose(
        self,
        trace: AgentTrace,
        *,
        disabled_source_ids: frozenset[str],
        seed: int,
    ) -> ActionProposal | None:
        rendered, active_ids = self._render(trace, disabled_source_ids)
        raw = self.generator(rendered, seed)
        if not raw.strip():
            return None
        output = ModelActionOutput.model_validate(parse_model_json(raw))
        return self._bind(output, trace, active_ids)
