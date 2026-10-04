"""Small bounded loopback adapter. Public exposure requires an authenticated TLS proxy."""
from __future__ import annotations

import math
import socket
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import BoundedSemaphore, Timer
from uuid import uuid4

from pydantic import Field

from .gateway import ReviewRequest
from .llm_adapter import parse_model_json
from .models import StrictModel


class HandleRequest(StrictModel):
    handle: str = Field(strict=True, min_length=1, max_length=128)


class ProofRequest(HandleRequest):
    proof: str = Field(strict=True, min_length=1, max_length=16_384)


class ResetRequest(StrictModel):
    actor_id: str = Field(strict=True, min_length=1, max_length=128)
    proof: str = Field(strict=True, min_length=1, max_length=16_384)


class RevokeSessionRequest(ResetRequest):
    session_id: str = Field(strict=True, min_length=1, max_length=128)


class ActorRequest(StrictModel):
    actor_id: str = Field(strict=True, min_length=1, max_length=128)


class TextRequest(StrictModel):
    text: str = Field(strict=True, max_length=32_768)


class CodeRequest(StrictModel):
    code: str = Field(strict=True, max_length=32_768)


class EmptyRequest(StrictModel):
    pass


class SafetyHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, gateway, *, ingress_timeout_seconds: float = 5.0):
        if address[0] != '127.0.0.1':
            raise ValueError('HTTP adapter must bind IPv4 loopback; terminate TLS at a trusted proxy')
        if (isinstance(ingress_timeout_seconds, bool) or not isinstance(ingress_timeout_seconds, (int, float))
                or not math.isfinite(ingress_timeout_seconds) or not 0 < ingress_timeout_seconds <= 30):
            raise ValueError('ingress timeout must be finite and in (0, 30] seconds')
        self.ingress_timeout_seconds = float(ingress_timeout_seconds)
        self.gateway = gateway
        self.capacity = BoundedSemaphore(16)
        super().__init__(address, SafetyHandler)

    def process_request(self, request, client_address):
        if not self.capacity.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.capacity.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.capacity.release()


class SafetyHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'MOSAIC'
    sys_version = ''

    def setup(self):
        super().setup()
        self.connection.settimeout(3)
        # A socket timeout applies to individual reads. A trickle of bytes must
        # not keep a worker occupied forever, including before authentication.
        self._ingress_deadline = time.monotonic() + self.server.ingress_timeout_seconds
        self._ingress_timer = Timer(self.server.ingress_timeout_seconds, self._expire_ingress)
        self._ingress_timer.daemon = True
        self._ingress_timer.start()

    def _expire_ingress(self):
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def _finish_ingress(self):
        self._ingress_timer.cancel()
        if time.monotonic() >= self._ingress_deadline:
            raise TimeoutError('absolute request ingress deadline exceeded')

    def finish(self):
        self._ingress_timer.cancel()
        try:
            super().finish()
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass

    def log_message(self, *_args):
        # Do not put credentials, prompts, account data or raw URLs in access logs.
        pass

    def _send(self, status, payload):
        import json
        raw = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
        self._ingress_timer.cancel()
        self.close_connection = True
        try:
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            # Peer disappearance cannot change a committed operation's status.
            pass

    def send_error(self, code, message=None, explain=None):
        self._send(code, {'error': 'invalid_http_request', 'request_id': str(uuid4())})

    def do_GET(self):
        try:
            self._finish_ingress()
        except TimeoutError:
            self.close_connection = True
            return
        self._send(200, {'status': 'up', 'mode': 'safety-backend'}) if self.path == '/health' else self.send_error(404)

    def do_POST(self):
        credential = None
        try:
            if len(self.path) > 128 or '?' in self.path:
                raise ValueError('invalid route')
            if self.headers.get_all('Transfer-Encoding'):
                raise ValueError('transfer encoding not accepted')
            lengths = self.headers.get_all('Content-Length', [])
            if len(lengths) != 1 or not lengths[0].isdigit() or not 1 <= int(lengths[0]) <= 262_144:
                raise ValueError('invalid request length')
            content_types = self.headers.get_all('Content-Type', [])
            if len(content_types) != 1 or content_types[0].split(';')[0].strip().lower() != 'application/json':
                raise ValueError('one unambiguous JSON content type required')
            auth = self.headers.get_all('Authorization', [])
            if len(auth) != 1 or not auth[0].startswith('Bearer ') or len(auth[0]) > 16_400:
                self._send(401, {'error': 'authentication_required', 'request_id': str(uuid4())})
                return
            credential = auth[0][7:]
            # Authenticate before parsing or echoing any request body.
            from .auth import IdentityClaims
            self.server.gateway.identity_authority.verify(credential, IdentityClaims)
            if not self.server.gateway.admit(credential):
                self._send(429, {'error': 'rate_limit_exceeded', 'request_id': str(uuid4())})
                return
            raw = self.rfile.read(int(lengths[0]))
            if len(raw) != int(lengths[0]):
                raise ValueError('incomplete request body')
            self._finish_ingress()  # Stop the ingress watchdog BEFORE business execution.
            body = parse_model_json(raw.decode('utf-8'))
            g = self.server.gateway
            if self.path == '/v1/review':
                result = g.review(credential, ReviewRequest.model_validate(body))
            elif self.path in ('/v1/execute', '/v1/confirmation-view'):
                request = HandleRequest.model_validate(body)
                method = g.execute if self.path.endswith('execute') else g.confirmation_view
                result = method(credential, request.handle)
            elif self.path in ('/v1/approve', '/v1/cancel'):
                request = ProofRequest.model_validate(body)
                method = g.approve if self.path.endswith('approve') else g.cancel
                result = method(credential, request.handle, request.proof)
            elif self.path == '/v1/reset':
                request = ResetRequest.model_validate(body)
                result = g.reset(credential, request.actor_id, request.proof)
            elif self.path == '/v1/revoke-session':
                request = RevokeSessionRequest.model_validate(body)
                result = g.revoke_session(credential, request.actor_id, request.session_id, request.proof)
            elif self.path == '/v1/screen':
                result = g.screen(credential, TextRequest.model_validate(body).text)
            elif self.path == '/v1/sandbox':
                result = g.run_code(credential, CodeRequest.model_validate(body).code)
            elif self.path == '/v1/audit/checkpoint':
                EmptyRequest.model_validate(body)
                result = g.checkpoint(credential)
            elif self.path == '/v1/operator/state':
                result = g.operator_state(credential, ActorRequest.model_validate(body).actor_id)
            elif self.path == '/v1/operator/reconcile':
                result = g.reconcile(credential, HandleRequest.model_validate(body).handle)
            else:
                self.send_error(404)
                return
            self._send(200, result)
        except PermissionError:
            self._record_rejection(credential, 'permission_denied')
            self._send(403, {'error': 'permission_denied', 'request_id': str(uuid4())})
        except (ValueError, TypeError, RecursionError):
            self._record_rejection(credential, 'invalid_request')
            self._send(400, {'error': 'invalid_or_untrusted_request', 'request_id': str(uuid4())})
        except (TimeoutError, socket.timeout):
            self.close_connection = True
        except Exception:
            # Detailed diagnostic data stays in the trusted audit/state layer.
            self._send(503, {'error': 'service_unavailable_no_automatic_retry', 'request_id': str(uuid4())})

    def _record_rejection(self, credential, reason):
        from .auth import IdentityClaims
        try:
            g = self.server.gateway
            # Validation and counting share the revocation transaction boundary.
            with g.store._atomic():
                identity, _ = g.identity_authority.verify(credential, IdentityClaims)
                if g.store.session_revoked(identity.actor_id, identity.session_id):
                    return
                g.store._record_rejection_locked(g.circuit.namespace, identity.actor_id, reason)
        except Exception:
            # Invalid credentials cannot choose a victim actor. No execution is
            # attempted when admission or audit storage is unavailable.
            pass
