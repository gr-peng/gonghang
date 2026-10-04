"""Disposable Docker execution with no host-execution fallback or bank tools."""
from __future__ import annotations

import os
import json
import math
import re
import selectors
import shutil
import subprocess
import time
import sys
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import Field

from .canonical import sha256_hex
from .models import StrictModel


class SandboxConfig(StrictModel):
    image: str = Field(pattern=r'^sha256:[0-9a-f]{64}$')
    timeout_seconds: int = Field(default=5, strict=True, ge=1, le=30)
    output_bytes: int = Field(default=16_384, strict=True, ge=256, le=65_536)


class SandboxResult(StrictModel):
    status: Literal['succeeded', 'failed', 'timeout', 'output_limit']
    exit_code: int | None
    stdout: str
    stderr: str
    duration_seconds: float
    code_sha256: str
    image: str
    cleanup_verified: bool


class DockerSandbox:
    def __init__(self, config: SandboxConfig, *, docker_binary: str | None = None,
                 owner: str | None = None):
        self.config = SandboxConfig.model_validate(config.model_dump())
        self.binary = docker_binary or shutil.which('docker') or 'docker'
        self.owner = owner or str(uuid4())
        if not re.fullmatch(r'[a-zA-Z0-9-]{1,64}', self.owner):
            raise ValueError('invalid sandbox deployment owner')

    def command(self, name: str) -> list[str]:
        return [self.binary, 'create', '--rm', '-i', '--pull=never', f'--name={name}',
                f'--label=mosaic.sandbox.owner={self.owner}', '--network=none', '--read-only',
                f'--label=mosaic.sandbox.expires={time.time() + self.config.timeout_seconds + 10}',
                '--cap-drop=ALL', '--security-opt=no-new-privileges', '--pids-limit=32',
                '--memory=128m', '--memory-swap=128m', '--cpus=0.5', '--user=65534:65534',
                '--ulimit=nofile=64:64', '--ulimit=fsize=1048576:1048576',
                '--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=16m,mode=1777',
                '--workdir=/tmp', '--entrypoint=python', self.config.image, '-I', '-S', '-B', '-']

    def _check(self):
        try:
            result = subprocess.run([self.binary, 'image', 'inspect', self.config.image, '--format', '{{.Id}}'],
                capture_output=True, timeout=5, check=True)
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError('isolated Docker runtime or pinned local image unavailable') from exc
        if result.stdout.decode().strip() != self.config.image:
            raise RuntimeError('pinned image identity mismatch')

    def remaining_containers(self) -> list[str]:
        result = subprocess.run([self.binary, 'ps', '-aq', '--filter', f'label=mosaic.sandbox.owner={self.owner}'],
                                capture_output=True, check=True, timeout=5)
        return result.stdout.decode().split()

    def recover_expired(self) -> None:
        """Reap only expired containers in this stable deployment namespace.

        Another live worker's unexpired container must never be removed. Labels
        are runtime-owned; malformed/legacy records require operator review.
        """
        for container in self.remaining_containers():
            result = subprocess.run([self.binary, 'inspect', '--format', '{{json .Config.Labels}}', container],
                                    capture_output=True, timeout=5)
            if result.returncode:
                if container not in self.remaining_containers():
                    continue  # Another supervisor already removed it.
                raise RuntimeError('sandbox recovery could not inspect container')
            labels = json.loads(result.stdout)
            try:
                expiry = float(labels['mosaic.sandbox.expires'])
                if labels['mosaic.sandbox.owner'] != self.owner or not math.isfinite(expiry):
                    raise ValueError('invalid recovery label')
            except (KeyError, TypeError, ValueError) as exc:
                raise RuntimeError('sandbox recovery labels require operator review') from exc
            if expiry <= time.time():
                subprocess.run([self.binary, 'rm', '-f', container], capture_output=True, timeout=5)
                if container in self.remaining_containers():
                    raise RuntimeError('expired sandbox cleanup not verified')

    def _watch(self, name: str):
        watcher = subprocess.Popen([sys.executable, '-I', str(Path(__file__).with_name('_sandbox_watchdog.py')),
            self.binary, name, str(self.config.timeout_seconds)], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            with selectors.DefaultSelector() as ready:
                ready.register(watcher.stdout, selectors.EVENT_READ)
                if not ready.select(timeout=3) or watcher.stdout.readline() != b'ready\n':
                    raise RuntimeError('sandbox supervisor failed to start')
            return watcher
        except BaseException:
            watcher.stdin.close()
            watcher.wait(timeout=8)
            raise
        finally:
            watcher.stdout.close()

    def run(self, code: str) -> SandboxResult:
        if not isinstance(code, str) or not 1 <= len(code.encode()) <= 32_768:
            raise ValueError('sandbox accepts 1 to 32768 UTF-8 bytes')
        self._check()
        self.recover_expired()
        name = 'mosaic-sandbox-' + str(uuid4())
        # Create stopped, then arm a separate supervisor BEFORE executing code.
        # A crash during create can only leave a stopped, labelled container.
        try:
            subprocess.run(self.command(name), capture_output=True, check=True, timeout=5)
            watcher = self._watch(name)
        except BaseException:
            subprocess.run([self.binary, 'rm', '-f', name], capture_output=True, timeout=5)
            raise
        start = time.monotonic()
        stdout, stderr = bytearray(), bytearray()
        status = None
        try:
            process = subprocess.Popen([self.binary, 'start', '-ai', name], stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        except BaseException:
            watcher.stdin.close()
            watcher.wait(timeout=8)
            raise
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ, stdout)
        selector.register(process.stderr, selectors.EVENT_READ, stderr)
        selector.register(process.stdin, selectors.EVENT_WRITE, None)
        pending = memoryview(code.encode())
        for stream in (process.stdin, process.stdout, process.stderr):
            os.set_blocking(stream.fileno(), False)
        cleanup_ok = False
        try:
            while selector.get_map():
                if time.monotonic() - start >= self.config.timeout_seconds:
                    status = 'timeout'
                    break
                for key, mask in selector.select(timeout=0.05):
                    if mask & selectors.EVENT_WRITE:
                        try:
                            sent = os.write(key.fd, pending[:4096])
                            pending = pending[sent:]
                        except BrokenPipeError:
                            pending = memoryview(b'')
                        if not pending:
                            selector.unregister(key.fileobj)
                            key.fileobj.close()
                    else:
                        chunk = os.read(key.fd, 4096)
                        if not chunk:
                            selector.unregister(key.fileobj)
                        else:
                            available = max(0, self.config.output_bytes - len(stdout) - len(stderr))
                            key.data.extend(chunk[:available])
                            if len(chunk) > available:
                                status = 'output_limit'
                                break
                if status is not None:
                    break
            if status is None:
                remaining = max(0.01, self.config.timeout_seconds - (time.monotonic() - start))
                try:
                    status = 'succeeded' if process.wait(timeout=remaining) == 0 else 'failed'
                except subprocess.TimeoutExpired:
                    status = 'timeout'
            if status == 'failed' and time.monotonic() - start >= self.config.timeout_seconds - 0.1:
                status = 'timeout'  # The independent supervisor may finish first.
        finally:
            selector.close()
            watcher.stdin.close()
            # Killing only the client is insufficient: explicitly remove exactly
            # this invocation's container, then verify absence by its unique name.
            try:
                subprocess.run([self.binary, 'rm', '-f', name], capture_output=True, timeout=5)
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
                for stream in (process.stdin, process.stdout, process.stderr):
                    stream.close()
                watcher.wait(timeout=8)
            result = subprocess.run([self.binary, 'ps', '-aq', '--filter', f'name=^/{name}$'],
                                    capture_output=True, check=True, timeout=5)
            cleanup_ok = not result.stdout.strip()
            if not cleanup_ok:
                raise RuntimeError('sandbox cleanup not verified; operator intervention required')
        return SandboxResult(status=status, exit_code=process.returncode, stdout=stdout.decode('utf-8', errors='replace'),
            stderr=stderr.decode('utf-8', errors='replace'), duration_seconds=time.monotonic() - start,
            code_sha256=sha256_hex(code), image=self.config.image, cleanup_verified=cleanup_ok)
