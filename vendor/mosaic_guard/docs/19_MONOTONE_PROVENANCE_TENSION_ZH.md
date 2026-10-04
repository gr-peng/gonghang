# 单调安全与来源因果信号的可观测增益冲突

## 当前发现

完成签名来源链和真实模型 smoke 后，当前实验设计暴露出一个必须先解决的结构问题：

> 安全内核仍然成立，但在现有转账策略和风险映射下，来源因果检测器很可能没有可改变最终裁决的空间。

这不是代码错误，而是基础策略、provenance 语义和单调组合共同产生的结果。

## 最小例子

以 `to_account` 为例：

1. provenance 缺失或不可信时，策略配置为 `on_missing: deny`；
2. 若签名 lineage 完整地保留了非授权 RAG 祖先，参数契约把基础风险升为 `red`；
3. 当前 `FieldCausalGuard` 对非授权来源控制 `target` 或 `amount` 同样只产生 `red`；
4. 最终风险是 `max(base, detector)`。

因此：

```text
lineage 缺失：       max(deny, red/deny) = deny
非授权 lineage 已知：max(red, red)       = red
合法 delegation：    detector 应保持 green
```

在前两种危险情形中，加入 detector 后的最终裁决与强 provenance 基线相同。若实验只报告 detector 自己的命中率或内部信号，而最终义务、可执行集合和危险提议都没有变化，就不能宣称系统安全性获得了增量提升。

## 不能采用的修补

- 不能把缺失 provenance 从 `deny` 降为可执行等级来制造 detector 的提升空间；这会破坏 fail-closed 契约。
- 不能故意丢弃已经可验证的 RAG 祖先来制造“来源洗白”。这属于 provenance 实现缺陷，不是科学问题。
- 不能只把 detector 命中计为成功，而忽略最终义务没有变化。
- 不能按个案调整阈值、风险等级或规则。

## 保持安全契约的候选研究出口

### A. 检验 `red -> deny` 的真实增量

若干预标签足够可靠，可以让来源因果信号对高置信的非授权 `target` / `amount` 控制输出 `deny`，而强 provenance 基线仍为 `red`。此时真正检验的是：额外拒绝是否减少用户即使经过确认/MFA仍可能执行的诱导动作，并且是否通过 authorization-equivalent benign controls。

这不改变 `final_level >= base_level`，但风险量化规则必须在 calibration 前统一冻结，不能逐案例设置。

### B. 把主要贡献移到授权前的行为控制

来源因果信号可用于定位并抑制导致危险字段的内部路径，降低 LLM 产生危险 `ActionProposal` 的概率；确定性内核继续作为最终后盾。主要指标改为危险提议率、授权效果保持、其他危险动作替代效应和控制成本，而不是把规则拦截本身算作神经方法收益。

这条路线与现有 Prompt-Path / SI-FAC 更接近，但必须保留无干预、普通 prompt 防御和仅 provenance 基线。

### C. 聚焦当前外部规则尚未覆盖的动作级控制

当前参数契约检查字段来源，但没有独立的 `action_type` provenance binding。来源因果检测器可以对非授权来源改变动作类型输出 `deny`。这可能形成一个较窄但裁决可观测的增量问题。

### D. 将内部信号限定为审计或人工分流工具

若 A/B/C 均不成立，来源因果方法仍可用于事故定位、红队回放和人工审核排序，但不应作为在线授权方法的主要论文贡献。

## 下一步决策

在批量生成 100–300 个 base scenarios 前，应先用一个小型真实模型 pilot 区分：

1. Qwen3.8-27B 是否会在多来源冲突中产生来源驱动的受保护字段变化；
2. 强 lineage/provenance 基线对应的最终等级是什么；
3. detector 是否能改变最终义务或危险提议，而不只是产生一条重复的 `red` 信号；
4. authorization-equivalent 变体是否同样被升级。

如果第二和第三项表明 detector 对最终行为没有增量作用，应优先转向 B 或停止在线 NeSy 主张，而不是扩大数据规模。
