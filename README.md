# finbot · A股智能体工具（Skill + Agent 编排骨架）

面向 A 股市场的自动化研究助手：**定时爬取财经新闻与历史交易数据 → 提取因子特征 →
用模型给出"今日最可能走强/涨停"的相对排序 → 结合用户实仓与风控生成操作计划 →
产出每日简报**。整条链路通过 **Claude Code 的 Skills + Agents** 编排，既能一键程序化
运行，也能让智能体逐层做新闻解读与研判。

> ⚠️ **重要声明**：没有任何模型能"可靠地"预测哪些股票会涨停。涨停由资金博弈、市场情绪
> 与突发消息驱动，本质上不可被稳定预测。本项目是一个**量化研究 / 辅助决策框架**，输出的
> 分数是**当日横截面的相对排序**，不是概率保证，**不构成投资建议**。市场有风险，决策与
> 风险由使用者自行承担。

---

## 架构总览

```
                    ┌─────────────────────────────────────────────┐
                    │      Claude Code 编排层 (.claude/)           │
                    │                                              │
  daily-stock-      │  Agents:  market-analyst  → 市场画像/新闻解读 │
  briefing (skill)  │           stock-picker    → 候选研判         │
        │           │           strategy-advisor→ 风控操作计划      │
        ▼           │  Skills:  crawl-market-data / analyze-market │
   编排三个agent ───▶│           predict-limitup / build-strategy   │
                    └───────────────────────┬─────────────────────┘
                                            │ 通过 CLI 调用
                                            ▼
   ┌──────────────────────── finbot (src/finbot) ─────────────────────────┐
   │ data ──▶ features ──▶ models ──▶ strategy ──▶ pipeline ──▶ report     │
   │ 爬取     因子工程     涨停排序    组合风控     编排         Markdown报告 │
   │ (AkShare / mock 回退)         (LightGBM/规则)  (实仓+风控)              │
   └──────────────────────────────────────────────────────────────────────┘
```

- **确定性的 Python 管线**（`src/finbot`）负责数据、因子、模型、风控等可复现的计算，
  每个阶段把结构化结果写到 `artifacts/<date>/*.json`。
- **Claude Code 智能体**（`.claude/agents`）在结构化结果之上叠加判断：解读新闻催化、
  剔除"有分无逻辑"的票、以风控优先制定计划。
- **Skills**（`.claude/skills`）是可复用的能力封装，把每个 CLI 阶段暴露给智能体或你直接调用。

## 快速开始

```bash
# 1) 安装
pip install -e .                 # 核心：pandas/numpy/pyyaml
pip install akshare lightgbm     # 可选：真实数据源 + 训练模型（缺失会自动回退）

# 2) 配置（可选；不配也能用示例 + mock 跑通）
cp config/config.example.yaml config/config.yaml
cp config/portfolio.example.json config/portfolio.json   # 填入你的真实持仓

# 3) 一键跑完整流程（mock 数据，离线可用）
finbot run --date 2026-06-02 --source mock
#   → reports/report_2026-06-02.md  与  artifacts/2026-06-02/*.json

# 4) 真实数据（需联网 + akshare）
finbot run            # 默认今天，数据源取 config 的 akshare
```

### 分阶段命令（也是各 Skill 的底层调用）

| 命令 | 作用 | 对应 Skill |
| ---- | ---- | ---- |
| `finbot crawl`    | 爬行情快照 + 财经新闻 | crawl-market-data |
| `finbot features` | 构建因子特征矩阵      | analyze-market |
| `finbot predict`  | 模型打分，输出候选榜  | predict-limitup |
| `finbot strategy` | 结合实仓与风控出计划  | build-strategy |
| `finbot run`      | 串起全部 + 生成报告   | daily-stock-briefing |

## 在 Claude Code 里怎么用

直接对 Claude 说：**"跑一下今天的A股分析"** 或 **"结合我的持仓给今天的策略"**。
它会触发 `daily-stock-briefing` 技能，依次调度：

1. `market-analyst` —— `finbot crawl` 后解读新闻，给出市场温度与主线板块；
2. `stock-picker` —— `finbot predict` 拿候选榜，叠加板块/催化研判出观察清单；
3. `strategy-advisor` —— `finbot strategy` 结合你的 `config/portfolio.json` 与风控出操作计划。

## 因子与模型

- 因子家族（`config.yaml` 的 `features.*` 开关）：技术面、资金面、情绪面、板块联动、涨停基因。
- 模型：`LightGBM` 排序器；未训练时回退**透明的规则打分**，开箱即用。
- 训练自己的模型：用 `finbot.features.build_features` 跑历史多日构建带标签
  （标签 = 次日是否涨停）的数据集，调用 `LimitUpRanker.train(X, y)`，模型存到
  `models_store/limitup_lgbm.txt` 后自动启用。

## 定时运行

- 本地/服务器：`python scripts/daily_run.py --once`（建议配合系统 cron）。
- GitHub Actions：`.github/workflows/daily-briefing.yml` 已配置交易日收盘后定时运行
  （默认 mock，可在 dispatch 时切 `akshare`），报告作为 artifact 上传。

## 目录结构

```
src/finbot/         核心管线：data / features / models / strategy / pipeline / cli
.claude/agents/     三个子智能体定义
.claude/skills/     五个技能（四个能力 + 一个编排）
config/             示例配置与持仓模板（真实文件 git 忽略）
scripts/daily_run.py 定时运行入口
tests/              端到端冒烟测试（mock 数据）
```

## 路线图

- [ ] 接入 Tushare 作为第二数据源（已预留 `DataProvider` 抽象）
- [ ] 因子库扩充（龙虎榜、北向资金、连板梯队）
- [ ] 回测框架与因子有效性评估（IC / 分层回测）
- [ ] 真实新闻情绪用 LLM 打分替代关键词
- [ ] 与 lab_bot 基础设施集成（通知 / 调度 / 持久化）

## 免责声明

本项目仅供学习与量化研究。所有输出均不构成投资建议，作者不对任何使用本工具产生的
投资结果负责。请遵守所在地证券法规，理性投资。
