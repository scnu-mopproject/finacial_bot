# CLAUDE.md — finbot 项目工作指引

给在本仓库工作的 Claude Code 会话的指引。

## 这是什么
面向 A 股的量化研究助手：本地数据仓库 → 经济先验因子(含跨资产宏观) → 横截面**前瞻收益排序**
→ walk-forward 回测 → 风险约束的目标组合与**调仓指令** → 每周调仓。Python 确定性管线 +
Claude Code 的 Skills/Agents 编排。设计原理见 `docs/REDESIGN.md`。

## 关键约定
- **数据源**：AkShare（免费，无 token）；任何失败回退确定性 mock，离线/CI 也能跑。命令加 `--source mock` 强制离线。
- **数据仓库**：`finbot update` 一次下载、每日增量到 `data/warehouse/`（已 gitignore），后续从本地秒级读。**这是回测的前提**。
- **入口是 CLI**（`src/finbot/cli.py`）：`update / factors / build-dataset / train / backtest / regime / construct / run / speculate`。Skills 是对这些命令的封装。改了管线先用 CLI 自测。
- **中间产物** `artifacts/<date>/*.json`、**数据集** `data/datasets/`、**报告** `reports/portfolio_<date>.md`（均 gitignore）。
- **绝不提交**：真实数据(`data/`,`artifacts/`)、`config/config.yaml`、`config/portfolio.json`、模型(`models_store/`)。模板用 `*.example.*`。

## 改代码时
- 新增因子：在 `src/finbot/features/registry.py` 写 `_build_*` 风格逻辑、登记到 `FACTOR_REGISTRY`（注明 family/direction/经济逻辑）；它会自动进入中性化与合成分。**跨资产因子务必防前视**（只用决策时点前已发生的宏观数据）。新因子必须在 `finbot backtest` 中证明**增量 IC**，否则删除。
- 新增数据源（如 Tushare）：在 `src/finbot/data/provider.py` 加 `DataProvider` 子类并在 `get_provider` 注册；仓库层只面向 provider 接口。
- 改完跑 `python -m pytest -q`（mock 冒烟，无需联网）。

## 不可逾越的纪律（产品价值观）
- 模型输出是**横截面相对排序分**，不是涨停、不是概率、**更不是保证**。所有面向用户的文案都必须如实说明。
- 涨停不可被可靠预测；不得生成"必涨/高准确率预测"之类表述。涨停观察池(`speculate`)是**降级的投机参考**，不进入组合主链路。
- 风控线（止损/止盈/单仓上限/行业上限/总敞口/换手上限）是**硬约束**；风险约束优先于换手平滑。
- 一切信号以**含摩擦(T+1/封板/成本)的 walk-forward 回测**验证；无回测不下结论。
- 所有策略/组合输出都要带免责声明，不承诺收益；回测好≠未来赚钱。

## 实盘节奏
- 默认**每周调一次仓**（H=5，`strategy.rebalance_days`）；产物是目标权重 + 调仓清单（次日开盘、跳过封板）。
- 组合成分与权重**动态轮动**，但靠缓冲带(进15%/留30%)+换手上限渐进新陈代谢。

## 智能体分工
- `market-analyst`：`finbot regime` + 新闻 → 市场状态与跨资产解读（不选具体票）。
- `stock-picker`：在因子排序榜 + 回测 IC 上叠加逻辑/催化研判（不碰仓位）。
- `strategy-advisor`：`finbot construct` 结合实仓与风控出调仓计划（风控优先，可否决）。
- `daily-stock-briefing` 技能把三者串起来。
