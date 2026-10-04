import os

import pytest

from scns_guard.sandbox import DockerSandbox, SandboxConfig


def test_sandbox_requires_pinned_image_and_has_no_host_fallback():
    with pytest.raises(ValueError):
        SandboxConfig(image='python:latest')
    runner = DockerSandbox(SandboxConfig(image='sha256:' + 'a' * 64), docker_binary='/nonexistent/docker')
    with pytest.raises(RuntimeError, match='unavailable'):
        runner.run('print(1)')


def test_sandbox_flags_are_code_owned_and_resource_bounded():
    runner = DockerSandbox(SandboxConfig(image='sha256:' + 'a' * 64))
    command = runner.command('mosaic-sandbox-fixture')
    for flag in ('--network=none', '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges',
                 '--pids-limit=32', '--memory=128m', '--user=65534:65534', '--pull=never'):
        assert flag in command
    assert not any(item in command for item in ('--volume', '-v', '--privileged', '--env', '-e'))
    with pytest.raises(ValueError):
        runner.run('x' * 32_769)


@pytest.mark.skipif(not os.environ.get('MOSAIC_SANDBOX_IMAGE'), reason='explicit local Docker integration opt-in')
def test_real_container_isolation_limits_and_cleanup(tmp_path, monkeypatch):
    config = SandboxConfig(image=os.environ['MOSAIC_SANDBOX_IMAGE'], timeout_seconds=3, output_bytes=4096)
    runner = DockerSandbox(config)
    assert runner.run('print(6 * 7)').stdout.strip() == '42'
    dummy_secret = tmp_path / 'host-private-fixture'
    dummy_secret.write_text('synthetic-secret-not-real')
    monkeypatch.setenv('MOSAIC_TEST_SECRET', 'synthetic-env-secret')
    probes = {
        'network': 'import socket; socket.create_connection(("1.1.1.1", 443), timeout=1)',
        'readonly': 'open("/root-write-probe", "w").write("x")',
        'hostpath': f'print(open({str(dummy_secret)!r}).read())',
    }
    for code in probes.values():
        result = runner.run(code)
        assert result.status == 'failed'
        assert result.exit_code != 0
    assert runner.run('import os; print(os.getuid()); print(os.environ.get("MOSAIC_TEST_SECRET"))').stdout.strip() == '65534\nNone'
    assert runner.run('while True: pass').status == 'timeout'
    assert runner.run('print("x" * 100000)').status == 'output_limit'
    assert runner.remaining_containers() == []


@pytest.mark.skipif(not os.environ.get('MOSAIC_SANDBOX_IMAGE'), reason='explicit local Docker integration opt-in')
@pytest.mark.parametrize('failure', ['kill', 'stop'])
def test_parent_failure_does_not_leave_running_code(failure):
    import subprocess
    import sys
    import time
    import signal
    runner = DockerSandbox(SandboxConfig(image=os.environ['MOSAIC_SANDBOX_IMAGE'], timeout_seconds=30))
    child = '''import sys
from scns_guard.sandbox import DockerSandbox, SandboxConfig
r = DockerSandbox(SandboxConfig(image=sys.argv[1], timeout_seconds=int(sys.argv[2])))
print(r.owner, flush=True)
r.run('import time; time.sleep(60)')
'''
    process = subprocess.Popen([sys.executable, '-c', child, runner.config.image, '3' if failure == 'stop' else '30'],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    owner = process.stdout.readline().strip()
    def remaining():
        return subprocess.check_output([runner.binary, 'ps', '-aq', '--filter',
            'label=mosaic.sandbox.owner=' + owner], text=True, timeout=5).split()
    try:
        deadline = time.monotonic() + 10
        running = False
        while time.monotonic() < deadline:
            ids = remaining()
            if ids:
                state = subprocess.run([runner.binary, 'inspect', '--format', '{{.State.Running}}', ids[0]],
                                       capture_output=True, text=True, timeout=5)
                if state.stdout.strip() == 'true':
                    running = True
                    break
            time.sleep(0.05)
        assert running, 'fixture must reach a running container'
        if failure == 'kill':
            process.kill()
            process.wait(timeout=5)
        else:
            os.kill(process.pid, signal.SIGSTOP)
        deadline = time.monotonic() + 5
        while remaining() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert remaining() == []
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        for container in remaining():
            subprocess.run([runner.binary, 'rm', '-f', container], capture_output=True, timeout=5, check=True)
        process.stdout.close()
        process.stderr.close()


@pytest.mark.skipif(not os.environ.get('MOSAIC_SANDBOX_IMAGE'), reason='explicit local Docker integration opt-in')
def test_restart_recovery_preserves_unexpired_and_other_deployments():
    import subprocess
    from uuid import uuid4
    config = SandboxConfig(image=os.environ['MOSAIC_SANDBOX_IMAGE'])
    runner = DockerSandbox(config, owner=str(uuid4()))
    other = DockerSandbox(config, owner=str(uuid4()))
    names = ['mosaic-sandbox-' + str(uuid4()) for _ in range(3)]
    try:
        for sandbox, name, expired in ((runner, names[0], True), (runner, names[1], False), (other, names[2], True)):
            command = sandbox.command(name)
            if expired:
                command = ['--label=mosaic.sandbox.expires=0' if arg.startswith('--label=mosaic.sandbox.expires=')
                           else arg for arg in command]
            subprocess.run(command, capture_output=True, check=True, timeout=5)
        restarted = DockerSandbox(config, owner=runner.owner)
        restarted.recover_expired()
        assert len(restarted.remaining_containers()) == 1
        assert len(other.remaining_containers()) == 1
        state = subprocess.check_output([runner.binary, 'inspect', '--format', '{{.State.Status}}', names[1]],
                                        text=True, timeout=5).strip()
        assert state == 'created'
    finally:
        for name in names:
            subprocess.run([runner.binary, 'rm', '-f', name], capture_output=True, timeout=5)
        assert runner.remaining_containers() == []
        assert other.remaining_containers() == []
