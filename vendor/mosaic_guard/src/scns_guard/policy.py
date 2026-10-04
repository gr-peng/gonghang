from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml
from pydantic import Field, ValidationError, field_validator, model_validator

from .canonical import canonical_data, sha256_hex
from .enums import ArgumentRole, RiskLevel, SourceKind, SourceTrust
from .lattice import risk_join
from .models import ActionProposal, PolicyEvaluation, RuleTrace, StrictModel, TrustedFact
from .money import checked_add_minor_units, is_minor_unit_name, validate_minor_units
from .value_types import latest_fact_value, strict_contains, strict_equal


class PolicyError(ValueError):
    """Raised when a policy is malformed or uses an unsupported expression."""




class _UniqueKeySafeLoader(yaml.SafeLoader):
    """Bounded safe YAML; aliases are deliberately outside the policy language."""

    def __init__(self, stream):
        super().__init__(stream)
        self._policy_depth = 0
        self._policy_nodes = 0

    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            raise PolicyError('policy YAML aliases are unsupported')
        self._policy_depth += 1
        self._policy_nodes += 1
        try:
            if self._policy_depth > 64 or self._policy_nodes > 32_768:
                raise PolicyError('policy YAML depth/node budget exceeded')
            return super().compose_node(parent, index)
        finally:
            self._policy_depth -= 1


def _construct_unique_mapping(
    loader: _UniqueKeySafeLoader,
    node: yaml.nodes.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise PolicyError("policy mapping keys must be hashable") from exc
        if duplicate:
            raise PolicyError(f"duplicate YAML mapping key: {key!r}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


class FieldContract(StrictModel):
    role: ArgumentRole
    required: bool = True
    provenance_required: bool = True
    min_trust: SourceTrust = SourceTrust.EXTERNAL
    allowed_source_kinds: frozenset[SourceKind] | None = None
    on_missing: RiskLevel = RiskLevel.DENY
    on_violation: RiskLevel = RiskLevel.RED
    causal_sensitive: bool = True

    @field_validator("min_trust", mode="before")
    @classmethod
    def parse_min_trust(cls, value: Any) -> SourceTrust:
        return SourceTrust.parse(value)

    @field_validator("on_missing", "on_violation", mode="before")
    @classmethod
    def parse_risk_levels(cls, value: Any) -> RiskLevel:
        return RiskLevel.parse(value)


class ActionContract(StrictModel):
    write_action: bool
    allow_extra_params: bool = False
    fields: dict[str, FieldContract]
    allowed_control_source_kinds: frozenset[SourceKind] = Field(
        default_factory=lambda: frozenset(
            {SourceKind.USER, SourceKind.SYSTEM, SourceKind.TRUSTED_SERVICE}
        )
    )
    min_control_trust: SourceTrust = SourceTrust.USER

    @field_validator("min_control_trust", mode="before")
    @classmethod
    def parse_control_trust(cls, value: Any) -> SourceTrust:
        return SourceTrust.parse(value)


class PolicyRule(StrictModel):
    rule_id: str
    action: str
    when: dict[str, Any]
    effect: RiskLevel
    reason: str

    @field_validator("effect", mode="before")
    @classmethod
    def parse_effect(cls, value: Any) -> RiskLevel:
        return RiskLevel.parse(value)

    @model_validator(mode="after")
    def validate_condition(self) -> "PolicyRule":
        validate_condition(self.when)
        return self


class PolicySpec(StrictModel):
    policy_id: str
    version: str
    description: str = ""
    default_level: RiskLevel = RiskLevel.DENY
    action_contracts: dict[str, ActionContract]
    rules: tuple[PolicyRule, ...]
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("default_level", mode="before")
    @classmethod
    def parse_default_level(cls, value: Any) -> RiskLevel:
        return RiskLevel.parse(value)

    @model_validator(mode="after")
    def validate_policy_invariants(self) -> "PolicySpec":
        if self.default_level is not RiskLevel.DENY:
            raise ValueError("default_level must be deny for fail-closed authorization")
        if not self.policy_id.strip() or not self.version.strip():
            raise ValueError("policy_id and version must be non-empty")
        rule_ids = [rule.rule_id for rule in self.rules]
        if any(not rule_id.strip() for rule_id in rule_ids):
            raise ValueError("rule_id must be non-empty")
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("rule_id values must be unique")
        known_actions = set(self.action_contracts)
        unknown_actions = sorted(
            {rule.action for rule in self.rules if rule.action != "*" and rule.action not in known_actions}
        )
        if unknown_actions:
            raise ValueError(
                f"rules refer to action types without contracts: {unknown_actions!r}"
            )
        return self

    @property
    def digest(self) -> str:
        return sha256_hex(self)


_ALLOWED_CONDITION_KEYS = {
    "all",
    "any",
    "not",
    "equals",
    "compare",
    "contains",
    "in",
    "exists",
    "fact_equals",
    "fact_contains",
    "fact_not_contains",
}
_ALLOWED_COMPARATORS = {"<", "<=", ">", ">=", "==", "!="}
_UNKNOWN = object()


def _is_minor_unit_operand(value: Any) -> bool:
    if isinstance(value, dict) and set(value) == {"sum_minor"}:
        return True
    if isinstance(value, str) and value.startswith("$params."):
        return is_minor_unit_name(value[len("$params.") :])
    if isinstance(value, dict) and set(value) == {"fact"}:
        reference = value["fact"]
        return isinstance(reference, dict) and is_minor_unit_name(
            str(reference.get("predicate", ""))
        )
    return False


def _validate_minor_unit_literal(value: Any) -> None:
    if isinstance(value, str) and value.startswith("$"):
        return
    if isinstance(value, dict) and set(value) in ({"fact"}, {"sum_minor"}):
        return
    if isinstance(value, dict) and set(value) == {"literal"}:
        value = value["literal"]
    validate_minor_units(
        value,
        field_name="monetary policy comparison literal",
        allow_zero=True,
    )


def _require_exact_keys(payload: Any, expected: set[str], *, operator: str) -> None:
    if not isinstance(payload, dict):
        raise PolicyError(f"{operator} requires an object payload")
    actual = set(payload)
    if actual != expected:
        raise PolicyError(
            f"{operator} requires exactly {sorted(expected)!r}; got {sorted(actual)!r}"
        )


def validate_operand(value: Any) -> None:
    if isinstance(value, str) and value.startswith("$"):
        if value in {"$actor", "$session"}:
            return
        if value.startswith("$params.") and value[len("$params.") :]:
            return
        raise PolicyError(f"Unsupported policy reference: {value}")
    if not isinstance(value, dict):
        return
    if set(value) == {"literal"}:
        return
    if set(value) == {"sum_minor"}:
        terms = value["sum_minor"]
        if not isinstance(terms, list) or not 2 <= len(terms) <= 16:
            raise PolicyError("sum_minor requires 2 to 16 bounded integer operands")
        literal_total = 0
        for term in terms:
            validate_operand(term)
            _validate_minor_unit_literal(term)
            literal = term.get("literal") if isinstance(term, dict) and set(term) == {"literal"} else term
            if type(literal) is int:
                try:
                    literal_total = checked_add_minor_units(literal_total, literal, field_name="sum_minor")
                except OverflowError as exc:
                    raise PolicyError("sum_minor literal sum overflows") from exc
        return
    if set(value) == {"fact"}:
        reference = value["fact"]
        _require_exact_keys(reference, {"predicate", "subject"}, operator="fact operand")
        if not isinstance(reference["predicate"], str) or not reference["predicate"].strip():
            raise PolicyError("fact operand predicate must be a non-empty string")
        validate_operand(reference["subject"])
        return
    raise PolicyError(
        "dictionary operands must be wrapped as {'literal': ...} or "
        "{'fact': {'predicate': ..., 'subject': ...}} or {'sum_minor': [...]}"
    )


def validate_condition(condition: Any) -> None:
    if not isinstance(condition, dict) or len(condition) != 1:
        raise PolicyError("Every condition must be a one-key object")
    op, payload = next(iter(condition.items()))
    if op not in _ALLOWED_CONDITION_KEYS:
        raise PolicyError(f"Unsupported condition operator: {op}")
    if op in {"all", "any"}:
        if not isinstance(payload, list) or not payload:
            raise PolicyError(f"{op} requires a non-empty list")
        for child in payload:
            validate_condition(child)
        return
    if op == "not":
        validate_condition(payload)
        return
    if op == "equals":
        _require_exact_keys(payload, {"left", "right"}, operator=op)
        validate_operand(payload["left"])
        validate_operand(payload["right"])
        return
    if op == "compare":
        _require_exact_keys(payload, {"left", "op", "right"}, operator=op)
        if payload["op"] not in _ALLOWED_COMPARATORS:
            raise PolicyError(f"Unsupported comparator: {payload['op']!r}")
        validate_operand(payload["left"])
        validate_operand(payload["right"])
        if _is_minor_unit_operand(payload["left"]) or _is_minor_unit_operand(
            payload["right"]
        ):
            _validate_minor_unit_literal(payload["left"])
            _validate_minor_unit_literal(payload["right"])
        return
    if op == "contains":
        _require_exact_keys(payload, {"collection", "item"}, operator=op)
        validate_operand(payload["collection"])
        validate_operand(payload["item"])
        return
    if op == "in":
        _require_exact_keys(payload, {"item", "collection"}, operator=op)
        validate_operand(payload["item"])
        validate_operand(payload["collection"])
        return
    if op == "exists":
        _require_exact_keys(payload, {"value"}, operator=op)
        validate_operand(payload["value"])
        return
    if op == "fact_equals":
        _require_exact_keys(payload, {"predicate", "subject", "value"}, operator=op)
        validate_operand(payload["subject"])
        validate_operand(payload["value"])
    else:
        _require_exact_keys(payload, {"predicate", "subject", "item"}, operator=op)
        validate_operand(payload["subject"])
        validate_operand(payload["item"])
    if not isinstance(payload["predicate"], str) or not payload["predicate"].strip():
        raise PolicyError(f"{op} predicate must be a non-empty string")



class ConditionEvaluator:
    """Deterministic, non-Turing-complete evaluator for the policy DSL.

    Security predicates use three-valued logic: ``True``, ``False``, or
    ``Unknown`` (represented internally by ``None``). A rule matches only on
    ``True``. In particular, negating a missing trusted fact remains unknown;
    it does not turn absence into permission.
    """

    def __init__(
        self,
        action: ActionProposal,
        verified_facts: Mapping[tuple[str, str], list[TrustedFact]],
    ) -> None:
        self.action = action
        self.verified_facts = verified_facts

    def resolve(self, value: Any) -> Any:
        if isinstance(value, str) and value.startswith("$"):
            if value == "$actor":
                return self.action.actor_id
            if value == "$session":
                return self.action.session_id
            if value.startswith("$params."):
                field = value[len("$params.") :]
                return self.action.params[field] if field in self.action.params else _UNKNOWN
            raise PolicyError(f"Unknown reference: {value}")
        if isinstance(value, dict) and set(value) == {"literal"}:
            return value["literal"]
        if isinstance(value, dict) and set(value) == {"sum_minor"}:
            total = 0
            for term in value["sum_minor"]:
                resolved = self.resolve(term)
                if resolved is _UNKNOWN or resolved is None:
                    return _UNKNOWN
                try:
                    total = checked_add_minor_units(total, resolved, field_name="sum_minor")
                except (ValueError, TypeError, OverflowError):
                    return _UNKNOWN
            return total
        if isinstance(value, dict) and set(value) == {"fact"}:
            fact_ref = value["fact"]
            predicate = str(fact_ref["predicate"])
            subject = self.resolve(fact_ref["subject"])
            if subject is _UNKNOWN or subject is None:
                return _UNKNOWN
            rows = self.verified_facts.get((predicate, str(subject)), [])
            resolved = latest_fact_value(predicate, rows)
            return resolved if resolved is not None else _UNKNOWN
        return value

    def _fact_values(self, payload: Mapping[str, Any]) -> list[Any]:
        predicate = str(payload["predicate"])
        subject = self.resolve(payload["subject"])
        if subject is _UNKNOWN or subject is None:
            return []
        facts = self.verified_facts.get((predicate, str(subject)), [])
        # No stale fallback and no arbitrary UUID ordering for concurrent facts.
        value = latest_fact_value(predicate, facts)
        return [] if value is None else [value]

    def _evaluate(self, condition: dict[str, Any]) -> bool | None:
        validate_condition(condition)
        op, payload = next(iter(condition.items()))
        try:
            if op == "all":
                values = [self._evaluate(child) for child in payload]
                if any(value is False for value in values):
                    return False
                if any(value is None for value in values):
                    return None
                return True
            if op == "any":
                values = [self._evaluate(child) for child in payload]
                if any(value is True for value in values):
                    return True
                if any(value is None for value in values):
                    return None
                return False
            if op == "not":
                value = self._evaluate(payload)
                return None if value is None else not value
            if op == "equals":
                left = self.resolve(payload["left"])
                right = self.resolve(payload["right"])
                if left is _UNKNOWN or right is _UNKNOWN or left is None or right is None:
                    return None
                return strict_equal(left, right)
            if op == "compare":
                left = self.resolve(payload["left"])
                right = self.resolve(payload["right"])
                if left is _UNKNOWN or right is _UNKNOWN or left is None or right is None:
                    return None
                if _is_minor_unit_operand(payload["left"]) or _is_minor_unit_operand(
                    payload["right"]
                ):
                    validate_minor_units(
                        left,
                        field_name="resolved monetary comparison left operand",
                        allow_zero=True,
                    )
                    validate_minor_units(
                        right,
                        field_name="resolved monetary comparison right operand",
                        allow_zero=True,
                    )
                comparator = payload["op"]
                if comparator in {'==', '!='}:
                    equal = strict_equal(left, right)
                    return equal if comparator == '==' or equal is None else not equal
                if type(left) is not type(right) or type(left) not in (int, float, str):
                    return None
                if comparator == "<":
                    return left < right
                if comparator == "<=":
                    return left <= right
                if comparator == ">":
                    return left > right
                if comparator == ">=":
                    return left >= right
                raise PolicyError(f"Unsupported comparator: {comparator}")
            if op == "contains":
                collection = self.resolve(payload["collection"])
                item = self.resolve(payload["item"])
                if collection is _UNKNOWN or item is _UNKNOWN or collection is None or item is None:
                    return None
                return strict_contains(collection, item)
            if op == "in":
                item = self.resolve(payload["item"])
                collection = self.resolve(payload["collection"])
                if collection is _UNKNOWN or item is _UNKNOWN or collection is None or item is None:
                    return None
                return strict_contains(collection, item)
            if op == "exists":
                value = self.resolve(payload["value"])
                if value is _UNKNOWN:
                    return None
                return value is not None
            if op == "fact_equals":
                values = self._fact_values(payload)
                expected = self.resolve(payload["value"])
                if not values or expected is _UNKNOWN or expected is None:
                    return None
                return strict_equal(values[-1], expected)
            if op == "fact_contains":
                values = self._fact_values(payload)
                item = self.resolve(payload["item"])
                if not values or item is _UNKNOWN or item is None:
                    return None
                return strict_contains(values[-1], item)
            if op == "fact_not_contains":
                values = self._fact_values(payload)
                item = self.resolve(payload["item"])
                if not values or item is _UNKNOWN or item is None:
                    return None
                contained = strict_contains(values[-1], item)
                return None if contained is None else not contained
        except (KeyError, TypeError, ValueError):
            return None
        raise PolicyError(f"Unsupported condition operator: {op}")

    def evaluate(self, condition: dict[str, Any]) -> bool:
        """A policy rule matches only when the three-valued result is true."""

        return self._evaluate(condition) is True


class PolicyEngine:
    def __init__(self, spec: PolicySpec) -> None:
        self.spec = spec

    @classmethod
    def from_yaml(cls, path: str | Path) -> "PolicyEngine":
        try:
            with Path(path).open("rb") as stream:
                payload = stream.read(1_048_577)
            if len(payload) > 1_048_576:
                raise PolicyError("policy YAML byte budget exceeded")
            raw = yaml.load(payload.decode("utf-8"), Loader=_UniqueKeySafeLoader)
        except (yaml.YAMLError, UnicodeError, RecursionError) as exc:
            raise PolicyError(f"invalid YAML policy: {exc}") from exc
        return cls.from_mapping(raw)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "PolicyEngine":
        try:
            return cls(PolicySpec.model_validate(dict(raw)))
        except (ValidationError, TypeError, ValueError) as exc:
            raise PolicyError(f"invalid policy: {exc}") from exc

    def snapshot(self) -> dict[str, Any]:
        return canonical_data(self.spec)

    def contract_for(self, action_type: str) -> ActionContract | None:
        return self.spec.action_contracts.get(action_type)

    def source_is_delegated_for_control(
        self,
        action_type: str,
        source_id: str,
        action: ActionProposal,
    ) -> bool:
        contract = self.contract_for(action_type)
        source = action.source_by_id(source_id)
        if contract is None or source is None:
            return False
        return (
            source.kind in contract.allowed_control_source_kinds
            and source.trust >= contract.min_control_trust
        )

    def source_is_delegated_for_field(
        self,
        action_type: str,
        field: str,
        source_id: str,
        action: ActionProposal,
    ) -> bool:
        contract = self.contract_for(action_type)
        source = action.source_by_id(source_id)
        if contract is None or source is None:
            return False
        field_contract = contract.fields.get(field)
        if field_contract is None:
            return False
        kind_allowed = (
            field_contract.allowed_source_kinds is None
            or source.kind in field_contract.allowed_source_kinds
        )
        return kind_allowed and source.trust >= field_contract.min_trust

    def _contract_check(
        self,
        action: ActionProposal,
        *,
        verified_source_ids: frozenset[str] | None = None,
    ) -> tuple[list[str], list[RiskLevel]]:
        violations: list[str] = []
        effects: list[RiskLevel] = []
        contract = self.contract_for(action.action_type)
        if contract is None:
            return [f"unknown action type: {action.action_type}"], [RiskLevel.DENY]
        if action.write_action != contract.write_action:
            violations.append("write/read classification does not match policy contract")
            effects.append(RiskLevel.DENY)

        expected_fields = set(contract.fields)
        actual_fields = set(action.params)
        if not contract.allow_extra_params:
            for extra in sorted(actual_fields - expected_fields):
                violations.append(f"unexpected action parameter: {extra}")
                effects.append(RiskLevel.DENY)

        seen_bindings: set[str] = set()
        for binding in action.argument_bindings:
            if binding.field in seen_bindings:
                violations.append(f"duplicate provenance binding: {binding.field}")
                effects.append(RiskLevel.DENY)
            seen_bindings.add(binding.field)

        for field, field_contract in contract.fields.items():
            if field_contract.required and field not in action.params:
                violations.append(f"missing required parameter: {field}")
                effects.append(field_contract.on_missing)
                continue
            if field not in action.params:
                continue
            binding = action.binding_for(field)
            if not field_contract.provenance_required:
                continue
            if binding is None or not binding.observed_provenance_complete:
                violations.append(f"missing or incomplete provenance for field: {field}")
                effects.append(field_contract.on_missing)
                continue
            if binding.role != field_contract.role:
                violations.append(
                    f"role mismatch for {field}: observed={binding.role.value}, "
                    f"expected={field_contract.role.value}"
                )
                effects.append(field_contract.on_violation)
            if not binding.source_ids:
                violations.append(f"empty provenance set for field: {field}")
                effects.append(field_contract.on_missing)
                continue
            for source_id in binding.source_ids:
                source = action.source_by_id(source_id)
                if source is None:
                    violations.append(f"unknown provenance source {source_id!r} for field: {field}")
                    effects.append(RiskLevel.DENY)
                    continue
                if verified_source_ids is not None and source_id not in verified_source_ids:
                    violations.append(
                        f"unverified runtime lineage source {source_id!r} controls field: {field}"
                    )
                    effects.append(field_contract.on_missing)
                    continue
                kind_allowed = (
                    field_contract.allowed_source_kinds is None
                    or source.kind in field_contract.allowed_source_kinds
                )
                if source.trust < field_contract.min_trust or not kind_allowed:
                    violations.append(
                        f"non-delegated source {source_id!r} ({source.kind.value}, "
                        f"trust={source.trust.name.lower()}) controls field: {field}"
                    )
                    effects.append(field_contract.on_violation)
        return violations, effects

    def evaluate(
        self,
        action: ActionProposal,
        verified_facts: Mapping[tuple[str, str], list[TrustedFact]],
        *,
        enforce_argument_contracts: bool = True,
        verified_source_ids: frozenset[str] | None = None,
    ) -> PolicyEvaluation:
        trace: list[RuleTrace] = []
        matched_effects: list[RiskLevel] = []
        matched_rule_ids: list[str] = []
        evaluator = ConditionEvaluator(action, verified_facts)

        for rule in self.spec.rules:
            if rule.action not in {"*", action.action_type}:
                continue
            matched = evaluator.evaluate(rule.when)
            trace.append(
                RuleTrace(
                    rule_id=rule.rule_id,
                    matched=matched,
                    effect=rule.effect if matched else None,
                    details={"reason": rule.reason},
                )
            )
            if matched:
                matched_rule_ids.append(rule.rule_id)
                matched_effects.append(rule.effect)

        if matched_effects:
            base_level = risk_join(*matched_effects)
        else:
            base_level = self.spec.default_level
            trace.append(
                RuleTrace(
                    rule_id="__default__",
                    matched=True,
                    effect=self.spec.default_level,
                    details={"reason": "no permitting rule matched; fail closed"},
                )
            )

        violations: list[str] = []
        if enforce_argument_contracts:
            violations, contract_effects = self._contract_check(
                action,
                verified_source_ids=verified_source_ids,
            )
            if contract_effects:
                base_level = risk_join(base_level, *contract_effects)

        return PolicyEvaluation(
            policy_id=self.spec.policy_id,
            policy_version=self.spec.version,
            policy_hash=self.spec.digest,
            base_level=base_level,
            matched_rule_ids=tuple(matched_rule_ids),
            trace=tuple(trace),
            argument_contract_violations=tuple(violations),
        )


def load_policy(path: str | Path) -> PolicyEngine:
    return PolicyEngine.from_yaml(path)
