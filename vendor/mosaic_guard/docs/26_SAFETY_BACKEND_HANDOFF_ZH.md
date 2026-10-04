# MOSAIC Guard 安全后端交付说明

2026-09-06 当前版本：代码 0.1.1 / 策略 0.2.2。Pro 修复已整合，本机 281 项测试全部通过；当前验收和升级入口见 `29_PRO_INTEGRATION_ACCEPTANCE_20260906_ZH.md`。下列旧日期条目保留为历史记录。

2026-09-05 更新：已修复严格事实类型、旧会话计数隔离、最终身份有效期及沙箱独立监督与恢复。策略版本为 0.2.1；升级步骤、边界与本轮验收见 `27_SECURITY_REFACTOR_20260905_ZH.md`。下文 2026-09-04 的测试数量为历史验收。

评估日期：2026-09-04。当前安全后端可以独立启动并接入队友的规划器，默认使用持久化模拟银行。交付范围是安全模块、接口和验收证据，不包含客服界面。真实登录、真实 MFA、真实银行接口和全队业务场景由对应团队组件提供。

## 已完成的安全功能

| 能力 | 当前实现 | 验证位置 |
|---|---|---|
| 权限分级 | 查询、确认、确认加 MFA、拒绝；按用户当日累计转账金额判断 | `test_runtime_safety.py` |
| 可信接入 | 身份和确认使用独立密钥；严格 schema；用户及会话隔离 | `test_gateway.py` |
| 来源与注入边界 | 验证来源签名、内容摘要、祖先记录和所属会话；模型不能提交身份或来源绑定 | `test_lineage.py`、`test_gateway.py` |
| 执行可靠性 | 完整动作与服务端编号绑定；一次性令牌；预留先于执行；重复请求返回历史结果 | `test_execution_once.py` |
| 中断与人工接管 | 提交前可信取消、会话撤销、只读核账、带签名及状态版本的人工解锁 | `test_security_operations.py`、`test_client_operations.py` |
| 重启与并发 | 持久化令牌、动作、回执和安全锁；模拟银行跨进程串行提交 | `test_persistent_runtime.py`、`test_durable_bank.py` |
| 异常结果 | 工具结果逐字段匹配动作；不一致或失联返回 unknown；不自动重发 | `test_runtime_safety.py`、`test_process_recovery.py` |
| 敏感数据 | 文本筛查、普通响应账户脱敏、内部回执与客服响应隔离 | `test_service.py`、`test_security_operations.py` |
| 生成代码隔离 | 固定镜像、无网络、非 root、只读根目录、资源和输出限制、清理确认 | `test_sandbox.py`、独立容器验收 |
| 部署与审计 | 私有密钥、策略摘要检查、共享限流、有界 HTTP 服务、签名审计检查点 | `test_service.py`、`test_security_operations.py` |

代码始终保持 `final_level >= base_level`。模型、RAG、记忆和学习检测器不能签发可信事实或解除确认义务。

## 接入结构

```text
可信登录服务 ── 身份签名 ───────────────────────┐
可信客服后端 ── 来源记录 ──┐                    │
                          ▼                    ▼
LLM 规划器 ── 任务 JSON ─────────── 安全后端 review
                                       │
                                 服务端动作编号
                                       │
可信确认界面 ── 展示完整参数 ── 确认签名          │
真实 MFA 服务 ── 独立验证 ───── MFA 签名 ────────┤
                                       ▼
                                approve / execute
                                       │
                    当前事实 + 规则 + 安全锁 + 令牌
                                       │
                         原子预留并消耗令牌
                                       │
                         模拟银行或可信银行适配器
                                       │
                            审计回执 + 固定结果响应

生成代码 ── 独立 sandbox:run 权限 ── 隔离容器
                              无银行工具和宿主机密钥
```

接入时必须遵守：

1. 模型持有的身份凭据最多包含 `agent:review` 和 `agent:execute`。确认、MFA、运维和审计凭据不能进入模型上下文。
2. `frontend` 是测试中的可信来源生产者名称，指服务器组件，不是浏览器。其密钥不能下发网页或模型。来源的 `metadata.actor_id` 和 `metadata.session_id` 由认证后的服务器填写。模型提取字段后重新签名，不等于用户授权。
3. 用户确认时必须看到 `confirmation-view` 的完整账户、金额及备注，签名绑定对应 `handle` 和 `action_digest`。不能拿脱敏账户代替完整参数确认。

## 启动与复现

在项目根目录运行。初始化目录必须不存在，程序不会覆盖旧密钥或账本。

```bash
PYTHONPATH=src .venv/bin/python scripts/serve_safety.py init \
  --directory artifacts/state/team-demo

PYTHONPATH=src .venv/bin/python scripts/serve_safety.py serve \
  --config artifacts/state/team-demo/service.json --demo-bank
```

默认监听 `127.0.0.1:8765`。`GET /health` 仅检查 HTTP 进程，不代表银行、身份和 Docker 都已就绪。跨机器接入须由部署方提供受信任的 TLS 反向代理及网络访问控制；本服务不直接绑定公网。

初始化生成六个独立的 256 位密钥。状态目录权限为 0700，密钥及主数据库为 0600。启动检查所属用户、文件类型、权限、策略摘要和持久化密钥指纹。不要将状态目录放入 Git、交付包或模型上下文。

```bash
PYTHONPATH=src .venv/bin/python scripts/run_engineering_demo.py
make PYTHON=.venv/bin/python reproduce
make PYTHON=.venv/bin/python verify-bundle
```

全量复现默认明确跳过实际容器测试。要纳入容器测试，先取得可信本地镜像的不可变 ID，再设置环境变量：

```bash
MOSAIC_SANDBOX_IMAGE=sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea \
  make PYTHON=.venv/bin/python reproduce
```

上述 ID 是本次验收的 Python 3.12 slim 镜像，不保证其他机器已经下载。运行器使用 `--pull=never`，不会在请求中下载镜像。初始化时加入 `--sandbox-image <不可变镜像ID>` 才启用代码沙箱；未配置或 Docker 不可用时拒绝执行，不降级为宿主机 Python。当前持久化模拟部署面向 macOS/Linux 本地文件系统，不支持网络共享 SQLite 文件或 Windows 文件锁。

## HTTP 契约

除 `/health` 外均为 POST，要求 `Authorization: Bearer <短期身份凭据>` 和 `Content-Type: application/json`。不接受 URL 中的身份信息、重复 JSON 键、额外字段或分块请求。单次请求最多 256 KiB；文本、代码及模型输出另有较小限制。最多 16 个连接线程，同一用户每分钟最多接纳 60 次请求，沙箱每个服务实例最多并行 2 个任务。

| 路径 | 所需 scope | 正文 | 用途 |
|---|---|---|---|
| `/v1/review` | `agent:review` | `model_output`, `trace` | 补问或安全审查及服务端编号 |
| `/v1/execute` | `agent:execute` | `handle` | 检查当前事实后执行 |
| `/v1/confirmation-view` | `human:confirm` | `handle` | 可信界面所需的完整参数 |
| `/v1/approve` | `human:confirm` | `handle`, `proof` | 验证确认或 MFA 签名 |
| `/v1/cancel` | `human:confirm` | `handle`, `proof` | 禁止尚未预留的写请求继续执行 |
| `/v1/revoke-session` | `operator:revoke` | `actor_id`, `session_id`, `proof` | 撤销会话，保留历史结果 |
| `/v1/operator/state` | `audit:read` | `actor_id` | 查询安全锁及当前解锁绑定值 |
| `/v1/operator/reconcile` | `audit:read` | `handle` | 只读查询后端实际结果 |
| `/v1/reset` | `operator:reset` | `actor_id`, `proof` | 人工审核后解锁 |
| `/v1/screen` | `agent:review` | `text` | 进入模型之前的数据最小化 |
| `/v1/sandbox` | `sandbox:run` | `code` | 受限容器执行 |
| `/v1/audit/checkpoint` | `audit:read` | `{}` | 导出签名审计检查点 |

请求 schema 位于 `schemas/gateway_*.schema.json`。`client.py` 提供 `SafetyClient`，不签发凭据、不跟随重定向、不自动重试。

```python
from scns_guard.client import SafetyClient

agent = SafetyClient('http://127.0.0.1:8765', agent_identity_credential)
reviewed = agent.review(review_request)
if reviewed['status'] == 'reviewed':
    handle = reviewed['handle']
    # 可信界面和认证服务独立处理确认；模型不得调用签发函数。
    outcome = agent.execute(handle)
# 缺字段时交给客服补问，拒绝时停止；不可假设总有 handle。
```

`CredentialAuthority.issue()` 仅供可信签发服务或测试夹具使用，不存在 HTTP 签发接口。身份最长有效期默认 300 秒，确认服务最长 120 秒。凭据包含签发者、接收服务、密钥版本、生效和失效时间、唯一 nonce 和严格类型的 claims。

确认和 MFA claims 必须包含用户、会话、操作类型、目标 `handle` 和完整 `action_digest`。证明只能使用一次；重复 HTTP 提交不会生成新令牌。参数变更后必须重新 review 并取得新确认。模型 `no_action/cancelled` 仅表示不提出本轮动作，不能撤销此前请求。

## 客服结果如何显示

只使用 `outcome.status`、固定 `message` 和校验后的 `data`。不要让 LLM 重写余额或宣称交易成功。

| 状态 | 客服处理 |
|---|---|
| `needs_confirmation` | 展示完整参数并进入可信确认流程 |
| `needs_mfa` | 完成确认和独立 MFA；勿在聊天中索要验证码 |
| `blocked` | 本次未执行；提示或转人工 |
| `authorized_not_executed` | 仅审查通过，不能宣称完成 |
| `succeeded` | 可信工具返回了与动作匹配的完成结果 |
| `unknown` | 保留原编号，人工核账；不要新建请求自动重发 |

HTTP 503、连接中断或超时也不能解释成未扣款。错误响应不返回账户、签名、原始异常或内部回执。沙箱输出标记 `trusted_for_banking=false`，不能替代交易回执或作为授权依据，应按不可信纯文本处理。

## 故障恢复与审计

写请求状态依次为 pending、started、finished。started 和令牌消耗先提交到运行时数据库，再调用银行。模拟银行把实际余额变化提交到另一数据库后才返回，两者不构成跨数据库原子事务。

进程在提交前或提交后异常退出时，原编号都不会再次执行。模拟部署重启时，在银行跨进程锁内扫描遗留 started 请求，并锁定用户。运维人员核账后，向独立确认服务申请绑定当前 `reset_binding` 的签名才能解锁。解锁不删除旧请求、不把未知历史结果改写成成功，也不撤回已完成转账。

工具或事实服务失败累计 3 次，拒绝或已认证的异常请求累计 5 次，会触发安全锁。计数自人工重置起累计，成功不清零，比只统计连续失败更保守；不同会话共享用户计数。等待确认不算故障。

SQLite 回执链支持多个连接串行追加；持久化回执和失败计数在同一事务提交。检查点绑定回执前缀、事件数量及事件摘要，由独立 HMAC 密钥签名，`audit.verify_checkpoint()` 检查覆盖范围内的修改和截断。

检查点须由独立保管方留存。本模块提供导出和校验，没有替队伍部署外部不可变存储。能修改数据库并取得全部密钥的宿主机管理员仍在信任边界内。备份恢复、密钥轮换和策略升级须停机审核，不能删令牌消耗记录或恢复旧数据库来解除异常。

## 数据与部署责任

全部演示账户和用户均为构造数据，未连接真实银行。`screen` 识别部分凭据、电话、邮箱、身份证和长账户号码，并移除格式控制字符；不等于完整个人信息检测或语义内容审核。应在发送 LLM 前使用，并遵守资料最小化原则。

普通响应隐藏完整账户，去除备注、签名和完整账本；可信确认界面与受限核账保留必要的完整参数。内部回执包含原始动作、来源和事实，须按敏感审计资料管理。文件权限不等于数据库加密。真实数据的授权、磁盘加密、备份权限、保留期限和获批删除流程由部署方落实。为防止重放，本模块不提供模型可调用的审计或令牌历史删除接口。

当前业务动作是余额查询和转账。新增卡片、订阅或理财动作，必须同步提供 schema、来源规则、权限规则、可信事实、结果契约及后端测试；未知动作默认拒绝。全队业务场景覆盖要求不能由这两个动作的安全验收替代。

## 验收材料

本次含实际容器的全量测试为 233 项通过；工程演示 16/16，独立容器检查 8/8。最新准确结果以 `artifacts/test_report.txt`、`coverage_report.txt` 和 `reproducibility_manifest.json` 为准。工程演示在 `artifacts/engineering_demo.json`，容器证据在 `artifacts/engineering-acceptance-20260904/sandbox_acceptance.json`。

旧 Qwen 实验保持原样，本轮未新增 LLM 调用。工程测试不被解释为科研新方法证据。

安全自评使用官方模板，文件为 `artifacts/engineering-acceptance-20260904/MOSAIC_Guard_安全自评报告.docx`，队名和提交日期由队伍补充。自评仅覆盖本模块，不代表整套客服产品获得生产认证。

官方依据：[比赛主页及中国赛区赛题](https://fintechathon.g-ican.com/)、[安全自评模板](https://fintechathon.g-ican.com/templates/ai-security-self-assessment.docx)。2026-09-04 已保存网页资源及原模板；赛题列出安全自评，模板说明为可选，本次按准备该材料处理。
