# Verification and test matrix

## 1. Current automated scope

| Invariant or failure mode | Primary test file | Current status |
|---|---|---|
| lattice join algebra and obligation monotonicity | `test_lattice.py` | automated |
| detector cannot lower hard-policy risk | `test_controller.py` | automated |
| detector may only add obligations | `test_controller.py` | automated |
| unsigned/untrusted facts cannot authorize | `test_controller.py`, `test_trust_tokens.py` | automated |
| future/expired facts and tokens rejected | `test_trust_tokens.py` | automated |
| fact selection independent of caller ordering | `test_trust_tokens.py` | automated |
| tokens bind parameters, actor, session, sources, and field provenance | `test_trust_tokens.py` | automated |
| fresh-state re-decision prevents stale execution | `test_controller.py` | automated |
| default deny, unique rule IDs, and unknown action/rule targets | `test_policy.py` | automated |
| duplicate YAML keys and invalid reference syntax rejected | `test_policy.py` | automated |
| missing fact remains unknown under negation | `test_policy.py` | automated |
| malformed DSL and pseudo-fact defaults rejected | `test_policy.py` | automated |
| direct provenance contract catches protected-field control | `test_policy.py` | deterministic synthetic |
| explicit provenance does not invent laundered lineage | `test_policy.py` | deterministic synthetic |
| source/action envelope is structurally unambiguous | `test_policy.py` | automated |
| transfer money uses bounded integer minor units with exact boundary, overflow, and digest behavior | `test_money.py`, `test_schemas.py` | automated |
| direct and laundered source-field replay path | `test_causal.py` | deterministic synthetic |
| benign delegated influence is not authority violation | `test_causal.py` | deterministic synthetic |
| model cannot self-report trusted provenance | `test_llm_adapter.py` | automated |
| model JSON rejects duplicate keys and non-finite numbers | `test_llm_adapter.py` | automated |
| missing required fields produce a code-owned clarification without calling facts, authorization or tools | `test_planning.py` | automated |
| model cannot set write flags, ask sensitive-field questions or inject confirmation/provenance into v2 planning | `test_planning.py` | adversarial automated |
| completed follow-up receives fresh field provenance and still requires an exact-action confirmation token | `test_planning.py` | scripted mock-bank end-to-end |
| signed lineage preserves transformation ancestry and prevents trust escalation | `test_lineage.py` | automated |
| forged source IDs, parent edges, timestamps, and message content fail verification | `test_lineage.py` | adversarial automated |
| authenticated-lineage decisions replay only with the correct authority | `test_lineage.py` | automated |
| OpenAI-compatible requests record model/runtime revisions, exact prompts, outputs, seeds, and costs | `test_openai_compatible_adapter.py` | automated with fake endpoint |
| source spans align to an exact server-tokenized prompt before activation capture | `test_openai_compatible_adapter.py`, `test_activations.py` | automated |
| grouped split leakage rejected | `test_dataset_training.py` | automated |
| base groups and wording families stay within one split | `test_task_suite.py` | automated |
| source edits regenerate descendants, preserve multi-parent union, and reissue signed IDs | `test_interventions.py` | automated |
| hidden ancestors are retained for lineage but not rendered to the planner | `test_interventions.py` | automated |
| forged content, missing runners, unknown IDs, and ID collisions fail explicitly | `test_interventions.py` | adversarial automated |
| no-action, errors, field replacement, and authorized reference changes are distinct | `test_controlled_replay.py` | automated |
| multi-intervention evidence and formal receipts survive independent replay audit | `test_intervention_runner.py` | fake endpoint end-to-end test |
| saved real-model subdirectories are included in bundle integrity hashes | `test_reproducibility.py` | automated |
| thresholded linear predicate pipeline | `test_dataset_training.py` | synthetic rows only |
| policy snapshot/hash deterministic | `test_canonical.py` | automated |
| receipt tampering detected | `test_receipts.py` | automated |
| formal/action/token/fact/execution/internal receipt consistency | `test_receipts.py` | automated |
| deterministic decision replay | `test_receipts.py` | automated, conditional on detector outputs |
| bounded policy update expansion detected | `test_policy_diff.py` | automated |
| Z3 search preserves missing-fact semantics and integer minor-unit domains | `test_policy_diff.py` | runs only with `smt` extra |
| schemas export | `test_schemas.py` | automated |
| delivered manifest detects mutation/path traversal and excludes editable-install metadata | `test_reproducibility.py` | automated |
| legal execution-state transitions | `test_state_machine.py` | automated |

## 2. Commands

```bash
make test
make reproduce
```

A direct coverage run is:

```bash
coverage erase
PYTHONPATH=src coverage run -m pytest -q
coverage report -m
```

Coverage is an engineering diagnostic, not evidence of security completeness.

## 3. Required adversarial regression pattern

Every security bug fix should include:

1. the smallest action/policy/fact/token construction that violates an invariant;
2. an assertion showing the unsafe behavior before the fix;
3. the implementation repair;
4. a regression test that does not depend on timing or random test order;
5. an update to the claim ledger when semantics change.

## 4. Important untested properties

The current suite does not establish:

- real LLM source-to-field causal identification;
- authenticated source lineage across processes and durable producer registries;
- distributed atomicity between authorization and tool commit;
- key rotation, revocation, one-time tokens, or HSM behavior;
- receipt-head anchoring against full-history rewrite;
- universal non-expansion for all DSL expressions;
- concurrency safety;
- production availability under detector/service failures;
- adaptive-attack robustness;
- correctness of human-authored base policy.

These are not hidden test debt; they are explicit boundaries on the claims.
