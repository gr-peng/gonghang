"""Fixed-seed local stress of the *durable mock*, with real independent processes.

No LLM calls, Docker daemon or financial service. Creates temporary keys/state,
never operates on a user's deployment. A failure is not retried with a new handle.
"""
from __future__ import annotations

import argparse
import faulthandler
import json
import subprocess
import random
import statistics
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from scns_guard.auth import ControlClaims, IdentityClaims
from scns_guard.causal import AgentTrace, SourceMessage
from scns_guard.deployment import build_demo_runtime, initialize
from scns_guard.enums import SourceKind, SourceTrust
from scns_guard.gateway import ReviewRequest
from scns_guard.lineage import LineageAuthority
from scns_guard.llm_adapter import parse_model_json

ROOT = Path(__file__).resolve().parents[1]


def prepare(gateway, amount=100):
    credential = gateway.identity_authority.issue(IdentityClaims(actor_id='user-1', session_id='stress',
        scopes=('agent:review', 'agent:execute', 'human:confirm')), key_id='v1', ttl=300)
    params = {'from_account': 'acct-user', 'to_account': 'acct-alice', 'amount_minor': amount}
    content = {'text': 'Mock-only security stress fixture', 'payload': params}
    source = gateway.lineage.issue_root(kind=SourceKind.USER, trust=SourceTrust.USER,
        content=content, producer_id='frontend', metadata={'actor_id': 'user-1', 'session_id': 'stress'})
    trace = AgentTrace(trace_id='stress', actor_id='user-1', session_id='stress',
        messages=(SourceMessage(source=source, **content),))
    review = gateway.review(credential, ReviewRequest(trace=trace, model_output=json.dumps({
        'schema_version': 'mosaic-planner-v2', 'kind': 'task', 'action_type': 'transfer', 'params': params})))
    proof = gateway.control_authority.issue(ControlClaims(operation='confirmation', actor_id='user-1',
        session_id='stress', target=review['handle'], binding=review['action_digest']), key_id='v1', ttl=120)
    gateway.approve(credential, review['handle'], proof)
    return credential, review


def execute_worker():
    """Private fixture subprocess; bounded input, no supplied deployment paths in normal CLI."""
    faulthandler.dump_traceback_later(40, exit=True)
    request = parse_model_json(sys.stdin.read(131_073))
    runtime = build_demo_runtime(request['config'])
    try:
        rows = []
        for credential, handle in request['tasks']:
            start = time.monotonic()
            outcome = runtime.gateway.execute(credential, handle)
            rows.append({'status': outcome['status'], 'latency_ms': 1000 * (time.monotonic() - start)})
        print(json.dumps({'rows': rows}), flush=True)
    finally:
        runtime.close()
        faulthandler.cancel_dump_traceback_later()


def process_batch(config, tasks, workers):
    # Explicit child lifetimes avoid an unbounded executor-shutdown wait. Inputs
    # and outputs are small; all children receive their work before any is joined.
    children = []
    deadline = time.monotonic() + 60
    try:
        for index in range(workers):
            batch = [(credential, review['handle']) for credential, review in tasks[index::workers]]
            if not batch:
                continue
            child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--mock-worker'],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            children.append(child)
            child.stdin.write(json.dumps({'config': str(config), 'tasks': batch}))
            child.stdin.close()
            child.stdin = None
        rows = []
        for child in children:
            output, errors = child.communicate(timeout=max(0.01, deadline - time.monotonic()))
            if child.returncode != 0:
                raise RuntimeError(f'mock worker exit={child.returncode}; {errors[-4000:]}')
            rows.extend(parse_model_json(output)['rows'])
        return rows
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=5)


def execute_thread(args):
    gateway, credential, handle = args
    start = time.monotonic()
    result = gateway.execute(credential, handle)
    return {'status': result['status'], 'latency_ms': 1000 * (time.monotonic() - start)}


def parallel_case(workers, mode, kind, rng):
    with tempfile.TemporaryDirectory(prefix='mosaic-security-stress-') as folder:
        config = initialize(Path(folder) / 'private', policy_path=ROOT / 'configs/transfer_policy.yaml')
        runtime = build_demo_runtime(config)
        try:
            # Same request races must never redispatch. Distinct normal requests
            # must all complete: "deny everything" is not a successful defense.
            count = max(8, workers * 2)
            unique = 1 if kind == 'same_handle' else count
            approvals = [prepare(runtime.gateway, amount=100) for _ in range(unique)]
            tasks = [approvals[i % unique] for i in range(count)]
            rng.shuffle(tasks)
            start = time.monotonic()
            if mode == 'process':
                outputs = process_batch(config, tasks, workers)
            else:
                with ThreadPoolExecutor(max_workers=workers) as executor:
                    futures = [executor.submit(execute_thread, (runtime.gateway, c, r['handle'])) for c, r in tasks]
                    outputs = [future.result(timeout=45) for future in futures]
            elapsed = time.monotonic() - start
            final = [runtime.gateway.execute(c, r['handle']) for c, r in approvals]
            bank = runtime.bank.snapshot()
            assert bank['execution_count'] == unique, bank
            assert bank['accounts']['acct-user']['balance_minor'] == 500_000 - 100 * unique
            assert bank['accounts']['acct-alice']['balance_minor'] == 10_000 + 100 * unique
            assert sum(a['balance_minor'] for a in bank['accounts'].values()) == 510_000
            assert bank['daily_spent_minor']['user-1'] == unique * 100
            assert all(o['status'] == 'succeeded' for o in final), final
            # In-flight observations are allowed to be unknown, never "failed
            # so resubmit". Final durable history is successful in this case.
            assert all(o['status'] in ('succeeded', 'unknown') for o in outputs), outputs
            consumed = runtime.store._db.execute('SELECT count(*) FROM token_lifecycle').fetchone()[0]
            assert consumed == unique
            times = sorted(o['latency_ms'] for o in outputs)
            return {'mode': mode, 'workers': workers, 'case': kind, 'requests': count,
                'unique_authorized_writes': unique, 'backend_writes': bank['execution_count'],
                'terminal_successes': len(final), 'inflight_unknown': sum(o['status'] == 'unknown' for o in outputs),
                'tokens_consumed': consumed, 'elapsed_seconds': round(elapsed, 4),
                'p50_ms': round(statistics.median(times), 3),
                'p95_ms': round(times[min(len(times) - 1, int(.95 * len(times)))], 3),
                'balance_conserved': True, 'passed': True}
        finally:
            runtime.close()


def lineage_case(depth):
    authority = LineageAuthority({'fixture': b'L' * 32})
    root = authority.issue_root(kind=SourceKind.USER, trust=SourceTrust.USER,
        content={}, producer_id='fixture', source_id='root')
    nodes = parents = [root]
    for level in range(depth):
        pair = [authority.derive(kind=SourceKind.MEMORY, content={}, producer_id='fixture',
            transformation='summary', parents=parents, source_id=f'{level}-{branch}') for branch in range(2)]
        nodes = nodes + pair
        parents = pair
    count = [0]
    original = authority._signature_valid
    def counted(record):
        count[0] += 1
        return original(record)
    authority._signature_valid = counted
    start = time.monotonic()
    verified = authority.verified_source_ids(nodes)
    elapsed = time.monotonic() - start
    assert len(verified) == len(nodes)
    assert count[0] <= 2 * len(nodes)
    measured_checks = count[0]
    # Memoization must be per-call, never a reusable authorization cache.
    forged = nodes[0].model_copy(update={'signature': '0' * 64})
    assert not authority.verified_source_ids([forged, *nodes[1:]])
    return {'case': 'shared_ancestry', 'depth': depth, 'nodes': len(nodes),
        'valid_catalog_signature_checks': measured_checks, 'elapsed_seconds': round(elapsed, 4),
        'tampered_root_rejected_next_call': True, 'passed': True}


def parser_case(rng, cases=2000):
    rejected = 0
    for _ in range(cases):
        text = json.dumps({'value': rng.randint(-10**9, 10**9), 'note': '[]{}"\\ unicode安全'})
        assert isinstance(parse_model_json(text), dict)
        invalid = rng.choice(['{"x":1e999}', '{"x":NaN}', '{"x":1,"x":2}',
            '{"x":' + '[' * rng.randint(65, 200) + '0}', '[]'])
        try:
            parse_model_json(invalid)
        except ValueError:
            rejected += 1
        else:
            raise AssertionError('malformed or unsupported JSON accepted')
    assert rejected == cases
    return {'case': 'strict_json_seeded', 'valid_accepted': cases, 'invalid_rejected': rejected, 'passed': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed', type=int, default=20260906)
    parser.add_argument('--workers', type=int, nargs='+', default=[1, 2, 8, 16, 32])
    parser.add_argument('--modes', nargs='+', choices=('thread', 'process'), default=['thread', 'process'])
    parser.add_argument('--cases', nargs='+', choices=('same_handle', 'distinct_handles'), default=['same_handle', 'distinct_handles'])
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts/security-review-20260906/stress-results.json')
    options = parser.parse_args()
    if not options.workers or any(not 1 <= n <= 32 for n in options.workers):
        parser.error('workers must be in [1, 32]')
    rng = random.Random(options.seed)
    results = []
    try:
        for mode in options.modes:
            for n in options.workers:
                for kind in options.cases:
                    row = parallel_case(n, mode, kind, rng)
                    results.append(row)
                    print(json.dumps(row), flush=True)
        results.extend(lineage_case(depth) for depth in (8, 16, 32, 63))
        results.append(parser_case(rng))
        status, error = 'pass', None
    except Exception as exc:
        status, error = 'fail', f'{type(exc).__name__}: {exc}'
    payload = {'status': status, 'created_at': datetime.now(timezone.utc).isoformat(), 'seed': options.seed,
        'python': sys.version, 'scope': 'local durable mock; no real model, Docker, production load or financial API',
        'clock': 'real monotonic for latency; no performance benchmark claims',
        'cases_completed': len(results), 'results': results, 'error': error}
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + '\n')
    faulthandler.cancel_dump_traceback_later()
    print(options.output, status, flush=True)
    return 0 if status == 'pass' else 1


if __name__ == '__main__':
    if sys.argv[1:] == ['--mock-worker']:
        execute_worker()
    else:
        raise SystemExit(main())
