"""Initialize private keys or serve the explicitly selected mock backend."""
import argparse
from pathlib import Path

from scns_guard.deployment import initialize, build_demo_runtime
from scns_guard.http_service import SafetyHTTPServer


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    init = sub.add_parser('init')
    init.add_argument('--directory', required=True)
    init.add_argument('--policy', default=str(Path(__file__).resolve().parents[1] / 'configs/transfer_policy.yaml'))
    init.add_argument('--sandbox-image')
    serve = sub.add_parser('serve')
    serve.add_argument('--config', required=True)
    serve.add_argument('--demo-bank', required=True, action='store_true', help='explicitly acknowledge simulated banking only')
    args = parser.parse_args()
    if args.command == 'init':
        print(initialize(args.directory, policy_path=args.policy, sandbox_image=args.sandbox_image))
        return
    runtime = build_demo_runtime(args.config)
    server = SafetyHTTPServer(('127.0.0.1', runtime.config.port), runtime.gateway)
    print(f'MOSAIC safety backend: http://127.0.0.1:{runtime.config.port}; durable mock bank only', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        runtime.close()


if __name__ == '__main__':
    main()
