"""Five Qwen calls followed by mock-only safety integration checks."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path

from scns_guard.adapters.openai_compatible import OpenAICompatibleConfig, OpenAICompatibleGenerator
from scns_guard.planning import PLANNER_VERSION, PLANNING_SYSTEM_PROMPT
from scns_guard.runtime_demo import run_runtime_demo

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--run-id', required=True)
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,99}', args.run_id):
        raise SystemExit('invalid run ID')
    run_dir = ROOT / 'artifacts/model_smoke' / args.run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    snapshot = run_dir / 'source_snapshot'
    shutil.copytree(ROOT / 'src', snapshot / 'src', ignore=shutil.ignore_patterns('__pycache__', '*.egg-info'))
    shutil.copy2(__file__, snapshot / 'run_runtime_safety_smoke.py')
    shutil.copy2(ROOT / 'configs/transfer_policy.yaml', snapshot / 'transfer_policy.yaml')
    config = OpenAICompatibleConfig.model_validate_json((ROOT / 'configs/qwen38_27b_sglang.json').read_text())
    config = config.model_copy(update={'base_url': args.base_url, 'system_prompt': PLANNING_SYSTEM_PROMPT,
        'prompt_template_id': PLANNER_VERSION, 'capture_token_spans': False, 'source_metadata_mode': 'full', 'max_tokens': 512})
    (run_dir / 'config.json').write_text(config.model_dump_json(indent=2), encoding='utf-8')
    generator = OpenAICompatibleGenerator(config, trace_path=run_dir / 'model_calls.jsonl')
    summary = run_runtime_demo(policy_path=ROOT / 'configs/transfer_policy.yaml',
                               receipt_path=run_dir / 'receipts.jsonl', generator=generator)
    summary['run_id'] = args.run_id
    summary['model_fingerprint_boundary'] = 'reused_config_metadata_not_full_weight_byte_verification'
    (run_dir / 'summary.json').write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding='utf-8')
    hashes = {str(p.relative_to(run_dir)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(run_dir.rglob('*')) if p.is_file()}
    (run_dir / 'manifest.json').write_text(json.dumps({'run_id': args.run_id, 'file_sha256': hashes}, indent=2))
    print(json.dumps({key: summary[key] for key in ('run_id', 'attempts', 'passed', 'receipt_count', 'receipts_replay_valid')}))
    return 0 if summary['passed'] == summary['attempts'] and summary['receipts_replay_valid'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
