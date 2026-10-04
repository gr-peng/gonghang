# Research strategy note

## Why this project can fit the revised strategy

A weak version of this project would be another benchmark-defense paper: add a classifier, evaluate on several datasets, report lower attack success, and leave readers unable to determine what the model learned or whether the comparison is reproducible.

The stronger version has a different center:

> discover whether source-to-action-field causal information survives provenance laundering inside an LLM, identify when it is confused with legitimate delegation, and build a control plane whose safety does not depend on the learned answer being correct.

This combines three forms of value:

1. **Knowledge discovery:** a mechanistic statement about internal source information and its limits.
2. **A falsifiable correction to common intuition:** influence is not automatically authority; generic exposure is not field-specific control.
3. **Deployable artifact:** a deterministic action gateway, trusted-token protocol, version diff, and replayable receipts useful to other domains.

## Recommended paper shape

Avoid a broad “we beat many baselines on many datasets” narrative. Use a narrow claim chain:

1. **Phenomenon:** explicit provenance degrades under controlled source transformations.
2. **Mechanism question:** does an internal source-field signal survive?
3. **Critical confound:** authorized external information also influences actions.
4. **Method:** intervention-derived field labels plus a fixed deployable predicate.
5. **Safety theorem:** predicate output cannot create permission.
6. **Decisive experiment:** incremental value over strong provenance at matched authorized availability.
7. **Boundary map:** where the signal succeeds or fails across transformations/models.
8. **Artifact:** exact-action tool gateway and dual receipts.

One model family and one carefully controlled task may be more convincing than ten heterogeneous datasets if the intervention, split, and replay protocol is strong.

## What counts as a meaningful result

### Strong positive

A simple frozen predicate predicts held-out source-field effects after summary/multi-turn laundering, adds to a strong provenance resolver, and survives authorization-equivalence audits.

### Strong boundary result

The signal exists for action type but not argument values, or survives direct paraphrase but not agent-to-agent summary, with a reproducible explanation tied to model architecture/readout.

### Strong negative

Existing action-level/hidden-state causal signals systematically confuse required authorized influence with malicious control even after reasonable reference construction. A rigorous negative map plus the safe composition architecture can challenge an assumption in prior defenses.

### Weak result to avoid

- small average attack-rate improvement;
- gains from more model calls or a stronger judge;
- a threshold tuned separately on every benchmark;
- blanket escalation that lowers utility;
- manually curated examples;
- a policy that embeds the test answers;
- a complicated probe that cannot be deployed or interpreted;
- claims based only on the deterministic toy harness.

## Relationship to financial knowledge discovery

The same research style can later transfer to the advisor's financial examples:

- identify a widely used rule/formula and its hidden failure condition;
- construct controlled counterfactuals or natural experiments;
- ask an LLM to assist hypothesis generation or extraction, not to certify truth;
- use symbolic constraints to make the final claim auditable;
- deploy a system that lets domain researchers inspect and test the discovered rule.

For the current safety competition, the analogous “rule people may be wrong about” is:

> A source that causally influences a tool action is necessarily the source that holds authority over it.

The project should test and refine that statement rather than merely wrapping a classifier in logic.

## Immediate research action

The next decisive step is one grouped open-model pilot with three protected fields, two laundering transformations, and authorization-equivalent source relocation. Do not scale the benchmark before this pilot determines whether the internal construct exists.
