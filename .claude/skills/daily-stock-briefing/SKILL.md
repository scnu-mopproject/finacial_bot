---
name: daily-stock-briefing
description: 一键生成A股每日观察简报。当用户说"今天有什么票""跑一下今日分析""生成每日简报""分析今日行情"时使用。它编排 market-analyst → stock-picker → strategy-advisor 三个子智能体，串起"爬数据→市场画像→选股研判→策略建议"的完整流程，并产出最终简报。
---

# 每日A股观察简报（编排技能）

这是把整条流水线串起来的**总编排技能**。它本身不做分析，而是按顺序调度三个子智能体，并在它们之间传递结构化结果。

## 何时使用
用户想要一份"今天的完整分析/简报"，或想跑通端到端流程时。

## 编排步骤

> 默认日期为今天；如用户指定日期，用 `--date YYYY-MM-DD` 贯穿全程。
> 没有真实数据源时加 `--source mock` 也能完整演示。

1. **数据 + 市场状态** — 委派给 `market-analyst` 子智能体：
   - 它会 `finbot update` + `finbot regime` 并解读新闻，产出市场状态(risk-on/off)、跨资产解读与主线板块。
2. **因子选股研判** — 委派给 `stock-picker` 子智能体：
   - 它基于 `finbot run` 的因子排序榜 + `finbot backtest` 的 IC，叠加催化研判输出观察清单。
3. **组合与调仓** — 委派给 `strategy-advisor` 子智能体：
   - 它会 `finbot construct --portfolio config/portfolio.json`，结合实仓/风控/regime 输出**目标组合 + 调仓指令**。
4. **汇总简报** — 整合三份结果为一份 Markdown 简报，并提示：
   - 程序化报告已写入 `reports/portfolio_<date>.md`。

## 快捷方式
只想要程序化报告、不需要逐层 LLM 研判，直接：
```bash
finbot run --date <YYYY-MM-DD> --portfolio config/portfolio.json
```
一次性产出 regime、目标组合、调仓指令与 `reports/portfolio_<date>.md`。

## 重要纪律
- 始终保留风险免责声明。
- 模型分数是相对排序，不是涨停概率，更不是保证。涨停不可被可靠预测。
- 三个子智能体的结论如有冲突（如选股看好但策略风控否决），以**风控优先**，并向用户说明分歧。
