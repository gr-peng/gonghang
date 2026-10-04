from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Any

from .models import ActionProposal, TrustedFact
from .money import MAX_MINOR_UNITS, is_minor_unit_name, validate_minor_units
from .policy import PolicyEngine
from .trust import FactAuthority


@dataclass(frozen=True)
class PolicyDiffCase:
    case_id: str
    action: ActionProposal
    facts: tuple[TrustedFact, ...]


@dataclass(frozen=True)
class PolicyDiffFinding:
    case_id: str
    old_level: int
    new_level: int
    expansion: bool
    old_matched_rules: tuple[str, ...]
    new_matched_rules: tuple[str, ...]


class BoundedPolicyDiffer:
    """Exact policy-version diff over an explicit finite test domain."""

    def __init__(self, fact_authority: FactAuthority) -> None:
        self.fact_authority = fact_authority

    def compare(
        self,
        old: PolicyEngine,
        new: PolicyEngine,
        cases: Iterable[PolicyDiffCase],
        *,
        enforce_argument_contracts: bool = True,
    ) -> tuple[PolicyDiffFinding, ...]:
        findings: list[PolicyDiffFinding] = []
        for case in cases:
            verified = self.fact_authority.verified_index(case.facts)
            old_eval = old.evaluate(
                case.action,
                verified,
                enforce_argument_contracts=enforce_argument_contracts,
            )
            new_eval = new.evaluate(
                case.action,
                verified,
                enforce_argument_contracts=enforce_argument_contracts,
            )
            findings.append(
                PolicyDiffFinding(
                    case_id=case.case_id,
                    old_level=int(old_eval.base_level),
                    new_level=int(new_eval.base_level),
                    expansion=new_eval.base_level < old_eval.base_level,
                    old_matched_rules=old_eval.matched_rule_ids,
                    new_matched_rules=new_eval.matched_rule_ids,
                )
            )
        return tuple(findings)

    def assert_no_expansion(
        self,
        old: PolicyEngine,
        new: PolicyEngine,
        cases: Iterable[PolicyDiffCase],
    ) -> None:
        expansions = [finding for finding in self.compare(old, new, cases) if finding.expansion]
        if expansions:
            details = ", ".join(
                f"{item.case_id}: {item.old_level}->{item.new_level}" for item in expansions
            )
            raise AssertionError(f"policy update expands permissions on bounded domain: {details}")


def find_numeric_expansion_with_z3(
    old: PolicyEngine,
    new: PolicyEngine,
    *,
    action_template: ActionProposal,
    verified_facts: Mapping[tuple[str, str], list[TrustedFact]],
    parameter: str,
    lower: int,
    upper: int,
) -> dict[str, Any] | None:
    """Optional SMT search for a numeric permission-expansion counterexample.

    The compiler intentionally covers the first prototype's comparison-heavy
    transfer fragment. Unsupported dynamic/string expressions raise an error
    instead of being silently approximated.
    """

    try:
        import z3  # type: ignore
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("install the 'smt' extra to use Z3 policy diff") from exc

    if is_minor_unit_name(parameter):
        lower = validate_minor_units(
            lower,
            field_name="SMT lower monetary bound",
            allow_zero=True,
        )
        upper = validate_minor_units(
            upper,
            field_name="SMT upper monetary bound",
            allow_zero=True,
        )
    if lower > upper:
        raise ValueError("SMT lower bound must not exceed upper bound")

    minor_unit_domain = is_minor_unit_name(parameter)
    symbol = z3.Int(parameter) if minor_unit_domain else z3.Real(parameter)
    missing = object()

    def resolve(value: Any):
        if isinstance(value, str) and value == f"$params.{parameter}":
            return symbol
        if isinstance(value, str) and value.startswith("$params."):
            field = value[len("$params.") :]
            return action_template.params[field] if field in action_template.params else missing
        if value == "$actor":
            return action_template.actor_id
        if value == "$session":
            return action_template.session_id
        if isinstance(value, dict) and set(value) == {"fact"}:
            ref = value["fact"]
            subject = resolve(ref["subject"])
            if subject is missing or subject is None:
                return missing
            rows = verified_facts.get((str(ref["predicate"]), str(subject)), [])
            return rows[-1].value if rows else missing
        if isinstance(value, dict) and set(value) == {"literal"}:
            return value["literal"]
        if isinstance(value, dict) and set(value) == {"sum_minor"}:
            terms = [resolve(term) for term in value["sum_minor"]]
            if any(term is missing or term is None for term in terms):
                return missing
            for term in terms:
                if not z3.is_expr(term):
                    validate_minor_units(term, field_name="SMT sum_minor operand", allow_zero=True)
            expression = sum(terms)
            if z3.is_expr(expression):
                if not minor_unit_domain:
                    raise ValueError("SMT sum_minor requires an integer minor-unit parameter")
                lo = z3.simplify(z3.substitute(expression, (symbol, z3.IntVal(lower)))).as_long()
                hi = z3.simplify(z3.substitute(expression, (symbol, z3.IntVal(upper)))).as_long()
            else:
                lo = hi = expression
            if not 0 <= lo <= hi <= MAX_MINOR_UNITS:
                raise ValueError("SMT sum_minor domain may overflow; narrow bounds or use bounded policy diff")
            return expression
        return value

    def bool_expr(value: Any):
        return z3.BoolVal(value) if isinstance(value, bool) else value

    def known_boolean(value: Any):
        expression = bool_expr(value)
        return expression, z3.Not(expression)

    def unknown_boolean():
        return z3.BoolVal(False), z3.BoolVal(False)

    def compile_condition(condition: dict[str, Any]):
        """Compile three-valued policy semantics as (definitely_true, definitely_false)."""

        op, payload = next(iter(condition.items()))
        if op == "all":
            children = [compile_condition(child) for child in payload]
            return (
                z3.And(*[child[0] for child in children]),
                z3.Or(*[child[1] for child in children]),
            )
        if op == "any":
            children = [compile_condition(child) for child in payload]
            return (
                z3.Or(*[child[0] for child in children]),
                z3.And(*[child[1] for child in children]),
            )
        if op == "not":
            definitely_true, definitely_false = compile_condition(payload)
            return definitely_false, definitely_true
        if op == "exists":
            value = resolve(payload["value"])
            if value is missing:
                return unknown_boolean()
            return known_boolean(value is not None)
        if op in {"equals", "compare", "contains", "in"}:
            if op == "equals":
                left, right = resolve(payload["left"]), resolve(payload["right"])
            elif op == "compare":
                left, right = resolve(payload["left"]), resolve(payload["right"])
            elif op == "contains":
                left, right = resolve(payload["collection"]), resolve(payload["item"])
            else:
                left, right = resolve(payload["item"]), resolve(payload["collection"])
            if left is missing or right is missing or left is None or right is None:
                return unknown_boolean()
            try:
                if op == "equals":
                    expression = left == right
                elif op == "compare":
                    expression = {
                        "<": left < right,
                        "<=": left <= right,
                        ">": left > right,
                        ">=": left >= right,
                        "==": left == right,
                        "!=": left != right,
                    }[payload["op"]]
                elif op == "contains":
                    expression = right in left
                else:
                    expression = left in right
            except (TypeError, ValueError) as exc:
                raise ValueError(f"SMT prototype cannot compile {op!r}: {exc}") from exc
            return known_boolean(expression)
        if op in {"fact_equals", "fact_contains", "fact_not_contains"}:
            subject = resolve(payload["subject"])
            if subject is missing or subject is None:
                return unknown_boolean()
            rows = verified_facts.get((str(payload["predicate"]), str(subject)), [])
            if not rows:
                return unknown_boolean()
            value = rows[-1].value
            if op == "fact_equals":
                expected = resolve(payload["value"])
                if expected is missing or expected is None:
                    return unknown_boolean()
                return known_boolean(value == expected)
            item = resolve(payload["item"])
            if item is missing or item is None:
                return unknown_boolean()
            try:
                present = item in value
            except TypeError as exc:
                raise ValueError(f"SMT prototype cannot compile {op!r}: {exc}") from exc
            return known_boolean(present if op == "fact_contains" else not present)
        raise ValueError(f"unsupported condition operator {op!r}")

    def risk_expression(policy: PolicyEngine):
        matches = []
        scored = []
        for rule in policy.spec.rules:
            if rule.action not in {"*", action_template.action_type}:
                continue
            condition, _ = compile_condition(rule.when)
            matches.append(condition)
            scored.append(z3.If(condition, int(rule.effect), -1))
        if not matches:
            return z3.IntVal(int(policy.spec.default_level))
        maximum = scored[0]
        for candidate in scored[1:]:
            maximum = z3.If(candidate > maximum, candidate, maximum)
        base = z3.If(z3.Or(*matches), maximum, int(policy.spec.default_level))
        static_violations, static_effects = policy._contract_check(action_template)
        del static_violations
        for effect in static_effects:
            base = z3.If(int(effect) > base, int(effect), base)
        return base

    old_risk = risk_expression(old)
    new_risk = risk_expression(new)
    solver = z3.Solver()
    solver.add(symbol >= lower, symbol <= upper, new_risk < old_risk)
    if solver.check() != z3.sat:
        return None
    model = solver.model()
    value = model.eval(symbol, model_completion=True)
    return {
        "parameter": parameter,
        "counterexample": (
            str(value.as_long()) if minor_unit_domain else value.as_decimal(20)
        ),
        "old_level": model.eval(old_risk, model_completion=True).as_long(),
        "new_level": model.eval(new_risk, model_completion=True).as_long(),
    }
