from __future__ import annotations

from collections.abc import Iterable

from .enums import DecisionStatus, ObligationType, RiskLevel


def risk_join(*levels: RiskLevel | str | int) -> RiskLevel:
    if not levels:
        return RiskLevel.GREEN
    return max(RiskLevel.parse(level) for level in levels)


def obligations_for(level: RiskLevel) -> frozenset[ObligationType]:
    level = RiskLevel.parse(level)
    if level is RiskLevel.GREEN:
        return frozenset()
    if level is RiskLevel.YELLOW:
        return frozenset({ObligationType.CONFIRMATION})
    if level is RiskLevel.RED:
        return frozenset({ObligationType.CONFIRMATION, ObligationType.MFA})
    return frozenset({ObligationType.HUMAN_REVIEW})


def status_for(level: RiskLevel, discharged: Iterable[ObligationType]) -> DecisionStatus:
    level = RiskLevel.parse(level)
    if level is RiskLevel.DENY:
        return DecisionStatus.DENY
    discharged_set = frozenset(discharged)
    missing = obligations_for(level) - discharged_set
    if ObligationType.MFA in missing:
        return DecisionStatus.REQUIRE_MFA
    if ObligationType.CONFIRMATION in missing:
        return DecisionStatus.REQUIRE_CONFIRMATION
    return DecisionStatus.ALLOW


def is_executable(level: RiskLevel, discharged: Iterable[ObligationType]) -> bool:
    return status_for(level, discharged) is DecisionStatus.ALLOW


def non_expansion_holds(
    base_level: RiskLevel,
    final_level: RiskLevel,
    discharged: Iterable[ObligationType],
) -> bool:
    """Check the executable-set implication for one decision snapshot.

    If the final (joined) decision executes, the base decision must also execute
    under the same trusted obligation tokens.
    """

    if final_level < base_level:
        return False
    return (not is_executable(final_level, discharged)) or is_executable(
        base_level, discharged
    )
