# Implementation guide

## 1. Environment

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
pytest -q
```

Optional dependencies:

```bash
pip install -e '.[smt]'   # Z3 numeric policy-diff search
pip install -e '.[ml]'    # local probe-training experiments
```

The core runtime intentionally depends only on Pydantic and PyYAML.

## 2. Run the current prototype

```bash
python scripts/run_demo.py
python scripts/run_synthetic_experiment.py
python scripts/export_json_schemas.py
python scripts/verify_receipts.py artifacts/demo_receipts.jsonl
```

Equivalent CLI:

```bash
mosaic-guard demo
mosaic-guard experiment
mosaic-guard schemas
mosaic-guard verify-receipts artifacts/demo_receipts.jsonl
```

## 3. Create a structured action

```python
from scns_guard.models import ActionProposal, ArgumentBinding, SourceRef
from scns_guard.enums import ArgumentRole, SourceKind, SourceTrust

user = SourceRef(
    source_id="user-turn-1",
    kind=SourceKind.USER,
    trust=SourceTrust.USER,
)

action = ActionProposal(
    session_id="session-1",
    actor_id="user-1",
    action_type="transfer",
    params={
        "from_account": "acct-user",
        "to_account": "acct-alice",
        "amount_minor": 50_000,
    },
    sources=(user,),
    argument_bindings=(
        ArgumentBinding(
            field="from_account",
            source_ids=(user.source_id,),
            role=ArgumentRole.IDENTIFIER,
            observed_provenance_complete=True,
        ),
        ArgumentBinding(
            field="to_account",
            source_ids=(user.source_id,),
            role=ArgumentRole.TARGET,
            observed_provenance_complete=True,
        ),
        ArgumentBinding(
            field="amount_minor",
            source_ids=(user.source_id,),
            role=ArgumentRole.AMOUNT,
            observed_provenance_complete=True,
        ),
    ),
)
```

Runtime provenance, not the LLM, must construct `argument_bindings`.

## 4. Decide and execute

```python
from scns_guard.controller import SafetyController
from scns_guard.policy import PolicyEngine
from scns_guard.receipts import ReceiptLedger
from scns_guard.tokens import TokenAuthority
from scns_guard.trust import FactAuthority

policy = PolicyEngine.from_yaml("configs/transfer_policy.yaml")
fact_authority = FactAuthority({"bank-core": b"replace-me"})
token_authority = TokenAuthority("obligation-service", b"replace-me")

controller = SafetyController(
    policy=policy,
    fact_authority=fact_authority,
    token_authority=token_authority,
    receipt_ledger=ReceiptLedger("artifacts/receipts.jsonl"),
)

# Decide without executing.
receipt = controller.decide(action, facts=current_signed_facts)
print(receipt.formal.decision.status)

# Execute only after obtaining exact-action tokens. Facts are fetched again.
receipt = controller.execute(
    action,
    fact_supplier=get_fresh_signed_facts,
    tool=tool_adapter,
    tokens=(confirmation_token, mfa_token),
)
```

Do not pass cached facts through `fact_supplier` in a production integration.

## 5. Integrate a local LLM

`StructuredLLMAdapter` accepts a callable returning one strict JSON object:

```python
from scns_guard.llm_adapter import StructuredLLMAdapter


def generate_json(messages, seed):
    # Wrap vLLM, Transformers, SGLang, or a provider here.
    # Return: {"action_type": ..., "params": ..., "write_action": ...}
    return local_model.generate(messages, seed=seed)

adapter = StructuredLLMAdapter(
    generate_json,
    provenance_resolver=my_runtime_provenance_resolver,
)
action = adapter.propose(trace, disabled_source_ids=frozenset(), seed=0)
```

The default resolver marks all field provenance incomplete. This is intentional fail-closed behavior.

A production resolver should combine:

- typed tool outputs with field lineage;
- parser-level value origin;
- data-flow object IDs;
- explicit user selections;
- trusted service certificates;
- cross-step lineage union.

It must not accept the model's natural-language attribution as trusted metadata.

## 6. Generate causal labels

```python
from scns_guard.causal import CounterfactualSourceAttributor

attributor = CounterfactualSourceAttributor(
    adapter,
    seeds=(0, 1, 2, 3, 4),
)
report = attributor.attribute(trace, action)
for estimate in report.estimates:
    print(estimate.source_id, estimate.field, estimate.mean_effect)
```

The adapter receives `disabled_source_ids`; use this hook to construct matched shadow contexts. For real experiments, replace raw deletion with the multi-intervention protocol in `04_SOURCE_CAUSAL_METHOD.md`.

## 7. Activate a causal guard

```python
from scns_guard.causal import FieldCausalGuard

guard = FieldCausalGuard(
    attributor,
    effect_threshold=0.5,
    min_full_replay_consistency=0.9,
    audit_only=True,
)

controller = SafetyController(
    policy=policy,
    fact_authority=fact_authority,
    token_authority=token_authority,
    detectors=(guard,),
)

receipt = controller.decide(
    action,
    facts=current_signed_facts,
    detector_context={"trace": trace},
)
```

Keep `audit_only=True` until held-out calibration and kill gates pass. Changing it to `False` is a versioned security change.

## 8. Integrate a learned predicate

`predicates.py` provides a simple deployable linear model format and a detector adapter. The expected input is one feature dictionary per source-field pair:

```python
predicate_inputs = [
    {
        "source_id": "rag-17",
        "field": "to_account",
        "features": {
            "layer_42_source_projection": 1.3,
            "field_readout_alignment": 0.8,
        },
    }
]
```

The output is still checked against the policy's delegation mask and joined into risk. The learned score cannot authorize.

## 9. Extend the policy DSL

Before adding an operator:

1. define total behavior for missing and ill-typed values;
2. validate it at policy load time;
3. add deterministic evaluator tests;
4. update the bounded/SMT diff path or fail explicitly there;
5. prove it cannot execute code or call the LLM;
6. add an unsafe-policy regression case.

Do not add arbitrary Python expressions.

## 10. Add a real tool

A tool adapter implements:

```python
class ActionTool(Protocol):
    name: str
    def snapshot(self) -> dict: ...
    def execute(self, action: ActionProposal) -> dict: ...
```

The real tool should independently validate its typed contract, use idempotency keys derived from the action/decision, and reject duplicate or mismatched requests. The safety controller is a reference monitor, not a reason to remove backend authorization.

## 11. Policy-version diff

The complete current DSL can be compared over explicit test cases:

```python
from scns_guard.policy_diff import BoundedPolicyDiffer

differ = BoundedPolicyDiffer(fact_authority)
findings = differ.compare(old_policy, new_policy, cases)
assert not any(item.expansion for item in findings)
```

The optional Z3 function searches a numeric interval for `new_risk < old_risk`. It is a deliberately narrow compiler for the transfer fragment and must raise on unsupported operators rather than approximate them.

## 12. Receipt replay

```python
from scns_guard.receipts import ReceiptLedger
from scns_guard.replay import ReceiptReplayer

ledger = ReceiptLedger("artifacts/receipts.jsonl")
results = ReceiptReplayer(
    fact_authority=fact_authority,
    token_authority=token_authority,
).replay_chain(ledger.receipts)
```

Replay uses the decision timestamp for signature/expiry verification and the recorded detector outputs. It does not recompute the detector model.

## 13. Known implementation debt

Priority items:

- attach currency/exponent semantics and backend-native money validation to integer minor units;
- add authenticated source-lineage objects;
- add concurrency/idempotency/TOCTOU controls around backend commit;
- externally anchor receipt heads;
- add a full policy-to-SMT compiler or adopt an audited policy engine;
- store detector artifact hashes and environment manifests;
- add multi-turn state facts and token revocation;
- add real open-model adapters and activation capture;
- add benchmark importers without weakening the strict paired protocol.
