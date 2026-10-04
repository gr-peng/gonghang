# Pro 返回代码整合与本机验收

日期：2026-09-06。当前工程 `/Users/gaojincheng/Desktop/比赛/MOSAIC_Guard_v0.1.0` 已整合返回包，代码版本 **0.1.1**，策略版本 **0.2.2**。目录名沿用原名，实际版本以 pyproject.toml、version.py 和策略文件为准。

## 来源与合并

输入为 `MOSAIC_Guard_security_fixed_20260906.zip`，SHA-256：`36f52aa0d4911da762018e4b26623206a20e7d98377369af87c2a6ab497e3ae7`。归档路径、重复项和符号链接检查通过，542 个声明文件哈希全部匹配。

按交给 Pro 的 2026-09-05 快照、本地当前工程、返回工程进行三方逐文件比较：本地没有漂移，没有合并冲突。审查后引入全部 27 个源码/测试/配置/脚本/文档变更文件及返回验收证据，共 105 个新增或替换文件。返回包没有删除原有测试；新增 28 个回归/故障测试用例。原始返回包解压副本保存在工程旁 `incoming_pro_20260906/`，本机整合前文件备份和合并记录在 `MOSAIC_Guard_integration_20260906/`；项目内也保存了 `artifacts/security-review-20260906/local-integration/merge_record.json`。

主要引入：实际派发前授权证据重验、同时间戳事实冲突拒绝、取消证明与状态原子提交、批准令牌绝对截止时间、熔断重置版本失效、执行后异常的 unknown/503 语义，以及来源 DAG、JSON/YAML、HTTP 慢请求和银行文件锁的资源边界。逐项原因见 Pro 原始审查报告 `28_INDEPENDENT_SECURITY_REVIEW_ZH.md`。

## 本机实际执行

Pro 环境的结果是 274 passed / 7 skipped，且只能分阶段复现；以下是本机重新执行的结果，不把历史输出冒充当前验收。

| 验证 | 本机结果 |
|---|---|
| 单次完整 make reproduce | 通过，summary.execution_mode = single_full_command |
| 全量 pytest | **281 passed，0 skipped** |
| coverage 下重复全量测试 | **281 passed，0 skipped；覆盖率 87%** |
| 真实 Docker 与 SMT | 已补齐 Pro 跳过的 4 个容器用例和 3 个 SMT 用例 |
| 运行时安全 / 执行一次 / 工程 demo | 10/10、10/10、16/16，回执重放通过 |
| 并发压力测试 | **25 个汇总检查全部通过**；线程和独立进程分别使用 1、2、8、16、32 个 worker |
| 请求与效果 | **512 次并发调用、266 个独立授权写入、266 次实际模拟银行写入**；全部终态成功，余额守恒，无额外效果 |
| 来源图验证 | 17/33/65/127 个节点分别只执行 17/33/65/127 次签名验证；下次调用篡改 root 被拒绝 |
| 固定种子 JSON | 2000 个正常输入接受，2000 个非法/不支持输入拒绝 |

原始本机压力结果及输出在 `artifacts/security-review-20260906/local-integration/`，完整测试与覆盖率在 `artifacts/test_report.txt`、`coverage_test_report.txt` 和 `coverage_report.txt`。压力测试 seed 为 20260906，运行目标始终为新建临时模拟部署；不是 HTTP 吞吐测试或生产 SLA 测量。

执行命令：

```bash
MOSAIC_SANDBOX_IMAGE=sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea \
  make PYTHON=.venv/bin/python reproduce
PYTHONPATH=src .venv/bin/python scripts/run_security_stress.py \
  --output artifacts/security-review-20260906/local-integration/stress-results.json
make PYTHON=.venv/bin/python manifest
make PYTHON=.venv/bin/python verify-bundle
```

本机完整测试需要回环 HTTP 和既有 Docker 的访问权限。压力驱动额外置于 180 秒全局有界子进程中，未触发超时。最终文件清单在加入本机压力结果和本文后重新生成并校验；最新计数以 `reproducibility_manifest.json` 与校验输出为准。

## 升级边界

本次整合的是代码和验收材料，没有启动或迁移真实部署。已有 worker 应停机审核后更新 `service.json.policy_sha256`，不得删除策略固定检查，不得重新初始化数据库、替换密钥或释放 consumed/started 历史。generation 语义收紧，旧 reset proof 需针对当前状态重新签署；旧批准令牌的处理按 `28_INDEPENDENT_SECURITY_REVIEW_ZH.md` 升级说明执行。

真实银行、登录/MFA、外部审计保管和长期存储容量验证仍属于外部集成或后续验证范围。本轮没有调用 LLM、GPU 或真实银行；测试结果不构成生产安全认证。
