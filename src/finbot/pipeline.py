"""Daily end-to-end orchestration.

Stages: ingest -> filter universe -> features -> rank -> strategy -> report.
Each stage writes a JSON artifact under ``artifacts/<date>/`` so the Claude Code
Agents can pick up structured intermediate results and add interpretation.

The functions are intentionally small and composable so a Skill can invoke just
one stage (e.g. only ``crawl`` or only ``predict``) via the CLI.
"""
from __future__ import annotations

import json
import logging
from datetime import date as _date
from pathlib import Path
from typing import Dict, Optional

import pandas as pd

from .config import Config, REPO_ROOT, load_config
from .data import get_provider
from .features import build_features
from .models import LimitUpRanker
from .strategy import Portfolio, build_strategy

log = logging.getLogger(__name__)


def today_str() -> str:
    return _date.today().strftime("%Y-%m-%d")


def _artifacts_dir(date: str) -> Path:
    d = REPO_ROOT / "artifacts" / date
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_json(date: str, name: str, payload) -> Path:
    path = _artifacts_dir(date) / name
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    log.info("wrote %s", path)
    return path


# --- universe filtering --------------------------------------------------
def filter_universe(universe: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    uni = universe.copy()
    if cfg.get("data.universe.exclude_st", True) and "is_st" in uni.columns:
        uni = uni[~uni["is_st"].fillna(False)]
    min_amt = cfg.get("data.universe.min_amount_yi", 0.5)
    if "amount_yi" in uni.columns:
        uni = uni[pd.to_numeric(uni["amount_yi"], errors="coerce").fillna(0) >= min_amt]
    return uni.reset_index(drop=True)


# --- individual stages ---------------------------------------------------
def stage_crawl(date: str, cfg: Config) -> Dict:
    provider = get_provider(
        cfg.get("data.source", "akshare"), cfg.get("data.fallback_to_mock", True)
    )
    universe = provider.universe(date)
    news = provider.news(date)
    payload = {
        "date": date,
        "data_source": provider.name,
        "n_universe": int(len(universe)),
        "n_news": int(len(news)),
        "news": news.to_dict(orient="records"),
        "universe_preview": universe.head(50).to_dict(orient="records"),
    }
    _write_json(date, "01_raw.json", payload)
    # Persist full frames for the next stages (parquet falls back to csv).
    _dump_frame(date, "universe", universe)
    _dump_frame(date, "news", news)
    return payload


def stage_features(date: str, cfg: Config) -> pd.DataFrame:
    provider = get_provider(
        cfg.get("data.source", "akshare"), cfg.get("data.fallback_to_mock", True)
    )
    universe = _load_frame(date, "universe")
    news = _load_frame(date, "news")
    if universe is None:
        universe = provider.universe(date)
        news = provider.news(date)
    universe = filter_universe(universe, cfg)
    feats = build_features(provider, universe, news, date, cfg)
    _dump_frame(date, "features", feats)
    _write_json(
        date, "02_features.json",
        {"date": date, "n_rows": int(len(feats)), "columns": list(feats.columns)},
    )
    return feats


def stage_predict(date: str, cfg: Config) -> pd.DataFrame:
    feats = _load_frame(date, "features")
    if feats is None:
        feats = stage_features(date, cfg)
    ranker = LimitUpRanker(
        model_type=cfg.get("model.type", "lightgbm"),
        store_dir=str(cfg.path("model.store_dir")),
    )
    candidates = ranker.rank(
        feats,
        top_n=int(cfg.get("model.top_n", 20)),
        min_score=float(cfg.get("model.min_score", 0.0)),
    )
    _dump_frame(date, "candidates", candidates)
    _write_json(
        date, "03_candidates.json",
        {
            "date": date,
            "model_type": cfg.get("model.type"),
            "using_trained_model": ranker._model is not None,  # noqa: SLF001
            "candidates": candidates.to_dict(orient="records"),
            "note": "score = 相对排序分(0-1)，非校准概率，更非保证。仅作研究观察池。",
        },
    )
    return candidates


def stage_strategy(date: str, cfg: Config, portfolio: Optional[Portfolio] = None) -> Dict:
    candidates = _load_frame(date, "candidates")
    if candidates is None:
        candidates = stage_predict(date, cfg)
    if portfolio is None:
        pf_path = cfg.path("strategy.portfolio_file")
        portfolio = Portfolio.from_file(pf_path) if pf_path.exists() else Portfolio.empty()
    plan = build_strategy(
        candidates,
        portfolio,
        risk=cfg.get("strategy.risk", {}),
        style=cfg.get("strategy.style", "balanced"),
    )
    plan["date"] = date
    _write_json(date, "04_strategy.json", plan)
    return plan


# --- full run + report ---------------------------------------------------
def run_daily(date: Optional[str] = None, cfg: Optional[Config] = None) -> Dict:
    date = date or today_str()
    cfg = cfg or load_config()
    log.info("=== finbot daily run for %s ===", date)
    raw = stage_crawl(date, cfg)
    stage_features(date, cfg)
    candidates = stage_predict(date, cfg)
    plan = stage_strategy(date, cfg)
    report_path = write_report(date, cfg, raw, candidates, plan)
    return {
        "date": date,
        "data_source": raw["data_source"],
        "n_candidates": int(len(candidates)),
        "report": str(report_path),
        "artifacts_dir": str(_artifacts_dir(date)),
    }


def run_portfolio(date: Optional[str] = None, cfg: Optional[Config] = None,
                  portfolio=None) -> Dict:
    """New end-to-end flow (design §3-§10 / M7): warehouse -> factors -> rank ->
    regime -> target portfolio -> rebalance orders -> report."""
    from .data import Warehouse, get_provider
    from .features.registry import build_factor_panel
    from .models import Ranker
    from . import regime as regime_mod
    from .portfolio import Portfolio, build_target_portfolio, rebalance_orders

    date = date or today_str()
    cfg = cfg or load_config()
    wh = Warehouse(root=str(cfg.path("data.warehouse_dir")), benchmark=cfg.get("data.benchmark", "000985"))

    # ensure the warehouse has data (auto-backfill on first run)
    if wh.watermark("daily_bar") is None:
        provider = get_provider(cfg.get("data.source", "akshare"), cfg.get("data.fallback_to_mock", True))
        wh.update(date, provider=provider, history_days=int(cfg.get("data.history_days", 250)),
                  incremental_days=int(cfg.get("data.incremental_days", 10)))

    panel = build_factor_panel(wh)
    if panel.empty:
        return {"error": "empty factor panel; run `finbot update` first"}

    ranker = Ranker(store_dir=str(cfg.path("model.store_dir")))
    scored = ranker.rank(panel, top_n=10_000)
    reg = regime_mod.classify(wh)

    if portfolio is None:
        pf_path = cfg.path("strategy.portfolio_file")
        portfolio = Portfolio.from_file(pf_path) if pf_path.exists() else Portfolio.empty()
    equity = portfolio.equity or 1.0
    prev_weights = {p.code: p.market_value / equity for p in portfolio.positions}

    # regime modulates total exposure (risk-off -> hold more cash)
    exposure = min(float(cfg.get("strategy.risk.max_total_exposure", 0.90)),
                   float(reg.get("suggested_exposure", 0.90)))
    target = build_target_portfolio(
        scored, prev_weights=prev_weights,
        n_holdings=int(cfg.get("strategy.n_holdings", 12)),
        enter_pct=float(cfg.get("strategy.enter_pct", 0.15)),
        hold_pct=float(cfg.get("strategy.hold_pct", 0.30)),
        max_total_exposure=exposure,
        max_position_pct=float(cfg.get("strategy.risk.max_position_pct", 0.15)),
        max_turnover=float(cfg.get("strategy.max_turnover", 0.30)),
    )

    bars = wh.bars()
    prices = bars.sort_values("date").groupby("code")["close"].last().to_dict() if not bars.empty else {}
    basics = wh.basics()
    names = (basics.sort_values("date").groupby("code")["name"].last().to_dict()
             if not basics.empty and "name" in basics.columns else {})
    orders = rebalance_orders(portfolio, target["weights"], prices=prices, names=names)

    payload = {"date": date, "using_model": ranker.using_model, "regime": reg,
               "target": target, "orders": orders}
    _write_json(date, "10_portfolio.json", payload)
    report_path = write_portfolio_report(date, cfg, reg, target, orders, scored, ranker.using_model)
    return {"date": date, "regime": reg["regime"], "n_holdings": len(target["weights"]),
            "n_orders": len([o for o in orders if o["action"] != "HOLD"]),
            "report": str(report_path), "artifacts_dir": str(_artifacts_dir(date))}


def write_portfolio_report(date, cfg, reg, target, orders, scored, using_model) -> Path:
    out_dir = cfg.path("report.output_dir")
    out_dir.mkdir(parents=True, exist_ok=True)
    rebalance_days = cfg.get("strategy.rebalance_days", 5)
    lines = [
        f"# A股组合调仓简报 · {date}",
        "",
        f"- 市场状态: **{reg['regime']}** (score {reg['score']}) | 建议敞口 {reg['suggested_exposure']:.0%} | 因子侧重: {reg['factor_emphasis']}",
        f"- 板块倾斜: {', '.join(reg['sector_tilt']) or '—'}",
        f"- 排序信号: {'已训练模型' if using_model else '因子合成分(未训练模型)'} | 调仓周期: 每 {rebalance_days} 交易日",
        "",
        "## 一、目标组合",
        "",
        f"目标持仓 {len(target['weights'])} 只 | 现金缓冲 {target['cash']:.1%} | 本次换手 {target['turnover']:.1%}",
        "",
        "| 代码 | 板块 | 目标权重 |",
        "| ---- | ---- | ---- |",
    ]
    sec = dict(zip(scored["code"], scored.get("sector", pd.Series(dtype=str))))
    for code, w in sorted(target["weights"].items(), key=lambda kv: -kv[1]):
        lines.append(f"| {code} | {sec.get(code, '')} | {w:.1%} |")
    lines += ["", "## 二、调仓指令（次日开盘执行，跳过封板标的）", "",
              "| 动作 | 代码 | 名称 | 当前→目标 | 金额 | 约股数 |",
              "| ---- | ---- | ---- | ---- | ---- | ---- |"]
    actionable = [o for o in orders if o["action"] != "HOLD"]
    for o in actionable:
        lines.append(f"| **{o['action']}** | {o['code']} | {o['name']} | "
                     f"{o['current_weight']:.1%}→{o['target_weight']:.1%} | "
                     f"{o['delta_value']:+,.0f} | {o.get('delta_shares')} |")
    if not actionable:
        lines.append("| — | — | — | 无需调仓 | — | — |")
    lines += [
        "", "---", "",
        "> 程序化调仓建议，仅供研究参考，**不构成投资建议**；排序分是相对排序而非涨停/收益保证。",
        "> 建议交由 `market-analyst`/`stock-picker`/`strategy-advisor` 智能体复核新闻与风控后，人工确认下单。",
    ]
    path = out_dir / f"portfolio_{date}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    log.info("wrote portfolio report %s", path)
    return path


def write_report(date: str, cfg: Config, raw: Dict, candidates: pd.DataFrame, plan: Dict) -> Path:
    out_dir = cfg.path("report.output_dir")
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# A股每日观察报告 · {date}",
        "",
        f"- 数据源: `{raw['data_source']}`  |  全市场快照: {raw['n_universe']} 只  |  新闻: {raw['n_news']} 条",
        "",
        "## 一、涨停概率候选榜 (Top)",
        "",
        "| 排名 | 代码 | 名称 | 板块 | 分数 | 主要驱动因子 |",
        "| ---- | ---- | ---- | ---- | ---- | ---- |",
    ]
    for i, c in enumerate(candidates.to_dict(orient="records"), 1):
        lines.append(
            f"| {i} | {c['code']} | {c.get('name','')} | {c.get('sector','')} | "
            f"{c['score']:.3f} | {c.get('drivers','')} |"
        )
    s = plan.get("summary", {})
    lines += [
        "",
        "## 二、策略建议",
        "",
        f"账户权益 ≈ {s.get('equity', 0):,.0f}  |  现金 {s.get('cash', 0):,.0f}  |  "
        f"持仓 {s.get('n_positions', 0)} 只  |  风格 {s.get('style', '')}  |  建议新开仓 {s.get('n_new_buys', 0)} 笔",
        "",
        "### 持仓管理",
    ]
    for a in plan.get("manage_existing", []):
        lines.append(f"- **{a['action']}** {a['code']} {a['name']} (盈亏 {a['pnl_pct']:.1%}) — {a['reason']}")
    if not plan.get("manage_existing"):
        lines.append("- (无持仓)")
    lines += ["", "### 新开仓建议"]
    for e in plan.get("new_entries", []):
        lines.append(
            f"- **BUY** {e['code']} {e['name']} ({e.get('sector','')}) — "
            f"分数 {e['score']:.3f}，建议仓位 {e['suggested_pct']:.1%} (≈{e['suggested_amount']:,.0f})"
        )
    if not plan.get("new_entries"):
        lines.append("- (今日无满足风控的新开仓)")
    lines += [
        "",
        "---",
        "",
        "> " + plan.get("disclaimer", ""),
        "",
        "> 本报告由 finbot 程序化生成，建议交由 Claude Code 的 `market-analyst` / "
        "`stock-picker` / `strategy-advisor` 智能体进一步解读新闻催化与逻辑后再决策。",
    ]
    path = out_dir / f"report_{date}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    log.info("wrote report %s", path)
    return path


# --- frame persistence helpers ------------------------------------------
def _frame_path(date: str, name: str, ext: str) -> Path:
    return _artifacts_dir(date) / f"_{name}.{ext}"


def _dump_frame(date: str, name: str, df: pd.DataFrame) -> None:
    try:
        df.to_parquet(_frame_path(date, name, "parquet"), index=False)
    except Exception:  # noqa: BLE001 - pyarrow may be absent; csv is always available
        df.to_csv(_frame_path(date, name, "csv"), index=False)


def _load_frame(date: str, name: str) -> Optional[pd.DataFrame]:
    pq = _frame_path(date, name, "parquet")
    csv = _frame_path(date, name, "csv")
    if pq.exists():
        try:
            return pd.read_parquet(pq)
        except Exception:  # noqa: BLE001
            pass
    if csv.exists():
        return pd.read_csv(csv, dtype={"code": str})
    return None
