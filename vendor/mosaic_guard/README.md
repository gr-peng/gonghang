# MOSAIC Guard

2026-09-06 本机整合验收：281 项全部通过（含真实 Docker 与 SMT），并发压力检查 25/25。详见 [本机整合与验收记录](docs/29_PRO_INTEGRATION_ACCEPTANCE_20260906_ZH.md)。

**2026-09-06 security patch: code 0.1.1 / policy 0.2.2.** Start with
[独立安全审查、回归证据和升级说明](docs/28_INDEPENDENT_SECURITY_REVIEW_ZH.md).
The directory keeps its original name for compatibility. New evidence is under
`artifacts/security-review-20260906/`; historical Docker/model artifacts are not
new-version validation. Run `make security-review` for the added regressions and
`make security-stress` for local durable-mock stress. Existing deployments require
an explicitly reviewed policy-pin update, not database/key recreation.

Current priority: build the safety module for the AI competition system, not its
customer-service UI. Research
findings are recorded separately and do not block engineering delivery. See
`docs/23_COMPETITION_ENGINEERING_PLAN_ZH.md` for the current plan.

The current engineering slice adds actor-wide daily cumulative risk, a
single-process mock-bank transaction boundary, an opt-in runtime safety circuit,
and receipt-derived public outcomes. Integration and evidence boundaries are in
`docs/24_RUNTIME_SAFETY_INTEGRATION_ZH.md`.

Write execution now also uses atomic request reservation and one-time obligation
tokens. Stable request IDs return prior results without another tool call; an
unfinished reservation remains uncertain across SQLite reopen. See
`docs/25_EXECUTION_ONCE_ZH.md` for persistence and integration requirements.

The safety backend now provides authenticated HTTP integration and a no-retry
client, persistent circuits/audits, independent approval proofs, revocation and
reconciliation, a durable mock bank, private keys, data minimization and optional
restricted Docker execution. Start with `docs/26_SAFETY_BACKEND_HANDOFF_ZH.md`.
Real identity/MFA and banking services remain external integrations.

**M**onotone **O**bligation **S**afety with **A**ttribution, **I**ntegrity, and **C**ausal evidence.

MOSAIC Guard is a research prototype for placing a deterministic safety control plane between an LLM agent and side-effecting tools. The agent may understand natural language and propose a structured action, but it is never the authorizer. A restricted policy kernel, verified trusted facts, exact-action tokens, argument/delegation contracts, and an execution mediator make the decision. Learned signals may only make that decision stricter.

> **Evidence status:** runnable software prototype plus deterministic synthetic falsification harness. No real-LLM security result, production guarantee, or established paper novelty is claimed.

## 1. Core safety invariant

For an action \(a\), let \(b(a)\) be the hard-policy risk and let learned/audit modules emit \(s_1(a),\ldots,s_k(a)\) in the ordered lattice

\[
\texttt{green}<\texttt{yellow}<\texttt{red}<\texttt{deny}.
\]

The controller computes

\[
r(a)=b(a)\vee s_1(a)\vee\cdots\vee s_k(a),
\]

where \(\vee\) is maximum risk. Therefore a detector may require confirmation, MFA, review, or denial, but it cannot turn a hard-policy denial into permission or discharge an obligation.

The implementation also enforces:

- only signed, current trusted facts can satisfy identity, ownership, balance, recipient, and limit predicates;
- optional authenticated-lineage enforcement verifies runtime-issued source IDs, content hashes, parent closure, transformation edges, producer identity, and timestamps before provenance can satisfy a protected-field contract;
- transfer amounts, balances, and limits use bounded signed-64-bit integer minor units; binary floats, booleans, non-positive transfer amounts, excessive precision, and overflow fail closed;
- missing trusted facts use three-valued `unknown` semantics, including under negation, so absence cannot become permission;
- confirmation/MFA tokens bind the full action **and security envelope**: action type, parameters, actor, session, write flag, runtime source records, and field provenance;
- every write is re-evaluated from fresh facts immediately before tool execution;
- formal authorization evidence and internal causal evidence are stored in separate receipt layers;
- policy updates can be checked for permission expansion over an explicit finite domain, with an optional Z3 search for a bounded numeric fragment.

## 2. Scientific question

The broad claim “neural signal + symbolic rules secures agents” is already too crowded. This repository instead isolates one falsifiable mechanism question:

> After summaries, paraphrases, multi-turn planning, and source mixing make explicit provenance incomplete, does an LLM retain a transferable **source-to-action-field causal signal** that identifies which source changes which protected field—and can that signal distinguish unauthorized control from legitimate delegation?

The code currently implements paired source replay as an **audit baseline and label generator**, not as a completed causal-identification method. A real paper requires multi-intervention agreement, grouped holdouts, shortcut controls, authorization-equivalent benign pairs, and incremental value over a strong argument-level provenance resolver. See `docs/04_SOURCE_CAUSAL_METHOD.md`, `docs/05_EXPERIMENT_PROTOCOL.md`, and `docs/06_NOVELTY_AUDIT.md`.

## 3. Runtime architecture

```text
untrusted user/RAG/tool/memory/agent text
                  │
                  ▼
        runtime-owned source registry
                  │
                  ▼
        LLM proposes structured action
                  │
        ┌─────────┴──────────┐
        ▼                    ▼
provenance resolver   causal/probe audit
        │              obligation-only
        └─────────┬──────────┘
                  ▼
       deterministic SafetyController
       hard policy + trusted facts +
       argument contracts + risk join +
       exact-action tokens + state machine
                  │
                  ▼
 allow / confirmation / MFA / deny
                  │
                  ▼
         sandboxed/real tool mediator
                  │
                  ▼
       hash-chained dual-layer receipt
```

## 4. Repository map

```text
src/scns_guard/
  policy.py          restricted policy DSL and deterministic evaluator
  money.py           exact bounded integer-minor-unit validation/arithmetic
  controller.py      sole authorization point and execution-time re-check
  causal.py          paired source-to-field replay and delegation-aware guard
  lineage.py         signed runtime lineage and explicit provenance resolver
  activations.py     recorded prompt/source token-alignment checks
  adapters/          audited OpenAI-compatible SGLang/vLLM model boundary
  predicates.py      interface for a learned source-field predicate
  trust.py           signed trusted facts for the local prototype
  tokens.py          security-envelope-bound confirmation/MFA tokens
  receipts.py        append-only local hash-chained dual receipts
  replay.py          conditional formal decision replay and integrity checks
  policy_diff.py     bounded update diff and optional Z3 numeric search
  simulator.py       minimal bank ledger and deterministic toy agent
  llm_adapter.py     fail-closed JSON adapter boundary for a local LLM
  dataset.py         grouped research scenarios and leakage checks
  training.py        simple calibrated linear-predicate training path
  experiment.py      three-arm deterministic falsification harness
configs/              transfer policy and detector configuration
schemas/              generated JSON Schemas for public interfaces
examples/             deterministic structured fixtures, explicitly non-LLM
tests/                invariant, adversarial, replay, and experiment tests
docs/                 method, threat model, novelty, experiments, handoff
artifacts/            generated reports, receipts, smoke outputs, hashes
scripts/              demo, experiment, export, training, and verification
```

`schemas/transfer_params.schema.json` is the action-specific public contract
for exact positive integer `amount_minor`; the generic `ActionProposal.params`
container remains extensible for additional action types.

Start with `docs/INDEX.md`. A local coding agent should read `AGENTS.md` and `docs/08_CODEX_HANDOFF.md` before editing.

## 5. Install and reproduce

Python 3.11 or newer is required.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
make reproduce
```

`make reproduce` performs compilation, tests, coverage diagnostics, schema/example export, the transfer demo, the deterministic three-arm harness, receipt replay, and source/artifact hashing. Verify the delivered tree later with:

```bash
make verify-bundle
```

Optional extras:

```bash
pip install -e '.[ml]'   # NumPy/scikit-learn predicate experiments
pip install -e '.[smt]'  # execute the optional Z3 policy-diff regression
```

Without `z3-solver`, the corresponding test is explicitly reported as skipped; no SMT run is implied.

One non-executing Qwen3.8-27B/SGLang integration smoke can be launched through
`scripts/run_model_smoke.py`. It records exact prompts, decoding settings,
server-tokenized source spans, raw output, policy decision, and hashes. This is
an interface test, not an LLM-security evaluation. Per-layer prompt activation
capture is a separate, offline read-only forward through
`scripts/extract_prompt_activations.py`.

The repository also includes a one-group, seven-variant source-conflict pilot.
`scripts/run_source_conflict_pilot.py` records two same-seed calls per variant
without tool execution; `scripts/run_remote_pilot_activations.sh` captures all
recorded prompt-source activations in one model load. The pilot is exploratory,
not a security benchmark; see `docs/20_QWEN_SOURCE_CONFLICT_PILOT_ZH.md`.

The completed six-group exploratory experiment is implemented by
`scripts/run_multi_intervention_pilot.py`: six base tasks, real model-generated
summaries, delegated-service controls and authenticated source interventions.
`scripts/verify_intervention_run.py RUN_DIR` checks a completed run without
calling the model. The saved run contains 288 planner calls, 18 summary calls,
252 paired comparisons and 273 replayed receipts, with 15 output-validation
errors retained. See `docs/21_MULTI_INTERVENTION_PROTOCOL_ZH.md` and
`docs/22_MULTI_INTERVENTION_RESULTS_ZH.md`. This remains
an audit-only research instrument; no learned authorization path is added.

## 6. Individual commands

The new versioned business entry point supports missing-field clarification before
authorization. `PlanningLLMAdapter` accepts task drafts; `PlanningService` reviews
complete actions but never executes them. Run the scripted mock-bank continuation:

```bash
PYTHONPATH=src python scripts/run_planning_demo.py
```

This demonstrates clarification, a new user-supplied field, exact-action
confirmation and mock execution. It uses a scripted planner and fixture token,
not a real authentication interface. The existing v1 experiment adapter is retained.

```bash
make test
make demo
make experiment
make schemas
make examples
make verify
make manifest
make verify-bundle
```

Equivalent CLI after installation:

```bash
mosaic-guard demo --policy configs/transfer_policy.yaml \
  --receipts artifacts/demo_receipts.jsonl
mosaic-guard experiment --policy configs/transfer_policy.yaml \
  --repeats 20 --seed 7 --output artifacts/synthetic_results.json
mosaic-guard verify-receipts artifacts/demo_receipts.jsonl
mosaic-guard schemas --output-dir schemas
```

## 7. Deterministic smoke result

The included toy agent is deliberately constructed so that explicit provenance sees direct control while a “laundered” fixture drops the observed field binding. Paired replay then detects the induced field change. By construction:

| Arm | Direct escalation | Washed escalation | Benign over-escalation | Permission expansion |
|---|---:|---:|---:|---:|
| hard rules | 0 | 0 | 0 | 0 |
| hard + explicit provenance | 1 | 0 | 0 | 0 |
| hard + provenance + paired replay | 1 | 1 | 0 | 0 |

These values only confirm that the software encodes the intended toy distinction. They must never be quoted as evidence that an LLM defense works.

## 8. Security and claim boundary

This is not production banking software. Important unresolved items include:

- the prototype uses exact integer minor units, but does not yet model currency identity, currency exponents, FX conversion, or jurisdiction-specific rounding;
- prototype HMAC secrets stand in for authenticated services, KMS/HSM, rotation, and revocation;
- source lineage is authenticated with prototype HMACs in one process, but cross-process service identity, asymmetric verification, rotation, and revocation remain unimplemented;
- tokens are not one-time and the backend lacks a distributed authorization/commit protocol;
- a local hash chain needs external head anchoring or WORM storage to resist complete-history rewrite;
- paired source deletion is not automatically a valid causal intervention;
- receipt replay proves deterministic evaluation under recorded inputs, not policy correctness or real-world fact truth;
- the optional SMT compiler covers only an explicit numeric fragment, not the full policy language;
- compromised policy, fact, token, mediator, or tool services are outside the present trusted-computing-base model.

Read `SECURITY.md`, `docs/01_THREAT_MODEL.md`, `docs/10_CLAIM_LEDGER.md`, and `docs/18_RISK_REGISTER.md` before integration.

## 9. Immediate next milestone

The next scientifically decisive milestone is one frozen open-weight model and one transfer schema, with 100–300 grouped base scenarios containing direct, laundered, irrelevant, and authorization-equivalent variants. Generate expensive multi-intervention source-field labels, fit the simplest fixed predicate first, and evaluate its incremental value over strong explicit provenance at a matched benign-escalation budget. The exact acceptance and kill gates are in `docs/05_EXPERIMENT_PROTOCOL.md` and `docs/08_CODEX_HANDOFF.md`.

## License

MIT. See `LICENSE`.
