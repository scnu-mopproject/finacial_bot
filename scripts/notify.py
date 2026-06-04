#!/usr/bin/env python3
"""Push a report to a chat webhook (WeChat Work / Server酱 / Telegram).

Channel + credentials come from environment variables so nothing secret is
committed. Best-effort: a failed push logs and returns non-zero but never raises
into the daily job.

Env:
  FINBOT_NOTIFY=wecom|serverchan|telegram|none   (default: none)
  # WeChat Work group robot:
  WECOM_WEBHOOK=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...
  # Server酱 (sct):
  SERVERCHAN_SENDKEY=SCT...
  # Telegram:
  TELEGRAM_BOT_TOKEN=...   TELEGRAM_CHAT_ID=...
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

log = logging.getLogger("finbot.notify")

# WeChat Work markdown content cap is ~4096 bytes; keep a safe margin.
_WECOM_LIMIT = 3800


def _post_json(url: str, payload: dict, timeout: float = 15.0) -> None:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - trusted webhook URL
        resp.read()


def _post_form(url: str, fields: dict, timeout: float = 15.0) -> None:
    data = urllib.parse.urlencode(fields).encode("utf-8")
    with urllib.request.urlopen(url, data=data, timeout=timeout) as resp:  # noqa: S310
        resp.read()


def send(title: str, body: str, channel: str | None = None) -> bool:
    """Send ``body`` (markdown) to the configured channel. Returns success."""
    channel = (channel or os.environ.get("FINBOT_NOTIFY", "none")).lower()
    try:
        if channel == "none":
            log.info("FINBOT_NOTIFY=none; skipping push")
            return True
        if channel == "wecom":
            url = os.environ["WECOM_WEBHOOK"]
            content = f"# {title}\n{body}"[:_WECOM_LIMIT]
            _post_json(url, {"msgtype": "markdown", "markdown": {"content": content}})
        elif channel == "serverchan":
            key = os.environ["SERVERCHAN_SENDKEY"]
            _post_form(f"https://sctapi.ftqq.com/{key}.send",
                       {"title": title, "desp": body})
        elif channel == "telegram":
            token = os.environ["TELEGRAM_BOT_TOKEN"]
            chat_id = os.environ["TELEGRAM_CHAT_ID"]
            text = f"*{title}*\n\n{body}"[:4000]
            _post_json(f"https://api.telegram.org/bot{token}/sendMessage",
                       {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})
        else:
            log.error("unknown FINBOT_NOTIFY channel: %s", channel)
            return False
        log.info("pushed report via %s", channel)
        return True
    except KeyError as exc:
        log.error("missing env var for channel %s: %s", channel, exc)
        return False
    except Exception as exc:  # noqa: BLE001 - never crash the daily job on a push failure
        log.error("push failed (%s): %s", channel, exc)
        return False


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="push a report file to a chat webhook")
    parser.add_argument("--file", required=True, help="path to the markdown report")
    parser.add_argument("--title", default="finbot 调仓简报")
    parser.add_argument("--channel", default=None)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    path = Path(args.file)
    if not path.exists():
        log.error("report not found: %s", path)
        return 1
    ok = send(args.title, path.read_text(encoding="utf-8"), channel=args.channel)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
