# Novelty audit as of 2026-09-03

## Verdict

The broad proposal “use neural/causal signals plus symbolic rules to secure LLM agents” is **not sufficiently novel**. The collision surface is now dense. The repository should be presented as:

- a solid competition/product prototype;
- a research instrument for one unresolved mechanism question;
- not a finished new method paper.

The strongest remaining paper hypothesis is:

> An internal source-specific, field-specific causal predicate can recover non-delegated control after explicit provenance laundering and adds information beyond generic IPI exposure, action-level shadow replay, and argument-level provenance—while authorization-equivalent benign influence remains usable.

Even this is conditional and must be demonstrated, not assumed.

## Collision matrix

| Work | What it already establishes | Collision with this project | What remains |
|---|---|---|---|
| **DeepProbLog** (arXiv:1805.10872) | neural predicates inside probabilistic logic programs | “neural predicate + logic” is not new | safety-specific non-expansion and causal construct may differ |
| **CaMeL / Defeating Prompt Injections by Design** (arXiv:2503.18813) | separates trusted control from untrusted data and enforces capabilities/data flow | structural safety layer, provenance, tool mediation | internal signal for cases where lineage/control inference is incomplete |
| **AgentSpec** (arXiv:2503.18666, ICSE 2026) | lightweight runtime safety-rule DSL | runtime rules and interpretable enforcement are not new | this DSL is only infrastructure |
| **Progent** (arXiv:2504.11703) | symbolic privilege policies, deterministic tool checks, SMT-classified narrowing/expansion updates | privilege control and non-silent expansion are directly covered | our lattice proof is useful but not a standalone novelty claim |
| **FormalJudge** (arXiv:2602.11136) | neuro-symbolic oversight, LLM-to-formal compilation, Dafny/Z3 verification | “formal neuro-symbolic agent safety” is not new | this project does not currently solve trustworthy NL-to-rule compilation |
| **AttriGuard** (arXiv:2603.10749) | frames IPI defense as action-level causal attribution using counterfactual shadow replay | direct collision with source/action causal attribution | source-to-field localization, delegation-aware interpretation, and internal surrogate after laundering must add real value |
| **Ghost in the Agent / NeuroTaint** (arXiv:2604.23374) | neural/causal source–sink taint tracking and offline auditing | source influence/taint inference is not empty territory | exact protected-field authorization integration and online deployability need distinction |
| **PACT / Granularity Mismatch** (arXiv:2605.11039) | argument-level provenance interpreted through semantic roles; identifies provenance inference and contract synthesis as bottlenecks | direct collision with field contracts and delegation masks | internal signal may be studied specifically as a provenance-inference aid |
| **Hidden-state IPI exposure probes** (arXiv:2608.02657) | simple probes can predict IPI exposure from internal states and gate reasoning | “LLM hidden states contain IPI signals” is not new | source identity, protected field, causal effect, and authority separation are stricter targets |
| **AgentFlow** (arXiv:2608.22868) | flow/path policy language, multi-dimensional labels, runtime monitor, bounded SMT verifier | field labels, trust flow, policy language, verifier are covered | learned recovery from lost lineage may remain |
| **Influence Is Not Authority** (arXiv:2608.29942) | authorization-equivalent audits show influence signals can shift on harmless source relocation and need not encode authority | directly attacks a naive causal-guard premise | our design must treat this as the main falsification test, not an afterthought |

## Consequences for claim language

Do not claim:

- first neuro-symbolic LLM-agent safety system;
- first deterministic rule layer for agents;
- first use of SMT to prevent privilege expansion;
- first causal attribution defense;
- first argument-level or field-level provenance system;
- first hidden-state IPI detector;
- first source/taint tracking method;
- formal verification of real-world safety merely because a solver checks rules.

Potentially defensible claims, only after evidence:

1. **Source-field causal construct after provenance laundering.** The target is not generic attack exposure but which source changes which protected field.
2. **Counterfactual-effect supervision.** A deployable internal predicate is trained on grouped, intervention-derived source-field effects rather than attack labels.
3. **Delegation-aware causal interpretation.** Influence is converted to risk only when the hard policy says the source lacks authority for that field.
4. **Incremental value over argument-level provenance.** The signal helps exactly where a strong provenance resolver is incomplete, under held-out transformations.
5. **Obligation-only composition.** The uncertain predicate is structurally unable to create permission, with executable regression tests and version-diff gates.
6. **Authorization-equivalence as a first-class benchmark.** The paper evaluates protected effect, availability, continuation, and later inspection—not just attack success or task success.

Claims 3, 5, and 6 are partly architectural/evaluative and may not be enough alone. Claim 1 or 2 needs a strong empirical mechanism result.

## Strongest scientific framing

A useful title-level question is:

> **Do LLMs retain source-to-field causal information after provenance laundering, and does that information separate unauthorized control from legitimate delegation?**

This framing fits a knowledge-discovery strategy better than “our system gets a better benchmark score.” It can yield three honest outcomes:

### Positive mechanism

A fixed low-complexity internal predicate transfers across wording, source combinations, transformations, and models, adds to explicit provenance, and passes authorization-equivalence controls.

### Boundary result

The signal exists only in certain layers/models/source transformations or only for action type but not arguments. This can still establish a useful map of where internal provenance is preserved or lost.

### Negative result

The signal tracks generic conflict/exposure or required influence but cannot separate authority. A rigorous negative result, paired with a safe deterministic control plane, may be more valuable than forcing a weak algorithmic claim.

## Required novelty experiment

The decisive comparison is not against a weak text classifier. It is:

```text
strong argument-level provenance resolver + delegation contracts
versus
same system + internal source-field predicate
```

under:

- held-out source laundering transformations;
- authorization-equivalent source relocation;
- identical protected action/effect;
- matched detector budget;
- fixed thresholds;
- held-out source combinations and turn depth.

The research hypothesis fails if the internal signal has no stable incremental value or if its gain is purchased by broadly escalating legitimate delegated workflows.

## Product-versus-paper conclusion

| Dimension | Current status |
|---|---|
| competition demo | strong enough to continue |
| deterministic safety kernel | implemented prototype |
| deployable production system | not yet; trust, money, anchoring, and integration work remain |
| field-causal algorithm | baseline implementation only |
| real LLM evidence | absent |
| novelty | plausible narrow gap, not established |
| paper claim | premature |
