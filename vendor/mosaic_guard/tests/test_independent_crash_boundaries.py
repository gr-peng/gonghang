"""Extra real process-death points (mock only), complement existing before/after bank tests."""
import json
import subprocess
import sys

import pytest

from scns_guard.deployment import initialize, build_demo_runtime
from .test_gateway import identity, proof, review_request


@pytest.mark.parametrize('when,status,effects,consumed,locked', [
    ('after_bind', 'succeeded', 1, 1, False),
    ('after_reserve', 'unknown', 0, 1, True),
    ('after_receipt', 'unknown', 1, 1, True),
    ('after_finish', 'succeeded', 1, 1, False),
])
def test_crash_at_durable_boundary_never_redispatches_started_work(tmp_path, root, when, status, effects, consumed, locked):
    config = initialize(tmp_path / 'private', policy_path=root / 'configs/transfer_policy.yaml')
    runtime = build_demo_runtime(config)
    credential = identity(runtime.gateway.identity_authority)
    review = runtime.gateway.review(credential, review_request(runtime.gateway.lineage))
    runtime.gateway.approve(credential, review['handle'], proof(runtime.gateway.control_authority, review))
    runtime.close()
    result = subprocess.run([sys.executable, str(root / 'scripts/crash_safety_fixture.py'), '--mock-fixture'],
        input=json.dumps({'config': str(config), 'credential': credential, 'handle': review['handle'], 'when': when}),
        text=True, capture_output=True, timeout=15)
    assert result.returncode == 73, result.stderr
    runtime = build_demo_runtime(config)
    try:
        first = runtime.gateway.execute(credential, review['handle'])
        assert first['status'] == status
        second = runtime.gateway.execute(credential, review['handle'])
        if status == 'succeeded':
            assert second == first  # Terminal receipts are immutable history.
        else:
            # Each unresolved observation may append an audit receipt with a new
            # hash; this is NOT a second bank dispatch or a terminal result.
            assert {k: v for k, v in second.items() if k != 'receipt_hash'} == {
                k: v for k, v in first.items() if k != 'receipt_hash'}
        assert runtime.bank.snapshot()['execution_count'] == effects
        assert runtime.store._db.execute('SELECT count(*) FROM token_lifecycle').fetchone()[0] == consumed
        assert runtime.gateway.circuit.snapshot('user-1')['locked'] == locked
    finally:
        runtime.close()
