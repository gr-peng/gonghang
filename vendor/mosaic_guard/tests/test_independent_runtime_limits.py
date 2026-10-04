"""Bounded local fault/HTTP tests; all sockets bind loopback and locks are temporary."""
import json
import socket
import subprocess
import sys
import threading
import time
from http.client import HTTPConnection

import pytest

from scns_guard.auth import ControlClaims
from scns_guard.policy import PolicyEngine, PolicyError
from .test_gateway import gateway, identity, proof, review_request
from .test_service import service, request
from .test_durable_bank import bank_at


def test_new_incident_invalidates_earlier_reset_approval(gateway):
    g, auth, proofs, _, _ = gateway
    g.store.record_rejection(g.circuit.namespace, 'user-1', 'first')
    state = g.circuit.snapshot('user-1')
    op = identity(auth, actor='operator', session='ops', scopes=('operator:reset',))
    signed = proofs.issue(ControlClaims(operation='reset', actor_id='operator', session_id='ops',
        target='user-1', binding=g.reset_binding('user-1', state['generation'])), key_id='v1')
    g.store.record_rejection(g.circuit.namespace, 'user-1', 'new-incident')
    with pytest.raises((ValueError, PermissionError)):
        g.reset(op, 'user-1', signed)
    assert g.circuit.snapshot('user-1')['denials'] == 2


def test_post_commit_validation_error_is_not_reported_as_bad_client_input(service, monkeypatch):
    _, runtime, server = service
    g = runtime.gateway
    cred = identity(g.identity_authority)
    reviewed = g.review(cred, review_request(g.lineage))
    g.approve(cred, reviewed['handle'], proof(g.control_authority, reviewed))
    def disk_corruption(*args, **kwargs):
        raise ValueError('synthetic terminal receipt validation fault')
    monkeypatch.setattr(g.store, 'finish', disk_corruption)
    status, output = request(server, '/v1/execute', {'handle': reviewed['handle']}, cred)
    assert runtime.bank.snapshot()['execution_count'] == 1
    assert status == 503, 'a possibly committed write must not be classified as invalid client input'
    assert output['error'] == 'service_unavailable_no_automatic_retry'
    assert g.execute(cred, reviewed['handle'])['status'] == 'unknown'
    assert runtime.bank.snapshot()['execution_count'] == 1


def test_duplicate_content_type_is_rejected(service):
    _, runtime, server = service
    credential = identity(runtime.gateway.identity_authority)
    client = HTTPConnection(*server.server_address, timeout=2)
    try:
        client.putrequest('POST', '/v1/screen')
        client.putheader('Authorization', 'Bearer ' + credential)
        client.putheader('Content-Length', '13')
        client.putheader('Content-Type', 'application/json')
        client.putheader('Content-Type', 'text/plain')
        client.endheaders(b'{"text":"ok"}')
        # Exact body length is 13; no incomplete-packet behavior involved.
        response = client.getresponse()
        assert response.status == 400
        response.read()
    finally:
        client.close()


@pytest.mark.parametrize('stage', ['headers', 'body'])
def test_trickled_request_cannot_renew_total_ingress_deadline(service, stage):
    _, runtime, server = service
    server.ingress_timeout_seconds = 0.2
    sock = socket.create_connection(server.server_address, timeout=1)
    sock.settimeout(0.08)
    if stage == 'headers':
        sock.sendall(b'POST /v1/screen HTTP/1.1\r\nHost: localhost\r\nX-Slow: ')
    else:
        credential = identity(runtime.gateway.identity_authority)
        sock.sendall(('POST /v1/screen HTTP/1.1\r\nHost: localhost\r\n'
            'Content-Type: application/json\r\nContent-Length: 10000\r\n'
            'Authorization: Bearer ' + credential + '\r\n\r\n').encode())
    try:
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline:
            try:
                sock.sendall(b' ')
            except OSError:
                break
            time.sleep(0.02)
        # Leave ample scheduling slack; peer closure returns data/EOF, not a
        # per-recv timeout renewed indefinitely by an occasional incoming byte.
        try:
            sock.recv(65536)
        except (ConnectionResetError, BrokenPipeError):
            pass
        except socket.timeout:
            pytest.fail('slow connection stayed open beyond the total ingress deadline')
    finally:
        sock.close()
    # Normal clients must still be usable, not an all-deny implementation.
    cred = identity(runtime.gateway.identity_authority, actor='healthy', session='normal')
    assert request(server, '/v1/screen', {'text': 'normal'}, cred)[0] == 200


def test_cross_process_bank_lock_has_a_deadline(tmp_path):
    path = tmp_path / 'bank.db'
    bank = bank_at(path)
    bank.lock_timeout_seconds = 0.1
    holder = subprocess.Popen([sys.executable, '-c',
        'import fcntl,sys,time; f=open(sys.argv[1],"a"); '
        'fcntl.flock(f,fcntl.LOCK_EX); print("ready",flush=True); time.sleep(0.8)',
        str(path) + '.lock'], stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == 'ready'
        start = time.monotonic()
        with pytest.raises(TimeoutError):
            bank.snapshot()
        assert time.monotonic() - start < 0.6
    finally:
        holder.terminate()
        holder.communicate(timeout=3)
    assert bank.snapshot()['execution_count'] == 0
    bank.close()


@pytest.mark.parametrize('payload', [
    'metadata:\n  first: &anchor {x: 1}\n  second: *anchor\n',
    'metadata:\n  value: ' + '[' * 200 + '0' + ']' * 200 + '\n',
])
def test_yaml_complexity_errors_are_explicit_policy_errors(tmp_path, root, payload):
    file = tmp_path / 'policy.yaml'
    base = (root / 'configs/transfer_policy.yaml').read_text().split('\nmetadata:', 1)[0]
    file.write_text(base + '\n' + payload)
    with pytest.raises(PolicyError, match='(alias|budget|depth|nesting)'):
        PolicyEngine.from_yaml(file)
