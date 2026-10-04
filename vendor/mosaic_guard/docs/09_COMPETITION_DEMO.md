# 比赛演示脚本

## 当前演示主线，2026-09-04

按 Infty 的要求，比赛以可用的安全 AI 系统为主，科研发现放附录。工程计划见 `23_COMPETITION_ENGINEERING_PLAN_ZH.md`。

五分钟展示顺序：

1. 介绍用途：AI 负责理解和提议，规则与可信用户交互负责授权。
2. 正常业务与缺字段补问；用户补齐后展示完整操作参数。
3. 没有确认不执行；明确确认后执行模拟转账，展示账本变化。
4. 展示外部材料篡改、伪造确认、账户越权或余额变化时的处理。
5. 展示命中的规则、需要的确认、最终状态和可重放记录。

当前可运行的补问到执行流程：

```bash
PYTHONPATH=src python scripts/run_planning_demo.py
```

此命令使用脚本规划器、模拟账本和显式测试确认令牌，不能描述成真实用户已经通过身份认证。真实 Qwen 另有六个已保存接口功能检查，位于 `artifacts/model_smoke/planner-v2-qwen38-20260904-a/`，只规划和审查，不执行银行操作。

交互前端与可信确认页面还未完成。现阶段以命令行和保存记录展示，不宣称完整客服平台或生产级安全。

## 历史场景目录

以下保留原型阶段的产品与研究案例供选用；研究机制和新颖性不再是主展示或交付前提。涉及来源洗白的合成机制演示必须保留其原有证据标签。

## One-sentence pitch

MOSAIC Guard lets an LLM understand and propose actions, but keeps authority in a deterministic, replayable control plane; uncertain neural evidence can only demand more proof, never grant more privilege.

## Five-minute flow

### Scene 1 — normal high-value transfer

User requests a transfer of 1500.

- hard policy verifies identity, ownership, account state, recipient, balance, and daily limit;
- base risk is `red`;
- the system requests exact-parameter confirmation and MFA;
- tokens are issued for the specific source account, recipient, amount, actor, and session;
- execution succeeds and a receipt records before/after state.

Show:

```bash
mosaic-guard demo
```

### Scene 2 — direct RAG manipulation

A RAG document changes `to_account` to an attacker account.

- field provenance says an external RAG source controls a `target` role;
- argument contract escalates to `red` before any neural detector is needed;
- the LLM's explanation cannot override this.

### Scene 3 — source laundering

The same malicious target is carried through a summary so outer argument provenance appears user-derived.

- explicit provenance baseline misses the structured toy case;
- paired shadow replay removes the upstream source;
- only `to_account` changes;
- the delegation mask says RAG cannot choose the recipient;
- the causal signal adds `red`.

State clearly that this is a deterministic demonstration of the intended mechanism, not yet a result on a real LLM.

### Scene 4 — benign delegated information

A trusted payee-resolution service supplies an account identifier, or a RAG document supplies only a memo.

- both sources causally influence some field;
- the delegation contract permits that source/role pair;
- causal influence alone does not trigger a security claim.

This is the visible answer to “influence is not authority.”

### Scene 5 — detector failure cannot grant permission

Inject a detector output of `green` into an action that the base policy denies.

- final risk remains `deny` because the join takes maximum risk;
- there is no code path from detector score to permit.

The invariant is covered by automated tests.

### Scene 6 — stale state and bound-token defense

- obtain confirmation for `amount_minor=40000` (400 currency units);
- change `balance_minor` to `10000` (100 currency units) before execution;
- `execute()` retrieves fresh signed facts and denies;
- modify amount or recipient after confirmation;
- the token digest no longer matches.

### Scene 7 — receipt integrity

- replay the decision from policy snapshot, signed facts, tokens, and detector outputs;
- modify a stored action amount;
- hash-chain verification fails;
- explain that production needs an externally anchored chain head.

## What to show on screen

1. concise architecture diagram from `03_ARCHITECTURE_AND_INTERFACES.md`;
2. `transfer_policy.yaml` field contract for `to_account`;
3. normal demo JSON with `require_mfa` then `allow`;
4. three-arm synthetic summary;
5. one receipt with separate `formal` and `internal` layers;
6. `pytest -q` result;
7. novelty slide that openly distinguishes prior work from the unresolved hypothesis.

## Avoid in the pitch

Do not say:

- “formally guarantees the bank is safe”;
- “causal detector is proven correct”;
- “first neuro-symbolic agent security system”;
- “the source-laundering experiment validates LLM behavior”;
- “zero attack rate” based on the toy harness;
- “the LLM understands authorization.”

Say instead:

- deterministic non-expansion relative to the configured base policy;
- trusted, parameter-bound obligation discharge;
- replayable conditional decisions;
- an audit-ready research hypothesis for source-laundering cases.
