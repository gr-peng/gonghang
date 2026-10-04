"""Authorized mock-only process-exit fixture. Never import into the live router."""
import argparse
import json
import os
import sys

from scns_guard.deployment import build_demo_runtime


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mock-fixture', required=True, action='store_true')
    parser.parse_args()
    request = json.loads(sys.stdin.read(32_768))
    if request['when'] not in ('before', 'after', 'after_bind', 'after_reserve', 'after_receipt', 'after_finish'):
        raise ValueError('unknown crash boundary')
    runtime = build_demo_runtime(request['config'])
    bank = runtime.bank
    boundaries = {
        'after_bind': (runtime.store, 'bind'),
        'after_reserve': (runtime.store, 'reserve'),
        'after_receipt': (runtime.gateway.ledger, 'append'),
        'after_finish': (runtime.store, 'finish'),
    }
    if request['when'] in boundaries:
        owner, name = boundaries[request['when']]
        original = getattr(owner, name)
        def exit_after_commit(*args, **kwargs):
            original(*args, **kwargs)
            os._exit(73)
        setattr(owner, name, exit_after_commit)
        runtime.gateway.execute(request['credential'], request['handle'])
        raise RuntimeError('fixture did not reach selected durable boundary')
    class ExitTool:
        name = bank.name
        snapshot = bank.snapshot
        transaction = bank.transaction
        def execute(self, action):
            if request['when'] == 'after':
                bank.execute(action)
            os._exit(73)
    runtime.gateway.tool = ExitTool()
    runtime.gateway.execute(request['credential'], request['handle'])
    raise RuntimeError('fixture did not reach execution')


if __name__ == '__main__':
    main()
