#!/usr/bin/env python3
"""Unattended daily job for the deployment server (driven by cron).

Pipeline:  trading-day guard -> finbot update -> finbot run -> (optional) LLM
briefing -> push report to chat webhook.

Each stage is isolated: a failure is logged and reported but the job exits with a
non-zero code so cron / your monitoring can alert. Designed to run inside the
Docker container with volumes mounted for config/, data/, reports/.

Env toggles:
  FINBOT_SOURCE=akshare|mock     data source (default akshare)
  FINBOT_SKIP_TRADING_GUARD=1    run even on non-trading days (for testing)
  FINBOT_WITH_LLM=1              also generate the Claude briefing (needs ANTHROPIC_API_KEY)
  FINBOT_NOTIFY / channel envs   see scripts/notify.py
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))   # finbot package
sys.path.insert(0, str(REPO_ROOT))           # scripts.* (notify, briefing_llm)

log = logging.getLogger("finbot.daily")


def is_trading_day(date: str) -> bool:
    """Best-effort A-share trading-day check.

    Uses the akshare trade calendar when available; otherwise falls back to a
    weekday check (can't see holidays offline, but avoids weekends). Override
    with FINBOT_SKIP_TRADING_GUARD=1.
    """
    if os.environ.get("FINBOT_SKIP_TRADING_GUARD") == "1":
        return True
    d = dt.date.fromisoformat(date)
    if d.weekday() >= 5:  # Sat/Sun
        return False
    try:
        import akshare as ak

        cal = ak.tool_trade_date_hist_sina()
        days = set(cal["trade_date"].astype(str))
        return date in days
    except Exception as exc:  # noqa: BLE001 - fall back to weekday rule
        log.warning("trade-calendar check failed (%s); using weekday rule", exc)
        return True


def run() -> int:
    from finbot.config import load_config
    from finbot import pipeline

    date = os.environ.get("FINBOT_DATE") or pipeline.today_str()
    source = os.environ.get("FINBOT_SOURCE", "akshare")
    os.environ.setdefault("FINBOT_DATA_SOURCE", source)

    if not is_trading_day(date):
        log.info("%s is not a trading day; skipping.", date)
        print({"date": date, "skipped": "non-trading-day"})
        return 0

    cfg = load_config()
    cfg.raw.setdefault("data", {})["source"] = source

    # 1) update warehouse + run the portfolio pipeline
    from finbot.data import Warehouse, get_provider
    wh = Warehouse(root=str(cfg.path("data.warehouse_dir")), benchmark=cfg.get("data.benchmark", "000985"))
    provider = get_provider(source, cfg.get("data.fallback_to_mock", True))
    wh.update(date, provider=provider,
              history_days=int(cfg.get("data.history_days", 250)),
              incremental_days=int(cfg.get("data.incremental_days", 10)))
    summary = pipeline.run_portfolio(date, cfg)
    log.info("pipeline summary: %s", summary)
    report_path = Path(summary.get("report", ""))

    # 2) optional LLM briefing (richer narrative); falls back to the programmatic report
    if os.environ.get("FINBOT_WITH_LLM") == "1":
        try:
            from scripts.briefing_llm import generate_briefing  # type: ignore
        except ImportError:
            sys.path.insert(0, str(REPO_ROOT))
            from scripts.briefing_llm import generate_briefing  # type: ignore
        try:
            briefing = generate_briefing(date, REPO_ROOT)
            report_path = REPO_ROOT / "reports" / f"briefing_{date}.md"
            report_path.write_text(briefing, encoding="utf-8")
            log.info("LLM briefing written to %s", report_path)
        except Exception as exc:  # noqa: BLE001 - briefing is enhancement, not critical
            log.error("LLM briefing failed (%s); using programmatic report", exc)

    # 3) push the report
    if report_path and report_path.exists():
        from scripts.notify import send
        send(f"finbot 调仓简报 · {date}", report_path.read_text(encoding="utf-8"))

    print(summary)
    return 0


def main(argv=None) -> int:
    argparse.ArgumentParser(description="finbot unattended daily job").parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        return run()
    except Exception as exc:  # noqa: BLE001 - non-zero exit so cron alerting fires
        log.exception("daily job failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
