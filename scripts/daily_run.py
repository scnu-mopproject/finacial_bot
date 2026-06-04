#!/usr/bin/env python3
"""Scheduled daily runner.

Two modes:

- ``--once``    : run the pipeline a single time and exit (use this from cron or
                  a CI schedule — the most robust option).
- ``--serve``   : keep a long-lived process that triggers the pipeline at the
                  ``schedule.post_market`` time from config (uses the optional
                  ``schedule`` dependency).

For most setups, prefer ``--once`` driven by an external scheduler (cron / GitHub
Actions) rather than a long-running process.
"""
from __future__ import annotations

import argparse
import logging
import sys

from finbot.config import load_config
from finbot import pipeline


def run_once(date: str | None = None) -> int:
    cfg = load_config()
    summary = pipeline.run_portfolio(date, cfg)
    logging.info("daily run complete: %s", summary)
    print(summary)
    return 0


def serve() -> int:
    try:
        import schedule  # optional dependency
    except ImportError:
        logging.error("the 'schedule' package is required for --serve; pip install schedule")
        return 1
    import time

    cfg = load_config()
    when = cfg.get("schedule.post_market", "15:30")
    logging.info("scheduling daily run at %s (%s)", when, cfg.get("schedule.timezone"))
    schedule.every().day.at(when).do(lambda: run_once())
    while True:
        schedule.run_pending()
        time.sleep(30)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="finbot scheduled runner")
    parser.add_argument("--date", default=None, help="trading date YYYY-MM-DD")
    parser.add_argument("--once", action="store_true", help="run once and exit (default)")
    parser.add_argument("--serve", action="store_true", help="run forever, trigger on schedule")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    if args.serve:
        return serve()
    return run_once(args.date)


if __name__ == "__main__":
    sys.exit(main())
