---
name: speculate-watch
description: (降级功能)高风险投机涨停观察池，仅作情绪/题材参考。当用户明确想看"今天哪些票最热/可能冲涨停"时使用，但必须强调这是投机参考、不是预测、不进入组合主链路。
---

# 投机涨停观察池（已降级）

> 重构后**不再把"预测涨停"当作核心目标**——它信噪比极差、反身性强，且 T+1+封板下
> 多数不可稳定成交。本技能仅保留一个**明确标注风险**的情绪观察榜。

## 用法
```bash
finbot speculate --top 15
finbot speculate --top 15 --source mock
```

## 输出
`heat`（动量/关注度代理）排序的观察榜，含 `pct_chg / turnover_rate / up_streak / sealed_today`。
- `heat` **不是涨停概率**，只是"近期强度+换手+连阳"的合成关注度。
- `sealed_today=true` 表示当日已接近涨停，**通常买不进**。

## 纪律（必须随输出传达）
- 这是**高风险投机参考**，不是预测、不是建议、不承诺收益。
- 它**不进入**组合优化主链路（factors → rank → construct）。
- 严肃投资请用主链路：`finbot run` 的目标组合 + 调仓指令，并由 `strategy-advisor` 风控复核。
