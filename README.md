# FinPilot · 可解释财富管理智能体

面向工行杯「财富管理服务」方向，将账单核对、现金流、目标预留、风险约束、方案比较和模拟操作连接起来。详见[赛道适配评估与待补强项](docs/competition/ICBC-WEALTH.md)。

本仓库收录 **2026-10-04 FinPilot 财富管理版**：19 个页面，所有屏幕保持最大 430px 的竖版布局，统一透明 Liquid Glass；包含评委体验修复、已微调的 finance-v4 LoRA 适配器，以及同版本答辩材料。当前是现金流驱动的个人资金管理原型；完整资产负债、按需术语解释和真实用户理解验证列为待补强项。

[答辩 PPT](materials/FinPilot-presentation.pptx) · [PDF 预览](materials/FinPilot-presentation.pdf) · [参赛文档](docs/competition/README.md) · [部署说明](docs/competition/DEPLOYMENT.md) · [版本与验证](materials/README.md)

## 当前功能

- **记账与纠错**：文字提取、手动录入、CSV 预览校正、重复识别、原 ID 编辑与并发保护。
- **收支分析**：月／年／自定义区间、趋势、分类、同期对比、筛选小计，选定区间可以带入助手。
- **资金规划**：从账本现金流核对生活开销、还款、应急金和目标预留；目标独立记录，进度可撤销，期限追问只读测算。
- **投资研究**：历史自选、行情、组合与报告；异常 OHLC 数据隔离，报告使用可归因事实。
- **模拟银行**：转账、申购、赎回，经参数确认、TOTP、去重和回执核对后执行。
- **模型与反馈**：Qwen3-4B + LoRA；用户同意、结构化修正、人工审核、离线评估、发布与回滚。

这是单用户比赛原型，银行本金与操作均为模拟；历史行情不代表实时市场。当前未启用 OCR，没有接入真实银行或证券交易。模型和测试数据以合成为主，结果不是通用能力或真实收益的证明。

## 目录

| 路径 | 内容 |
|---|---|
| [AI_accounting_agent/frontend/liquid-glass/](AI_accounting_agent/frontend/liquid-glass) | 当前使用的网页、样式、导航和图表 |
| [AI_accounting_agent/backend/](AI_accounting_agent/backend) | 记账、投研、资金规划、银行模拟与反馈后端 |
| [run.py](run.py)、[scripts/](scripts) | 启动、同源代理、数据合成、训练、评估与发布工具 |
| [artifacts/finance-v4/](artifacts/finance-v4) | 当前 LoRA 权重、分词器、训练／验证／测试语料与评估 |
| [artifacts/accounting-v2/](artifacts/accounting-v2) | 基础记账合成语料 |
| [tests/](tests) | 后端、前端及隔离浏览器验收 |
| [docs/competition/](docs/competition) | 技术文档、演示脚本、安全自评、提交核对 |
| [materials/](materials) | PPT、PDF、文档压缩包、发布验证与文件清单 |
| [vendor/mosaic_guard/](vendor/mosaic_guard) | 保留原归属与 MIT 许可的操作安全内核 |
| [THIRD_PARTY/](THIRD_PARTY) | 基座模型许可 |

仓库沿用当前应用的源码路径，避免整理目录改变导入、资源和启动逻辑。旧页面目录仅保留对齐测试需要的原始问题素材，当前网站入口在 `frontend/liquid-glass/`。

## 本机体验与部署

需要 Python 3.11+。基础体验不需要 Node 构建、GPU 或模型下载：

```bash
git clone https://github.com/gr-peng/gonghang.git
cd gonghang
python run.py --demo
```

启动器创建虚拟环境、安装核心依赖，启动独立演示账本、两个后端及同源网页代理。**在运行代码的这台电脑上**打开 `http://localhost:5500`。如果代码运行在远程服务器，浏览器必须使用该服务器配置的受控入口，不能把服务器的 localhost 当成本机地址。

基础模式默认关闭 AI，保留可手动操作的功能。要启用当前微调模型，参见[本地模型部署、评估与发布](docs/competition/DEPLOYMENT.md#本地微调模型)。仓库包含 finance-v4 适配器，Qwen3-4B 基座需另行获取；运行配置由 `.env.example` 复制后在自己的环境填写。

## 验证

整理发布前，在本仓库目录再次运行后端回归、前端检查与隔离启动检查；结果见 [materials/PUBLISH-VALIDATION.json](materials/PUBLISH-VALIDATION.json)。此前线上对应版本的完整验收记录见 [VALIDATION.json](VALIDATION.json)：104 项应用回归、11 组评审浏览器流程、11 组银行浏览器流程，以及 19 页 × 3 种宽度布局检查。

```bash
python -m pip install -r AI_accounting_agent/backend/requirements-core.txt -r tests/requirements.txt
python -m pytest -q tests/test_backend.py tests/test_accounting_v2.py tests/test_upstream_data.py tests/test_remote_access.py tests/test_finance_workspace.py tests/test_bank_import.py tests/test_learning_loop.py tests/test_model_release.py tests/test_finance_planning.py tests/test_judge_fixes.py
node tests/navigation.test.mjs
node tests/chart_geometry.test.mjs
node tests/visuals.test.mjs
```

浏览器资金流程须在隔离账本运行；历史真实模型验收与本次发布检查分别记录。当前模型的合成开发留出结果为记账 174/174、银行意图 76/79、画像依据 64/64；本次整理未重新训练。

## 材料与来源

答辩材料共 13 页，配套 8 分钟演示脚本。可直接下载 PPT 修改、在 GitHub 预览 PDF，或阅读[材料总览](materials/README.md)。发布源码和材料不等于提交比赛；成员、学校与正式模板由参赛团队核对。

源自 [FinTechathon](https://github.com/gr-peng/FinTechathon)。原项目、MOSAIC Guard 和 Qwen 的归属分别保留，详见[来源与许可](docs/competition/ATTRIBUTION.md)。运行时账本、环境密钥、访问凭据、会话和基座权重不进入本仓库。
