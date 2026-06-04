# finbot · A股因子化组合智能体（Skill + Agent 编排）

面向 A 股的量化研究助手：**本地数据仓库 → 经济先验因子（含跨资产宏观）→ 横截面前瞻收益
排序 → walk-forward 回测验证 → 风险约束的目标组合与调仓指令 → 每周调仓**。整条链路通过
**Claude Code 的 Skills + Agents** 编排，可一键程序化运行，也可让智能体逐层做新闻解读、
选股研判与风控复核。

> ⚠️ **重要声明**：本项目**不预测涨停、不保证收益**。它预测的是"未来一段持有期里股票的
> **相对强弱排序**"（相对排序分，非概率、非保证）。现实目标是**一个小而稳定的正信息系数
> (IC) + 良好的风险控制**，靠一篮子分散 + 长期累积，不是押中单只。所有输出均不构成投资
> 建议；市场有风险，决策与风险自负。设计原理见 [`docs/REDESIGN.md`](docs/REDESIGN.md)。

---

## 为什么这样设计（一句话）

> 把"个股未来相对强弱"分解成 **宏观 → 板块 → 个股** 多层、有经济因果的驱动，用**中性化**
> 提纯 alpha，用**含摩擦的 walk-forward 回测**验证，用**风险模型 + 换手控制**做成可执行的
> 动态组合；确定性计算 + Agent 判断 + 人工拍板。

完整推导见 `docs/REDESIGN.md`（含为何放弃"预测涨停"：信噪比差、反身性、且 T+1 + 封板下不可执行）。

## 架构总览

```
        ┌──────────── Claude Code 编排层 (.claude/) ────────────┐
        │ Agents: market-analyst(状态/新闻) stock-picker(选股研判) │
        │         strategy-advisor(组合/风控复核)                  │
        │ Skills: update-data analyze-market run-backtest          │
        │         speculate-watch daily-stock-briefing(编排)       │
        └───────────────────────────┬──────────────────────────────┘
                                     │ 通过 CLI 调用
   ┌──────────────────────── finbot (src/finbot) ────────────────────────┐
   │ data/warehouse ─▶ features/registry ─▶ labels ─▶ models/ranker        │
   │ 本地增量仓库      经济因子+中性化       前瞻收益    LightGBM排序        │
   │      │                                              │                  │
   │      └─▶ backtest (IC/分层/含T+1·封板·成本) ◀────────┘                  │
   │      regime(跨资产状态) ─▶ portfolio(风险模型+构建+调仓) ─▶ 报告        │
   └──────────────────────────────────────────────────────────────────────┘
```

## 快速开始

```bash
pip install -e .                 # 核心：pandas/numpy/pyyaml
pip install akshare lightgbm     # 可选：真实数据源 + 训练模型（缺失会自动回退）

cp config/config.example.yaml config/config.yaml          # 可选
cp config/portfolio.example.json config/portfolio.json    # 填你的真实持仓

# 一键端到端（mock 数据，离线可用）：自动建仓库→因子→排序→regime→目标组合→调仓指令→报告
finbot run --source mock --portfolio config/portfolio.json
#   → reports/portfolio_<date>.md  与  artifacts/<date>/10_portfolio.json

finbot run            # 真实数据（需联网 + akshare），默认今天
```

### 命令一览（也是各 Skill 的底层调用）

| 命令 | 作用 | 对应 Skill |
| ---- | ---- | ---- |
| `finbot update`        | 增量更新本地数据仓库（行情/基础/指数/宏观/新闻） | update-data |
| `finbot factors`       | 计算中性化因子面板 / `--catalog` 看因子目录 | analyze-market |
| `finbot build-dataset` | 因子 + 前瞻收益标签 → 训练/回测面板 | analyze-market |
| `finbot train`         | walk-forward 训练排序模型，报告样本外 IC | — |
| `finbot backtest`      | IC / 分层 / 含摩擦净值，验证有没有 edge | run-backtest |
| `finbot regime`        | 市场状态(risk-on/off)与板块倾斜 | (market-analyst) |
| `finbot construct`     | 结合实仓生成目标组合 + 调仓指令 | (strategy-advisor) |
| `finbot run`           | 串起全部 + 生成调仓简报 | daily-stock-briefing |
| `finbot speculate`     | (降级)高风险投机涨停观察池，仅情绪参考 | speculate-watch |

## 在 Claude Code 里怎么用

直接说：**"跑一下今天的A股分析"** 或 **"结合我的持仓给今天的调仓建议"**，触发
`daily-stock-briefing`，依次调度：

1. `market-analyst` —— `finbot update` + `finbot regime` + 新闻解读 → 市场状态与主线板块；
2. `stock-picker` —— 基于 `finbot run` 的因子排序榜 + `finbot backtest` 的 IC，叠加催化研判；
3. `strategy-advisor` —— `finbot construct` 结合你的 `portfolio.json` 与风控 → **目标组合 + 调仓指令**。

## 实盘怎么操作（要点）

- 系统给的不是"买 X"，而是**目标持仓权重 + 调仓清单**（BUY/SELL/TRIM/ADD）。
- **每周调一次仓**（默认 H=5），平时持有不动、只盯风控止损；调仓只**部分轮换**（缓冲带 + 换手上限）。
- 执行用**次日开盘**（尊重 T+1），**已封涨停的票跳过**（买不进）。
- 组合**成分与权重都动态轮动**，但渐进式新陈代谢，不是固定名单、也不是每期大洗牌。

## 因子与模型

- 因子家族（`finbot factors --catalog`）：价值、质量、动量、反转、低波、流动性、情绪、
  **跨资产宏观敏感度**（对人民币/商品/利率的滚动 beta × 趋势）。
- **中性化**：每日横截面 去极值→标准化→对行业+市值回归取残差，提纯相对选股能力。
- 模型：`LightGBM` 排序；未训练时回退**透明的因子合成分**，开箱即用。
- 训练：`finbot train` 走 walk-forward，报告样本外 Rank IC。

## 定时运行 / 部署

- 本地：`python scripts/daily_run.py --once`（配合系统 cron）。
- GitHub Actions：`.github/workflows/daily-briefing.yml`（交易日收盘后，默认 mock，可切 akshare）。
- **独立服务器（推荐）**：Docker + cron 的无人值守部署，含交易日守卫、Claude 智能体简报
  （`scripts/briefing_llm.py`）、以及企业微信/Server酱/Telegram 推送（`scripts/notify.py`）。
  完整步骤见 **[`docs/DEPLOY.md`](docs/DEPLOY.md)**；一键日任务入口 `scripts/daily_job.py`。

## 目录结构

```
src/finbot/
  data/        provider(数据源抽象) + warehouse(本地增量仓库)
  features/    registry(因子库 + 中性化 + 合成分)
  labels.py    前瞻收益标签 + 面板构建
  models/      ranker(LightGBM 排序 + 合成分回退)
  backtest/    metrics(IC/分层/绩效) + engine(walk-forward + 摩擦)
  portfolio/   risk(风险模型) + construct(目标组合) + account(实仓→调仓指令)
  regime.py    市场状态判定
  pipeline.py  端到端编排   cli.py  命令行   speculate.py  (降级)投机观察池
.claude/agents/  三个子智能体    .claude/skills/  技能
config/  示例配置与持仓模板    tests/  测试    docs/REDESIGN.md  设计文档
```

## 路线图（已完成 M1–M9）

- [x] M1 本地数据仓库 + 跨资产宏观数据
- [x] M2 前瞻收益标签 + 面板（防前视）
- [x] M3 经济先验因子库 + 中性化
- [x] M4 walk-forward 回测 + 因子验证（含 A股摩擦）
- [x] M5 LightGBM 排序模型（合成分回退）
- [x] M6 风险模型 + 目标组合构建 + 调仓指令
- [x] M7 市场 regime + 端到端组合管线 + Agent 重接
- [x] M8 涨停榜降级为投机观察池
- [x] M9 清理旧链路 + 文档
- [ ] 后续：接 Tushare 第二数据源、基本面数据补齐价值/质量因子、新闻情绪用 LLM 打分、北向资金替代信号细化

## 免责声明

仅供学习与量化研究。所有输出不构成投资建议；请遵守证券法规，理性投资。回测好 ≠ 未来赚钱，
上线前务必用模拟盘/小资金验证。
