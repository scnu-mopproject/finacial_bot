# finbot 部署指南（独立服务器 · Docker + Claude 智能体 + webhook 推送）

把 finbot 部署成一个**每个交易日收盘后自动跑一次、产出调仓简报并推送到你手机**的无人值守任务。
它不是常驻 Web 服务，而是 cron 驱动的批处理。

---

## 0. 关键前置

- **服务器位置 / 网络**：AkShare 多为国内接口（东方财富/新浪/财联社）。**建议用国内云（阿里云/腾讯云等）、
  直连、不要挂全局代理**；海外服务器或全局 VPN 可能连不上行情接口。
- **配置**：2 vCPU / 4–8GB 内存、20–40GB 盘足够。
- **不自动交易**：系统只产**调仓建议**，下单永远人工确认。

---

## 1. 准备代码与密钥

```bash
git clone <your-repo> /srv/finbot && cd /srv/finbot

# 业务配置（git 忽略，仅放服务器）
cp config/config.example.yaml   config/config.yaml
cp config/portfolio.example.json config/portfolio.json   # 填你的真实持仓
chmod 600 config/portfolio.json

# 部署密钥与推送渠道
cp .env.example .env            # 填 ANTHROPIC_API_KEY、推送 webhook 等
chmod 600 .env

mkdir -p reports logs
```

`.env` 关键项（详见文件内注释）：
- `FINBOT_SOURCE=akshare`
- `FINBOT_WITH_LLM=1` + `ANTHROPIC_API_KEY=...`（启用 Claude 简报；不需要可设 0）
- `FINBOT_NOTIFY=wecom|serverchan|telegram` + 对应渠道的 webhook/token

## 2. 构建镜像

```bash
docker compose build
```

## 3. 首次回填 + 冒烟测试

首次会回填全市场历史（较慢，仅一次；之后每天增量很快）。先用 mock 验证链路，再切真实数据：

```bash
# 离线冒烟（不联网、不需要 API key）：跑通 update->run->(LLM)->notify
docker compose run --rm -e FINBOT_SOURCE=mock -e FINBOT_SKIP_TRADING_GUARD=1 finbot

# 真实数据首次回填（联网，可能数十分钟）
docker compose run --rm -e FINBOT_SKIP_TRADING_GUARD=1 finbot
```

产物：`reports/portfolio_<date>.md`（程序化）或 `reports/briefing_<date>.md`（Claude 简报），
中间结果在持久卷 `finbot_data` 的 `artifacts/<date>/`。

## 4. 定时调度（host cron）

```bash
mkdir -p /srv/finbot/logs
# 编辑路径后安装；交易日 15:40 运行，非交易日由任务内部跳过
crontab deploy/crontab.example
```

> cron 不认节假日，但 `daily_job.py` 内置交易日历守卫（akshare 可用时按真实交易日，
> 否则退化为按工作日）。需要强制运行可设 `FINBOT_SKIP_TRADING_GUARD=1`。

---

## 5. 数据持久化与备份（重要）

- **数据仓库 `finbot_data` 卷是最宝贵的状态**（增量积累的历史 + artifacts）。容器可随时重建，
  这个卷不能丢。定期备份：
  ```bash
  docker run --rm -v finbot_finbot_data:/data -v $PWD:/backup alpine \
    tar czf /backup/finbot_data_$(date +%F).tgz -C /data .
  ```
- `config/`、`.env`、`reports/` 以宿主机目录挂载，随机器备份即可。
- **绝不提交**：`config/config.yaml`、`config/portfolio.json`、`.env`、`data/`、`models_store/`
  （均已在 `.gitignore` / `.dockerignore`）。

---

## 6. 两种部署形态对比

| | 纯量化管线（FINBOT_WITH_LLM=0） | + Claude 智能体（=1） |
|---|---|---|
| 依赖 | 无需 API key | 需 ANTHROPIC_API_KEY + 外网到 api.anthropic.com |
| 产物 | `portfolio_<date>.md`（表格化调仓） | `briefing_<date>.md`（融合三角色研判的中文简报） |
| 成本 | 仅服务器 | + Claude API 调用费 |
| 稳定性 | 最稳 | 简报失败会自动回退到程序化报告 |

建议先用纯管线跑稳，再开 LLM 简报。

## 7. 训练模型（可选）

容器内可定期（如每月）重训排序模型；模型存到 `finbot_models` 卷后下次运行自动启用：
```bash
docker compose run --rm finbot sh -c "finbot update && finbot train"
```
> 训练前先 `finbot backtest` 确认因子有 edge；样本外 IC 赢不过合成分就别用（防过拟合）。

## 8. 排错

- **akshare 连接失败**：检查服务器是否挂了代理 / 是否海外；`docker compose run --rm finbot sh -c "finbot regime --source akshare -v"`。
- **简报没推送**：看 `logs/daily.log`；确认 `.env` 的 `FINBOT_NOTIFY` 与对应密钥；先 `python scripts/notify.py --file reports/xxx.md` 单测。
- **任务被跳过**：多半是非交易日；测试加 `FINBOT_SKIP_TRADING_GUARD=1`。

---

## 免责声明
本系统仅供研究学习，所有输出不构成投资建议。回测/模拟 ≠ 未来收益，上线前请用模拟盘/小资金验证；
下单务必人工确认，切勿接入自动交易。
