# Codex handoff

## Objective

Latest delivery: `26_SAFETY_BACKEND_HANDOFF_ZH.md`. Persistent circuits/chains,
authenticated HTTP, independent control proofs, revocation/reconciliation,
private keys, durable mock-bank recovery, data minimization and Docker isolation
are implemented. Actual identity/MFA, real banking and whole-team UI/business
coverage remain external integrations. Historical backlog below is not the
current completion status.

Build the safety module for the team's banking assistant without
weakening the fixed safety contract. Per Infty's 2026-09-04 priority update,
engineering delivery comes first; research is optional parallel evidence, not a
prerequisite. The current plan is `23_COMPETITION_ENGINEERING_PLAN_ZH.md`.

The versioned planning/clarification interface and scripted mock-bank continuation
are implemented in `planning.py` and `planning_demo.py`. The safety-only scope
explicitly excludes customer-service UI. Daily cumulative risk, the mock-bank
transaction boundary, an opt-in safety circuit and receipt-grounded outcomes are
implemented; see `24_RUNTIME_SAFETY_INTEGRATION_ZH.md`. Request reservation,
one-time tokens, revocation and SQLite persistence are now implemented in the
write path; see `25_EXECUTION_ONCE_ZH.md`. Next prioritize persistent circuit
state, authenticated integration and safety self-assessment evidence. The team
building the assistant owns its UI.
Historical research task IDs below remain references, not the delivery order.

## Non-negotiable invariants

Never change these to make an experiment pass:

1. `final_level >= base_level` for every decision.
2. detector output cannot discharge an obligation.
3. LLM/model output cannot create trusted facts, trusted provenance, confirmation, or MFA.
4. tokens bind the complete action/security envelope (including runtime source records and field provenance), actor, session, issuer, and expiry.
5. every write calls a fresh fact supplier immediately before execution.
6. unknown actions, missing trusted predicates, malformed policy expressions, and incomplete protected-field provenance fail closed.
7. formal receipt and internal evidence remain separate.
8. no prompt retry, fallback model, per-case threshold, per-case direction/dose, or broad all-deny rule may be counted as method success.
9. synthetic outputs remain labelled `synthetic_smoke_test_not_empirical_result`.
10. no unrun experiment may enter a results table.

## Start here

```bash
cd source_causal_nesy_guard
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
make reproduce
```

Expected current baseline: all tests pass; the synthetic causal arm catches deliberately laundered structured cases by construction.

## P0 engineering tasks

### P0.1 Monetary correctness

**Status: completed in the 2026-09-04 Codex audit working tree.** Transfer
amounts, balances, limits, facts, policy thresholds, schemas, digests, and the
toy ledger now use checked signed-64-bit integer minor units. See
`tests/test_money.py` and `tests/test_schemas.py`.

The implementation requirement was to replace all `float` amounts/balances/limits with integer cents or `Decimal` quantized at the policy boundary.

Acceptance:

- no binary floating-point amount in action, policy comparisons, facts, ledger, or schemas;
- tests for `0.1 + 0.2`, boundary equality, negative zero, excessive precision, and overflow;
- canonical digest is stable across serialization.

### P0.2 Authenticated source-lineage objects

**Status: completed for the in-process prototype on 2026-09-04.** `lineage.py`
issues signed immutable source records, preserves transitive ancestry across
generic transformations, verifies content/producer/time/parent closure, and
provides a conservative exact-payload provenance resolver. `SafetyController`
can require verified lineage before protected-field bindings satisfy a contract.
Cross-process authentication and production key management remain P2 work.

Introduce a runtime-owned lineage record with:

- immutable source object ID;
- source kind and trust class;
- parent IDs and transformation operation;
- content hash;
- producer/component identity;
- timestamp and optional signature;
- field-level derivation edges.

Do not allow the LLM to author or mutate it. Extend argument contracts to consume these objects.

Acceptance:

- summary/translation/quotation preserves upstream lineage union;
- missing lineage fails closed for protected fields;
- forged source IDs and parent edges fail verification;
- direct and laundered provenance cases are represented without hard-coded scenario branches.

### P0.3 Open-model adapter

**Status: partially completed on 2026-09-04.** The OpenAI-compatible adapter
successfully called the existing school-host SGLang service for Qwen3.8-27B,
recorded two exact same-seed replays, and aligned runtime sources to server
token positions. A separate Transformers runner verified the identical prompt
token hash and captured all 65 prompt hidden-state outputs for the source span.
This is an integration smoke only; generated-token activations, a full
weight-byte fingerprint, grouped data, causal labels, a learned predicate, and
security metrics have not been produced yet.

A subsequent single-group exploratory pilot captured 14 frozen calls and all
65 prompt hidden-state outputs for 26 source spans. It found a position-sensitive
recipient change only when source metadata was hidden from the model. This is
enough to justify a small intervention-labeling experiment, but not enough to
claim P0.3 complete: generated-token states and a full weight-byte fingerprint
are still absent. See `20_QWEN_SOURCE_CONFLICT_PILOT_ZH.md`.

Add one adapter for the selected local model stack. Prefer a thin interface around the user's existing vLLM/Transformers/SGLang setup.

Capture:

- exact model/revision/tokenizer;
- prompt template and source delimiters;
- decoding configuration and seed;
- structured action JSON;
- per-layer activations at frozen token positions;
- source span indices;
- wall-clock and token counts.

Acceptance:

- deterministic replay where the backend permits it;
- invalid JSON fails closed, with no silent retry counted in evaluation;
- source ablation can reuse a common random seed;
- one trace can be reproduced from a manifest.

### P0.4 Grouped scenario generator

**Latest implementation:** `task_suite.py` adds six independently parameterized
base tasks (three disjoint wording families, recipient and amount fields), with
direct, model-summary, and delegated-service conditions. Group/template isolation
is tested. This remains a small exploratory suite, not a trained-predicate or
generalization result. The six-group real-model run is complete with 288 planner
and 18 summary calls; all configured group/family splits passed the saved-run
audit. The one-group pilot below is historical and preserved unchanged.

**Earlier single-group status, superseded by the update above.** The runtime
generates one base group with seven linked variants and generator-authored
authorized effects. Both metadata-visible and opaque-metadata configurations
were executed twice per variant against Qwen3.8-27B. Group-level train/test
splitting, broad paraphrase/turn-depth coverage, and a multi-group frozen dataset
are not implemented, so this is not E2 evidence.

Implement a data schema where one base task owns all counterfactual siblings. Generate:

- clean user value;
- authorized trusted-tool value;
- direct untrusted control;
- summary-laundered control;
- source identity and position swaps;
- paraphrases;
- irrelevant-source negative controls.

Acceptance:

- group-level split enforcement;
- no counterfactual siblings cross splits;
- ground truth is authored by the generator, never inferred by the model;
- exact protected action/effect equivalence is checked programmatically.

### P0.5 Multi-intervention causal labeler

**Latest implementation:** `interventions.py` and `controlled_replay.py` provide
generic deletion, visible-descendant relocation, identity rename, content/field
replacement and negative-control replay. Content edits regenerate affected
summaries and reissue immutable lineage; identity-only edits preserve content.
`run_multi_intervention_pilot.py` runs a fixed real-model suite. The old
`CounterfactualSourceAttributor` remains compatible; the new multi-intervention
reports are audit-only and are not automatically mapped to detector risk.

Effect profiles preserve unavailable comparisons and heterogeneous intervention
responses rather than producing an unjustified pooled causal label. General
cross-role identity swaps, multi-turn relocation and calibrated intervention
uncertainty are still open. See `21_MULTI_INTERVENTION_PROTOCOL_ZH.md`.

The completed run and audit are described in `22_MULTI_INTERVENTION_RESULTS_ZH.md`.
Same-seed outcomes matched in 35/36 pairs; the discrepant case also changed under
negative controls. Do not treat its individual restoration as an identified
intervention effect. Do not reuse these inspected tasks as confirmation data.

Generalize `CounterfactualSourceAttributor` from deletion only to an intervention protocol:

```python
class Intervention(Protocol):
    intervention_id: str
    def apply(trace, source_id) -> AgentTrace: ...
```

Implement ablation, control attenuation, source relocation, source identity swap, position swap, field replacement, and negative control.

Acceptance:

- each report records intervention ID and validity assumptions;
- estimates include between-seed and between-intervention uncertainty;
- contradictory interventions are surfaced, not averaged away;
- authorization-equivalent pairs are a mandatory report section.

## P1 research tasks

### Research follow-up notes from the six-group run

- The versioned clarification/no-action product interface is now implemented and
  tested independently of research progress. Frozen pilot inputs and errors remain
  unchanged; no per-case defaults or retries are used.
- Freeze a new repeat-control protocol and new task families before inference.
  Separate backend/model repeat variation from content/position/identity effects.
- Keep the next predicate-training and activation-control tasks below deferred
  until the intervention evidence supports a transferable labeling rule.

### P1.1 Simple predicate first

Train regularized logistic regression on frozen source-field activation features. Labels come from the causal report, not `attack/benign`.

Required baselines:

- text n-grams;
- source kind/position/context length;
- generic IPI-exposure probe;
- action-level replay score;
- explicit provenance resolver;
- random/permuted labels.

Acceptance:

- group split;
- frozen threshold;
- calibration plot/data;
- source-field localization metrics;
- washed-source incremental gain at matched benign escalation;
- authorization-equivalence audit.

### P1.2 Prompt-Path/SI-FAC integration

Use existing internal features only after writing a clean adapter. Do not import prior success claims as facts.

Test:

- whether source-specific vectors survive summary laundering;
- whether they predict field effect, not just source presence;
- whether a fixed layer/readout transfers;
- whether source identity and position swaps break the signal;
- whether codebook/readout effects dominate the apparent attribution.

### P1.3 Full version-diff gate

Extend policy/model update checks to include:

- policy DSL changes;
- provenance resolver changes;
- detector model artifact;
- threshold;
- `audit_only` activation;
- risk mapping.

A de-escalation must produce a review artifact with concrete changed cases.

## Product backlog (now higher priority than optional research)

- integrate persistent request/token storage and authenticated revocation;
- backend-supported reconciliation/idempotency and distributed commit semantics;
- persistent circuit state and authenticated operator reset;
- receipt head anchoring;
- sandboxed real/mock API;
- human-readable rejection reason from rule trace;
- red-team replay artifacts for the team's demo;
- machine-readable safety, authorized-effect, availability and cost metrics.

## Files most likely to change

| Task | Primary files |
|---|---|
| money type | `models.py`, `policy.py`, `simulator.py`, schemas, tests |
| source lineage | `models.py`, `llm_adapter.py`, `policy.py`, `causal.py` |
| real model | new `adapters/`, `causal.py`, experiment scripts |
| predicate training | `predicates.py`, new `training/`, scripts |
| data protocol | new `data_generation/`, `experiment.py` |
| SMT | `policy_diff.py` or new compiler module |
| receipts | `models.py`, `receipts.py`, `replay.py` |

## Required command before every handoff

```bash
make reproduce
```

This captures compile/test/coverage reports, regenerates schemas and structured examples, runs the transfer demo and synthetic harness, verifies receipt replay, and writes source/artifact hashes. Also run static typing/linting after adding the selected tools. Do not reformat generated result artifacts into claimed evidence.

## Definition of done for the next milestone

The next milestone is an integrable safety backend that enforces exact-action
confirmation/MFA, rejects unsafe proposals, prevents duplicate execution and
returns grounded outcomes with rule receipts. It must expose clear contracts for
the separate assistant/UI team. Document executed evidence and remaining
deployment limitations. No research result or customer-service UI is required.

### Optional research milestone, not a product prerequisite

A future predicate study would require:

- exact manifests and replay scripts;
- explicit provenance baseline;
- expensive source-field multi-intervention labels;
- a simple fixed predicate;
- held-out washed-source evaluation;
- authorization-equivalence audit;
- matched utility/cost accounting;
- no permission expansion;
- claim ledger updated with only executed evidence.
