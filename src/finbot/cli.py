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
    """Build the labelled forward-return panel from the warehouse (design §6 / M2)."""
    from .data import Warehouse
    from .labels import build_dataset

    cfg, _ = _resolve(args)
    wh = Warehouse(root=str(cfg.path("data.warehouse_dir")), benchmark=cfg.get("data.benchmark", "000985"))
    h = args.h or int(cfg.get("model.holding_period_days", 5))
    panel = build_dataset(wh, h=h, out_dir=str(cfg.path("data.warehouse_dir").parent / "datasets"))
    summary = {
        "horizon_h": h,
        "labelled_rows": int(len(panel)),
        "date_range": [str(panel["date"].min()), str(panel["date"].max())] if not panel.empty else [],
        "excess_ret_mean": float(panel["excess_ret"].mean()) if not panel.empty else None,
        "note": "label = forward H-day excess return vs benchmark; T+1 entry, no look-ahead.",
    }
    _print_json(summary)
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
    cfg, date = _resolve(args)
    summary = pipeline.run_daily(date, cfg)
    _print_json(summary)
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

    p_ds = sub.add_parser("build-dataset", help="从数据仓库构建前瞻收益标签面板（用于训练/回测）")
    _add_common(p_ds)
    p_ds.add_argument("--h", type=int, default=None, help="持有期/前瞻天数 H（默认取配置，5）")
    p_ds.set_defaults(func=cmd_build_dataset)

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

    p_run = sub.add_parser("run", help="完整跑一遍：爬取->特征->预测->策略->报告")
    _add_common(p_run)
    p_run.set_defaults(func=cmd_run)

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
