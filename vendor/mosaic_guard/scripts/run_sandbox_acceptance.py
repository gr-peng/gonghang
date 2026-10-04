"""Run actual local-container probes and save bounded, synthetic-only evidence."""
import argparse
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from scns_guard.sandbox import DockerSandbox, SandboxConfig


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    runner = DockerSandbox(SandboxConfig(image=args.image, timeout_seconds=3, output_bytes=4096))
    with tempfile.TemporaryDirectory(prefix='mosaic-sandbox-host-fixture-') as directory:
        host_file = Path(directory) / 'private.txt'
        host_file.write_text('synthetic-host-secret')
        cases = [
            ('normal_calculation', 'print(6 * 7)', 'succeeded'),
            ('network_isolation', 'import socket; socket.create_connection(("1.1.1.1",443),timeout=1)', 'failed'),
            ('readonly_root', 'open("/root-write-probe","w").write("x")', 'failed'),
            ('host_file_isolation', f'print(open({str(host_file)!r}).read())', 'failed'),
            ('nonroot_no_inherited_secret', 'import os; print(os.getuid()); print(os.environ.get("MOSAIC_TEST_SECRET"))', 'succeeded'),
            ('wall_timeout', 'while True: pass', 'timeout'),
            ('output_limit', 'print("x" * 100000)', 'output_limit'),
            ('memory_limit', 'print(len(bytearray(512 * 1024 * 1024)))', 'failed'),
        ]
        results = []
        for name, code, expected in cases:
            result = runner.run(code)
            passed = result.status == expected and result.cleanup_verified
            if name == 'normal_calculation':
                passed &= result.stdout.strip() == '42'
            if name == 'nonroot_no_inherited_secret':
                passed &= result.stdout.strip() == '65534\nNone'
            # Limit diagnostic output independently of the runtime byte cap.
            results.append({'name': name, 'expected': expected, 'passed': passed,
                **result.model_dump(mode='json'), 'stdout': result.stdout[:256], 'stderr': result.stderr[:512]})
    payload = {'created_at': datetime.now(timezone.utc).isoformat(), 'image': args.image,
        'evidence_boundary': 'authored harmless probes in actual local Docker containers; not proof against kernel escape',
        'passed': sum(item['passed'] for item in results), 'attempts': len(results),
        'remaining_owned_containers': runner.remaining_containers(), 'results': results}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({key: value for key, value in payload.items() if key != 'results'}, ensure_ascii=False))
    if payload['passed'] != payload['attempts'] or payload['remaining_owned_containers']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
