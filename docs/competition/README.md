# FinPilot · 参赛材料入口

面向个人资金管理的可解释智能体：把账单、生活目标、资金安排和可核对的银行操作放在同一条流程里。当前为单用户比赛原型，使用持久化模拟银行；不连接真实银行或证券账户。

本版更新于 2026-10-04：修复评委体验发现的九类逻辑与交互问题，保持 430px 竖版透明 Liquid Glass。PPT、PDF、技术文档、演示脚本、验证报告和源码包使用同一实现版本。

## 材料

- [评审修复与验收](REVIEW-FIXES.md)：逐项问题、修复行为和验收边界。
- [技术文档](TECHNICAL.md)：场景、架构、数据口径、模型、反馈训练与恢复逻辑。
- [安全自评](SECURITY.md)：权限分级、攻击面、验证证据和已知风险。
- [工行杯财富管理服务适配评估](ICBC-WEALTH.md)：方向要求、现有实现、缺项、作品简介与提交条件。
- [演示脚本](DEMO.md)：8 分钟答辩与 4 分钟可选视频。
- [部署与复现](DEPLOYMENT.md)：源码、基础功能、本地模型和测试。
- [来源与许可](ATTRIBUTION.md)：原项目、安全包和模型的归属。
- [提交核对](SUBMISSION.md)：三人队伍、提交入口和未代填的信息。

答辩 [PPT](../../materials/FinPilot-presentation.pptx)、[PDF](../../materials/FinPilot-presentation.pdf) 和文档下载包集中在 [materials/](../../materials/README.md)，当前完整验证报告在 [VALIDATION.json](../../VALIDATION.json)。维护环境可用 `scripts/build_competition_materials.py` 根据本地验收证据生成；未通过发布门槛的模型不能写成上线成果。

2026-10-04 当前代码与材料整理至 [gr-peng/gonghang](https://github.com/gr-peng/gonghang)。仓库包含可运行源码和当前适配器，未代提交比赛平台。发布范围与复核结果见 [材料总览](../../materials/README.md)。
