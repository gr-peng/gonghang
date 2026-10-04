import json
import subprocess
import sys

import pytest

from scns_guard.deployment import initialize, build_demo_runtime

from .test_gateway import identity, proof, review_request


@pytest.mark.parametrize('when', ['before', 'after'])
def test_real_process_exit_preserves_reservation_and_durable_bank(tmp_path, root, when):
    config = initialize(tmp_path / 'private', policy_path=root / 'configs/transfer_policy.yaml')
    runtime = build_demo_runtime(config)
    g = runtime.gateway
    cred = identity(g.identity_authority)
    review = g.review(cred, review_request(g.lineage))
    g.approve(cred, review['handle'], proof(g.control_authority, review))
    action = g._load(review['handle'], g.authenticate(cred, 'agent:execute'))
    runtime.close()
    result = subprocess.run([sys.executable, str(root / 'scripts/crash_safety_fixture.py'), '--mock-fixture'],
        input=json.dumps({'config': str(config), 'credential': cred, 'handle': review['handle'], 'when': when}),
        text=True, capture_output=True, timeout=15)
    assert result.returncode == 73
    runtime = build_demo_runtime(config)
    try:
        g = runtime.gateway
        assert g.execute(cred, review['handle'])['status'] == 'unknown'
        assert runtime.bank.snapshot()['execution_count'] == int(when == 'after')
        assert bool(runtime.bank.reconcile(action)) == (when == 'after')
        assert g.circuit.snapshot('user-1')['locked']
    finally:
        runtime.close()
