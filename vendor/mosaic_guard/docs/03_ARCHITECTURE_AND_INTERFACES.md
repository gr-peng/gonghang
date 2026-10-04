# Architecture and interfaces

## 1. Runtime topology

```text
Untrusted sources
user / RAG / tool text / memory / other agents
               │
               ▼
Source registry + trace builder
(runtime-owned source IDs, kind, trust, lineage)
               │
               ▼
LLM agent
understands language; emits only {action_type, params, write_action}
               │
               ├──────────────► causal audit / learned source predicate
               │                    advisory, obligation-only
               ▼
Provenance resolver
runtime metadata; never trusts model self-attribution
               │
               ▼
Structured ActionProposal
               │
               ▼
MOSAIC SafetyController  ◄── signed facts / bound tokens
               │
               ├── restricted hard-policy evaluator
               ├── argument contract check
               ├── risk-lattice join
               ├── state-machine transition check
               └── dual receipt builder
               │
               ▼
allow / confirmation / MFA / deny
               │
               ▼
Tool mediator → bank ledger or other side-effecting backend
```

## 2. Component responsibilities

| Component | File | Responsibility | May authorize? |
|---|---|---|---:|
| action schema | `models.py` | exact typed action, source, binding, fact, token, decision, receipt records | no |
| source/LLM adapter | `llm_adapter.py` | call local model and parse strict JSON; provenance fails closed | no |
| lineage authority | `lineage.py` | issue/verify source records, parent closure, content hashes, and exact explicit provenance | contributes trusted provenance |
| model runtime adapter | `adapters/openai_compatible.py` | one-shot SGLang/vLLM request plus exact prompt/output/seed/token-span trace | no |
| activation capture | `activations.py`, `scripts/extract_prompt_activations.py` | require exact prompt-token replay before extracting per-layer source features | no |
| hard policy | `policy.py` | finite trusted-fact rules and argument contracts | yes |
| fact authority | `trust.py` | sign/verify trusted facts in prototype | contributes trusted facts |
| token authority | `tokens.py` | issue/verify exact-action confirmation and MFA tokens | discharges obligations |
| causal attributor | `causal.py` | paired shadow replay, source-to-field effect report | no |
| learned predicate | `predicates.py` | consume hidden-state/source features and emit risk | no |
| safety controller | `controller.py` | sole decision point; join and execution-time re-check | **yes, uniquely** |
| state machine | `state_machine.py` | forbid illegal execution transitions | yes, structurally |
| receipt ledger | `receipts.py` | append-only local hash chain | no |
| receipt replay | `replay.py` | reproduce formal decision conditional on detector records | no |
| policy diff | `policy_diff.py` | detect version-to-version de-escalation | deployment gate |
| bank simulator | `simulator.py` | toy facts and side effects | backend only |

## 3. Structured action contract

An `ActionProposal` contains:

```json
{
  "session_id": "...",
  "actor_id": "...",
  "action_type": "transfer",
  "params": {
    "from_account": "acct-user",
    "to_account": "acct-alice",
    "amount_minor": 150000,
    "memo": "rent"
  },
  "sources": ["runtime-owned SourceRef objects"],
  "argument_bindings": ["runtime-owned field provenance"],
  "write_action": true
}
```

The LLM adapter accepts only `action_type`, `params`, and `write_action` from model JSON. It rejects invalid JSON, duplicate object keys, non-finite numeric constants, and extra fields. A model-provided `argument_bindings` field is therefore rejected. The default provenance resolver marks every field incomplete. The optional authenticated resolver accepts only exact field/value bindings backed by signed runtime source records whose content and complete parent closure verify; paraphrase recovery remains a learned/audit problem.

## 4. Trusted fact schema

A `TrustedFact` has:

- predicate and subject;
- typed value;
- issuer;
- issuance and optional expiry time;
- trust marker;
- signature;
- optional metadata.

The toy bank emits facts such as:

```text
authenticated(user-1) = true
owned_accounts(user-1) = [acct-user]
account_active(acct-user) = true
account_balance_minor(acct-user) = 500000
recipient_allowed(acct-alice) = true
daily_remaining_minor(user-1) = 1000000
```

Natural-language claims are not converted into these facts. A production system would retrieve them from authenticated services over a mutually authenticated channel and verify asymmetric signatures or service identity.

## 5. Policy DSL

The YAML policy uses:

- one action contract per action type;
- one field contract per parameter;
- non-recursive rules with a single effect level;
- three-valued fail-closed trusted-fact semantics and default deny;
- a small condition language: `all`, `any`, `not`, `equals`, `compare`, `contains`, `in`, `exists`, `fact_equals`, `fact_contains`, and `fact_not_contains`.

No Python `eval`, arbitrary callbacks, recursion, dynamic code loading, or LLM judgment occurs in the policy kernel.

Example:

```yaml
- rule_id: transfer-small
  action: transfer
  effect: yellow
  when:
    all:
      - fact_equals:
          predicate: authenticated
          subject: $actor
          value: true
      - fact_contains:
          predicate: owned_accounts
          subject: $actor
          item: $params.from_account
      - compare:
          left: $params.amount_minor
          op: "<="
          right: 100000
```

## 6. Argument/delegation contract

Each field contract declares:

- semantic role such as `target`, `amount`, `identifier`, or `content`;
- whether the field is required;
- whether provenance must be complete;
- minimum source trust;
- permitted source kinds;
- risk on missing or invalid provenance;
- whether field-causal influence is security-sensitive.

For the transfer prototype:

- source account, recipient, and amount must originate from the user or a trusted service;
- a RAG source may supply memo content;
- an external source controlling recipient or amount deterministically raises the base risk to `red`;
- the causal guard asks the same role-aware question when explicit provenance is incomplete.

## 7. Decision sequence

`SafetyController.execute()` performs:

1. deep-copy the complete action;
2. call a fresh fact supplier;
3. verify fact signatures and expiry;
4. when configured, verify source-lineage signatures, timestamps, content binding, and parent closure;
5. evaluate the hard policy and argument contracts;
6. run or ingest detector signals;
7. compute the risk join;
8. verify exact-action obligation tokens;
9. transition to `AUTHORIZED`, `OBLIGATION_PENDING`, or `DENIED`;
10. execute only on `ALLOW`;
11. snapshot before/after tool state;
12. seal one dual-layer receipt in the hash chain.

No result from an earlier conversational turn is inherited as authorization.

## 8. Receipt schema

The formal layer contains:

- complete action;
- verified trusted facts;
- verified bound tokens;
- complete policy snapshot and hash;
- rule trace, contract violations, and final decision;
- optional tool execution with before/after state.

The internal layer contains:

- causal/probe/classifier signals;
- source/field evidence;
- detector IDs, versions, scores, and timestamps;
- the fixed note that internal evidence is risk evidence, not authorization proof.

Receipt replay reproduces the deterministic policy decision using the recorded detector outputs. Recomputing a detector requires a separate reproducibility bundle containing exact model weights, code, seeds, prompts, and environment.

## 9. Production substitutions

| Prototype | Production replacement |
|---|---|
| HMAC fact/token signing | authenticated service identity, asymmetric signatures, HSM/KMS |
| Python process | isolated reference monitor with least privilege |
| integer minor units without currency metadata | currency-aware checked money type aligned with the backend ledger |
| local JSONL hash chain | WORM/append-only store with external head anchoring |
| in-process HMAC lineage records | cross-process authenticated data-flow service with asymmetric signatures, rotation, and revocation |
| Python Horn-style evaluator | audited policy engine or compiled verified fragment |
| toy source ablation | model-specific paired intervention pipeline |
| OpenAI-compatible SGLang/vLLM adapter | production runtime identity, full weight fingerprint, availability controls, and activation-capable inference path |
