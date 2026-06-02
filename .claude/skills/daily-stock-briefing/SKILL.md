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

1. **数据 + 市场画像** — 委派给 `market-analyst` 子智能体：
   - 它会 `finbot crawl` 并解读新闻，产出市场温度与主线板块。
2. **选股研判** — 委派给 `stock-picker` 子智能体：
   - 它会 `finbot predict` 拿候选榜，结合上一步的板块结论输出观察清单。
3. **策略建议** — 委派给 `strategy-advisor` 子智能体：
   - 它会 `finbot strategy --portfolio config/portfolio.json`，结合实仓与风控输出操作计划。
4. **汇总简报** — 把三份结果整合为一份 Markdown 简报，并提示用户：
   - 程序化完整报告已写入 `reports/report_<date>.md`（可直接 `finbot run` 生成）。

## 快捷方式
如果用户只想要程序化报告、不需要逐层 LLM 研判，直接：
```bash
finbot run --date <YYYY-MM-DD>
```
这会一次性产出候选榜、策略与 `reports/report_<date>.md`。

## 重要纪律
- 始终保留风险免责声明。
- 模型分数是相对排序，不是涨停概率，更不是保证。涨停不可被可靠预测。
- 三个子智能体的结论如有冲突（如选股看好但策略风控否决），以**风控优先**，并向用户说明分歧。
