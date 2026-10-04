# Formal model

## 1. Risk lattice

Let

\[
\mathcal L=\{\mathsf G,\mathsf Y,\mathsf R,\mathsf D\}
\]

with

\[
\mathsf G<\mathsf Y<\mathsf R<\mathsf D.
\]

The implementation names these levels `green`, `yellow`, `red`, and `deny`. Their obligations are

\[
O(\mathsf G)=\varnothing,
\quad O(\mathsf Y)=\{\mathsf{confirm}\},
\quad O(\mathsf R)=\{\mathsf{confirm},\mathsf{MFA}\},
\]

while \(\mathsf D\) is non-executable regardless of tokens.

## 2. Hard-policy decision

Let \(F_T\) be the set of currently verified trusted facts and let \(P\) be a fixed policy version. The deterministic policy kernel returns

\[
b_P(a,F_T)\in\mathcal L.
\]

The runtime uses a finite, non-recursive Horn-style fragment with **three-valued, fail-closed** trusted-fact semantics and a default-deny policy. Conditions may be true, false, or unknown; negation preserves unknown. A rule matches only when its condition is definitely true, so a missing or ill-typed security operand cannot satisfy either a positive or a negated permitting condition.

Argument contracts may add a deterministic provenance penalty \(p_P(a)\in\mathcal L\). In the implementation this penalty is joined into the base policy evaluation because runtime provenance metadata is part of the trusted control plane:

\[
\tilde b_P(a,F_T)=b_P(a,F_T)\vee p_P(a).
\]

## 3. Source-to-field causal support

Let \(X=(x_1,\ldots,x_n)\) be the source-labelled context and let an agent with randomness \(\omega\) propose

\[
A(X;\omega)=(\tau(X;\omega),\theta(X;\omega)).
\]

For source \(j\) and action field \(f\), an initial paired-replay estimator is

\[
\widehat I_{j,f}
=
\frac1K\sum_{k=1}^K
 d_f\!\left(
 A(X;\omega_k),
 A(X^{(-j)};\omega_k)
 \right),
\]

where \(X^{(-j)}\) is a declared intervention on source \(j\), the same random seed \(\omega_k\) is used in each pair, and \(d_f\in[0,1]\) measures change in field \(f\). For the action type, \(f=\tau\).

This quantity has a causal interpretation only if the intervention is valid. Simple source deletion can create distribution shift, remove legitimate evidence, or alter planning trajectories. The code therefore treats paired ablation as an auditable baseline and label generator, not as a completed causal identification argument.

## 4. Delegation mask

Let

\[
D_P(j,f,a)\in\{0,1\}
\]

indicate whether policy \(P\) permits source \(j\) to determine field \(f\) for action \(a\). This separates influence from authority.

The unauthorized-support score is

\[
U(a)=\max_{j,f}
\widehat I_{j,f}\bigl(1-D_P(j,f,a)\bigr).
\]

A quantizer \(q\) maps the evidence to the risk lattice. For example, high-confidence control of a recipient or amount maps to `red`, while control of the action type can map to `deny`.

Crucially, \(q\) cannot issue an allow decision. It only emits a lattice element.

## 5. Final decision

For detector outputs \(s_1(a),\ldots,s_k(a)\),

\[
r(a)=\tilde b_P(a,F_T)\vee s_1(a)\vee\cdots\vee s_k(a).
\]

Let \(T(a)\) be the set of verified, unexpired tokens bound to the exact digest of \(a\). Execution is allowed only when

\[
r(a)\neq\mathsf D
\quad\text{and}\quad
O(r(a))\subseteq T(a).
\]

## 6. Non-expansion proposition

**Proposition.** Fix a policy version, trusted facts, action, and trusted token set. If the final controller allows the action, then the hard-policy controller using the same token set also allows it.

**Proof.** By construction,

\[
r(a)\ge \tilde b_P(a,F_T).
\]

The obligation mapping is monotone over the executable levels: increasing risk never removes a required token. Therefore

\[
O(\tilde b_P(a,F_T))\subseteq O(r(a)).
\]

If the final controller allows, then \(r(a)\neq\mathsf D\) and all obligations in \(O(r(a))\) are discharged. Hence all obligations in \(O(\tilde b_P(a,F_T))\) are also discharged, and the base controller allows. ∎

This proposition is deliberately narrow. It does not prove that the base policy is correct, that trusted facts correspond to reality, or that detector evidence is accurate.

## 7. Action-bound evidence

The prototype action digest is

\[
h(a)=\mathrm{SHA256}(\mathrm{canonicalJSON}(
\mathrm{session},\mathrm{actor},\tau,\theta,\mathrm{writeFlag},
\mathrm{sources},\mathrm{argumentBindings})).
\]

Each confirmation/MFA token signs \(h(a)\), actor, session, obligation type, issuer, and expiry. Changing a recipient, amount, source account, action type, actor, session, source record, or field-level provenance binding invalidates the token. This binds both the committed effect and the security-relevant authorization context; runtime source records must still be authenticated rather than authored by the LLM.

## 8. State machine

The legal write path is

```text
PROPOSED -> EVALUATED -> AUTHORIZED -> EXECUTED
                      -> OBLIGATION_PENDING
                      -> DENIED
                                  AUTHORIZED -> FAILED
```

Every new side-effect attempt starts from fresh facts; a previous `ALLOW` receipt
is not an execution capability. Duplicate delivery of a completed request returns
its historical receipt without another side effect. See `25_EXECUTION_ONCE_ZH.md`.

Live tokens must also be unconsumed and unrevoked. The runtime atomically commits
the request reservation and token consumption before dispatch. A reservation
without a terminal result blocks redispatch; it does not prove whether the bank
effect occurred. These lifecycle requirements only restrict the executable set.
Historical receipt replay checks signatures at the recorded time, not current
token availability, and does not independently prove the lifecycle database log.

## 9. Version differential

For old and new policy/model configurations, a permission expansion exists on state \(z=(a,F_T,T)\) when

\[
r_{\mathrm{new}}(z)<r_{\mathrm{old}}(z)
\]

or, more directly, when the new system allows and the old system does not under the same trusted inputs. The repository implements exact comparison over an explicit finite domain and an optional Z3 counterexample search for the numeric comparison fragment.
