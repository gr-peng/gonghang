import json
import stat
import threading
from http.client import HTTPConnection

import pytest

from scns_guard.deployment import initialize, build_demo_runtime
from scns_guard.http_service import SafetyHTTPServer

from .test_gateway import identity, proof, review_request


@pytest.fixture
def service(tmp_path, root):
    config = initialize(tmp_path / 'private', policy_path=root / 'configs/transfer_policy.yaml')
    runtime = build_demo_runtime(config)
    server = SafetyHTTPServer(('127.0.0.1', 0), runtime.gateway)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield config, runtime, server
    server.shutdown()
    server.server_close()
    thread.join(5)
    runtime.close()


def request(server, path, data, credential=None, **headers):
    conn = HTTPConnection(*server.server_address, timeout=5)
    headers = {'Content-Type': 'application/json', **headers}
    if credential:
        headers['Authorization'] = 'Bearer ' + credential
    conn.request('POST', path, body=data if isinstance(data, str) else json.dumps(data), headers=headers)
    response = conn.getresponse()
    status, payload = response.status, json.loads(response.read())
    conn.close()
    return status, payload


def test_http_flow_strict_body_authentication_and_grounded_response(service):
    _, runtime, server = service
    g = runtime.gateway
    cred = identity(g.identity_authority)
    req = review_request(g.lineage).model_dump(mode='json')
    assert request(server, '/v1/review', req)[0] == 401
    status, review = request(server, '/v1/review', req, cred)
    assert status == 200
    signed = proof(g.control_authority, review)
    assert request(server, '/v1/approve', {'handle': review['handle'], 'proof': signed}, cred)[0] == 200
    status, result = request(server, '/v1/execute', {'handle': review['handle']}, cred)
    assert status == 200 and result['status'] == 'succeeded'
    assert request(server, '/v1/execute', {'handle': review['handle'], 'actor_id': 'admin'}, cred)[0] == 400
    assert request(server, '/v1/execute', '{"handle":"a","handle":"b"}', cred)[0] == 400
    assert request(server, '/v1/execute', {}, cred, **{'Transfer-Encoding': 'chunked'})[0] == 400
    assert request(server, '/v1/issue-token', {}, cred)[0] == 404
    status, error = request(server, '/v1/execute', {'handle': 'not-owned'}, cred)
    assert status == 403
    assert set(error) == {'error', 'request_id'}


def test_service_configuration_private_keys_and_pinned_policy(service, tmp_path):
    config, _, _ = service
    assert stat.S_IMODE(config.parent.stat().st_mode) == 0o700
    raw = json.loads(config.read_text())
    secret = config.parent / 'identity.key'
    assert stat.S_IMODE(secret.stat().st_mode) == 0o600
    secret.chmod(0o644)
    with pytest.raises(ValueError, match='private'):
        build_demo_runtime(config)
    secret.chmod(0o600)
    raw['policy_sha256'] = '0' * 64
    config.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match='policy'):
        build_demo_runtime(config)


def test_public_screening_masks_credentials_and_contact_info(service):
    _, runtime, server = service
    cred = identity(runtime.gateway.identity_authority)
    status, result = request(server, '/v1/screen', {'text': '密码：abc123 邮箱 test@example.com 电话 13812345678'}, cred)
    assert status == 200
    assert result['redactions'] == 3
    assert 'abc123' not in result['safe_text']
    assert 'test@example.com' not in result['safe_text']


def test_non_loopback_http_is_rejected(service):
    _, runtime, _ = service
    with pytest.raises(ValueError, match='loopback'):
        SafetyHTTPServer(('0.0.0.0', 0), runtime.gateway)


def test_gateway_reopen_preserves_actions_approvals_and_bank_effect(service):
    config, runtime, _ = service
    g = runtime.gateway
    cred = identity(g.identity_authority)
    review = g.review(cred, review_request(g.lineage))
    signed = proof(g.control_authority, review)
    g.approve(cred, review['handle'], signed)
    second = build_demo_runtime(config)
    try:
        assert second.gateway.execute(cred, review['handle'])['status'] == 'succeeded'
        assert g.execute(cred, review['handle'])['status'] == 'succeeded'
        assert runtime.bank.snapshot()['execution_count'] == 1
        with pytest.raises(ValueError, match='used'):
            second.gateway.approve(cred, review['handle'], signed)
    finally:
        second.close()
