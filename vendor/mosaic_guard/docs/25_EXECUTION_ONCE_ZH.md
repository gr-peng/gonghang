# 防重复执行与一次性令牌

日期：2026-09-04。此更新仍是安全模块工程，不增加客服 UI，不接真实银行。

## 现在能保证到什么程度

在请求编号稳定、所有工作进程使用同一可信状态库、数据库未被删除或回滚、后端没有自行重试的条件下，同一写请求最多向工具发起一次调用。相同请求再次送达时，返回之前记录的结果，不再次扣款。

同时，确认与 MFA 令牌只可用于一次执行尝试。更换请求编号或重新生成 `action_id`，不能让旧令牌恢复可用。

这不等于分布式银行交易的恰好执行一次：登记执行之后、调用银行之前也可能中断。此时本模块会保留结果不确定的状态，牺牲自动恢复能力，等待人工核账，不猜测是否应该再转一次。

## 执行顺序

1. 用签发机构、用户、会话和 `request_id` 形成请求标识，把它绑定到完整动作摘要及逻辑工具名称。
2. 已完成的请求返回历史回执；已有执行尝试但无结果的请求返回 `unknown`；同一编号改参数或改工具则拒绝。
3. 尚未开始的请求读取当前事实，复查规则、来源、熔断状态和令牌。
4. 授权通过后，在一笔 SQLite 事务里保存授权依据、登记执行已开始，并消耗本次所需的令牌。事务必须先提交，才能调用工具。
5. 执行工具、校验结果、生成回执，再保存终态。工具异常、回执保存失败或模拟进程中断，都不能释放已经登记的执行尝试。

等待确认或 MFA、规则拒绝、可信事实读取失败时，不消耗令牌，也不登记工具执行已开始。请求编号仍绑定原动作；补齐义务后可继续，改变动作必须用新的请求编号和新的确认。

执行开始前的令牌撤销和令牌消耗在同一个数据库里排序：撤销先提交则执行不能获得该令牌；执行登记先提交则后来的撤销不撤回已经发生的授权，不会产生退款。

## 接入方式

已有代码默认得到与 `TokenAuthority` 对象同寿命的内存状态库。需要跨重启保护时，显式使用 SQLite 文件，并在整个服务中共享它：

```python
from pathlib import Path
from scns_guard.execution_store import ExecutionStore
from scns_guard.tokens import TokenAuthority
from scns_guard.controller import SafetyController
from scns_guard.outcomes import outcome_from_receipt

state_dir = Path('artifacts/state')
state_dir.mkdir(parents=True, exist_ok=True)
store = ExecutionStore(state_dir / 'authorization.sqlite3')
token_authority = TokenAuthority(
    issuer='obligation-service',
    secret=deployment_secret,
    state_store=store,
)
controller = SafetyController(
    policy=policy,
    fact_authority=fact_authority,
    token_authority=token_authority,
    lineage_authority=lineage_authority,
    circuit_breaker=circuit,
    receipt_ledger=receipt_ledger,
)
receipt = controller.execute(
    action,
    request_id=stable_request_id,
    fact_supplier=fact_supplier,
    tool=bank_adapter,
    tokens=trusted_confirmation_and_mfa_tokens,
)
public = outcome_from_receipt(receipt, action=action)
```

`deployment_secret`、身份、请求编号和令牌来自可信服务端，不由模型决定。服务退出时关闭状态库；不要为了重试而删除数据库。状态目录已加入忽略规则，不能把带有账户信息和签名材料的部署数据库直接提交到仓库。

### 请求编号规则

- `request_id` 是服务端为一次业务意图确定的稳定编号。未显式提供时使用 `action.action_id`，但生产接入不应依赖每次重新规划生成的新 ID。
- 网络重发要复用原始完整 `ActionProposal` 和编号。重新签发来源记录、修改金额、账户或会话，会改变安全摘要；即使可见金额相同，也不应静默继承旧授权。
- 同一用户明确要求再转一笔相同金额时，用新的编号并取得新的可信确认。此行为属于另一笔支付。
- 查询不使用支付结果缓存。重复查询余额会重新读取当前状态。
- 缓存返回的是历史执行结果。即使后来余额变化、令牌过期或安全锁开启，也不会把它当作新的授权或调用工具；上层应展示回执时间，不能声称刚刚又执行了一次。
- 同一请求在另一个工作进程仍执行时，本模块可能先返回 `unknown`。之后查询同一编号可以得到已经落盘的终态，但不能创建一个新编号自动重试。

### 撤销规则

```python
applied = token_authority.revoke(
    token,
    operator_id=authenticated_operator_id,
    reason='用户在执行前取消',
)
```

此 API 只供可信主机代码使用，当前没有实现操作者身份认证。`operator_id` 只是审计字段，不能靠传入一个名字获得撤销权限。模型的 `no_action/cancelled` 也不会自动调用它。

撤销仅影响尚未消耗的合法令牌；已消耗或已撤销时返回 `False`，不恢复令牌、不退款。对伪造签名或其他签发机构的令牌，API 会报错。

## 回执与重放

`TokenAuthority.verify()` 保留签名、动作绑定和时间验证语义，供历史回执重放使用；实时授权使用 `verify_for_use()`，并在工具调用前再次原子检查、消耗令牌。

因此，执行过的回执不会因为令牌现在已消耗而失效。重复请求直接返回原回执，不向回执链追加第二条伪装成新执行的记录。调用者应按 `receipt_hash` 去重。

SQLite 保存请求绑定、执行前授权依据、令牌状态、终态回执和生命周期事件。普通 `ReceiptReplayer` 仍只验证记录中的规则、事实与签名，并不独立证明数据库当时没有别的令牌消耗；不能把规则重放与持久化状态审计混为一谈。

状态库不可用或完整性检查失败时，代码会报错并停止执行，不临时切换到新的空状态库。若错误发生在工具执行后，上层必须按结果不确定处理。

## 已执行验证

`tests/test_execution_once.py` 覆盖：重复提交、修改请求内容、跨用户/会话、旧令牌换请求、独立新支付、等待 MFA、过期令牌、并发数据库连接争抢、撤销与消耗竞态、数据库重开、执行后超时、模拟进程中断、回执写入失败、状态库不可用及缓存回执篡改。

本轮全套 200 项自动化测试通过。新增脚本演示有 10 项集成检查，包含四次明确测试执行，其中两次故意制造执行后异常；所有重发都没有额外增加转账次数。七条正式回执可重放，生命周期事件单独记录。

```bash
PYTHONPATH=src .venv/bin/python scripts/run_execution_once_demo.py
make PYTHON=.venv/bin/python reproduce
make PYTHON=.venv/bin/python verify-bundle
```

结果文件是 `artifacts/execution_once_demo.json` 和 `artifacts/execution_once_receipts.jsonl`。本轮没有新增 Qwen 推理；这些是确定性执行协议的并发、故障与持久化测试。先前真实模型结果保持原样。

## 尚未覆盖

- 真实进程强杀、断电、文件系统损坏和数据库回滚攻击；目前通过 `BaseException` 模拟未完成执行，再关闭并重新打开 SQLite。
- 模拟账本本身的持久化，以及它和状态库之间的分布式事务。
- 真实银行后端内部自动重试、幂等键传递、交易查询与人工核账工作流。
- 运维身份认证、部署密钥管理、状态数据加密和访问控制。
- 共享的持久化熔断状态，以及多进程回执文件写入；当前 SQLite 生命周期并发控制不意味着 JSONL 回执文件也能由多进程同时安全写入。

下一步优先补持久化熔断和可信服务端接入封装，不建设客服界面。
