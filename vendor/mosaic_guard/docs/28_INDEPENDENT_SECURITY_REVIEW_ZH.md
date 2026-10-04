# MOSAIC Guard 独立安全审查与修复报告

日期：2026-09-06。交付代码版本：**0.1.1**；策略版本：**0.2.2**。
为避免破坏已有相对路径，项目目录仍叫 `MOSAIC_Guard_v0.1.0/`；实际版本以 `pyproject.toml` 和 `src/scns_guard/version.py` 为准。

## 结论

原版确有能复现的授权时效、取消原子性、事实冲突和结果语义问题，不只是提示词不够强。本次直接修改执行路径，并增加失败回归与故障注入测试。没有改成“全部拒绝”，没有让模型或学习分数取得授权权力，也没有删除原有测试。

原始包的 474 个声明文件在修改前校验通过。原版正确环境基线为 **246 passed, 7 skipped**。最终代码全量测试为 **274 passed, 7 skipped**，coverage 为 **83%（5194 statements，868 missed，四舍五入）**。新增 28 个测试用例，不等于 28 个漏洞；其中 22 个针对原版的回归在从上传 ZIP 重建的原始代码上全部失败、修补后通过，另有 2 个对第一轮修补进行进一步收紧的时钟边界测试，以及 4 个额外真实进程崩溃边界测试。

**这些结果只支持本地 durable mock 及所测契约，不是“LLM 操作绝对安全”的证明，也不是生产银行系统或容器安全认证。**

## 一、修复了哪些问题

| 编号与相对优先级 | 原始问题及触发条件 | 修复与主要位置 |
|---|---|---|
| R01 高：授权检查到执行之间的过期窗口 | 事实验证时仍有效，但等待预留、预留完成后、读取 snapshot 时已过期，原版仍能执行转账。身份或批准 token 在 snapshot 等待中失效也可能执行。 | `controller.py`、`execution_store.py`、`gateway.py`：在预留事务内和实际 dispatch 前复核；最终时间戳在可能阻塞的状态检查之后读取，身份在查询撤销状态后再次验时。 |
| R02 高：同时间戳的可信事实冲突 | 两个合法签名的 `authenticated` 事实同一时间发布，一真一假；原版会按 `fact_id` 排序选择，UUID/标识符碰巧决定授权。 | `value_types.py`、`policy.py`、`trust.py`：同一最新时间的值必须类型有效且严格相同，否则返回 Unknown，由策略 fail closed。较新事实仍按原本时序规则处理，不改成多数投票。 |
| R03 高：取消证明与取消状态分开提交 | 原版先消耗取消 proof，再单独写取消状态；后一步存储失败，proof 已消耗但支付还没被取消。等待到取消事务时身份/proof 也可能已过期。 | `gateway.py`、`runtime_store.py`：在取消的同一 SQLite 事务内重新验证身份/proof、消费 nonce、写取消状态及审计事件；失败一起回滚。 |
| R04 中：批准 token 超过原批准到期时间 | 原版用整数秒剩余 TTL 再加到带微秒的当前时刻。可稳定构造 token 比原 proof 多活 0.8 秒的情况。 | `tokens.py`、`gateway.py`：签发时传绝对截止时间，`token.expires_at <= proof.expires_at`，不再用相对秒数换算补时。 |
| R05 中：过时的熔断重置批准 | 操作员对 generation g 签名后，又发生拒绝事件，但未达到锁定阈值；原版不更新 generation，旧批准仍可清掉新增记录。 | `runtime_store.py`：每次实际计入的拒绝/失败状态变化都推进 generation，旧 reset binding 失效。已经锁定后不再计数的事件不伪装成新状态。 |
| R06 中：已提交写入的异常被报成请求错误 | mock bank 已提交，随后 `finish()` 因 `ValueError` 失败；HTTP 原版返回 400，混淆“输入无效”与“可能已提交”。 | `gateway.py`：跨入 execute 边界后的异常归为不确定服务故障，HTTP 返回 `503 / service_unavailable_no_automatic_retry`；保留 handle，后续查询为 unknown，不自动新建支付。 |
| R07 中：共享祖先图的重复递归 | 合法签名来源组成 diamond DAG；17 个节点产生 1515 次签名验证。随着层数增加会放大 CPU 开销；并非伪造签名才能触发。 | `lineage.py`：单次完整验证共享 memo、检测环、限定 128 节点。缓存不跨请求；修改 root 后下一次必须重新拒绝。resolver 按完整 catalog 批量验证。 |
| R08 中：JSON 数值/复杂度边界不完整 | 拒绝 NaN/Infinity 字面量不等于拒绝 `1e999`，后者可解析为非有限 float；深层 JSON 缺少一致边界。未把它夸大成已成功绕过金额授权。 | `llm_adapter.py`：finite `parse_float`、256 KiB、64 层、32768 节点、重复键拒绝、无效 Unicode 拒绝；保留原 `ValueError` 和兼容错误文本。 |
| R09 中：慢请求能持续占用工作线程 | 原来只有逐次 socket timeout。每隔一小段时间补字节可续住 header/body 读取；回归使用 0.2 秒可控总预算。 | `http_service.py`：增加总 ingress deadline（默认 5 秒），覆盖认证前 headers 及 body，超时关闭连接。读完请求后停止 watchdog，**不把银行写入中断当作撤销**。 |
| R10 低：重复 Content-Type 有歧义 | 同一请求给两条不同 Content-Type，原版只读取第一条并接受。没有据此声称已完成 request smuggling。 | `http_service.py`：只接受恰好一条明确的 JSON Content-Type。正常客户端仍可使用。 |
| R11 中/可用性：跨进程 bank flock 无限等待 | 另一个进程持有锁但没有退出时，原版后续操作可一直等。用真正独立持锁进程复现。 | `durable_bank.py`：非阻塞 flock + monotonic 等待预算，默认文件锁 5 秒、本地锁 5 秒；失败清理连接/文件描述符。不是整个任意 tool 的硬执行超时。 |
| R12 低/配置加固：YAML alias 和复杂度无预算 | 完整有效策略的 metadata 可包含 aliases 或 200 层嵌套。策略文件是可信运维输入，不将此项冒充远程授权绕过。 | `policy.py`：策略 YAML 限制 1 MiB、64 层、32768 节点；不支持 alias，统一 `PolicyError`。现有项目配置不需要 alias。 |

### 一个必须讲清的状态语义

执行前被拒绝，且尚未完成预留：不消费 token，也不调用 bank。

已经完成预留，但在 dispatch 前过期，或已经进入可能产生效果的阶段后出错：不释放 token，不自动重发，保守返回 unknown。调用者必须保留原 handle。

`finished` 返回持久化的原结果；`started` 的 unknown 查询可以产生不同的**审计观察 receipt hash**，但不能因此产生第二次 bank effect。这两个概念不能混为一谈。已提交之后的身份到期或撤销不会倒转银行效果。真实外部适配器还必须自己定义授权接受时点及幂等/核账协议；本次不声称在到期后能够自动撤回已经发出的远程请求。

## 二、实际验证与原始证据

本节路径相对于项目目录。测试不接真实 LLM、银行、GPU 或用户本地机器；HTTP 仅 loopback，私钥与数据库均为临时 mock fixture。

| 验证项目 | 实际结果 | 证据 |
|---|---|---|
| 原版正常基线 | 246 passed，7 skipped | `artifacts/security-review-20260906/baseline-correct-env.txt` |
| 从原始 ZIP 重新构建代码后跑新增漏洞回归 | 22 failed，2 deselected；退出码 1，符合预期 | `artifacts/security-review-20260906/exact-original-regressions.txt` |
| 第一轮修补的进一步时钟边界检查 | 2 个测试先失败，最终通过 | `dispatch-clock-refinement-before.txt`、最终全量报告 |
| 最终全量测试 | 274 passed，7 skipped | `artifacts/test_report.txt` |
| coverage 下再次全量测试 | 274 passed，7 skipped；总体 83% | `artifacts/coverage_test_report.txt`、`artifacts/coverage_report.txt` |
| 真实进程退出的六个边界 | 6 passed；涵盖 bind、reserve、bank 前/后、receipt append、finish | `artifacts/security-review-20260906/crash-boundaries-final.txt` |
| 并发/解析/来源图压力检查 | 25 个汇总检查；并发请求 512 次，独立授权写入 266 次，bank 实际写入 266 次，未观察到重复效果，余额守恒 | `artifacts/security-review-20260906/stress-results.json` |
| 来源图尺度 | 17、33、65、127 节点；有效 catalog 的签名验证次数分别等于节点数 | 压测 JSON 的 shared_ancestry 记录 |
| 固定种子 JSON 检查 | 2000 个正常输入接受、2000 个不支持/畸形输入拒绝 | 压测 JSON 的 strict_json_seeded 记录 |
| 运行安全 demo / execution-once / engineering | 分别 10/10、10/10、16/16；回执重放有效 | `artifacts/runtime_safety_demo.json`、`execution_once_demo.json`、`engineering_demo.json` |
| planning / schema / examples / 基础回执 | 已重新生成或执行；planning 重放及基础 2 条 receipt 验证有效 | `schemas/`、`examples/`、`artifacts/planning_demo.json`、`receipt_verification.json` |

并发分别使用 1、2、8、16、32 个 thread/独立进程，测试同一 handle 争抢与不同正常 handle。压力检查不是 HTTP 吞吐压测，也不是生产 SLA 或跨机器分布式事务测试。各分段最终调用均有 `EXIT_STATUS=0`；汇总 JSON 记录了源结果文件，使用 seed 20260906。

**保留失败和未完成记录。** 初次基线有 2 个子进程 import 失败，原因是未设置项目要求的 PYTHONPATH，不算产品漏洞。YAML 用例曾使用不完整策略，之后改为完整有效策略再复现。新增崩溃测试最初错误要求 unknown 观察的 receipt hash 恒定，已改为验证状态/业务结果恒定；未改任何原有测试断言。长时间组合压测与一次 `make reproduce` 被执行环境截断，原始日志保留；没有把这些调用记为通过。压测改为显式有界子进程，并分段完成。完整验收由独立完成的全量测试、coverage 和 `produce_artifacts()` 最终阶段组成，`reproduction_summary.json.execution_mode` 明确标注 componentwise，**不宣称单次 make reproduce 在本环境执行完成**。默认 `make reproduce` 仍执行所有步骤，没有删减测试。

### deterministic synthetic smoke test; not empirical LLM evidence

原有合成实验重新运行：seed 7、140 个构造场景、3 个原有比较分支。它只验证确定性代码路径，不用其中的零越权率宣称真实 LLM 攻击成功率为零。历史真实模型实验、Docker 验收文档和源代码快照原样保留，未重新执行，不代表新版本的当前实测。

## 三、尚未验证、仍需保留的边界

**Docker 与 SMT：** 当前没有 Docker 可执行文件，也没有 z3-solver；本次 4 个真实 Docker opt-in 用例、3 个 SMT 用例跳过。安装 z3 的尝试因网络解析失败，记录保留。已有历史容器证据不是本次复测。不能称“281 项全部通过”或“sandbox 已重新验证”。

**信任根与真实集成：** identity、approval、lineage、bank、audit 的签名服务/密钥，以及运行主机属于 TCB。签名只保证来源，不保证内容事实正确。必须由受信任的人机确认/MFA 服务生成批准，不能由 LLM、普通前端 JavaScript 或可注入工具输出代签。该仓库的 bank 是 durable mock；没有新增真正银行接口，也没有测试第三方支付幂等、跨地域故障、分布式时钟或真实 MFA UI。

**存储与核账：** runtime DB 与 mock bank DB 是两个提交域。采用先预留、保留 unknown、核账而不是假装分布式 exactly-once。哈希链不能防止能改写本机所有文件的人重算历史；需要外部可信 checkpoint/备份管理。receipt chain 和 bank execution_log 的历史读写量随运行增长；本轮没有做长周期存储重构、配额/轮转或百万条历史的性能验证。

**资源隔离：** ingress deadline 不是业务执行 deadline；文件锁预算也不隔离任意 Python callback。自定义 detector、fact supplier、外部 tool 仍应有适配器级预算/进程隔离和明确的 unknown 协议。没有声称封堵任意插件、宿主机攻陷、容器逃逸或任意 DoS。

**语言与金融安全：** 模块不能证明模型理解了用户真实意图。安全确认需要展示完整关键参数，风险提示不能替代确认。形式化策略的正确性与覆盖面也依赖业务定义；本次不构成法律、金融合规或安全认证。

## 四、复现入口

在项目目录中：

```bash
# 已有依赖环境：
make test
make security-review
make security-stress
make reproduce
PYTHONPATH=src python scripts/verify_manifest.py
```

新环境先按项目 README 安装依赖，例如 `python -m pip install -e '.[dev,ml,smt]'`。SMT 需要安装 z3-solver；Docker 集成需要实际可用 Docker 和文档指定的 pinned image opt-in。不要把缺失依赖的 skipped 当 pass。

资源受限环境可以分段运行压力检查：

```bash
PYTHONPATH=src python scripts/run_security_stress.py --modes process --workers 32 --cases same_handle --output artifacts/security-review-20260906/local-process32.json
```

短回归入口 `make security-review` 运行本次新增的 28 个用例；原有两个 bank 前/后崩溃测试仍在全量测试中。压测驱动没有给正常用户新增 HTTP 调试路由。

压缩包完整性在外层目录执行 `python 03_VERIFY_PACKAGE.py`。修改代码或生成新结果之后，原 manifest 失配是预期行为，不应为了消除报警而跳过校验。

## 五、升级注意事项

先停止旧 worker，备份原代码、service.json、runtime/bank 数据库及密钥。**不得新建数据库或重新 initialize 来“修复”已有 started/unknown 状态，不得清空 consumed-token、取消、审计或熔断记录。**

新策略版本与字节 hash 已变化。现有部署的 `service.json.policy_sha256` 需要在运维人工审核后显式更新；路径按实际部署位置调整。启动时报 pinned policy hash changed 是预期保护，不要删除检查。先对未决操作做人工核账。不要同时运行旧版与新版 worker。

本次没有修改 SQL 表结构，仍使用 schema version 2。重置 generation 的语义更严格；旧 operator reset proof 应作废并针对当前状态重新签署。不要依赖旧版签发的尚未到期批准 token：停服期间等待现有身份/控制凭据的最大有效期经过（当前内置上限身份 300 秒、控制 120 秒），保留持久化状态，对仍允许继续的 pending 请求重新获取批准。已经 started 的请求不能靠重新批准再次执行。

历史真实模型/容器实验及旧源码快照只供审计，**部署入口是项目根 `src/scns_guard`，不是 artifacts 下的历史 source snapshot**。旧 04_TESTED_ENVIRONMENT.json/05_REQUIREMENTS_TESTED.txt 标记原交付环境；新环境见外层 07_TESTED_ENVIRONMENT_20260906.json。

## 六、文件导航

完整说明：本文件；代码差异：`artifacts/security-review-20260906/changes.diff`；新增回归：`tests/test_independent_security_review.py`、`test_independent_runtime_limits.py`、`test_independent_crash_boundaries.py`；压力驱动：`scripts/run_security_stress.py`；新验收：`artifacts/security-review-20260906/`；原始被替换的根验收文件：其中的 `original-acceptance/`。

关于 socket 超时、JSON 非有限数字和 SQLite 写事务的外部语义核对采用 Python/SQLite 官方文档；本报告关于漏洞是否存在、是否修好及计数的依据是本仓库测试与原始输出，而不是把通用指南当作复现证据。
