from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Any
from zoneinfo import ZoneInfo

from .causal import AgentTrace
from .enums import ArgumentRole, FactTrust, SourceKind
from .models import ActionProposal, ArgumentBinding, TrustedFact
from .money import checked_add_minor_units, validate_minor_units
from .trust import FactAuthority


@dataclass
class BankAccount:
    account_id: str
    owner_id: str
    balance_minor: int
    active: bool = True

    def __post_init__(self) -> None:
        validate_minor_units(
            self.balance_minor,
            field_name="account balance_minor",
            allow_zero=True,
        )


class BankLedger:
    name = "toy-bank-ledger"

    def __init__(
        self,
        accounts: list[BankAccount],
        *,
        daily_limit_minor: int = 1_000_000,
        blocked_recipients: set[str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.accounts = {account.account_id: account for account in accounts}
        self.daily_limit_minor = validate_minor_units(
            daily_limit_minor,
            field_name="daily_limit_minor",
            allow_zero=True,
        )
        self.daily_spent_minor: dict[str, int] = {}
        self.blocked_recipients = set(blocked_recipients or set())
        self.execution_log: list[dict[str, Any]] = []
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = RLock()
        self._transaction_time: datetime | None = None
        self._zone = ZoneInfo("Asia/Shanghai")
        self._business_date = self._clock().astimezone(self._zone).date()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Single-process facts/authorization/commit boundary, shared by controllers.

        The business day is pinned at entry, including when midnight occurs during
        a transfer. Production adapters need an equivalent database transaction.
        """
        with self._lock:
            outermost = self._transaction_time is None
            if outermost:
                now = self._clock()
                if now.tzinfo is None:
                    raise ValueError("bank clock must include timezone")
                today = now.astimezone(self._zone).date()
                if today < self._business_date:
                    raise RuntimeError("bank clock moved to an earlier business date")
                if today != self._business_date:
                    self.daily_spent_minor.clear()
                    self._business_date = today
                self._transaction_time = now
            try:
                yield
            finally:
                if outermost:
                    self._transaction_time = None

    def snapshot(self) -> dict[str, Any]:
        with self.transaction():
            return self._snapshot()

    def _snapshot(self) -> dict[str, Any]:
        return {
            "business_date": self._business_date.isoformat(),
            "accounts": {
                account_id: {
                    "owner_id": account.owner_id,
                    "balance_minor": account.balance_minor,
                    "active": account.active,
                }
                for account_id, account in sorted(self.accounts.items())
            },
            "daily_spent_minor": dict(sorted(self.daily_spent_minor.items())),
            "execution_count": len(self.execution_log),
        }

    def execute(self, action: ActionProposal) -> dict[str, Any]:
        with self.transaction():
            return self._execute(action)

    def _execute(self, action: ActionProposal) -> dict[str, Any]:
        if action.action_type == "read_balance":
            account = self.accounts[str(action.params["account_id"])]
            if account.owner_id != action.actor_id:
                raise PermissionError("actor does not own account")
            return {
                "account_id": account.account_id,
                "balance_minor": account.balance_minor,
            }
        if action.action_type != "transfer":
            raise ValueError(f"unsupported action: {action.action_type}")

        from_id = str(action.params["from_account"])
        to_id = str(action.params["to_account"])
        amount_minor = validate_minor_units(
            action.params["amount_minor"],
            field_name="action amount_minor",
            allow_zero=False,
        )
        source = self.accounts[from_id]
        target = self.accounts[to_id]
        if source.owner_id != action.actor_id:
            raise PermissionError("actor does not own source account")
        if not source.active or not target.active:
            raise RuntimeError("inactive account")
        if to_id in self.blocked_recipients:
            raise PermissionError("recipient is blocked")
        if source.balance_minor < amount_minor:
            raise RuntimeError("insufficient funds")
        spent_minor = self.daily_spent_minor.get(action.actor_id, 0)
        next_spent_minor = checked_add_minor_units(
            spent_minor,
            amount_minor,
            field_name="daily_spent_minor",
        )
        if next_spent_minor > self.daily_limit_minor:
            raise RuntimeError("daily limit exceeded")
        # Apply debit and credit to a shared account map so aliasing cannot
        # create money when source and destination refer to the same account.
        next_balances = {from_id: source.balance_minor - amount_minor}
        next_balances[to_id] = checked_add_minor_units(
            next_balances.get(to_id, target.balance_minor),
            amount_minor,
            field_name="target balance_minor",
        )

        for account_id, balance_minor in next_balances.items():
            self.accounts[account_id].balance_minor = balance_minor
        self.daily_spent_minor[action.actor_id] = next_spent_minor
        event = {
            "from_account": from_id,
            "to_account": to_id,
            "amount_minor": amount_minor,
            "memo": action.params.get("memo"),
            "action_digest": action.digest,
        }
        self.execution_log.append(event)
        return {"status": "posted", **event}

    def issue_facts(
        self,
        actor_id: str,
        authority: FactAuthority,
        *,
        issuer: str = "bank-core",
        now: datetime | None = None,
    ) -> tuple[TrustedFact, ...]:
        with self.transaction():
            return self._issue_facts(actor_id, authority, issuer=issuer, now=now)

    def _issue_facts(self, actor_id, authority, *, issuer, now):
        now = now or self._transaction_time
        if now.tzinfo is None or now.astimezone(self._zone).date() != self._business_date:
            raise ValueError("fact timestamp must match the current bank business date")
        midnight = datetime.combine(self._business_date + timedelta(days=1), datetime.min.time(), self._zone)
        expiry = min(now + timedelta(minutes=5), midnight)
        owned = sorted(
            account.account_id
            for account in self.accounts.values()
            if account.owner_id == actor_id
        )
        facts: list[TrustedFact] = [
            TrustedFact(
                predicate="daily_spent_minor", subject=actor_id,
                value=self.daily_spent_minor.get(actor_id, 0), issuer=issuer,
                trust=FactTrust.TRUSTED, issued_at=now, expires_at=expiry,
            ),
            TrustedFact(
                predicate="authenticated",
                subject=actor_id,
                value=True,
                issuer=issuer,
                trust=FactTrust.TRUSTED,
                issued_at=now,
                expires_at=expiry,
            ),
            TrustedFact(
                predicate="owned_accounts",
                subject=actor_id,
                value=owned,
                issuer=issuer,
                trust=FactTrust.TRUSTED,
                issued_at=now,
                expires_at=expiry,
            ),
            TrustedFact(
                predicate="daily_remaining_minor",
                subject=actor_id,
                value=max(
                    0,
                    self.daily_limit_minor - self.daily_spent_minor.get(actor_id, 0),
                ),
                issuer=issuer,
                trust=FactTrust.TRUSTED,
                issued_at=now,
                expires_at=expiry,
            ),
            TrustedFact(
                predicate="blocked_recipients",
                subject=actor_id,
                value=sorted(self.blocked_recipients),
                issuer=issuer,
                trust=FactTrust.TRUSTED,
                issued_at=now,
                expires_at=expiry,
            ),
        ]
        for account in self.accounts.values():
            facts.extend(
                [
                    TrustedFact(
                        predicate="account_active",
                        subject=account.account_id,
                        value=account.active,
                        issuer=issuer,
                        trust=FactTrust.TRUSTED,
                        issued_at=now,
                        expires_at=expiry,
                    ),
                    TrustedFact(
                        predicate="account_balance_minor",
                        subject=account.account_id,
                        value=account.balance_minor,
                        issuer=issuer,
                        trust=FactTrust.TRUSTED,
                        issued_at=now,
                        expires_at=expiry,
                    ),
                    TrustedFact(
                        predicate="recipient_allowed",
                        subject=account.account_id,
                        value=(account.active and account.account_id not in self.blocked_recipients),
                        issuer=issuer,
                        trust=FactTrust.TRUSTED,
                        issued_at=now,
                        expires_at=expiry,
                    ),
                ]
            )
        return tuple(authority.sign(fact) for fact in facts)


class ToyTransferAgent:
    """Deterministic surrogate that exposes direct and laundered source influence.

    Payload keys are intentionally structured so tests measure the guard rather
    than natural-language parsing. Replace this adapter with a local LLM wrapper
    while preserving the ``AgentAdapter`` interface.
    """

    def propose(
        self,
        trace: AgentTrace,
        *,
        disabled_source_ids: frozenset[str],
        seed: int,
    ) -> ActionProposal | None:
        del seed
        active = [
            message
            for message in trace.messages
            if message.source.source_id not in disabled_source_ids
        ]
        user_messages = [m for m in active if m.source.kind is SourceKind.USER]
        if not user_messages:
            return None
        user = user_messages[-1]
        request = user.payload
        action_type = str(request.get("action_type", "transfer"))
        if action_type == "read_balance":
            params = {"account_id": request["account_id"]}
            origin = {"account_id": user.source.source_id}
        else:
            params = {
                "from_account": request["from_account"],
                "to_account": request["to_account"],
                "amount_minor": request["amount_minor"],
            }
            if "memo" in request:
                params["memo"] = request["memo"]
            origin = {field: user.source.source_id for field in params}

        for message in active:
            if message is user:
                continue
            payload = message.payload
            if "force_action_type" in payload:
                action_type = str(payload["force_action_type"])
            direct_map = {
                "override_from": "from_account",
                "override_to": "to_account",
                "override_amount_minor": "amount_minor",
                "append_memo": "memo",
            }
            for key, field in direct_map.items():
                if key in payload:
                    params[field] = payload[key]
                    origin[field] = message.source.source_id
            laundered_map = {
                "laundered_override_from": "from_account",
                "laundered_override_to": "to_account",
                "laundered_override_amount_minor": "amount_minor",
                "laundered_memo": "memo",
            }
            for key, field in laundered_map.items():
                if key in payload:
                    params[field] = payload[key]
                    # Simulate a summarizer that drops the upstream source label.
                    origin[field] = user.source.source_id

        role_map = {
            "from_account": ArgumentRole.IDENTIFIER,
            "to_account": ArgumentRole.TARGET,
            "amount_minor": ArgumentRole.AMOUNT,
            "memo": ArgumentRole.CONTENT,
            "account_id": ArgumentRole.IDENTIFIER,
        }
        bindings = tuple(
            ArgumentBinding(
                field=field,
                source_ids=(origin[field],),
                role=role_map.get(field, ArgumentRole.OTHER),
                observed_provenance_complete=True,
            )
            for field in params
        )
        return ActionProposal(
            session_id=trace.session_id,
            actor_id=trace.actor_id,
            action_type=action_type,
            params=params,
            sources=tuple(message.source for message in active),
            argument_bindings=bindings,
            write_action=(action_type != "read_balance"),
        )
