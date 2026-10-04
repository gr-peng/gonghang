"""Authenticated integration surface. Model JSON is never an authority channel."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Sequence
from uuid import uuid4
from threading import BoundedSemaphore

from pydantic import Field

from .auth import ControlClaims, CredentialAuthority, IdentityClaims
from .canonical import canonical_json, sha256_hex
from .causal import AgentTrace
from .circuit import SafetyCircuitBreaker
from .content_safety import public_params, screen_text, validate_action_text
from .controller import SafetyController
from .enums import ObligationType
from .lineage import LineageAuthority, VerifiedPayloadProvenanceResolver
from .models import ActionProposal, ObligationToken, StrictModel, TrustedFact
from .outcomes import outcome_from_receipt
from .planning import PlanningLLMAdapter
from .runtime_store import RuntimeStore, SQLiteReceiptLedger
from .tokens import TokenAuthority


class ReviewRequest(StrictModel):
    model_output: str = Field(strict=True, max_length=16_384)
    trace: AgentTrace


class SafetyGateway:
    def __init__(self, *, store: RuntimeStore, policy, fact_authority, tool,
                 fact_supplier: Callable[[str], Sequence[TrustedFact]], lineage_authority: LineageAuthority,
                 identity_authority: CredentialAuthority, control_authority: CredentialAuthority,
                 token_secret: bytes):
        if store.path == ':memory:' or len(token_secret) < 32:
            raise ValueError('gateway requires a persistent store and a 256-bit token key')
        if set(identity_authority.keys.values()) & set(control_authority.keys.values()):
            raise ValueError('identity and control credentials require independent keys')
        self.store, self.tool, self.fact_supplier = store, tool, fact_supplier
        self.lineage, self.identity_authority, self.control_authority = lineage_authority, identity_authority, control_authority
        self.tokens = TokenAuthority('mosaic-gateway', token_secret, state_store=store)
        self.circuit = SafetyCircuitBreaker(state_store=store)
        self.ledger = SQLiteReceiptLedger(store, circuit_namespace=self.circuit.namespace)
        self.controller = SafetyController(policy=policy, fact_authority=fact_authority, token_authority=self.tokens,
            lineage_authority=lineage_authority, circuit_breaker=self.circuit, receipt_ledger=self.ledger)
        self.sandbox = None
        self.audit_secret = None
        self._sandbox_capacity = BoundedSemaphore(2)
        with store._atomic():
            store._db.execute('''CREATE TABLE IF NOT EXISTS gateway_actions (
                handle TEXT PRIMARY KEY, actor_id TEXT NOT NULL, session_id TEXT NOT NULL,
                action_json TEXT NOT NULL, action_digest TEXT NOT NULL)''')
            store._db.execute('''CREATE TABLE IF NOT EXISTS gateway_proofs (
                issuer TEXT NOT NULL, nonce TEXT NOT NULL, PRIMARY KEY(issuer,nonce))''')
            store._db.execute('''CREATE TABLE IF NOT EXISTS gateway_tokens (
                handle TEXT NOT NULL, token_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL)''')

    def authenticate(self, credential: str, scope: str) -> IdentityClaims:
        identity, _ = self.identity_authority.verify(credential, IdentityClaims)
        if self.store.session_revoked(identity.actor_id, identity.session_id):
            raise PermissionError('session revoked')
        if scope not in identity.scopes:
            raise PermissionError('credential lacks required scope')
        # session_revoked may wait for the runtime DB lock. Do not return a
        # principal whose credential expired during that wait.
        self.identity_authority.verify(credential, IdentityClaims)
        return identity

    def admit(self, credential: str) -> bool:
        identity, _ = self.identity_authority.verify(credential, IdentityClaims)
        if self.store.session_revoked(identity.actor_id, identity.session_id):
            raise PermissionError('session revoked')
        return self.store.admit(sha256_hex(identity.actor_id))

    def _load(self, handle: str, identity: IdentityClaims) -> ActionProposal:
        if not isinstance(handle, str) or not 1 <= len(handle) <= 128:
            raise ValueError('invalid action handle')
        with self.store._lock:
            row = self.store._db.execute('SELECT * FROM gateway_actions WHERE handle=?', (handle,)).fetchone()
        if row is None or (row['actor_id'], row['session_id']) != (identity.actor_id, identity.session_id):
            raise PermissionError('action unavailable to this identity and session')
        action = ActionProposal.model_validate_json(row['action_json'])
        if action.digest != row['action_digest'] or (action.actor_id, action.session_id) != (identity.actor_id, identity.session_id):
            raise ValueError('stored action integrity check failed')
        return action

    def _validate_trace(self, trace: AgentTrace, identity: IdentityClaims) -> None:
        if (trace.actor_id, trace.session_id) != (identity.actor_id, identity.session_id):
            raise PermissionError('trace identity differs from authenticated principal')
        if len(canonical_json(trace).encode()) > 131_072 or len(trace.messages) > 64:
            raise ValueError('source trace exceeds limits')
        catalog = trace.source_catalog()
        if len(catalog) > 128:
            raise ValueError('source catalog exceeds limit')
        verified = self.lineage.verified_source_ids(catalog.values())
        if verified != frozenset(catalog):
            raise ValueError('source lineage authentication failed')
        for source in catalog.values():
            if (source.metadata.get('actor_id'), source.metadata.get('session_id')) != (identity.actor_id, identity.session_id):
                raise PermissionError('source does not belong to this identity and session')
        for message in (*trace.messages, *trace.lineage_messages):
            if not self.lineage.content_matches(message.source, {'text': message.text, 'payload': message.payload}):
                raise ValueError('source content was changed after signing')

    @staticmethod
    def _outcome(receipt, action):
        output = outcome_from_receipt(receipt, action=action).model_dump(mode='json')
        output['data'] = public_params(output['data'])
        return output

    def review(self, credential: str, request: ReviewRequest) -> dict:
        identity = self.authenticate(credential, 'agent:review')
        request = ReviewRequest.model_validate(request.model_dump())
        self._validate_trace(request.trace, identity)
        planner = PlanningLLMAdapter(lambda _messages, _seed: request.model_output,
                                    provenance_resolver=VerifiedPayloadProvenanceResolver(self.lineage))
        result = planner.plan(request.trace, seed=0)
        if result.clarification:
            return {'status': 'needs_clarification', 'missing_fields': list(result.clarification.missing_fields),
                    'message': result.clarification.question}
        if result.action is None:
            return {'status': 'no_action', 'message': '本轮未提交操作；此前操作须通过可信取消接口处理。'}
        action = result.action
        validate_action_text(action.params)
        # A failed fact service never yields an allow; no write takes place here.
        receipt = self.controller.decide(action, facts=tuple(self.fact_supplier(identity.actor_id)))
        handle = str(uuid4())
        with self.store._atomic():
            self.store._db.execute('INSERT INTO gateway_actions VALUES (?,?,?,?,?)',
                (handle, identity.actor_id, identity.session_id, canonical_json(action), action.digest))
            self.store._event('action_reviewed', handle=handle, action_digest=action.digest, receipt_hash=receipt.receipt_hash)
        return {'status': 'reviewed', 'handle': handle, 'action_digest': action.digest,
                'level': receipt.formal.decision.final_level.name.lower(), 'params': public_params(action.params),
                'outcome': self._outcome(receipt, action)}

    def confirmation_view(self, credential: str, handle: str) -> dict:
        identity = self.authenticate(credential, 'human:confirm')
        action = self._load(handle, identity)
        # This exact view goes only to the trusted confirmation UI, never to LLM
        # context. Masking before confirmation would hide a changed recipient.
        return {'handle': handle, 'action_digest': action.digest, 'action_type': action.action_type,
                'params': action.params, 'message': '请核对完整参数；此页面本身不代表已确认。'}

    def _control(self, signed: str, identity: IdentityClaims, target: str, binding: str, operations: set[str]):
        claim, envelope = self.control_authority.verify(signed, ControlClaims)
        if ((claim.actor_id, claim.session_id) != (identity.actor_id, identity.session_id)
                or claim.target != target or claim.binding != binding or claim.operation not in operations):
            raise PermissionError('control proof does not match principal, operation and exact target')
        return claim, envelope

    def _consume_proof_locked(self, envelope):
        inserted = self.store._db.execute('INSERT OR IGNORE INTO gateway_proofs VALUES (?,?)',
                                         (envelope.issuer, envelope.nonce)).rowcount
        if not inserted:
            raise ValueError('control proof already used')

    def approve(self, credential: str, handle: str, signed: str) -> dict:
        identity = self.authenticate(credential, 'human:confirm')
        action = self._load(handle, identity)
        claim, envelope = self._control(signed, identity, handle, action.digest, {'confirmation', 'mfa'})
        with self.store._atomic():
            self.authenticate(credential, 'human:confirm')
            # Check again under the mutation lock so a delayed proof cannot mint
            # fresh authority after its expiry or be replayed on another worker.
            self.control_authority.verify(signed, ControlClaims)
            self._consume_proof_locked(envelope)
            issued_at = datetime.now(timezone.utc)
            token = self.tokens.issue(action, ObligationType(claim.operation), ttl_seconds=120,
                now=issued_at, expires_at=datetime.fromtimestamp(envelope.expires_at, timezone.utc))
            self.store._db.execute('INSERT INTO gateway_tokens VALUES (?,?,?)', (handle, token.token_id, canonical_json(token)))
            self.store._event('approval_verified', handle=handle, obligation=claim.operation, proof_nonce=envelope.nonce)
        return {'accepted': True, 'obligation': claim.operation, 'handle': handle}

    def execute(self, credential: str, handle: str) -> dict:
        identity = self.authenticate(credential, 'agent:execute')
        action = self._load(handle, identity)
        with self.store._lock:
            rows = self.store._db.execute('SELECT payload_json FROM gateway_tokens WHERE handle=?', (handle,)).fetchall()
        tokens = tuple(ObligationToken.model_validate_json(row[0]) for row in rows)
        def fresh_facts():
            # The controller checks identity separately before facts and at
            # dispatch, so identity failures never become bank-failure counters.
            return self.fact_supplier(identity.actor_id)
        try:
            receipt = self.controller.execute(action, fact_supplier=fresh_facts,
                                              tool=self.tool, tokens=tokens, request_id=handle,
                                              verify_principal=lambda: self.authenticate(credential, 'agent:execute'))
            return self._outcome(receipt, action)
        except Exception as exc:
            # Past the execution boundary even a ValueError can be a post-commit
            # storage/receipt fault. Never label it as bad client input: callers
            # must retain this handle and reconcile, not create another payment.
            raise RuntimeError('execution outcome unavailable; retain handle and reconcile') from exc

    def cancel(self, credential: str, handle: str, signed: str) -> dict:
        identity = self.authenticate(credential, 'human:confirm')
        action = self._load(handle, identity)
        if not action.write_action:
            raise ValueError('cancellation is defined for pending write requests only')
        _, envelope = self._control(signed, identity, handle, action.digest, {'cancel'})
        def verify_control():
            current_identity = self.authenticate(credential, 'human:confirm')
            _, current_envelope = self._control(signed, current_identity, handle, action.digest, {'cancel'})
            self._consume_proof_locked(current_envelope)
        key = self.store.request_key(self.tokens.issuer, action, handle)
        cancelled = self.store.cancel(key, action, self.tool.name, operator_id=identity.actor_id,
                                      reason='user_requested', verify_control=verify_control)
        return {'handle': handle, 'cancelled': cancelled,
                'message': '已禁止后续提交。' if cancelled else '执行已开始或已有结果，不能取消；请人工核账。'}

    def reset_binding(self, actor: str, generation: int) -> str:
        return sha256_hex(['circuit-reset-v1', self.circuit.namespace, actor, generation])

    def reset(self, credential: str, actor: str, signed: str) -> dict:
        identity = self.authenticate(credential, 'operator:reset')
        with self.store._atomic():
            self.authenticate(credential, 'operator:reset')
            state = self.circuit.snapshot(actor)
            _, envelope = self._control(signed, identity, actor, self.reset_binding(actor, state['generation']), {'reset'})
            self._consume_proof_locked(envelope)
            self.store._reset_circuit_locked(self.circuit.namespace, actor, identity.actor_id, 'operator_reviewed', state['generation'])
        return self.circuit.snapshot(actor)

    def screen(self, credential: str, text: str) -> dict:
        self.authenticate(credential, 'agent:review')
        return screen_text(text)

    @staticmethod
    def session_binding(actor: str, session: str) -> str:
        return sha256_hex(['revoke-session-v1', actor, session])

    def revoke_session(self, credential: str, actor: str, session: str, signed: str) -> dict:
        identity = self.authenticate(credential, 'operator:revoke')
        with self.store._atomic():
            self.authenticate(credential, 'operator:revoke')
            _, envelope = self._control(signed, identity, actor, self.session_binding(actor, session), {'revoke_session'})
            self._consume_proof_locked(envelope)
            self.store._db.execute('INSERT OR IGNORE INTO revoked_sessions VALUES (?,?)', (actor, session))
            self.store._event('session_revoked', actor_id=actor, session_id=session, operator_id=identity.actor_id)
        return {'revoked': True, 'message': '此会话不能再授权新的执行；已开始的操作须人工核账。'}

    def checkpoint(self, credential: str) -> dict:
        from .audit import sign_checkpoint
        self.authenticate(credential, 'audit:read')
        if self.audit_secret is None:
            raise RuntimeError('audit signing key not configured')
        with self.store._atomic():
            return sign_checkpoint(self.ledger.chain_id, self.ledger.receipts, self.store.events(), self.audit_secret)

    def operator_state(self, credential: str, actor: str) -> dict:
        self.authenticate(credential, 'audit:read')
        state = self.circuit.snapshot(actor)
        return {'actor_id': actor, **state, 'reset_binding': self.reset_binding(actor, state['generation'])}

    def reconcile(self, credential: str, handle: str) -> dict:
        operator = self.authenticate(credential, 'audit:read')
        with self.store._lock:
            row = self.store._db.execute('SELECT action_json,action_digest FROM gateway_actions WHERE handle=?', (handle,)).fetchone()
        if row is None:
            raise PermissionError('action unavailable')
        action = ActionProposal.model_validate_json(row['action_json'])
        if action.digest != row['action_digest']:
            raise ValueError('stored action integrity check failed')
        if not callable(getattr(self.tool, 'reconcile', None)):
            raise RuntimeError('backend reconciliation adapter not configured')
        result = self.tool.reconcile(action)
        if result is not None:
            from .outcomes import validate_tool_result
            result = validate_tool_result(action, result)
        with self.store._atomic():
            self.store._event('operator_reconciled', operator_id=operator.actor_id, handle=handle,
                              backend_result_digest=sha256_hex(result))
        return {'handle': handle, 'action_digest': action.digest, 'backend_status': 'posted' if result else 'not_found',
                'backend_result': result, 'automatic_redispatch': False,
                'message': '核账只读，不修改历史回执，也不撤回已经提交的交易。'}

    def run_code(self, credential: str, code: str) -> dict:
        identity = self.authenticate(credential, 'sandbox:run')
        if self.circuit.snapshot(identity.actor_id)['locked'] or self.sandbox is None:
            raise RuntimeError('sandbox unavailable or actor locked')
        if not self._sandbox_capacity.acquire(blocking=False):
            raise RuntimeError('sandbox capacity reached')
        try:
            # Audit intent commits before launching any container.
            with self.store._atomic():
                self.store._event('sandbox_requested', actor_id=identity.actor_id, code_sha256=sha256_hex(code))
            try:
                result = self.sandbox.run(code)
            except Exception:
                with self.store._atomic():
                    self.store._event('sandbox_unavailable', actor_id=identity.actor_id, code_sha256=sha256_hex(code))
                self.store.record_rejection(self.circuit.namespace, identity.actor_id, 'sandbox_unavailable')
                raise
            with self.store._atomic():
                self.store._event('sandbox_finished', actor_id=identity.actor_id, status=result.status,
                    code_sha256=result.code_sha256, image=result.image, output_sha256=sha256_hex([result.stdout, result.stderr]),
                    cleanup_verified=result.cleanup_verified, duration_seconds=result.duration_seconds)
            if result.status != 'succeeded':
                self.store.record_rejection(self.circuit.namespace, identity.actor_id, 'sandbox_failure')
            output = result.model_dump(mode='json')
            output['stdout'] = screen_text(result.stdout)['safe_text']
            output['stderr'] = screen_text(result.stderr)['safe_text']
            output['trusted_for_banking'] = False
            return output
        finally:
            self._sandbox_capacity.release()
