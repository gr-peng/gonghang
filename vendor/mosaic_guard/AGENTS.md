# Instructions for coding agents

## Mission

Build a usable safety-focused AI system for the competition, maintaining the
deterministic, obligation-monotone safety control plane. Product engineering is
the primary task per Infty's 2026-09-04 direction. Record incidental research
findings, but do not make product delivery depend on novelty, probe training or
causal-identification experiments.

Scope clarification from Infty: this team owns the safety module, integration
contracts and safety evidence, NOT the customer-service interface. Do not build
chat pages, confirmation UI, dashboards or a full banking product here. Supply
typed review/execution/outcome interfaces to the team implementing the assistant.

## Read first

1. `docs/23_COMPETITION_ENGINEERING_PLAN_ZH.md`
2. `docs/SOURCE_PROBLEM_BRIEF.md` (fixed safety contract; research framing is historical)
3. `docs/02_FORMAL_MODEL.md`
4. `docs/00_EXECUTIVE_DECISION.md`
5. `docs/08_CODEX_HANDOFF.md`
6. `docs/10_CLAIM_LEDGER.md`

Read `docs/06_NOVELTY_AUDIT.md` when working on research claims, not as a product gate.

## Hard constraints

- Never add a code path where a learned score, LLM output, RAG text, memory text, or other-agent message grants permission or discharges an obligation.
- Preserve `final_level >= base_level`.
- Unknown/malformed/missing security inputs fail closed.
- Do not trust model self-reported provenance.
- Do not use `eval`, arbitrary Python expressions, or model calls in the policy kernel.
- Every write re-evaluates current complete parameters and fresh trusted facts.
- Tokens must remain bound to the complete action/security envelope, actor, session, issuer, and expiry.
- Keep formal policy evidence separate from internal causal evidence.
- Do not replace a failed method with prompt retries, fallback models, per-case rules, or all-deny behavior and count it as success.
- Never write unexecuted numbers into result files or prose.
- Keep synthetic outputs explicitly labelled as non-empirical.

## Engineering protocol

Before modifying code:

1. identify the invariant touched;
2. add or update a failing regression test;
3. implement the smallest change;
4. run all tests;
5. run demo, synthetic harness, schema export, and receipt replay;
6. update the claim ledger and docs if semantics changed.

Required command:

```bash
make reproduce
```

For a fast edit loop, `pytest -q` is acceptable, but a handoff is incomplete until the full reproduction command passes.

For security-sensitive changes, also create an adversarial test.

## Style

- Python 3.11+ with type hints.
- Pydantic models use `extra="forbid"`.
- Core dependencies remain minimal.
- Raise explicitly on unsupported semantics; never silently approximate.
- Prefer small deterministic functions over framework magic.
- Use integer minor units or `Decimal` when repairing money types.
- Store exact versions, hashes, seeds, and manifests for experiments.

## Research protocol

- Split by base-scenario group, never random rows.
- Counterfactual siblings stay in one split.
- Causal labels come from controlled interventions, not attack labels.
- Include authorization-equivalent benign pairs.
- Compare against strong explicit provenance and generic IPI probes.
- Freeze thresholds before test.
- Report protected authorized effect, availability, continuation, coverage, and cost along with attack outcomes.
- Stop and report a negative result when kill gates fail.
