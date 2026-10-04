# Restricted policy DSL reference

## 1. Design goal

The policy language is intentionally small, terminating, and auditable. It has no loops, recursion, arbitrary function calls, dynamic imports, or `eval`. A policy rule can only inspect the structured action and verified trusted facts supplied to one decision.

The evaluator is a reference prototype, not a formally verified compiler.

## 2. Policy structure

```yaml
policy_id: example
version: 1.0.0
default_level: deny
action_contracts: {...}
rules: [...]
metadata: {...}
```

`default_level: deny` is a hard schema invariant. The loader also rejects duplicate YAML mapping keys, duplicate rule IDs, malformed `$...` references, and action-specific rules whose action has no declared contract, preventing permissive defaults and silent configuration overrides/typos.

## 3. Risk lattice

```text
green < yellow < red < deny
```

All matching rule effects are joined by maximum risk. Argument-contract violations are then joined into that result. No “first matching allow wins” behavior exists.

Default obligation mapping:

| Level | Required result |
|---|---|
| `green` | no additional token |
| `yellow` | exact-action confirmation |
| `red` | exact-action confirmation and MFA |
| `deny` | non-executable; review must create a new authorized action or policy state |

## 4. References and operands

Supported references (all other strings beginning with `$` are load-time errors):

```yaml
$actor
$session
$params.<field>
```

A literal dictionary must be wrapped:

```yaml
{literal: {key: value}}
```

A trusted-fact value operand has exactly two keys:

```yaml
{fact: {predicate: account_balance_minor, subject: $params.from_account}}
```

Permissive defaults for missing facts are forbidden. A fact operand with a `default` key is rejected during policy loading.

Monetary operands use the `_minor` suffix, for example
`$params.amount_minor`, `account_balance_minor`, and
`daily_remaining_minor`. Comparisons involving these operands accept only
non-negative signed-64-bit integers. Binary floats, booleans, decimal strings,
negative values, and overflow are rejected at load time or evaluate to
`unknown` at runtime, so they cannot satisfy a permitting rule.

Policy version 0.2.0 adds a bounded integer sum operand:

```yaml
sum_minor:
  - {fact: {predicate: daily_spent_minor, subject: $actor}}
  - $params.amount_minor
```

It accepts 2 to 16 operands, validates each as a nonnegative signed-64-bit
integer and checks overflow at every addition. A missing/invalid runtime operand
or overflow returns unknown, including under negation. The bank, not the model,
supplies the signed daily spending fact. Current policy uses the sum for the
1000-unit confirmation/MFA boundary and separately checks remaining hard limit.

## 5. Condition grammar

### Boolean composition

```yaml
all: [<condition>, ...]
any: [<condition>, ...]
not: <condition>
```

`all` and `any` must be non-empty.

### Value operations

```yaml
equals: {left: <operand>, right: <operand>}
compare: {left: <operand>, op: "<=", right: <operand>}
contains: {collection: <operand>, item: <operand>}
in: {item: <operand>, collection: <operand>}
exists: {value: <operand>}
```

Comparators are exactly `<`, `<=`, `>`, `>=`, `==`, and `!=`.

### Trusted-fact operations

```yaml
fact_equals:
  predicate: authenticated
  subject: $actor
  value: true

fact_contains:
  predicate: owned_accounts
  subject: $actor
  item: $params.from_account

fact_not_contains:
  predicate: blocked_recipients
  subject: $actor
  item: $params.to_account
```

These operators inspect only facts that pass issuer, signature, issue-time, and expiry verification.

## 6. Three-valued semantics

Security conditions evaluate to `true`, `false`, or `unknown`. A rule matches only on `true`.

For negation:

| \(x\) | `not x` |
|---|---|
| true | false |
| false | true |
| unknown | unknown |

For conjunction, any false child makes the result false; otherwise any unknown child makes it unknown. For disjunction, any true child makes it true; otherwise any unknown child makes it unknown.

Consequently, neither

```yaml
not:
  fact_equals: ...
```

nor

```yaml
not:
  exists:
    value: {fact: ...}
```

turns a missing trusted fact into permission.

## 7. Action contracts

Each action declares:

- read/write classification;
- expected fields and whether extras are forbidden;
- semantic role of each field;
- whether provenance is required;
- minimum trust and allowed source kinds;
- risk on missing/violating provenance;
- whether field-level causal evidence is security-sensitive.

Example:

```yaml
to_account:
  role: target
  required: true
  provenance_required: true
  min_trust: user
  allowed_source_kinds: [user, trusted_service]
  on_missing: deny
  on_violation: red
  causal_sensitive: true
```

The source records and field bindings are runtime security metadata. They are included in the action digest and must not be authored by the LLM.

## 8. Partial transfer rule example

This illustrates syntax only. Deploy the complete current policy in
`configs/transfer_policy.yaml`, including account state, recipient eligibility,
daily cumulative obligations and the separate hard spending limit.

```yaml
- rule_id: transfer-small
  action: transfer
  effect: yellow
  reason: valid small transfer requires exact-parameter confirmation
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
          op: ">"
          right: 0
      - compare:
          left: {fact: {predicate: account_balance_minor, subject: $params.from_account}}
          op: ">="
          right: $params.amount_minor
```

A missing or ill-typed amount/fact makes the relevant condition unknown, so this permitting rule does not match.

## 9. Unsupported and prohibited patterns

Reject rather than approximate:

- unknown operators or comparator spellings;
- extra keys in operator payloads;
- fact-value defaults;
- arbitrary dictionary operands not wrapped as literals;
- Python expressions, regex execution, callbacks, or network lookups;
- LLM-generated trusted facts;
- a rule whose only purpose is to special-case one test prompt.

## 10. SMT coverage

`find_numeric_expansion_with_z3()` compiles a bounded numeric comparison fragment and preserves the evaluator's three-valued missing-fact semantics. It is a counterexample search, not a universal proof for the complete DSL. Unsupported expressions raise explicitly.

For `sum_minor`, the current SMT compiler accepts sums whose entire requested
integer search domain is within the monetary bounds. Potential overflow raises
an explicit error; narrow the bounds or use exact finite-domain policy diff.
