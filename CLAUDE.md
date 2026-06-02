# CLAUDE.md — finbot 项目工作指引

给在本仓库工作的 Claude Code 会话的指引。

## 这是什么
面向 A 股的研究助手：爬新闻+行情 → 因子工程 → 涨停概率排序 → 结合实仓的风控策略 →
每日简报。Python 确定性管线 + Claude Code 的 Skills/Agents 编排。

## 关键约定
- **数据源**：AkShare（免费，无 token）；任何失败都回退到确定性 mock，所以离线/CI 也能跑。
  跑命令时加 `--source mock` 可强制离线演示。
- **入口是 CLI**：`finbot {crawl,features,predict,strategy,run}`，定义在 `src/finbot/cli.py`。
  Skills 就是对这些命令的封装。改了管线先用 CLI 自测。
- **中间产物**写到 `artifacts/<date>/*.json`（已 gitignore）；阶段之间靠这些 JSON/parquet 传递。
- **报告**写到 `reports/report_<date>.md`（已 gitignore，仅 `.gitkeep` 入库）。
- **绝不提交**：真实数据(`data/`,`artifacts/`)、`config/config.yaml`、`config/portfolio.json`、
  训练好的模型(`models_store/`)。模板用 `*.example.*`。

## 改代码时
- 新增因子：在 `src/finbot/features/engineering.py` 加 `_build_xxx`，接入 `build_features`，
  再把列名加进 `src/finbot/models/limitup.py` 的 `FEATURE_COLUMNS`。
- 新增数据源（如 Tushare）：在 `src/finbot/data/provider.py` 加 `DataProvider` 子类，
  在 `get_provider` 注册。上层不应感知数据源差异。
- 改完跑 `python -m pytest -q`（mock 冒烟测试，无需联网）。

## 不可逾越的纪律（产品价值观）
- 模型输出是**相对排序分**，不是涨停概率，**更不是保证**。所有面向用户的文案都必须如实说明。
- 涨停不可被可靠预测；不得生成任何"必涨/高准确率预测"之类的表述。
- 策略层的风控线（止损/止盈/单仓上限/总敞口）是**硬约束**。
- 所有策略/选股输出都要带免责声明，不承诺收益。

## 智能体分工
- `market-analyst`：市场环境与新闻解读（不选具体票）。
- `stock-picker`：在模型候选榜上叠加逻辑/催化研判（不碰仓位）。
- `strategy-advisor`：结合实仓与风控出操作计划（风控优先，可否决选股）。
- `daily-stock-briefing` 技能负责把三者串起来。
