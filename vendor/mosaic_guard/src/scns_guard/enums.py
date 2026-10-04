from __future__ import annotations

from enum import Enum, IntEnum


class RiskLevel(IntEnum):
    """Ordered risk lattice used by both hard policy and detectors."""

    GREEN = 0
    YELLOW = 1
    RED = 2
    DENY = 3

    @classmethod
    def parse(cls, value: object) -> "RiskLevel":
        if isinstance(value, cls):
            return value
        if isinstance(value, int):
            return cls(value)
        if isinstance(value, str):
            normalized = value.strip().upper().replace("-", "_")
            return cls[normalized]
        raise TypeError(f"Cannot parse RiskLevel from {type(value)!r}")

    def label(self) -> str:
        return self.name.lower()


class DecisionStatus(str, Enum):
    ALLOW = "allow"
    REQUIRE_CONFIRMATION = "require_confirmation"
    REQUIRE_MFA = "require_mfa"
    DENY = "deny"


class ObligationType(str, Enum):
    CONFIRMATION = "confirmation"
    MFA = "mfa"
    HUMAN_REVIEW = "human_review"


class FactTrust(str, Enum):
    TRUSTED = "trusted"
    UNTRUSTED = "untrusted"


class SourceKind(str, Enum):
    USER = "user"
    RAG = "rag"
    TOOL = "tool"
    MEMORY = "memory"
    AGENT = "agent"
    TRUSTED_SERVICE = "trusted_service"
    SYSTEM = "system"
    UNKNOWN = "unknown"


class SourceTrust(IntEnum):
    EXTERNAL = 0
    TOOL_OUTPUT = 1
    USER = 2
    TRUSTED = 3

    @classmethod
    def parse(cls, value: object) -> "SourceTrust":
        if isinstance(value, cls):
            return value
        if isinstance(value, int):
            return cls(value)
        if isinstance(value, str):
            return cls[value.strip().upper()]
        raise TypeError(f"Cannot parse SourceTrust from {type(value)!r}")


class ArgumentRole(str, Enum):
    CONTENT = "content"
    TARGET = "target"
    AMOUNT = "amount"
    COMMAND = "command"
    CREDENTIAL = "credential"
    SELECTOR = "selector"
    IDENTIFIER = "identifier"
    OTHER = "other"


class ExecutionState(str, Enum):
    PROPOSED = "proposed"
    EVALUATED = "evaluated"
    OBLIGATION_PENDING = "obligation_pending"
    AUTHORIZED = "authorized"
    EXECUTED = "executed"
    DENIED = "denied"
    FAILED = "failed"
