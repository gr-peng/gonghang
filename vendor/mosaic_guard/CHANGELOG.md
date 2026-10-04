# Changelog

## 0.1.1 — 2026-09-06 security review

- Revalidate authorization after blocking boundaries and at reservation/dispatch.
- Make cancellation proof consumption, validation and state changes atomic.
- Bound approval token expiry; reject ambiguous latest facts; invalidate stale resets.
- Preserve uncertain post-commit outcomes; bound lineage, JSON/YAML and ingress/lock waits.
- Add 28 regressions/crash tests and fixed-seed local thread/process stress.
- Policy 0.2.2 changes semantics and hash; see docs/28_INDEPENDENT_SECURITY_REVIEW_ZH.md.
- No new real-model or Docker execution evidence is claimed.

## Unreleased

- deliver authenticated loopback safety HTTP/SDK integration, independent
  action-bound approvals, session revocation, operator reset and reconciliation.
- persist actor locks and multi-writer audit chains; add private configuration,
  policy/key fingerprints, rate limits and signed audit prefix checkpoints.
- add a durable mock bank and real process-exit recovery probes before/after
  commit, preserving unknown outcomes and blocking automatic redispatch.
- add sensitive-data projections and optional pinned Docker execution with
  network isolation, resource/output limits and verified cleanup; include
  integration demos, actual-container evidence and safety self-assessment.

- add request binding, atomic reserve-before-dispatch and single-use obligation
  tokens, with trusted-host revocation and optional persistent SQLite storage.
  Duplicate deliveries return the historical receipt; interrupted attempts are
  not released or retried automatically. Historical signature replay is separate
  from live token availability.
- add concurrent-connection, post-commit timeout, simulated process exit,
  receipt-write failure, revocation-race and store-reopen regressions plus a
  deterministic execution-lifecycle demo in one-command reproduction.

- restrict team scope to the safety module and integration evidence, not the
  assistant UI. Add policy 0.2.0 daily cumulative transfer risk, checked
  `sum_minor`, Shanghai-day rollover and the mock-bank transaction boundary.
- add an opt-in actor safety circuit, result contracts and receipt-only public
  outcomes; preserve failure receipts when facts or snapshots are unavailable.
- fix same-account transfer balance inflation by composing debit/credit updates.
- add mock integration reproduction and a five-call Qwen safety smoke with ten
  checks; keep the separate initial tunnel-readiness failure artifact.

- prioritize competition engineering per user direction; research milestones no
  longer block product delivery. Add v2 typed task drafts, code-owned missing-field
  clarification, non-executing review service, adversarial tests and a scripted
  clarification-to-confirmed-mock-execution demo; preserve the v1 experiment path.
- complete and independently audit the six-group real-model intervention pilot:
  288 planner calls, 18 summary calls, 252 pairs and 273 formal receipts; retain
  all 15 validation errors and the one non-identical same-seed repeat, without
  adding learned authorization or executing banking tools.
- add authenticated multi-intervention trace rewriting, hidden ancestor content,
  actual summary transformation callbacks, disjoint task-family splits,
  availability-aware field comparisons and an independent completed-run audit.
- include saved real-model artifact directories in the bundle integrity manifest;
  local reproduction does not rerun those model calls.

- add runtime-owned signed source-lineage records, transitive transformation
  ancestry, exact-payload provenance resolution, controller enforcement, and
  authenticated receipt replay with adversarial forgery/content tests.
- add an audited OpenAI-compatible SGLang/vLLM adapter with exact request,
  revision, seed, cost, and server-tokenized source-span traces.
- add prompt-token replay verification and offline per-layer source activation
  capture; record one non-executing Qwen3.8-27B integration smoke without
  treating it as security evidence.
- document the monotone-provenance tension that can make a detector signal
  decision-redundant under the current fail-closed transfer policy.
- add a one-group, seven-variant Qwen source-conflict pilot with full and opaque
  prompt-source metadata modes, two same-seed replays per variant, and batched
  prompt-source activation capture for all 14 opaque-mode calls.
- exclude editable-install `*.egg-info` build metadata from source manifests so the
  documented `pip install -e ...` workflow does not change the declared source set.
- replace transfer monetary floats with checked signed-64-bit integer minor units
  across actions, trusted facts, policy thresholds, ledger state, schemas, examples,
  receipts, and version-diff bounds; add precision, boundary, negative-zero,
  overflow, exact-arithmetic, digest round-trip, and integer-SMT-domain regressions.

## 0.1.0 — 2026-09-03

- implemented a restricted, terminating hard-policy DSL with three-valued fail-closed trusted-fact semantics;
- implemented signed trusted facts and obligation tokens bound to the complete action/security envelope, actor, session, issuer, and expiry;
- implemented monotone detector composition, sole-point authorization, and fresh execution-time re-evaluation;
- implemented field-level argument/delegation contracts and source-to-field paired replay as an audit baseline;
- added a deployable linear-predicate interface and grouped-split validation for future open-model experiments;
- implemented separate formal/internal receipt layers, an append-only local hash chain, and conditional decision replay;
- added bounded policy-version differencing and an optional Z3 numeric counterexample search that preserves missing-fact semantics;
- added a deterministic transfer simulator, three-arm minimum-falsification harness, strict JSON schemas, CLI, and structured examples;
- added threat model, formal model, novelty audit, experiment protocol, policy reference, risk register, claim ledger, requirements traceability, paper blueprint, competition demo, and Codex handoff;
- added one-command reproduction with compile/test/coverage reports and source/generated-artifact SHA-256 manifests;
- fixed nondeterministic policy hashing from unordered collections and prevented token reuse after provenance/security-envelope mutation.

No real LLM experiment, production-safety claim, or established paper novelty is claimed in this release.
