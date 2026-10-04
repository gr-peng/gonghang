"""Explicit private configuration for a loopback, durable mock-bank deployment."""
from __future__ import annotations

import hashlib
import os
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import Field

from .auth import CredentialAuthority
from .canonical import sha256_hex
from .durable_bank import DurableBankLedger
from .gateway import SafetyGateway
from .lineage import LineageAuthority
from .llm_adapter import parse_model_json
from .models import StrictModel
from .policy import PolicyEngine
from .runtime_store import RuntimeStore
from .sandbox import DockerSandbox, SandboxConfig
from .simulator import BankAccount
from .trust import FactAuthority


class ServiceConfig(StrictModel):
    version: Literal[1] = 1
    mode: Literal['durable_mock_only'] = 'durable_mock_only'
    policy_path: str
    policy_sha256: str = Field(pattern='^[0-9a-f]{64}$')
    port: int = Field(default=8765, strict=True, ge=1024, le=65535)
    sandbox: SandboxConfig | None = None


def _private(path: Path, *, directory: bool = False) -> None:
    info = path.lstat()
    if (stat.S_ISLNK(info.st_mode) or bool(info.st_mode & 0o077)
            or info.st_uid != os.getuid() or (directory and not stat.S_ISDIR(info.st_mode))
            or (not directory and not stat.S_ISREG(info.st_mode))):
        raise ValueError('state and keys must be private, owned by this user and not symlinks')


def initialize(directory: str | Path, *, policy_path: str | Path, sandbox_image: str | None = None) -> Path:
    directory, policy_path = Path(directory).absolute(), Path(policy_path).resolve()
    config = ServiceConfig(policy_path=str(policy_path), policy_sha256=hashlib.sha256(policy_path.read_bytes()).hexdigest(),
                           sandbox=SandboxConfig(image=sandbox_image) if sandbox_image else None)
    # Never overwrite an existing deployment or regenerate keys on startup.
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    for name in ('identity', 'approval', 'lineage', 'token', 'bank', 'audit'):
        fd = os.open(directory / f'{name}.key', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as handle:
            handle.write(secrets.token_hex(32) + '\n')
    path = directory / 'service.json'
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as handle:
        handle.write(config.model_dump_json(indent=2) + '\n')
    return path


@dataclass
class DemoRuntime:
    config: ServiceConfig
    gateway: SafetyGateway
    bank: DurableBankLedger
    store: RuntimeStore

    def close(self):
        self.bank.close()
        self.store.close()


def build_demo_runtime(config_path: str | Path) -> DemoRuntime:
    path = Path(config_path).absolute()
    _private(path.parent, directory=True)
    _private(path)
    config = ServiceConfig.model_validate(parse_model_json(path.read_text()))
    policy_path = Path(config.policy_path)
    if hashlib.sha256(policy_path.read_bytes()).hexdigest() != config.policy_sha256:
        raise ValueError('pinned policy hash changed; explicit review and configuration update required')
    keys = {}
    for name in ('identity', 'approval', 'lineage', 'token', 'bank', 'audit'):
        file = path.parent / f'{name}.key'
        _private(file)
        key = bytes.fromhex(file.read_text().strip())
        if len(key) != 32:
            raise ValueError('deployment keys must contain exactly 256 bits')
        keys[name] = key
    if len(set(keys.values())) != len(keys):
        raise ValueError('security roles require independent keys')
    for name in ('runtime.sqlite3', 'bank.sqlite3', 'bank.sqlite3.lock'):
        candidate = path.parent / name
        if candidate.exists() or candidate.is_symlink():
            _private(candidate)
        else:
            fd = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            os.close(fd)
    facts = FactAuthority({'bank-core': keys['bank']})
    store = RuntimeStore(path.parent / 'runtime.sqlite3')
    bank = None
    try:
        # Prevent accidental key replacement against an existing authorization DB.
        with store._atomic():
            store._db.execute('CREATE TABLE IF NOT EXISTS trust_anchor (id INTEGER PRIMARY KEY CHECK(id=1), digest TEXT NOT NULL)')
            anchor = sha256_hex({name: hashlib.sha256(key).hexdigest() for name, key in keys.items()})
            store._db.execute('INSERT OR IGNORE INTO trust_anchor VALUES (1,?)', (anchor,))
            if store._db.execute('SELECT digest FROM trust_anchor WHERE id=1').fetchone()[0] != anchor:
                raise ValueError('deployment keys changed; explicit key migration required')
        bank = DurableBankLedger(path.parent / 'bank.sqlite3', [
            BankAccount('acct-user', 'user-1', 500_000), BankAccount('acct-alice', 'alice', 10_000),
            BankAccount('acct-mallory', 'mallory', 0)], blocked_recipients={'acct-mallory'})
        gateway = SafetyGateway(store=store, policy=PolicyEngine.from_yaml(policy_path), fact_authority=facts,
            tool=bank, fact_supplier=lambda actor: bank.issue_facts(actor, facts),
            lineage_authority=LineageAuthority({'frontend': keys['lineage']}),
            identity_authority=CredentialAuthority(issuer='login', audience='guard', keys={'v1': keys['identity']}),
            control_authority=CredentialAuthority(issuer='approval', audience='guard', keys={'v1': keys['approval']}, max_ttl=120),
            token_secret=keys['token'])
        gateway.audit_secret = keys['audit']
        gateway.sandbox = (DockerSandbox(config.sandbox, owner=sha256_hex(str(path.parent.resolve())))
                           if config.sandbox else None)
        if gateway.sandbox is not None:
            gateway.sandbox.recover_expired()
        with bank.transaction():
            store.recover_unresolved(gateway.circuit.namespace)
        return DemoRuntime(config=config, gateway=gateway, bank=bank, store=store)
    except BaseException:
        if bank is not None:
            bank.close()
        store.close()
        raise
