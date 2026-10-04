# Executive decision: competition engineering first

## Current direction, 2026-09-04

Infty clarified that the primary task is a usable safety-focused AI competition
system. Research value is incidental and should be recorded without blocking
engineering. Prioritize task completion, clarification, safe confirmation and
execution, failure handling, replay and demonstration. The implementation plan is
`23_COMPETITION_ENGINEERING_PLAN_ZH.md`; the research analysis below is background,
not a prerequisite for product work. The fixed safety contract remains unchanged.

## Bottom line

The original direction—“combine a neural source-risk signal with symbolic rules for LLM-agent safety”—is sensible as a competition product, but too broad as a research claim. By September 2026, close work already covers:

- structural control/data-flow isolation;
- runtime rule DSLs;
- symbolic privilege control and SMT-checked monotonic policy updates;
- action-level causal attribution through shadow replay;
- argument-level provenance and semantic-role contracts;
- flow/path policies with field labels;
- offline neural/causal taint tracking;
- hidden-state probes for indirect prompt-injection exposure.

Therefore the repository makes a deliberate split.

### Product track

Build a deterministic tool gateway that is useful even if every learning module fails:

1. the agent emits a structured action only;
2. trusted services issue signed current facts;
3. a restricted policy kernel decides eligibility and base obligations;
4. confirmation/MFA tokens are bound to the exact action;
5. all learned modules can only increase risk;
6. every write is re-decided immediately before execution;
7. every decision produces a replayable dual receipt.

This is already a coherent competition demo and deployable architecture pattern.

### Research track

Ask one narrower scientific question:

> After summaries, paraphrases, multi-turn planning, and source mixing have made explicit provenance incomplete, does the model contain a transferable **source-to-action-field causal signal** that predicts which source actually changes a protected field? Can that signal distinguish unauthorized control from legitimate delegation?

The paper is justified only if the answer is supported under controlled interventions and held-out conditions. A negative answer can also be meaningful if it demonstrates a systematic failure of the common “influence implies attack” premise.

## Proposed paper object

The strongest possible paper is not “banking agent + logic.” It is a mechanism paper with a banking transfer system as the high-consequence testbed:

1. **Construct:** source-specific, field-specific causal support after provenance laundering.
2. **Safety composition:** uncertain evidence is compiled only into stricter obligations.
3. **Critical audit:** authorization-equivalent pairs test whether the signal confuses legitimate influence with authority.
4. **End-to-end evidence:** the exact action, trusted facts, policy proof trace, detector version, and execution are replayable without calling internal attribution a formal proof.

## Stop/go decision

Proceed to a real open-model experiment only after the software invariants pass. They now do in the included test suite.

Continue toward a paper only if, relative to a strong explicit-provenance/argument-contract baseline:

- held-out washed-source escalation improves materially;
- benign delegated workflows remain usable;
- the gain survives source identity swaps, position swaps, paraphrases, turn-depth shifts, and source-combination shifts;
- a small online predicate approximates the expensive causal label without per-case direction, dose, threshold, or rule tuning;
- the signal adds information beyond a generic IPI-exposure classifier;
- no measured permission expansion occurs.

Otherwise keep the deterministic kernel as the product contribution and report the mechanism failure honestly rather than patching examples.

## 2026-09-04 implementation update

The in-process authenticated-lineage layer and a Qwen3.8-27B integration path
now exist. This exposed a sharper design issue before dataset scaling: missing
protected-field provenance already maps to `deny`, while known non-delegated
target/amount provenance and the current causal guard both map to `red`.
Consequently, detector hits may leave the final decision unchanged. The next
pilot must measure changes in final obligations or dangerous proposals, not
internal detector accuracy alone.

That one-group pilot has now been run. With source kind/trust labels visible,
all seven variants retained the authorized recipient. With labels hidden from
the model but retained by the runtime, the before-position direct RAG variant
and an unbound opaque RAG variant changed the recipient. Strong provenance
already mapped them to MFA and deny respectively. Therefore the next justified
step is a small multi-intervention behavior-control experiment, not immediate
100--300-group scaling and not a claim that the current detector adds safety.
See `19_MONOTONE_PROVENANCE_TENSION_ZH.md` and
`20_QWEN_SOURCE_CONFLICT_PILOT_ZH.md`.

The follow-up six-group, 288-planner-call experiment is complete and independently
audited. Full metadata no longer preserved every baseline proposal: direct and
summary conditions preserved 4/6 and 5/6 respectively, compared with 0/6 and 4/6
under opaque metadata. Legal-service controls preserved their references, but
same-seed outcomes matched in only 35/36 pairs. Fifteen invalid outputs and nine
empty-recipient proposals occurred after necessary service fields were removed.
The existing policy escalated or denied all recorded reference-mismatched actions;
no learned module or bank execution was involved. Prioritize a typed missing-field
continuation contract and a new, frozen repeat-control study before probe training.
See `22_MULTI_INTERVENTION_RESULTS_ZH.md` for denominators and limitations.
