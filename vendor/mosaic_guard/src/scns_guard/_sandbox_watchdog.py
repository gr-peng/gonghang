"""Trusted, detached supervisor. Never receives or executes sandbox source code.

The service owns stdin's write end. EOF (including service SIGKILL), timeout,
or any byte requests removal of the already-created container. Launch this file
with Python isolated mode; it intentionally uses only the standard library.
"""
from __future__ import annotations

import selectors
import subprocess
import sys


def main() -> int:
    binary, container, timeout = sys.argv[1:]
    selector = selectors.DefaultSelector()
    try:
        selector.register(sys.stdin, selectors.EVENT_READ)
        print('ready', flush=True)
        selector.select(timeout=float(timeout))
    finally:
        selector.close()
        # A daemon outage cannot be repaired here. Expired-container recovery on
        # the next service start/run handles that case; never launch host code.
        for _ in range(3):
            try:
                result = subprocess.run([binary, 'rm', '-f', container],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, timeout=2)
                if result.returncode == 0:
                    return 0
            except (OSError, subprocess.TimeoutExpired):
                continue
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
