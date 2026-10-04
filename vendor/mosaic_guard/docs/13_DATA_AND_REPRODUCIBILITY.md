# Data and reproducibility contract

## 1. Evidence hierarchy

The repository uses four evidence tiers. They must never be merged in one result table without an explicit tier column.

| Tier | Object | What it can support |
|---|---|---|
| E0 | unit/invariant test | software semantics and regression claims |
| E1 | deterministic synthetic trace | code-path and measurement-pipeline smoke tests |
| E2 | frozen open-model trajectory dataset | empirical claims for the tested model/task distribution |
| E3 | independently reproduced held-out deployment or benchmark | broader transfer and deployment claims |

The archive contains E0 and E1 evidence, one real-model integration smoke under
`artifacts/model_smoke/`, and two single-group exploratory pilots under
`artifacts/pilot_runs/`. The opaque-metadata pilot includes all 65 prompt
hidden-state outputs for 14 calls and 26 source spans. These runs remain below
E2: one group cannot support uncertainty estimates, a held-out split, or an
LLM-security-effect claim. A further six-group exploratory run is saved under
`artifacts/intervention_runs/`: 288 planner calls, 18 summary calls, 252 paired
comparisons and 273 replayed receipts. It retains 15 output-validation errors
and one non-identical same-seed repeat. All its configured splits have now been
inspected, so they cannot serve as untouched confirmation data. This pilot is
not promoted to a validated E2 security result; there is no E3 evidence. See
`22_MULTI_INTERVENTION_RESULTS_ZH.md`.

## 2. Experimental object hierarchy

One base scenario generates a family of linked records:

```text
base scenario (group_id)
  ├── authorized reference action and protected effect
  ├── benign authorization-equivalent variants
  ├── direct-injection variants
  ├── laundering / paraphrase / multi-turn variants
  └── intervention replays
        └── source × action-field effect rows
              └── frozen features for an online predicate
```

All descendants of the same base scenario share one `group_id`. They must remain in one split.

## 3. Required immutable identifiers

A real-model dataset must store at least:

- `scenario_id`, `group_id`, and split;
- model repository/revision, tokenizer revision, precision, inference engine, and generation configuration;
- exact system/user/tool messages after rendering;
- runtime-owned source IDs, source hashes, lineage edges, and transformation IDs;
- tool schema and policy digest;
- generation seed and replay seed;
- full proposed structured action;
- authorized reference action and protected effect;
- each intervention definition and output;
- source-field effect label, consistency, and failure reason;
- feature extraction layer/token position and tensor shape;
- code commit or archive SHA-256.

A row without enough information to reconstruct its base scenario and intervention is not admissible evidence.

## 4. Split contract

The split is grouped, not row-random:

\[
G_{\mathrm{train}}\cap G_{\mathrm{cal}}=
G_{\mathrm{train}}\cap G_{\mathrm{test}}=
G_{\mathrm{cal}}\cap G_{\mathrm{test}}=\varnothing.
\]

At least one strong test split must hold out an entire transformation family, such as summarizer model, source combination, turn depth, or attack template family. Counterfactual siblings and authorization-equivalent siblings may never cross splits.

`validate_group_isolation()` enforces the first condition for predicate rows. It does not certify that semantic template families were correctly grouped; that requires a dataset audit.

`validate_task_isolation()` additionally checks task group uniqueness and prevents
configured wording-family IDs from crossing splits in the intervention suite.
That structural check does not prove semantic independence.

## 5. Threshold and model freezing

Use train data to fit parameters and calibration data to choose a threshold under a prespecified false-escalation constraint. Touch the test set once after:

- feature list is frozen;
- model architecture and regularization are frozen;
- threshold-selection rule is frozen;
- detector-to-obligation mapping is frozen;
- all kill gates and metrics are written down.

Any change after test inspection creates a new experiment version and requires a new untouched test group.

## 6. Required result artifacts

For every empirical run, retain:

```text
artifacts/runs/<run_id>/
  manifest.json
  scenario_index.jsonl
  trajectories.jsonl
  interventions.jsonl
  predicate_rows.jsonl
  model.json
  calibration_metrics.json
  test_metrics.json
  receipts.jsonl
  policy_diff.json
  stdout.log
  stderr.log
```

Large hidden-state tensors may be stored separately, but their path, shape, dtype, and SHA-256 must be in the manifest.

## 7. Reproduction commands for this archive

From the repository root:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev,ml,smt]'
make reproduce
```

The `smt` extra is optional for the current bounded tests. When it is unavailable, the test report must record the Z3 test as skipped rather than claiming SMT validation ran.

`make reproduce` regenerates the demo, deterministic smoke experiment, schemas, examples, receipt replay, compile/test/coverage reports, and a manifest covering source plus pre-manifest artifacts. It immediately verifies the declared hashes. A later extracted bundle can be checked with `make verify-bundle`. Neither command calls an LLM, so neither can produce an empirical LLM-security result.

The manifest now recursively includes preserved `model_smoke`, `pilot_runs` and
`intervention_runs` files. Verify a saved intervention run with
`PYTHONPATH=src python scripts/verify_intervention_run.py RUN_DIR`; this independently
recomputes scoring and formal decisions from recorded evidence without inference.
The run-local `source_snapshot/` is authoritative for the code used by that run;
the current working tree may contain later validators or documentation changes.

## 8. Reporting discipline

Every table must report:

- exact denominator and confidence interval;
- attack/source/transformation strata, not only a pooled mean;
- unauthorized execution, protected-effect preservation, normal completion, over-escalation, continuation after intervention, and latency/cost;
- missing outputs, invalid tool calls, and no-action outcomes as explicit categories;
- all frozen exclusions and failed groups.

Do not select a threshold per model, attack, source, or case unless the deployment claim explicitly includes that tuning procedure.
