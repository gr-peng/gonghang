# FinPilot · 财富管理服务参赛材料

作品改名为 FinPilot，工行方向调整为财富管理服务；保留竖版 Liquid Glass 和已评估的 finance-v4。本版补齐资产负债盘点、按需词条和情景试算，团队为两人。先阅读[方向对照与缺项](../docs/competition/ICBC-WEALTH.md)。

| 材料 | 用途 |
|---|---|
| [FinPilot-presentation.pptx](FinPilot-presentation.pptx) | 可编辑的 15 页答辩 PPT |
| [FinPilot-presentation.pdf](FinPilot-presentation.pdf) | 同版 PDF，适合直接预览 |
| [FinPilot-documents.zip](FinPilot-documents.zip) | 参赛文档与验证报告的下载包 |
| [参赛文档入口](../docs/competition/README.md) | 技术说明、安全自评、服务方案、演示脚本、提交核对 |
| [VALIDATION.json](../VALIDATION.json) | 2026-10-04 完整验收，以及有日期与边界说明的历史模型／安全内核结果 |
| [PUBLISH-VALIDATION.json](PUBLISH-VALIDATION.json) | 财富视图与理解支持、独立目录回归、布局、材料与线上检查 |
| [SNAPSHOT.json](SNAPSHOT.json) | 版本、来源与发布范围 |
| [SOURCE-MANIFEST.json](../SOURCE-MANIFEST.json) | 本仓库发布文件的 SHA-256 清单，不包含清单自身 |
| [SOURCE-MANIFEST.original.json](SOURCE-MANIFEST.original.json) | 整理前便携源码包的文件清单，用于追溯 |

源码已经展开在仓库中，直接使用 GitHub 的 **Code → Download ZIP** 或 `git clone` 获取，不在材料目录重复存放一份包含模型的源码压缩包。

PPT/PDF 已按财富管理服务重写定位，保留原版页面风格。文档包纳入本仓库更新后的导航与部署说明。原始 `scripts/build_competition_materials.py` 用于有完整本地验收记录的维护环境；它会检查 `.runtime/` 里的模型发布记录和浏览器证据，公开仓库不携带该私有运行目录。也可用保存的日期化报告和截图重新排版：

```bash
python -m pip install python-pptx
python scripts/build_competition_materials.py --validation VALIDATION.json --application-validation materials/PUBLISH-VALIDATION.json --screenshots materials/screenshots --presentation-only --output .runtime/presentation
```

这条命令不重跑模型或测试；不能把历史验证报告冒充新运行的结果。

finance-v4 是已部署并评估的适配器；本次整理没有重新训练。基座权重、私人账本、运行凭据及测试产生的状态没有随仓库发布。旧服务的实际访问入口由部署者单独维护。
