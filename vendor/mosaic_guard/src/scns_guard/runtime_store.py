"""Durable control-plane state. All mutation boundaries use SQLite transactions.

The database is trusted local state, not a defense against a compromised host or
restoring an old backup. Export audit checkpoints to a separately controlled sink.
"""
from __future__ import annotations

import time

from .canonical import canonical_json, sha256_hex
from .enums import DecisionStatus, ExecutionState
from .execution_store import ExecutionStore, RequestState
from .models import ActionProposal, DecisionReceipt, FormalReceiptLayer, InternalEvidenceLayer
from .receipts import ReceiptLedger


class RuntimeStore(ExecutionStore):
    def __init__(self, path=':memory:'):
        super().__init__(path)
        with self._atomic():
            self._db.execute('''CREATE TABLE IF NOT EXISTS circuit_configs (
                namespace TEXT PRIMARY KEY, failures INTEGER NOT NULL, denials INTEGER NOT NULL)''')
            self._db.execute('''CREATE TABLE IF NOT EXISTS circuit_state (
                namespace TEXT NOT NULL, actor_id TEXT NOT NULL, failures INTEGER NOT NULL,
                denials INTEGER NOT NULL, locked INTEGER NOT NULL, generation INTEGER NOT NULL,
                PRIMARY KEY(namespace, actor_id))''')
            self._db.execute('''CREATE TABLE IF NOT EXISTS circuit_observations (
                namespace TEXT NOT NULL, receipt_hash TEXT NOT NULL, PRIMARY KEY(namespace, receipt_hash))''')
            self._db.execute('''CREATE TABLE IF NOT EXISTS receipt_chains (
                chain_id TEXT NOT NULL, sequence INTEGER NOT NULL, payload_json TEXT NOT NULL,
                PRIMARY KEY(chain_id, sequence))''')
            self._db.execute('''CREATE TABLE IF NOT EXISTS cancelled_requests (
                request_key TEXT PRIMARY KEY)''')
            self._db.execute('''CREATE TABLE IF NOT EXISTS revoked_sessions (
                actor_id TEXT NOT NULL, session_id TEXT NOT NULL, PRIMARY KEY(actor_id,session_id))''')
            self._db.execute('''CREATE TABLE IF NOT EXISTS admission_windows (
                principal TEXT NOT NULL, window INTEGER NOT NULL, count INTEGER NOT NULL,
                PRIMARY KEY(principal,window))''')
            self._db.execute('''CREATE TABLE IF NOT EXISTS recovered_requests (
                namespace TEXT NOT NULL, request_key TEXT NOT NULL, PRIMARY KEY(namespace,request_key))''')
            self._db.execute('PRAGMA user_version=2')

    def configure_circuit(self, namespace: str, failures: int, denials: int) -> None:
        if not namespace.strip():
            raise ValueError('circuit namespace required')
        with self._atomic():
            self._db.execute('INSERT OR IGNORE INTO circuit_configs VALUES (?,?,?)', (namespace, failures, denials))
            row = self._db.execute('SELECT failures,denials FROM circuit_configs WHERE namespace=?', (namespace,)).fetchone()
            if tuple(row) != (failures, denials):
                raise ValueError('persistent circuit configuration differs; explicit migration required')

    def session_revoked(self, actor_id: str, session_id: str) -> bool:
        with self._lock:
            return self._db.execute('SELECT 1 FROM revoked_sessions WHERE actor_id=? AND session_id=?',
                                    (actor_id, session_id)).fetchone() is not None

    def recover_unresolved(self, namespace: str) -> None:
        """Call only while holding the backend's cross-process execution lock.

        This avoids classifying another live worker's in-flight attempt as a
        crash. A trusted operator must reconcile before resetting the lock.
        """
        with self._atomic():
            rows = self._db.execute("SELECT request_key,authorization_json FROM execution_requests WHERE state='started'").fetchall()
            for row in rows:
                inserted = self._db.execute('INSERT OR IGNORE INTO recovered_requests VALUES (?,?)',
                                             (namespace, row['request_key'])).rowcount
                if not inserted:
                    continue
                authorization = FormalReceiptLayer.model_validate_json(row['authorization_json'])
                actor = authorization.action.actor_id
                state = self.circuit_snapshot(namespace, actor)
                self._db.execute('INSERT OR REPLACE INTO circuit_state VALUES (?,?,?,?,1,?)',
                    (namespace, actor, state['failures'] + 1, state['denials'], state['generation'] + 1))
                self._event('unresolved_execution_recovered', namespace=namespace, actor_id=actor, request_key=row['request_key'])

    def admit(self, principal: str, *, limit: int = 60, now: float | None = None) -> bool:
        window = int(time.time() if now is None else now) // 60
        with self._atomic():
            self._db.execute('DELETE FROM admission_windows WHERE window < ?', (window - 1,))
            self._db.execute('INSERT OR IGNORE INTO admission_windows VALUES (?,?,0)', (principal, window))
            count = self._db.execute('SELECT count FROM admission_windows WHERE principal=? AND window=?',
                                     (principal, window)).fetchone()[0]
            if count >= limit:
                return False
            self._db.execute('UPDATE admission_windows SET count=count+1 WHERE principal=? AND window=?', (principal, window))
            return True

    def record_rejection(self, namespace: str, actor_id: str, reason_code: str) -> None:
        with self._atomic():
            self._record_rejection_locked(namespace, actor_id, reason_code)

    def _record_rejection_locked(self, namespace: str, actor_id: str, reason_code: str) -> None:
        state = self.circuit_snapshot(namespace, actor_id)
        if state['locked']:
            return
        state['denials'] += 1
        state['locked'] = state['denials'] >= state['denial_threshold']
        # Reset approval is bound to the exact incident state, not just lock transitions.
        state['generation'] += 1
        self._db.execute('INSERT OR REPLACE INTO circuit_state VALUES (?,?,?,?,?,?)',
            (namespace, actor_id, state['failures'], state['denials'], int(state['locked']), state['generation']))
        self._event('gateway_rejection', namespace=namespace, actor_id=actor_id, reason_code=reason_code, **state)

    def circuit_snapshot(self, namespace: str, actor_id: str) -> dict:
        with self._lock:
            config = self._db.execute('SELECT failures,denials FROM circuit_configs WHERE namespace=?', (namespace,)).fetchone()
            if config is None:
                raise ValueError('unknown circuit configuration')
            row = self._db.execute('SELECT failures,denials,locked,generation FROM circuit_state WHERE namespace=? AND actor_id=?',
                                   (namespace, actor_id)).fetchone()
            state = dict(row) if row is not None else dict(failures=0, denials=0, locked=False, generation=0)
            return {**state, 'locked': bool(state['locked']), 'failure_threshold': config[0], 'denial_threshold': config[1]}

    def _observe_locked(self, namespace: str, receipt: DecisionReceipt) -> None:
        # Reviews never count as executed attempts. Append + observation share a
        # commit, so a crash after appending cannot silently lose a failure.
        if not receipt.formal.controller_config.get('execution_protocol'):
            return
        if receipt.receipt_hash != sha256_hex(receipt.unsigned_payload()):
            raise ValueError('invalid circuit receipt')
        inserted = self._db.execute('INSERT OR IGNORE INTO circuit_observations VALUES (?,?)',
                                    (namespace, receipt.receipt_hash)).rowcount
        if not inserted:
            return
        actor = receipt.formal.action.actor_id
        state = self.circuit_snapshot(namespace, actor)
        if state['locked']:
            return
        execution = receipt.formal.execution
        fact_failure = any(s.detector_id == 'runtime-fact-supplier-error' for s in receipt.formal.decision.detector_signals)
        if fact_failure or (execution is not None and execution.state is ExecutionState.FAILED):
            state['failures'] += 1
            event = 'execution_failure'
        elif receipt.formal.decision.status is DecisionStatus.DENY:
            state['denials'] += 1
            event = 'denied_attempt'
        else:
            return
        state['locked'] = state['failures'] >= state['failure_threshold'] or state['denials'] >= state['denial_threshold']
        # Reset approval is bound to the exact incident state, not just lock transitions.
        state['generation'] += 1
        self._db.execute('INSERT OR REPLACE INTO circuit_state VALUES (?,?,?,?,?,?)',
            (namespace, actor, state['failures'], state['denials'], int(state['locked']), state['generation']))
        self._event(event, namespace=namespace, actor_id=actor, receipt_hash=receipt.receipt_hash, **state)

    def observe_circuit(self, namespace: str, receipt: DecisionReceipt) -> None:
        with self._atomic():
            self._observe_locked(namespace, receipt)

    def reset_circuit(self, namespace: str, actor_id: str, *, operator_id: str, reason: str,
                      expected_generation: int | None = None) -> None:
        if not operator_id.strip() or not reason.strip():
            raise ValueError('operator identity and reason required')
        with self._atomic():
            self._reset_circuit_locked(namespace, actor_id, operator_id, reason, expected_generation)

    def _reset_circuit_locked(self, namespace, actor_id, operator_id, reason, expected_generation):
        before = self.circuit_snapshot(namespace, actor_id)
        if expected_generation is not None and before['generation'] != expected_generation:
            raise ValueError('stale circuit reset proof')
        self._db.execute('INSERT OR REPLACE INTO circuit_state VALUES (?,?,0,0,0,?)',
                         (namespace, actor_id, before['generation'] + 1))
        self._event('reset', namespace=namespace, actor_id=actor_id, operator_id=operator_id, reason=reason, before=before)

    def _request_state(self, row, action, tool_name):
        current = super()._request_state(row, action, tool_name)
        if current.status == 'pending' and self._db.execute(
                'SELECT 1 FROM cancelled_requests WHERE request_key=?', (row['request_key'],)).fetchone():
            return RequestState('cancelled')
        return current

    def cancel(self, key: str, action: ActionProposal, tool_name: str, *, operator_id: str, reason: str,
               verify_control=None) -> bool:
        if not operator_id.strip() or not reason.strip():
            raise ValueError('operator identity and reason required')
        with self._atomic():
            # Proof validation/consumption, identity validation, cancellation and
            # audit either all commit or all roll back; there is no inter-tx gap.
            if verify_control is not None:
                verify_control()
            self._db.execute('''INSERT OR IGNORE INTO execution_requests
                (request_key,action_digest,tool_name,state) VALUES (?,?,?,'pending')''', (key, action.digest, tool_name))
            row = self._db.execute('SELECT * FROM execution_requests WHERE request_key=?', (key,)).fetchone()
            current = self._request_state(row, action, tool_name)
            applied = current.status in ('pending', 'cancelled')
            if applied:
                self._db.execute('INSERT OR IGNORE INTO cancelled_requests VALUES (?)', (key,))
            self._event('request_cancelled' if applied else 'cancellation_too_late', request_key=key,
                        operator_id=operator_id, reason=reason)
            return applied

    def load_chain(self, chain_id: str) -> tuple[DecisionReceipt, ...]:
        with self._lock:
            rows = self._db.execute('SELECT payload_json FROM receipt_chains WHERE chain_id=? ORDER BY sequence', (chain_id,)).fetchall()
            receipts = tuple(DecisionReceipt.model_validate_json(row[0]) for row in rows)
            ReceiptLedger.verify_chain(receipts)
            return receipts

    def append_receipt(self, chain_id: str, formal: FormalReceiptLayer, internal: InternalEvidenceLayer,
                       circuit_namespace: str | None) -> DecisionReceipt:
        with self._atomic():
            row = self._db.execute('SELECT sequence,payload_json FROM receipt_chains WHERE chain_id=? ORDER BY sequence DESC LIMIT 1',
                                   (chain_id,)).fetchone()
            previous = DecisionReceipt.model_validate_json(row[1]) if row else None
            if previous and (previous.sequence != row[0] or previous.receipt_hash != sha256_hex(previous.unsigned_payload())):
                raise ValueError('invalid persisted chain head')
            receipt = DecisionReceipt(sequence=previous.sequence + 1 if previous else 1,
                previous_receipt_hash=previous.receipt_hash if previous else None, formal=formal, internal=internal)
            receipt = receipt.model_copy(update={'receipt_hash': sha256_hex(receipt.unsigned_payload())})
            self._db.execute('INSERT INTO receipt_chains VALUES (?,?,?)', (chain_id, receipt.sequence, canonical_json(receipt)))
            if circuit_namespace is not None:
                self._observe_locked(circuit_namespace, receipt)
            return receipt


class SQLiteReceiptLedger(ReceiptLedger):
    """One durable chain across independent connections, validated on open/read."""

    def __init__(self, store: RuntimeStore, *, chain_id: str = 'safety', circuit_namespace: str | None = None):
        self.store = store
        self.chain_id = chain_id
        self.circuit_namespace = circuit_namespace
        self.path = None
        store.load_chain(chain_id)

    @property
    def receipts(self):
        return self.store.load_chain(self.chain_id)

    def append(self, formal, internal):
        return self.store.append_receipt(self.chain_id, formal, internal, self.circuit_namespace)
