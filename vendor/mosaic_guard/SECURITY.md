# Security policy

## Prototype warning

This repository is a research prototype. Do not connect it to real bank accounts, production credentials, or irreversible tools.

## Supported scope

Security invariants covered by the current tests include:

- monotone detector composition;
- signed trusted facts;
- complete action/security-envelope-bound obligation tokens;
- one-time token consumption and trusted-host revocation;
- atomic request binding/reservation with optional persistent SQLite state;
- no automatic redispatch after a terminal or unresolved execution attempt;
- execution-time re-evaluation;
- three-valued fail-closed trusted-fact semantics and default deny;
- receipt hash-chain integrity;
- bounded policy-expansion detection;
- duplicate-key/reference rejection in policy and model JSON boundaries;
- receipt-layer consistency and delivered-manifest mutation detection.
- authenticated gateway scopes and independently signed, one-time approvals;
- persistent safety locks, session revocation and bounded HTTP admission;
- durable mock-bank recovery after actual process exit;
- sensitive-data projection and optional isolated Docker execution;
- signed audit prefix checkpoint export and verification.

## Known unsafe-for-production areas

- currency identity, currency exponents, FX conversion, and production rounding rules;
- legacy demonstration constants remain in research/test paths; the deployment
  initializer creates independent private keys and refuses insecure permissions;
- no hardware-backed key management or rotation;
- checkpoint export exists, but independently operated immutable storage is not deployed;
- no distributed transaction/commit protocol;
- execution/token state defaults to in-memory unless a persistent store is configured;
- no protection against deletion, rollback or unauthorized edits of the trusted state database;
- gateway reset/revocation requires scoped identity and independent control proofs;
  actual login/MFA and the operator review process are external dependencies;
- no authenticated production provenance service;
- no production-audited banking/model integration;
- no formal verification of the complete policy implementation; the optional Z3 path covers only a numeric fragment;
- no security review of dependencies or deployment environment.

The current service is loopback-only and deliberately uses a durable mock bank.
The sandbox has no bank tools, network or host mounts and fails closed when its
pinned image/runtime is unavailable. Container isolation still trusts the Docker
daemon and host kernel. Text screening covers explicit patterns only, and internal
audit data remains sensitive plaintext protected by filesystem permissions.
See `docs/26_SAFETY_BACKEND_HANDOFF_ZH.md` for operational boundaries.

## Reporting a vulnerability

For a local/private handoff, record the issue with:

- affected commit/archive hash;
- minimal reproduction;
- violated invariant;
- exact action, policy version, fact/token inputs, and receipt;
- whether a real side effect occurred;
- proposed regression test.

Do not “fix” a failing invariant by weakening a test, adding a prompt retry, or broadening a deny rule without documenting the availability cost.
