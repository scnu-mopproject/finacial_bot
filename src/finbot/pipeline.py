"""End-to-end portfolio orchestration (design §3-§10).

Flow: warehouse (auto-backfill) -> neutralized factor panel -> ranking model ->
market regime -> target portfolio (regime modulates exposure) -> rebalance
orders vs live holdings -> Markdown report + JSON artifact.

The legacy limit-up flow was removed in M9; the speculative limit-up watch now
lives in ``finbot.speculate`` and is intentionally off the portfolio chain.
"""
from __future__ import annotations

import json
import logging
from datetime import date as _date
from pathlib import Path
from typing import Dict, Optional

import pandas as pd

from .config import Config, REPO_ROOT, load_config

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


def run_portfolio(date: Optional[str] = None, cfg: Optional[Config] = None,
                  portfolio=None) -> Dict:
    """warehouse -> factors -> rank -> regime -> target portfolio -> orders -> report."""
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
