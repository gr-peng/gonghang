"""Persistent mock bank, explicitly not an adapter for a real banking system.

A local file lock spans facts, authorization, durable commit and snapshot. Bank
effects commit before returning to the controller. Runtime and bank databases
remain separate; interrupted requests require reconciliation, never redispatch.
"""
from __future__ import annotations

import fcntl
import json
import math
import time
import os
import sqlite3
from contextlib import contextmanager
from datetime import date
from pathlib import Path

from .canonical import canonical_json, sha256_hex
from .money import validate_minor_units
from .simulator import BankAccount, BankLedger


class DurableBankLedger(BankLedger):
    name = 'durable-mock-bank'

    def __init__(self, path: str | Path, accounts: list[BankAccount], *, lock_timeout_seconds: float = 5.0, **kwargs):
        if (isinstance(lock_timeout_seconds, bool) or not isinstance(lock_timeout_seconds, (int, float))
                or not math.isfinite(lock_timeout_seconds) or not 0 < lock_timeout_seconds <= 60):
            raise ValueError('bank lock timeout must be finite and in (0, 60] seconds')
        self.lock_timeout_seconds = float(lock_timeout_seconds)
        super().__init__(accounts, **kwargs)
        self.path = Path(path)
        self._file_lock = os.open(str(self.path) + '.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        self._db = None
        acquired = False
        try:
            self._acquire_file_lock()
            acquired = True
            self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None, timeout=5)
            self._db.execute('PRAGMA synchronous=FULL')
            self._db.execute('PRAGMA journal_mode=WAL')
            self._db.execute('CREATE TABLE IF NOT EXISTS bank_state (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL, digest TEXT NOT NULL)')
            self._db.execute('CREATE TABLE IF NOT EXISTS bank_effects (action_id TEXT PRIMARY KEY, digest TEXT NOT NULL, result TEXT NOT NULL)')
            payload = canonical_json(self._persisted())
            self._db.execute('INSERT OR IGNORE INTO bank_state VALUES (1,?,?)', (payload, sha256_hex(payload)))
            self._load()
        except BaseException:
            if self._db is not None:
                self._db.close()
                self._db = None
            raise
        finally:
            if acquired:
                fcntl.flock(self._file_lock, fcntl.LOCK_UN)
            if self._db is None:
                os.close(self._file_lock)

    def _acquire_file_lock(self):
        deadline = time.monotonic() + self.lock_timeout_seconds
        while True:
            try:
                fcntl.flock(self._file_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except BlockingIOError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('mock bank transaction lock deadline exceeded') from None
                time.sleep(min(0.01, remaining))

    def _persisted(self):
        return {**self._snapshot(), 'execution_log': self.execution_log, 'daily_limit_minor': self.daily_limit_minor,
                'blocked_recipients': sorted(self.blocked_recipients)}

    def _load(self):
        row = self._db.execute('SELECT payload,digest FROM bank_state WHERE id=1').fetchone()
        if row is None or sha256_hex(row[0]) != row[1]:
            raise ValueError('mock bank integrity check failed')
        state = json.loads(row[0])
        if state['daily_limit_minor'] != self.daily_limit_minor or state['blocked_recipients'] != sorted(self.blocked_recipients):
            raise ValueError('mock bank configuration change requires explicit migration')
        self.accounts = {key: BankAccount(key, **value) for key, value in state['accounts'].items()}
        self.daily_spent_minor = {actor: validate_minor_units(amount, field_name='stored daily spending', allow_zero=True)
                                  for actor, amount in state['daily_spent_minor'].items()}
        self.execution_log = state['execution_log']
        self._business_date = date.fromisoformat(state['business_date'])

    @contextmanager
    def transaction(self):
        if not self._lock.acquire(timeout=self.lock_timeout_seconds):
            raise TimeoutError('mock bank local transaction lock deadline exceeded')
        acquired = False
        try:
            outer = self._transaction_time is None
            if outer:
                self._acquire_file_lock()
                acquired = True
                self._load()
            with super().transaction():
                yield
        finally:
            if acquired:
                fcntl.flock(self._file_lock, fcntl.LOCK_UN)
            self._lock.release()

    def reconcile(self, action):
        with self.transaction():
            row = self._db.execute('SELECT digest,result FROM bank_effects WHERE action_id=?', (action.action_id,)).fetchone()
            if row is None:
                return None
            if row[0] != action.digest:
                raise ValueError('backend idempotency key bound to a different action')
            return json.loads(row[1])

    def execute(self, action):
        with self.transaction():
            if not action.write_action and action.action_type == 'read_balance':
                return self._execute(action)
            existing = self.reconcile(action)
            if existing is not None:
                return existing
            try:
                self._db.execute('BEGIN IMMEDIATE')
                result = self._execute(action)
                payload = canonical_json(self._persisted())
                self._db.execute('UPDATE bank_state SET payload=?,digest=? WHERE id=1', (payload, sha256_hex(payload)))
                self._db.execute('INSERT INTO bank_effects VALUES (?,?,?)', (action.action_id, action.digest, canonical_json(result)))
                self._db.execute('COMMIT')
                return result
            except BaseException:
                if self._db.in_transaction:
                    self._db.execute('ROLLBACK')
                self._load()
                raise

    def close(self):
        with self._lock:
            self._db.close()
            os.close(self._file_lock)
