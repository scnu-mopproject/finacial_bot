---
name: analyze-market
description: 构建A股因子特征矩阵。当需要从原始行情/新闻计算技术面、资金面、情绪面、板块联动、涨停基因等因子时使用。是选股模型的输入准备步骤。
---

# 因子特征工程

把原始数据转成模型可用的特征矩阵。底层是 `finbot.features.build_features`。

## 用法
```bash
finbot features --date 2026-06-02            # 需先 crawl 过
finbot features --date 2026-06-02 --source mock
```

## 因子家族（可在 config.yaml 的 `features.*` 开关）
- **technical**：5/20日动量、波动率、均线多头排列、量比、距60日高点。
- **capital_flow**：成交额、换手率、活跃资金代理。
- **sentiment**：所属板块的新闻情绪均值与新闻条数。
- **board_linkage**：板块整体强度（联动/共振）。
- **limitup_genes**：近20日涨停次数、当日是否涨停等"涨停基因"。

## 产物
- `artifacts/<date>/_features.parquet`：每行一只股票的特征向量。
- `artifacts/<date>/02_features.json`：行数与列名摘要。

## 扩展
新增因子：在 `finbot/features/engineering.py` 写 `_build_xxx` 并接入 `build_features`，
再把列名加进 `finbot/models/limitup.py` 的 `FEATURE_COLUMNS`。
