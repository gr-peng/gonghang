from scns_guard.engineering_demo import run_engineering_demo


def test_engineering_demo_completes_with_exact_effects(root):
    result = run_engineering_demo(policy_path=root / 'configs/transfer_policy.yaml')
    assert result['passed'] == result['attempts'] == 16
    assert result['execution_count'] == 2
    assert result['receipts_replay_valid']
