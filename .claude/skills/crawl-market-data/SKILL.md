---
name: crawl-market-data
description: 爬取A股行情快照与财经新闻。当需要获取当日全市场行情、个股历史K线或财经新闻（东方财富/新浪/财联社）作为分析输入时使用。底层调用 finbot 的 AkShare 数据层，无数据源时自动回退到确定性 mock 数据。
---

# 爬取行情与新闻

获取分析所需的原始数据。底层是 `finbot.data`（AkShare，缺失时回退 mock）。

## 用法
```bash
# 爬取指定日期的全市场快照 + 当日财经新闻
finbot crawl --date 2026-06-02

# 演示/离线环境用确定性 mock 数据
finbot crawl --date 2026-06-02 --source mock
```

## 产物
- stdout：JSON 摘要（数据源、universe 数量、新闻数量）。
- `artifacts/<date>/01_raw.json`：完整新闻列表 + 行情快照预览。
- `artifacts/<date>/_universe.parquet`、`_news.parquet`：供后续阶段复用的全量数据。

## 数据字段
- 快照(universe)：`code, name, sector, close, pct_chg, amount_yi, turnover_rate, is_st, limit_up`
- 新闻(news)：`datetime, title, sector, polarity, source`

## 注意
- AkShare 为免费公开接口，可能限频或字段变动；失败时若 `data.fallback_to_mock` 为真会自动回退。
- 不要把爬取的原始数据提交进 git（`data/`、`artifacts/` 已在 .gitignore）。
- 切换 Tushare：在 `finbot.data.provider` 新增一个 `DataProvider` 子类即可，上层无需改动。
