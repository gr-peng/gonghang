# Limitations and kill gates

## Implemented limitations

### Money representation

The toy bank now uses checked signed-64-bit integer minor units for transfers,
balances, limits, trusted facts, and policy thresholds. It still does not model
currency identity, currency exponents, FX conversion, jurisdiction-specific
rounding, or reconciliation with a production backend ledger.

### Cryptography and trust

HMAC signing demonstrates the trust boundary but does not implement key rotation, HSM storage, asymmetric verification, issuer revocation, service identity, or cross-process authentication.

### Receipt anchoring

A local hash chain detects modification relative to a trusted head. It does not prevent an attacker with full storage control from rewriting the entire chain. Anchor heads externally.

### Atomicity

The controller re-fetches facts immediately before calling a tool, but the fact read and backend commit are not one atomic transaction. A real backend needs idempotency, optimistic concurrency/version checks, or a transactional authorization API.

### Policy language

The DSL is finite and auditable, but it is a prototype Python evaluator, not a formally verified compiler. The optional Z3 search covers only a numeric comparison fragment.

### Provenance

The prototype now has runtime-issued HMAC lineage records plus an exact-payload
resolver. It preserves and verifies transitive parent closure locally and fails
closed when protected-field lineage is absent or invalid. It is not a production
provenance service: cross-process component identity, asymmetric signatures,
rotation, revocation, durable registries, and semantic/paraphrase derivation are
not implemented. The default LLM resolver therefore remains fail-closed unless
the authenticated resolver is explicitly installed.

### Causal identification

Source ablation is not sufficient causal identification. The repository includes the estimator and the required validation design, not evidence that the intervention is valid on natural-language agents.

### Learned predicate

No real hidden-state model is trained. `predicates.py` is a deployment interface, not an empirical contribution.

## Product kill gates

Do not connect to a real side-effecting financial API if any holds:

- currency/exponent semantics are not bound to the action and backend ledger;
- trusted fact/token keys are embedded in application code;
- source metadata can be authored by the LLM;
- protected-field provenance can be missing without fail-closed behavior;
- backend lacks idempotency and independent authorization;
- receipt head is not anchored;
- policy update diff is skipped;
- execution can bypass the controller;
- confirmation UI does not show exact committed parameters;
- stale facts can be reused as execution authority.

## Research kill gates

Do not make the source-causal predicate the paper's main contribution if any holds:

- no incremental gain over strong provenance;
- no transfer to held-out laundering transformations;
- causal score is explained by source kind, position, context length, or attack wording;
- authorization-equivalent relocation crosses the attack threshold frequently;
- action-level generic exposure performs equally well;
- source or field localization is poor;
- performance requires per-case tuning;
- safety gain comes from broad denial/escalation;
- exact authorized effect is not preserved;
- online cost is incompatible with the deployment claim;
- results cannot be reproduced from frozen artifacts.

## Claim kill gates

Immediately remove or qualify any statement that:

- treats a solver result as proof of fact truth;
- treats internal attribution as authorization evidence;
- calls the toy metric an LLM result;
- calls a conditional invariant “complete security”;
- describes a component already covered by close work as the main novelty;
- reports an unexecuted result;
- omits availability/cost while reporting attack success;
- hides failed source families or transformations in a pooled average.
