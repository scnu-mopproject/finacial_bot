"""finbot command-line interface.

This is the surface the Claude Code Skills call. Every subcommand prints a JSON
summary to stdout (so an Agent can parse it) and writes richer artifacts under
``artifacts/<date>/``.

Examples
--------
    finbot crawl     --date 2026-06-02
    finbot features  --date 2026-06-02
    finbot predict   --date 2026-06-02 --top 20
    finbot strategy  --date 2026-06-02 --portfolio config/portfolio.json
    finbot run       --date 2026-06-02          # full pipeline + report
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Optional

from . import __version__
from .config import load_config
from . import pipeline
from .strategy import Portfolio


def _print_json(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--date", default=None, help="trading date YYYY-MM-DD (default: today)")
    p.add_argument("--config", default=None, help="path to an extra config file to overlay")
    p.add_argument("--source", default=None, choices=["akshare", "mock"], help="override data source")


def _resolve(args):
    cfg = load_config(args.config)
    if getattr(args, "source", None):
        cfg.raw.setdefault("data", {})["source"] = args.source
    date = args.date or pipeline.today_str()
    return cfg, date


def cmd_update(args) -> int:
    """Incrementally update the local data warehouse (design §4 / M1)."""
    from .data import Warehouse, get_provider

    cfg, date = _resolve(args)
    wh = Warehouse(
        root=str(cfg.path("data.warehouse_dir")),
        benchmark=cfg.get("data.benchmark", "000985"),
    )
    if args.status:
        _print_json({"warehouse": str(cfg.path("data.warehouse_dir")), "tables": wh.status()})
        return 0
    provider = get_provider(cfg.get("data.source", "akshare"), cfg.get("data.fallback_to_mock", True))
    counts = wh.update(
        asof=date,
        provider=provider,
        history_days=int(cfg.get("data.history_days", 250)),
        incremental_days=int(cfg.get("data.incremental_days", 10)),
    )
    _print_json({"date": date, "data_source": provider.name, "ingested_rows": counts, "tables": wh.status()})
    return 0


def cmd_build_dataset(args) -> int:
    """Build the labelled factor panel from the warehouse (design §5+§6 / M2+M3)."""
    from .data import Warehouse
    from .features.registry import build_factor_panel
    from .labels import build_dataset

    cfg, _ = _resolve(args)
    wh = Warehouse(root=str(cfg.path("data.warehouse_dir")), benchmark=cfg.get("data.benchmark", "000985"))
    h = args.h or int(cfg.get("model.holding_period_days", 5))
    features = None if args.labels_only else build_factor_panel(wh)
    panel = build_dataset(wh, h=h, features=features,
                          out_dir=str(cfg.path("data.warehouse_dir").parent / "datasets"))
    feat_cols = [c for c in panel.columns if c not in
                 ("date", "code", "sector", "fwd_ret", "bench_fwd_ret", "excess_ret")]
    summary = {
        "horizon_h": h,
        "labelled_rows": int(len(panel)),
        "n_factor_cols": len(feat_cols),
        "date_range": [str(panel["date"].min()), str(panel["date"].max())] if not panel.empty else [],
        "excess_ret_mean": float(panel["excess_ret"].mean()) if not panel.empty else None,
        "note": "factors neutralized; label = forward H-day excess return; T+1, no look-ahead.",
    }
    _print_json(summary)
    return 0


def cmd_factors(args) -> int:
    """Inspect the factor catalog or compute the latest neutralized factor panel."""
    from .data import Warehouse
    from .features.registry import build_factor_panel, registry_view

    cfg, _ = _resolve(args)
    if args.catalog:
        _print_json(registry_view().to_dict(orient="records"))
        return 0
    wh = Warehouse(root=str(cfg.path("data.warehouse_dir")), benchmark=cfg.get("data.benchmark", "000985"))
    panel = build_factor_panel(wh)
    if panel.empty:
        _print_json({"error": "empty factor panel; run `finbot update` first"})
        return 1
    last = panel[panel["date"] == panel["date"].max()]
    _print_json({"date": str(panel["date"].max()), "n_stocks": int(len(last)),
                 "factors": [c for c in panel.columns if c not in ("date", "code", "sector")]})
    return 0


def cmd_construct(args) -> int:
    """Build the target portfolio and rebalance orders from current holdings (M6)."""
    from .data import Warehouse
    from .features.registry import build_factor_panel
    from .models import Ranker
    from .portfolio import Portfolio, build_target_portfolio, rebalance_orders

    cfg, _ = _resolve(args)
    wh = Warehouse(root=str(cfg.path("data.warehouse_dir")), benchmark=cfg.get("data.benchmark", "000985"))
    panel = build_factor_panel(wh)
    if panel.empty:
        _print_json({"error": "empty factor panel; run `finbot update` first"})
        return 1
    ranker = Ranker(store_dir=str(cfg.path("model.store_dir")))
    scored = ranker.rank(panel, top_n=10_000)  # score the whole latest cross-section

    # current holdings -> weights
    pf = Portfolio.from_file(args.portfolio) if args.portfolio else (
        Portfolio.from_file(cfg.path("strategy.portfolio_file"))
        if cfg.path("strategy.portfolio_file").exists() else Portfolio.empty())
    equity = pf.equity or 1.0
    prev_weights = {p.code: p.market_value / equity for p in pf.positions}

    target = build_target_portfolio(
        scored, prev_weights=prev_weights,
        n_holdings=int(cfg.get("strategy.n_holdings", 12)),
        enter_pct=float(cfg.get("strategy.enter_pct", 0.15)),
        hold_pct=float(cfg.get("strategy.hold_pct", 0.30)),
        max_total_exposure=float(cfg.get("strategy.risk.max_total_exposure", 0.90)),
        max_position_pct=float(cfg.get("strategy.risk.max_position_pct", 0.15)),
        max_turnover=float(cfg.get("strategy.max_turnover", 0.30)),
    )

    # reference prices / names from the warehouse (latest close + basics)
    bars = wh.bars()
    prices = (bars.sort_values("date").groupby("code")["close"].last().to_dict()
              if not bars.empty else {})
    basics = wh.basics()
    names = (basics.sort_values("date").groupby("code")["name"].last().to_dict()
             if not basics.empty and "name" in basics.columns else {})
    orders = rebalance_orders(pf, target["weights"], prices=prices, names=names)

    _print_json({
        "date": str(panel["date"].max()),
        "using_model": ranker.using_model,
        "target": target,
        "rebalance_orders": [o for o in orders if o["action"] != "HOLD"],
        "disclaimer": "程序化调仓建议，仅供研究参考，不构成投资建议；执行用次日开盘并跳过封板标的。",
    })
    return 0


def cmd_train(args) -> int:
    """Walk-forward train the ranking model and report OOS IC (design §7 / M5)."""
    from .data import Warehouse
    from .features.registry import build_factor_panel
    from .labels import attach_labels, forward_returns
    from .models import Ranker

    cfg, _ = _resolve(args)
    wh = Warehouse(root=str(cfg.path("data.warehouse_dir")), benchmark=cfg.get("data.benchmark", "000985"))
    h = args.h or int(cfg.get("model.holding_period_days", 5))
    panel = build_factor_panel(wh)
    if panel.empty:
        _print_json({"error": "empty factor panel; run `finbot update` first"})
        return 1
    panel = attach_labels(panel, forward_returns(wh, h=h))
    ranker = Ranker(store_dir=str(cfg.path("model.store_dir")))
    result = ranker.train_walkforward(panel)
    _print_json(result)
    return 0


def cmd_backtest(args) -> int:
    """Walk-forward backtest + factor validation (design §8 / M4)."""
    from .data import Warehouse
    from .features.registry import build_factor_panel, composite_score
    from .labels import attach_labels, forward_returns
    from .backtest import backtest_signal

    cfg, _ = _resolve(args)
    wh = Warehouse(root=str(cfg.path("data.warehouse_dir")), benchmark=cfg.get("data.benchmark", "000985"))
    h = args.h or int(cfg.get("model.holding_period_days", 5))
    panel = build_factor_panel(wh)
    if panel.empty:
        _print_json({"error": "empty factor panel; run `finbot update` first"})
        return 1
    panel["score"] = composite_score(panel)             # transparent baseline signal
    panel = attach_labels(panel, forward_returns(wh, h=h))
    result = backtest_signal(
        panel, wh=wh,
        n_holdings=int(cfg.get("strategy.n_holdings", 12)),
        rebalance_days=int(cfg.get("strategy.rebalance_days", 5)),
    )
    _print_json(result)
    return 0


def cmd_crawl(args) -> int:
    cfg, date = _resolve(args)
    out = pipeline.stage_crawl(date, cfg)
    _print_json({k: v for k, v in out.items() if k not in ("news", "universe_preview")})
    return 0


def cmd_features(args) -> int:
    cfg, date = _resolve(args)
    feats = pipeline.stage_features(date, cfg)
    _print_json({"date": date, "n_rows": int(len(feats)), "columns": list(feats.columns)})
    return 0


def cmd_predict(args) -> int:
    cfg, date = _resolve(args)
    if args.top:
        cfg.raw.setdefault("model", {})["top_n"] = args.top
    candidates = pipeline.stage_predict(date, cfg)
    _print_json({"date": date, "candidates": candidates.to_dict(orient="records")})
    return 0


def cmd_strategy(args) -> int:
    cfg, date = _resolve(args)
    portfolio: Optional[Portfolio] = None
    if args.portfolio:
        portfolio = Portfolio.from_file(args.portfolio)
    plan = pipeline.stage_strategy(date, cfg, portfolio=portfolio)
    _print_json(plan)
    return 0


def cmd_run(args) -> int:
    """Full end-to-end portfolio flow (design §3-§10): warehouse -> factors ->
    rank -> regime -> target portfolio -> rebalance orders -> report."""
    from .portfolio import Portfolio

    cfg, date = _resolve(args)
    portfolio = Portfolio.from_file(args.portfolio) if args.portfolio else None
    summary = pipeline.run_portfolio(date, cfg, portfolio=portfolio)
    _print_json(summary)
    return 0


def cmd_regime(args) -> int:
    """Classify the market regime (design §10)."""
    from .data import Warehouse
    from . import regime as regime_mod

    cfg, _ = _resolve(args)
    wh = Warehouse(root=str(cfg.path("data.warehouse_dir")), benchmark=cfg.get("data.benchmark", "000985"))
    _print_json(regime_mod.classify(wh))
    return 0


def cmd_run_legacy(args) -> int:
    cfg, date = _resolve(args)
    _print_json(pipeline.run_daily(date, cfg))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="finbot", description="A股智能体工具 CLI")
    parser.add_argument("--version", action="version", version=f"finbot {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="verbose logging")
    sub = parser.add_subparsers(dest="command", required=True)

    p_update = sub.add_parser("update", help="增量更新本地数据仓库（行情/基础/指数/宏观/新闻）")
    _add_common(p_update)
    p_update.add_argument("--status", action="store_true", help="只显示仓库现状，不拉取")
    p_update.set_defaults(func=cmd_update)

    p_ds = sub.add_parser("build-dataset", help="构建带因子的前瞻收益面板（因子+标签，用于训练/回测）")
    _add_common(p_ds)
    p_ds.add_argument("--h", type=int, default=None, help="持有期/前瞻天数 H（默认取配置，5）")
    p_ds.add_argument("--labels-only", action="store_true", help="只建标签，不算因子")
    p_ds.set_defaults(func=cmd_build_dataset)

    p_fac = sub.add_parser("factors", help="查看因子目录(--catalog)或计算最新中性化因子面板")
    _add_common(p_fac)
    p_fac.add_argument("--catalog", action="store_true", help="只打印因子目录与经济逻辑")
    p_fac.set_defaults(func=cmd_factors)

    p_con = sub.add_parser("construct", help="构建目标组合并生成调仓指令（结合实仓）")
    _add_common(p_con)
    p_con.add_argument("--portfolio", default=None, help="持仓 JSON 文件路径")
    p_con.set_defaults(func=cmd_construct)

    p_tr = sub.add_parser("train", help="walk-forward 训练排序模型并报告样本外 IC")
    _add_common(p_tr)
    p_tr.add_argument("--h", type=int, default=None, help="持有期 H（默认取配置，5）")
    p_tr.set_defaults(func=cmd_train)

    p_bt = sub.add_parser("backtest", help="walk-forward 回测 + 因子验证(IC/分层/含摩擦净值)")
    _add_common(p_bt)
    p_bt.add_argument("--h", type=int, default=None, help="持有期 H（默认取配置，5）")
    p_bt.set_defaults(func=cmd_backtest)

    p_crawl = sub.add_parser("crawl", help="爬取行情快照 + 财经新闻")
    _add_common(p_crawl)
    p_crawl.set_defaults(func=cmd_crawl)

    p_feat = sub.add_parser("features", help="构建特征矩阵")
    _add_common(p_feat)
    p_feat.set_defaults(func=cmd_features)

    p_pred = sub.add_parser("predict", help="运行模型，输出涨停概率候选榜")
    _add_common(p_pred)
    p_pred.add_argument("--top", type=int, default=None, help="返回前 N 名")
    p_pred.set_defaults(func=cmd_predict)

    p_strat = sub.add_parser("strategy", help="结合实仓与风控生成策略")
    _add_common(p_strat)
    p_strat.add_argument("--portfolio", default=None, help="持仓 JSON 文件路径")
    p_strat.set_defaults(func=cmd_strategy)

    p_regime = sub.add_parser("regime", help="判定市场状态(risk-on/neutral/risk-off)与板块倾斜")
    _add_common(p_regime)
    p_regime.set_defaults(func=cmd_regime)

    p_run = sub.add_parser("run", help="完整跑一遍：数据->因子->排序->regime->目标组合->调仓指令->报告")
    _add_common(p_run)
    p_run.add_argument("--portfolio", default=None, help="持仓 JSON 文件路径")
    p_run.set_defaults(func=cmd_run)

    p_legacy = sub.add_parser("run-legacy", help="(弃用)旧涨停链路，M9 将移除")
    _add_common(p_legacy)
    p_legacy.set_defaults(func=cmd_run_legacy)

    return parser


def main(argv: Optional[list] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        return args.func(args)
    except Exception as exc:  # noqa: BLE001 - surface a clean error to the agent
        _print_json({"error": str(exc), "type": type(exc).__name__})
        return 1


if __name__ == "__main__":
    sys.exit(main())
