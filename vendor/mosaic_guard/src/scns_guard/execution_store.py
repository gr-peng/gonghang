"""Atomic request reservation and token lifecycle, with optional SQLite durability.

The reservation commits BEFORE calling a side-effecting backend. An interrupted
attempt is never automatically released: availability yields to at-most-once
dispatch. This is not a distributed transaction with the banking backend.
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock

from .canonical import canonical_json, sha256_hex
from .enums import DecisionStatus
from .models import ActionProposal, DecisionReceipt, FormalReceiptLayer, ObligationToken


@dataclass(frozen=True)
class RequestState:
    status: str
    receipt: DecisionReceipt | None = None


class ExecutionStore:
    def __init__(self, path: str | Path = ':memory:') -> None:
        self.path = str(path)
        self._lock = RLock()
        self._db = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False, timeout=5)
        self._db.row_factory = sqlite3.Row
        self._db.execute('PRAGMA synchronous=FULL')
        if self.path != ':memory:':
            self._db.execute('PRAGMA journal_mode=WAL')
        version = self._db.execute('PRAGMA user_version').fetchone()[0]
        if version not in (0, 1, 2):
            self._db.close()
            raise ValueError('unsupported execution store schema version')
        with self._atomic():
            self._db.execute('''CREATE TABLE IF NOT EXISTS execution_requests (
                request_key TEXT PRIMARY KEY, action_digest TEXT NOT NULL,
                tool_name TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('pending','started','finished')),
                authorization_json TEXT, receipt_json TEXT)''')
            self._db.execute('''CREATE TABLE IF NOT EXISTS token_lifecycle (
                issuer TEXT NOT NULL, token_id TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('consumed','revoked')), request_key TEXT,
                PRIMARY KEY(issuer, token_id))''')
            self._db.execute('''CREATE TABLE IF NOT EXISTS lifecycle_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, payload_json TEXT NOT NULL)''')
            self._db.execute(f'PRAGMA user_version={max(version, 1)}')

    @contextmanager
    def _atomic(self) -> Iterator[None]:
        with self._lock:
            self._db.execute('BEGIN IMMEDIATE')
            try:
                yield
                self._db.execute('COMMIT')
            except BaseException:
                if self._db.in_transaction:
                    self._db.execute('ROLLBACK')
                raise

    @staticmethod
    def request_key(issuer: str, action: ActionProposal, request_id: str) -> str:
        if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 200:
            raise ValueError('request_id must be a nonempty string of at most 200 characters')
        return sha256_hex([issuer, action.actor_id, action.session_id, request_id])

    def _event(self, event: str, **details: object) -> None:
        self._db.execute('INSERT INTO lifecycle_events(payload_json) VALUES (?)',
                         (canonical_json({'event': event, 'at': datetime.now(timezone.utc), **details}),))

    def _request_state(self, row: sqlite3.Row, action: ActionProposal, tool_name: str) -> RequestState:
        if row['action_digest'] != action.digest or row['tool_name'] != tool_name:
            return RequestState('conflict')
        state = row['state']
        if state == 'finished':
            receipt = DecisionReceipt.model_validate_json(row['receipt_json'])
            if (receipt.receipt_hash != sha256_hex(receipt.unsigned_payload())
                    or receipt.formal.action.digest != action.digest
                    or receipt.formal.execution is None
                    or receipt.formal.execution.tool_name != tool_name):
                raise ValueError('stored execution receipt failed integrity or binding check')
            return RequestState(state, receipt)
        if state not in ('pending', 'started'):
            raise ValueError('invalid stored execution state')
        return RequestState(state)

    def bind(self, key: str, action: ActionProposal, tool_name: str) -> RequestState:
        """Pin a request to its entire security envelope, even before approval."""
        with self._atomic():
            self._db.execute('''INSERT OR IGNORE INTO execution_requests
                (request_key,action_digest,tool_name,state) VALUES (?,?,?,'pending')''',
                (key, action.digest, tool_name))
            row = self._db.execute('SELECT * FROM execution_requests WHERE request_key=?', (key,)).fetchone()
            return self._request_state(row, action, tool_name)

    def token_available(self, token: ObligationToken) -> bool:
        with self._lock:
            row = self._db.execute('SELECT state FROM token_lifecycle WHERE issuer=? AND token_id=?',
                                   (token.issuer, token.token_id)).fetchone()
            return row is None

    def reserve(
        self, key: str, action: ActionProposal, tool_name: str, *,
        tokens: Sequence[ObligationToken], authorization: FormalReceiptLayer,
        verify_token: Callable[[ObligationToken], bool],
        circuit_namespace: str | None = None,
        verify_principal: Callable[[], object] | None = None,
        verify_authorization: Callable[[], bool] | None = None,
    ) -> RequestState:
        """Atomically persist the authorization intent and consume all used tokens."""
        if authorization.decision.status is not DecisionStatus.ALLOW or authorization.action.digest != action.digest:
            raise ValueError('reservation requires a matching allow decision')
        unique = {(token.issuer, token.token_id): token for token in tokens}
        if not authorization.decision.required_obligations <= {token.obligation for token in unique.values()}:
            raise ValueError('reservation is missing required obligation tokens')
        with self._atomic():
            row = self._db.execute('SELECT * FROM execution_requests WHERE request_key=?', (key,)).fetchone()
            if row is None:
                raise ValueError('request must be bound before reservation')
            current = self._request_state(row, action, tool_name)
            if current.status != 'pending':
                return current
            if self.session_revoked(action.actor_id, action.session_id):
                return RequestState('session_revoked')
            if circuit_namespace is not None and self.circuit_snapshot(circuit_namespace, action.actor_id)['locked']:
                return RequestState('safety_locked')
            if any(not verify_token(token) or not self.token_available(token) for token in unique.values()):
                return RequestState('token_unavailable')
            if verify_principal is not None:
                try:
                    verify_principal()
                except (ValueError, PermissionError):
                    return RequestState('identity_unavailable')
            # Waiting for BEGIN IMMEDIATE may outlive facts or a policy snapshot.
            # Recheck inside the same transaction, before consuming any token.
            if verify_authorization is not None and not verify_authorization():
                return RequestState('authorization_unavailable')
            for token in unique.values():
                self._db.execute('INSERT INTO token_lifecycle VALUES (?,?,?,?)',
                                 (token.issuer, token.token_id, 'consumed', key))
            self._db.execute('''UPDATE execution_requests SET state='started', authorization_json=?
                WHERE request_key=?''', (canonical_json(authorization), key))
            self._event('execution_reserved', request_key=key, action_digest=action.digest,
                        token_ids=sorted(token.token_id for token in unique.values()))
            return RequestState('reserved')

    def session_revoked(self, actor_id: str, session_id: str) -> bool:
        # Legacy host-only stores do not have a service identity layer.
        return False

    def finish(self, key: str, receipt: DecisionReceipt) -> None:
        """Persist a terminal response; a failed save leaves the reservation intact."""
        if receipt.receipt_hash != sha256_hex(receipt.unsigned_payload()) or receipt.formal.execution is None:
            raise ValueError('terminal receipt is not sealed or has no execution record')
        with self._atomic():
            row = self._db.execute('SELECT * FROM execution_requests WHERE request_key=?', (key,)).fetchone()
            if row is None or row['state'] != 'started':
                raise ValueError('no active execution reservation')
            authorization = FormalReceiptLayer.model_validate_json(row['authorization_json'])
            if (authorization.decision != receipt.formal.decision
                    or authorization.action.digest != receipt.formal.action.digest
                    or row['tool_name'] != receipt.formal.execution.tool_name):
                raise ValueError('terminal receipt differs from reserved authorization')
            self._db.execute("UPDATE execution_requests SET state='finished',receipt_json=? WHERE request_key=?",
                             (canonical_json(receipt), key))
            self._event('execution_finished', request_key=key, receipt_hash=receipt.receipt_hash)

    def revoke(self, token: ObligationToken, *, operator_id: str, reason: str) -> bool:
        if not operator_id.strip() or not reason.strip():
            raise ValueError('operator identity and revocation reason are required')
        with self._atomic():
            available = self.token_available(token)
            if available:
                self._db.execute('INSERT INTO token_lifecycle VALUES (?,?,?,NULL)',
                                 (token.issuer, token.token_id, 'revoked'))
            self._event('token_revoked' if available else 'token_revocation_noop',
                        issuer=token.issuer, token_id=token.token_id, operator_id=operator_id, reason=reason)
            return available

    def events(self) -> tuple[dict, ...]:
        with self._lock:
            return tuple(json.loads(row[0]) for row in self._db.execute(
                'SELECT payload_json FROM lifecycle_events ORDER BY sequence').fetchall())

    def close(self) -> None:
        with self._lock:
            self._db.close()
