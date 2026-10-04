# 部署与复现

以下命令供维护者或评委复现；使用现有服务器网站的用户只需专用打开链接。不要把远程服务器 localhost 地址交给本地电脑浏览器。

## 基础启动

需要 Python 3.11+；静态前端不需要 Node 构建。核心依赖在 `AI_accounting_agent/backend/requirements-core.txt`；测试在 `tests/requirements.txt`。

```bash
python run.py --demo
```

首次安装核心依赖，启动独立示例账本、原记账/投研服务和同源网页代理。示例源标记为 `synthetic_demo`。默认关闭 AI，可检查账本、图表、个人计划、银行模拟和安全确认；对话 AI 应显示未配置，不能假装已调用模型。

源码包的 README 使用可移植配置说明，移除了服务器私网地址。包中不含 `.env`、运行目录、私有账本、金融会话、实际动态码密钥或入口凭据。保留原投研源码；若没有相应历史数据，界面给出空态，不伪造实时行情。

## 本地微调模型

实际验证环境为 Linux / Python 3.11 / NVIDIA A40，本地训练与服务依赖列于 `requirements-training.txt`。基座从 [Qwen 官方模型](https://huggingface.co/Qwen/Qwen3-4B) 获取，保留其 Apache 许可；基座权重不放入源码包。

```bash
python -m pip install -r AI_accounting_agent/backend/requirements-training.txt
python scripts/build_finance_corpus.py --output artifacts/my-finance-run --version my-finance-run
CUDA_VISIBLE_DEVICES=0 python scripts/train_accounting_lora.py --base Qwen3-4B --data artifacts/my-finance-run --output artifacts/my-finance-run/adapter --initial-adapter artifacts/accounting-v2/adapter --epochs 2 --learning-rate 0.00003 --evaluate-all
python scripts/model_release.py prepare --artifact artifacts/my-finance-run --version qingcai-qwen3-4b-my-finance-run
```

`build_finance_corpus.py` 保留原记账划分，并按银行模板和汇总场景拆分。没有批准反馈时，反馈行数为零。实际本轮使用的隔离演示反馈、分项结果和版本应看生成报告；复现者不应把演示反馈标为真实用户反馈。

使用原 v2 起点需要对应适配器；源码包独立保存原合成记账语料。没有原适配器时可以去掉 `--initial-adapter` 从基座训练，结果不等同本轮复现，必须重新完整评估。当前源码包另含已批准的 v4 适配器、训练/留出数据和生成评估；仅重定位基座/数据路径字段，权重和科学结果不变。取得相同基座后，可直接运行下列完整复核，无需先重训：

```bash
CUDA_VISIBLE_DEVICES=0 python scripts/evaluate_accounting_adapter.py --artifact artifacts/finance-v4
```

复核会重新生成完整测试，并更新本地评测与元数据；随后重新登记该本地副本的发布记录。不要复用另一台服务器的绝对路径或登记文件。

服务使用 `.env.example` 配置 `LLM_PROVIDER=api`、回环 `LLM_BASE_URL`、准确模型名和 `ACCOUNTING_MODEL_SERVER=true`。配置基座、适配器、模型名和对应发布记录；发布记录的哈希不符或未通过完整评测，服务拒绝加载。

已批准的真实结构化反馈可用 `--feedback-db` 导出；同意和审核是发布前条件。`--feedback-fixture` 仅用于明确标注的隔离开发者演示，不能混称真实反馈。

## 版本发布与回滚

```bash
python scripts/model_release.py promote --version qingcai-qwen3-4b-my-finance-run
python scripts/model_release.py rollback
```

真实反馈版本需追加 `--feedback-database` 并复核同意，记录用于哪个版本。上述命令只更新本地配置和登记，随后维护者重启**本项目**启动器并检查模型健康及网页接口。账本、安全策略和账户余额不参与模型回滚；不按端口批量终止其他项目。

## 验证

```bash
python -m pip install -r tests/requirements.txt
python -m pytest -q tests/test_backend.py tests/test_accounting_v2.py tests/test_upstream_data.py tests/test_remote_access.py tests/test_finance_workspace.py tests/test_bank_import.py tests/test_learning_loop.py tests/test_model_release.py tests/test_finance_planning.py tests/test_judge_fixes.py
node tests/visuals.test.mjs
node tests/navigation.test.mjs
node tests/chart_geometry.test.mjs
```

Chromium 全流程验收为 `tests/e2e_finance_workspace.py`：需要维护者已有私网入口配置，创建隔离副本和临时端口，经两个真实代理验收。设置 `E2E_FINANCE_REAL_MODEL=1` 才是真实模型流程；不设置时不能声称模型验收通过。已安装的 Chromium 可通过 `PLAYWRIGHT_EXECUTABLE_PATH` 指定。

安全内核复现按 `vendor/mosaic_guard/README.md` / `AGENTS.md` 原流程。Docker 集成有显式开关，必须报告跳过项。原包未进行真实模型调用的测试，不能当作真实 LLM 攻击统计。

## 当前服务器保活

当前服务器已安装 `qingcai-app.service` 和 `qingcai-access.service` 用户服务，启用故障重启、开机启动和当前用户后台保活。原专用地址与 IP/凭据限制未变。用户服务配置属于该服务器，不能把其中绝对路径或凭据复制到公开材料。

服务器重启后自动启动已配置，但没有为了验收重启整台共享服务器；故障恢复用本项目进程单独验证。生产 HTTPS、正式银行登录和多人账户不是本轮原型范围，见安全自评。

## 评委体验修复的部署检查

账单编辑使用 PUT，网站同源代理和外层入口均须保留该方法。重启本项目托管启动器后核对账本健康、finance-v4 加载、研究数据质量接口。目标进度表采用增加列的兼容迁移，旧记录归入 legacy 目标；新目标经用户选择才归零。部署前备份账本和工作区 SQLite，不能用测试状态覆盖线上。

`tests/test_judge_fixes.py` 使用临时账本覆盖多笔拦截、完整性条件、目标隔离/撤销、只读测算、账单并发编辑、区间上下文和报告事实。`tests/e2e_judge_fixes.py` 只接受指定的隔离端口，需先用账本副本启动三个服务；禁止改成生产地址运行。浏览器全流程结果、截图和代码哈希收录在本轮验证报告；早期模型和安全内核结果单独保留来源日期。
