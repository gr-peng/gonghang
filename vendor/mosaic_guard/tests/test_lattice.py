from __future__ import annotations

import itertools

from scns_guard.enums import ObligationType, RiskLevel
from scns_guard.lattice import non_expansion_holds, obligations_for, risk_join


def test_join_is_commutative_associative_and_idempotent() -> None:
    levels = list(RiskLevel)
    for left, right, third in itertools.product(levels, repeat=3):
        assert risk_join(left, right) == risk_join(right, left)
        assert risk_join(risk_join(left, right), third) == risk_join(
            left, risk_join(right, third)
        )
        assert risk_join(left, left) == left


def test_obligation_chain_is_monotone() -> None:
    assert obligations_for(RiskLevel.GREEN) == frozenset()
    assert obligations_for(RiskLevel.YELLOW) == frozenset(
        {ObligationType.CONFIRMATION}
    )
    assert obligations_for(RiskLevel.RED) == frozenset(
        {ObligationType.CONFIRMATION, ObligationType.MFA}
    )


def test_single_decision_permission_non_expansion() -> None:
    token_sets = [
        frozenset(),
        frozenset({ObligationType.CONFIRMATION}),
        frozenset({ObligationType.CONFIRMATION, ObligationType.MFA}),
    ]
    for base in RiskLevel:
        for final in RiskLevel:
            for tokens in token_sets:
                if final >= base:
                    assert non_expansion_holds(base, final, tokens)
                else:
                    assert not non_expansion_holds(base, final, tokens)
