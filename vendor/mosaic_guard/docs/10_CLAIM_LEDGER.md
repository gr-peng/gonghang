# Claim ledger

This file separates implemented facts, synthetic checks, research hypotheses, and prohibited claims. Update it whenever code, policies, thresholds, or experiments change.

## Status labels

- **PROVED-IN-MODEL:** mathematical consequence of explicit assumptions and implemented semantics.
- **TESTED-PROTOTYPE:** exercised by automated tests in the local prototype.
- **SYNTHETIC-ONLY:** demonstrated only in the deterministic toy environment.
- **REAL-MODEL-SMOKE:** observed on a named real model only as an interface or instrumentation check; not a security-effect result.
- **EXPLORATORY-REAL-MODEL:** observed on a named real model under a frozen but
  statistically inadequate pilot; useful for choosing the next experiment, not
  for a security-effect or generalization claim.
- **HYPOTHESIS:** requires real-model evidence.
- **NOT-ESTABLISHED:** currently unsupported.
- **OUT-OF-SCOPE:** explicitly excluded.

## Ledger

| ID | Claim | Status | Current support | Boundary |
|---|---|---|---|---|
| C1 | learned detector output cannot reduce hard-policy risk in one decision | PROVED-IN-MODEL + TESTED-PROTOTYPE | max-risk lattice; `AuthorizationDecision` validator; lattice/controller tests | fixed policy/facts/tokens; does not cover version updates |
| C2 | final executable set is a subset of the base executable set under the same trusted tokens | PROVED-IN-MODEL + TESTED-PROTOTYPE | `non_expansion_holds`; exhaustive finite lattice test | base policy may itself be wrong |
| C3 | untrusted text/model output cannot satisfy trusted policy predicates | TESTED-PROTOTYPE | signed fact verification; untrusted-fact test | trusted issuer compromise excluded |
| C4 | confirmation/MFA is bound to the exact action/security envelope, actor, and session | TESTED-PROTOTYPE | HMAC token implementation; parameter/source/provenance mutation tests | prototype HMAC/key handling and replay prevention are not production grade |
| C5 | writes are re-evaluated from fresh facts before execution | TESTED-PROTOTYPE | `SafetyController.execute`; stale-balance regression test | fact supplier/backend atomicity still needs production engineering |
| C6 | receipts detect local record modification | TESTED-PROTOTYPE | SHA-256 chain and tamper test | complete chain can be rewritten unless head is externally anchored |
| C7 | formal decisions can be replayed conditional on recorded detector outputs | TESTED-PROTOTYPE | `ReceiptReplayer` test | detector inference itself is not recomputed |
| C8 | explicit provenance catches direct protected-field control in the toy setup | SYNTHETIC-ONLY | structured scenario harness | not an LLM result |
| C9 | paired source replay catches intentionally laundered protected-field control in the toy setup | SYNTHETIC-ONLY | structured toy agent and three-arm harness | true by construction; not causal evidence for natural language agents |
| C10 | delegation mask avoids escalating benign memo/trusted-service influence in the toy setup | SYNTHETIC-ONLY | causal guard tests | real authorization-equivalent cases may be harder |
| C11 | LLM hidden states contain a source-specific, field-specific causal predicate after laundering | HYPOTHESIS | no experiment in this repository | generic IPI exposure evidence from prior work is insufficient |
| C12 | the learned predicate generalizes across wording, sources, turns, and models | NOT-ESTABLISHED | none | requires grouped held-out evaluation |
| C13 | the learned predicate adds to a strong explicit-provenance resolver | NOT-ESTABLISHED | toy gap only | decisive research comparison not run |
| C14 | causal influence can be reliably separated from legitimate authority | HYPOTHESIS | delegation design, toy checks and six-group exploratory legal-service controls | descriptive controls are not a validated classifier or transferable causal label |
| C15 | MOSAIC Guard is a novel publishable method | NOT-ESTABLISHED | narrow gap identified in novelty audit | many components collide with 2025–2026 work |
| C16 | MOSAIC Guard is production-ready banking software | NOT-ESTABLISHED | explicitly false | currency semantics, HMAC, lineage, anchoring, backend atomicity, and audits remain |
| C17 | solver/rule proof establishes real-world fact truth | NOT-ESTABLISHED | prohibited interpretation | proof is conditional on supplied rules and facts |
| C18 | policy update cannot expand permissions | TESTED-PROTOTYPE over bounded domain | bounded diff; optional numeric Z3 search | universal equivalence for full DSL not implemented |
| C19 | text-only harms are prevented | OUT-OF-SCOPE | none | system gates structured tool actions |
| C20 | compromised trusted services are tolerated | OUT-OF-SCOPE | none | trusted computing base assumption |
| C21 | delivered source and generated artifacts match the included manifest | TESTED-PROTOTYPE | `verify_manifest`; mutation/path-escape/editable-metadata tests; `make verify-bundle` | SHA-256 integrity is not publisher authentication or a signature |
| C22 | transfer monetary values are exact bounded integer minor units across actions, trusted facts, policy thresholds, ledger state, schemas, and digests | TESTED-PROTOTYPE | `money.py`; transfer schema; boundary/precision/negative-zero/overflow/round-trip tests | currency identity, exponent, FX, and production backend semantics are not modeled |
| C23 | runtime-owned signed lineage preserves parent union and detects source-ID, parent-edge, timestamp, trust, and source-message tampering before protected-field provenance is accepted | TESTED-PROTOTYPE | `lineage.py`; `test_lineage.py`; authenticated receipt replay | prototype HMAC and in-process catalog only; no production service identity, rotation, or revocation |
| C24 | the Qwen3.8-27B SGLang integration can emit a strict transfer action, reproduce the same action under two same-seed calls, align a source to server token positions, and capture all 65 prompt hidden-state outputs | REAL-MODEL-SMOKE | `artifacts/model_smoke/qwen38-27b-smoke-20260904-f/`; activation SHA-256 `36287c73221db30b8385e6301e3283e3fffe9af0099aba24ff1b00bdb6aca3d2` | one clean prompt only; no attack, grouped split, causal label, trained predicate, generated-token activation, full weight-byte fingerprint, or security metric |
| C25 | Qwen3.8-27B showed a position-sensitive protected-field source effect in one opaque-metadata source-conflict group: `direct_rag_before` and `opaque_rag` changed the recipient to Mallory, while the same direct RAG text after the user source did not | EXPLORATORY-REAL-MODEL | two exact same-seed replays for each of seven variants in `artifacts/pilot_runs/qwen38-27b-source-conflict-opaque-20260904-a/`; 14-call activation SHA-256 `0e4da0ca9e0faaabac2dce6d40e782bdd44b1a712e47e3c397bf13f1e7a6c5ed` | one authored group and one model; prompt source metadata changed between pilots; no intervention-derived causal label, statistical uncertainty, trained predicate, held-out split, or generated-token activation |
| C26 | the current strong provenance baseline handled both recipient-changing opaque-pilot variants without neural evidence: known non-delegated RAG lineage produced `red`/MFA and missing exact protected-field provenance produced `deny` | EXPLORATORY-REAL-MODEL + TESTED-PROTOTYPE | frozen trajectories/receipts in the opaque pilot; authenticated-lineage and policy tests | this does not establish real-world safety; the one-group pilot supplies no evidence that the current neural `red` mapping improves the final decision |

## Further engineering and exploratory evidence

New engineering evidence added on 2026-09-04:

| ID | Claim | Status | Current support | Boundary |
|---|---|---|---|---|
| C27 | generic root-source content edits regenerate affected descendants, reissue immutable IDs/signatures, and preserve source trust and multi-parent ancestry | TESTED-PROTOTYPE | `interventions.py`; `test_interventions.py` | transformation validity is an experiment assumption; signature validity does not prove semantic summary faithfulness |
| C28 | multi-intervention replay keeps no-action, invalid output, action-type changes, comparable field substitutions, and changed authorized references separate | TESTED-PROTOTYPE | `controlled_replay.py`; `test_controlled_replay.py`; fake-endpoint runner test | does not establish causal identification or provide an automatic authority label |
| C29 | six generated base tasks use disjoint group IDs and wording families across configured splits | TESTED-PROTOTYPE | `task_suite.py`; `test_task_suite.py` | small exploratory suite; inspected partitions cannot later be reused as untouched confirmatory data |
| C30 | in the six-group Qwen pilot, direct baseline proposals preserved authorized effects in 4/6 full and 0/6 opaque cases; summary baselines preserved 5/6 and 4/6; delegated baselines and legitimate service field changes preserved 6/6 per mode | EXPLORATORY-REAL-MODEL | `artifacts/intervention_runs/qwen38-multi-intervention-20260904-a/`; 288 planner calls, 18 summary calls, 252 pairs; independent audit | three authored wording families, one seed, no representative sampling or untouched confirmation set; repeat outcomes matched in 35/36 pairs |
| C31 | all 273 recorded action proposals in the six-group run satisfy final_level >= base_level and their formal receipts replay; 60 reference-mismatched proposals required MFA or were denied | EXPLORATORY-REAL-MODEL + TESTED-PROTOTYPE | `run_audit.py`; saved audit and receipts; `22_MULTI_INTERVENTION_RESULTS_ZH.md` | no neural detector added, no tool execution or security-effect claim; 15 schema errors and 9 empty-recipient proposals remain observed interface failures |
| C32 | v2 planning derives missing required fields and write flags from code-owned contracts; clarification and no-action branches never invoke facts or authorization, and a completed follow-up still requires rule-mandated confirmation | TESTED-PROTOTYPE | `planning.py`; adversarial tests in `test_planning.py`; scripted `planning_demo.py` with authenticated receipt replay | model may still invent or misread values; trusted user-intent capture, interactive confirmation UI, persistent sessions and cancellation/revocation are not implemented |
| C33 | Qwen v2 planning passed six interface checks: missing recipient, missing amount, complete action, follow-up completion, unsupported request and claimed confirmation; three rule receipts replay | REAL-MODEL-SMOKE | `artifacts/model_smoke/planner-v2-qwen38-20260904-a/`; six raw calls, zero validation errors, 45 run-file hashes verified | authored structured user fixtures, one model and one call per case; no tool execution, no trusted free-text parser or production safety claim |

## Runtime safety engineering, 2026-09-04

| ID | Claim | Status | Current support | Boundary |
|---|---|---|---|---|
| C34 | policy 0.2.0 requires MFA when actor-wide daily spent plus proposed amount exceeds 1000 currency units; missing/overflowing spending fails closed | TESTED-PROTOTYPE | `test_runtime_safety.py`; bounded old/new rule diff; `sum_minor` evaluator and bounded SMT tests | signed bank facts and correct actor identity are trusted; 10000-unit hard ceiling is a prototype restriction |
| C35 | BankLedger serializes facts, authorization and commit across controllers sharing that bank instance and rolls spending at Shanghai midnight | TESTED-PROTOTYPE | concurrent split, midnight expiry and sequential execution regressions | in-process simulator only; no distributed transaction, durable daily ledger or idempotency |
| C36 | an explicitly injected safety circuit denies a locked actor across sessions; pending confirmation does not count as failure | TESTED-PROTOTYPE | failure/denial, valid-token bypass and manual-reset tests | opt-in; state/reset log in memory; production reset authentication, persistence and multi-worker sharing not implemented |
| C37 | public operation outcomes derive from a matching runtime receipt and validated tool-result fields; failed or inconsistent completion returns unknown, not success | TESTED-PROTOTYPE | false-result, pending, hash/action mismatch, fact/snapshot/tool fault tests | downstream UI must use this interface; no arbitrary LLM text verifier, compromised backend protection or automatic rollback |
| C38 | five Qwen calls supported ten successful mock-only safety integration checks, including cumulative MFA, claimed confirmation, RAG input, fault states and lockout | REAL-MODEL-SMOKE | `artifacts/model_smoke/runtime-safety-qwen38-20260904-b/`; ten replayable receipts | five authored inputs, one model/seed; fault requests reuse a proposal; fixture-issued tokens, no real bank or real-user authentication; not ten independent attack trials |
| C39 | same-account transfer conserves simulated balance instead of creating funds | TESTED-PROTOTYPE | account-alias regression; debit and credit aggregated before ledger mutation | only the mock ledger; banking rules may choose to reject self-transfers |

## Execution lifecycle engineering, 2026-09-04

| ID | Claim | Status | Current support | Boundary |
|---|---|---|---|---|
| C40 | repeated delivery of the same bound write request does not dispatch again and returns its recorded terminal receipt | TESTED-PROTOTYPE | `execution_store.py`, `test_execution_once.py`, `execution_demo.py` | stable request ID and intact shared store required; a new request with new trusted approval is a separate payment |
| C41 | required tokens are consumed atomically with the execution reservation; concurrent contenders cannot reuse one token | TESTED-PROTOTYPE | concurrent separate-SQLite-connection test, duplicate-token and new-request reuse tests | live authorities must share the same store; historical signature validation intentionally ignores current consumption |
| C42 | consumed/revoked tokens and request results survive SQLite close/reopen; an unfinished attempt cannot be automatically dispatched again | TESTED-PROTOTYPE | state reopen, post-commit timeout, simulated process exit and audit-write failure tests | no real process kill/power-loss test, durable mock banking ledger or distributed transaction; interrupted requests may remain unresolved |
| C43 | valid token revocation before reservation prevents execution and preserves historical signature validation | TESTED-PROTOTYPE | revocation race, signature forgery and reopen tests | trusted host API only; operator authentication absent; late revocation does not undo an existing reservation or bank effect |

## Safety backend integration delivery, 2026-09-04

Earlier rows describing in-memory circuits, host-only reset, embedded demo keys
or absence of crash tests describe the earlier interfaces. The deployed mock
gateway now uses the implementations below; the legacy host APIs remain available.

| ID | Claim | Status | Current support | Boundary |
|---|---|---|---|---|
| C44 | gateway circuit state and multi-writer receipt chains survive restart; failure observation and audit append share a transaction | TESTED-PROTOTYPE | `test_persistent_runtime.py` | trusted local SQLite; no host compromise or database rollback protection |
| C45 | authenticated gateway separates agent, human approval and operator scopes; independent one-use signed proofs bind exact actions or current reset generations | TESTED-PROTOTYPE | `test_gateway.py`, `test_security_operations.py` | HMAC service trust domain; actual login and biometric/SMS MFA must be supplied upstream |
| C46 | cancellation/session revocation committed before reservation prevents subsequent write dispatch | TESTED-PROTOTYPE | race regressions in persistent/runtime and security-operation tests | cannot undo an already reserved or committed operation |
| C47 | actual process exit before or after mock-bank commit preserves durable state, prevents redispatch and locks the actor at recovery | TESTED-PROTOTYPE | `test_process_recovery.py`, `crash_safety_fixture.py` with exit code 73 | local process exit, not power loss; separate databases are not a distributed atomic commit |
| C48 | private deployment configuration rejects insecure key permissions, changed policy hashes and accidental key replacement; HTTP has strict bodies, scopes, limits and no raw error disclosure | TESTED-PROTOTYPE | `test_service.py`, `test_client_operations.py` | loopback adapter; public TLS/identity infrastructure and operational key rotation are external |
| C49 | public bank responses mask full accounts and remove non-allowlisted data; deterministic text screening masks covered sensitive patterns | TESTED-PROTOTYPE | `test_security_operations.py`, `test_service.py` | not comprehensive PII detection, semantic content safety or arbitrary LLM factual verification |
| C50 | actual pinned local Docker probes demonstrate non-root execution, network/host-file isolation, read-only root, timeout, output/memory limits and container cleanup | TESTED-PROTOTYPE | `sandbox_acceptance.json` 8/8; opt-in `test_sandbox.py` | authored probes, trusted daemon/image/host kernel; not a proof against container escape |
| C51 | signed audit prefix checkpoints detect covered record modification/truncation and permit later legitimate appends | TESTED-PROTOTYPE | `audit.py`, `test_security_operations.py` | external custody must be provided; HMAC and local state do not resist compromise of all signing keys |
| C52 | trusted-service integration demonstration passes 16 checks and produces two intended mock transfers with replayable persistent receipts | TESTED-PROTOTYPE | `engineering_demo.py`, `engineering_demo.json` | scripted inputs/approval fixtures, no new LLM calls and no real-bank execution |

## Reporting rule

A result table may include C8–C10 only under a heading containing the exact phrase:

> deterministic synthetic smoke test; not empirical LLM evidence

C11–C15 may appear only as research questions or hypotheses until artifacts are produced and this ledger is updated with exact commands, hashes, and outputs.

## Security boundary repairs, 2026-09-05

| ID | Claim | Status | Current support | Boundary |
|---|---|---|---|---|
| C53 | signed banking facts require strict Boolean/account-list/money types; malformed operands remain Unknown under negation and cannot authorize | TESTED-PROTOTYPE | `value_types.py`, policy 0.2.1, `test_security_boundary_regressions.py` | predicate extensions must define their own domain contract; signature validity alone does not prove semantic truth |
| C54 | revoked credentials cannot increment user rejection counters; identity expiry is rechecked before fact retrieval and inside write reservation, leaving tokens unused when rejected | TESTED-PROTOTYPE | HTTP revoked-session and before-facts/facts/reservation expiry regressions | authenticated local identity service and clock remain trusted; completed historical receipts are not new dispatches |
| C55 | independent sandbox supervisor removes a container after service SIGKILL or timeout while the service is stopped; recovery removes expired containers only within its deployment | TESTED-PROTOTYPE | real Docker kill/stop/recovery tests in `test_sandbox.py` | trusted local Docker daemon and supervisor; simultaneous loss of both processes or daemon outage requires subsequent recovery/operator handling; not proof against container escape |


## Independent security patch, 2026-09-06 (code 0.1.1, policy 0.2.2)

Current-run evidence and limitations: `28_INDEPENDENT_SECURITY_REVIEW_ZH.md`.
Prior model and Docker evidence is preserved, not reexecuted in this environment.

| ID | Claim | Status | Current support | Boundary |
|---|---|---|---|---|
| C56 | authorization evidence is rechecked after blocking state/snapshot waits and before reservation/dispatch | TESTED-PROTOTYPE | added expiry regressions, `controller.py` | trusted clock/services; dispatch authorization is not remote rollback |
| C57 | cancellation proof nonce and cancellation state commit together; stale reset generations fail | TESTED-PROTOTYPE | cancellation/reset fault regressions | SQLite local transaction domain; no cross-bank cancellation guarantee |
| C58 | conflicting newest simultaneous facts become Unknown; token expiry does not exceed proof expiry | TESTED-PROTOTYPE | policy 0.2.2, conflict/fractional-time regressions | signed source truth remains a TCB assumption |
| C59 | lineage/JSON/YAML/ingress/lock inputs have explicit complexity or wait limits | TESTED-PROTOTYPE | tests and fixed-seed DAG/parser/local-HTTP stress | not a bound on arbitrary trusted callbacks or all DoS |
| C60 | tested mock crash/concurrency cases preserve effects and unknown outcomes without redispatch | TESTED-PROTOTYPE | six real process exits; 512 concurrent calls, 266 authorized effects | local mock, not real-bank distributed exactly-once or production throughput |

## Local integration acceptance, 2026-09-06

The independent patch above was integrated and reexecuted locally: 281 passed, zero skipped, 87% coverage; real Docker and SMT included; a single full reproduce command passed. Thread/process stress completed 25 checks, 512 calls and exactly 266 intended mock bank effects with conserved balances. See `29_PRO_INTEGRATION_ACCEPTANCE_20260906_ZH.md` and `artifacts/security-review-20260906/local-integration/`. This supersedes only the environment-specific skip status above; production and real-bank claim boundaries remain unchanged.
