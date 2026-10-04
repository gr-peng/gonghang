import pytest

from scns_guard.client import SafetyClient, SafetyServiceError

from .test_service import service
from .test_gateway import identity, review_request, proof


def test_team_client_and_operator_reconciliation(service):
    _, runtime, server = service
    g = runtime.gateway
    cred = identity(g.identity_authority)
    client = SafetyClient(f'http://127.0.0.1:{server.server_port}', cred)
    reviewed = client.review(review_request(g.lineage))
    client.approve(reviewed['handle'], proof(g.control_authority, reviewed))
    assert client.execute(reviewed['handle'])['status'] == 'succeeded'
    with pytest.raises(SafetyServiceError):
        client.post('/v1/operator/reconcile', {'handle': reviewed['handle']})
    auditor = SafetyClient(client.base_url, identity(g.identity_authority, scopes=('audit:read',)))
    found = auditor.post('/v1/operator/reconcile', {'handle': reviewed['handle']})
    assert found['backend_status'] == 'posted'
    assert found['automatic_redispatch'] is False
    state = auditor.post('/v1/operator/state', {'actor_id': 'user-1'})
    assert 'reset_binding' in state


def test_client_forbids_cleartext_remote_and_embedded_credentials():
    for url in ('http://remote.example', 'https://user:pass@example.com', 'https://example.com?key=bad'):
        with pytest.raises(ValueError):
            SafetyClient(url, 'credential')
