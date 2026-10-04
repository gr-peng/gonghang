# Conditional paper blueprint

## 1. Working title

**Influence Without Authority: Source-to-Field Causal Predicates for Monotone LLM-Agent Safety**

Do not use this as a novelty claim until the direct-collision experiment passes.

## 2. One-sentence scientific question

When external provenance is lost through summarization, paraphrase, source mixing, or multi-turn planning, can an LLM's internal state recover which source causally controls a protected action field while remaining invariant to benign, user-authorized delegation?

## 3. Core mathematical object

For source \(s_j\), action \(a\), and field \(f\), define an intervention family \(\mathcal I_j\) and effect

\[
I_{j,f}
=
\mathbb E_{\omega,\iota\sim\mathcal I_j}
\left[d_f\bigl(A(X;\omega),A(\iota(X);\omega)\bigr)\right].
\]

The online predicate estimates whether \(I_{j,f}\) exceeds a frozen threshold. The policy separately defines a delegation indicator \(D_P(s_j,a,f)\). Only

\[
U_{j,f}=\widehat I_{j,f}\,[1-D_P(s_j,a,f)]
\]

may generate an escalation signal. It never generates permission.

## 4. Conditional contributions

A submission may claim the following only after the corresponding evidence exists:

1. **Construct:** a source-specific, field-specific causal label that survives explicit-provenance laundering.
2. **Falsification protocol:** authorization-equivalent relocation, source/position swaps, targeted field interventions, and negative controls distinguish influence from authority and lexical shortcuts.
3. **Online surrogate:** a fixed simple predicate predicts the expensive effect on held-out source/transformation groups and adds information beyond explicit provenance and generic IPI-exposure probes.
4. **Safe composition:** a monotone obligation lattice guarantees that learned evidence cannot expand the base executable set under fixed policy, trusted facts, and tokens.
5. **Evidence architecture:** dual receipts separate conditional formal authorization from internal causal evidence and preserve exact replay inputs.

Items 1–3 are empirical hypotheses. Item 4 is a conditional proposition. Item 5 is an implemented systems property, not by itself a research novelty.

## 5. Decisive experiments

### Experiment A — provenance gap

Compare:

- hard policy only;
- hard policy + strong argument-level provenance/contracts;
- the same system + generic IPI-exposure probe;
- the same system + source-to-field predicate;
- expensive causal replay oracle.

Primary endpoint: correct escalation on held-out laundered protected-field control at matched benign over-escalation.

### Experiment B — influence versus authority

Use authorization-equivalent pairs where the exact committed action and protected effect are fixed, while a required value moves between the user and a legitimate tool. The main method must remain below its escalation threshold on delegated influence while detecting matched unauthorized control.

### Experiment C — causal and shortcut audit

Hold out and perturb:

- source identity;
- source position;
- lexical template;
- summarizer/paraphraser;
- turn depth;
- source combination;
- tool schema;
- model family, if feasible.

Include text/source/position-only controls and within-group label permutations.

### Experiment D — end-to-end safety and utility

Measure unauthorized execution, exact authorized effect, task completion, over-escalation, extra confirmation/MFA, continuation after a block, latency, tokens, and model calls. A method that succeeds by broad escalation fails.

## 6. Minimum figures and tables

1. Architecture: untrusted agent proposal → trusted policy → monotone detector join → obligations → mediator → dual receipt.
2. Construct figure: source × field influence matrix with delegation mask.
3. Authorization-equivalence figure: benign relocation and unauthorized control score distributions.
4. Held-out results table stratified by laundering transformation and source family.
5. Safety–availability frontier at frozen candidate thresholds; highlight the calibration-selected point only.
6. Shortcut/ablation table.
7. Cost and replay-coverage table.
8. Claim-to-artifact appendix.

## 7. Result interpretations

### Strong positive

The predicate adds stable held-out washed-source detection beyond strong provenance and generic exposure probes, localizes the correct protected field, and preserves delegated utility.

### Strong boundary result

Internal representations encode exposure or influence but not authorization. The paper identifies the exact delegation/source conditions under which causal guardrails become unreliable and shows why a separate authority model is necessary.

### Strong negative

After grouped and authorization-equivalent controls, no transferable source-to-field signal remains. This can still be publishable if the failed premise is widely used and the evaluation artifact is rigorous and reusable.

### Weak result

A classifier improves average attack accuracy on random splits or one benchmark while relying on source identity/keywords and increasing denial. Do not submit this as the main paper.

## 8. Recommended paper structure

1. Problem: provenance laundering creates an inference gap, but influence is not authority.
2. Threat model and authority/delegation semantics.
3. Source-to-field construct and intervention family.
4. Monotone obligation composition and conditional proposition.
5. Dataset and authorization-equivalence protocol.
6. Predicate and baselines.
7. Mechanism, safety, utility, and cost results.
8. Failures, claim boundaries, and related-work collision matrix.
9. Reproducibility and receipts.

## 9. Venue logic

The venue should be chosen after the result is known. A mechanism/evaluation result fits security or trustworthy-ML venues better than a generic FinTech framing. Banking is the consequential testbed and future deployment target, not sufficient novelty by itself.
