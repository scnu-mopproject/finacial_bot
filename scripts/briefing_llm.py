#!/usr/bin/env python3
"""Unattended daily LLM briefing (for server cron).

Reads finbot's deterministic outputs for a date — regime, ranked candidates,
target portfolio, rebalance orders, and the day's news — and asks Claude to
write a single Chinese 调仓简报 that fuses the three agent roles
(market-analyst / stock-picker / strategy-advisor) into one risk-controlled read.

Design notes:
- Model: claude-opus-4-8 with adaptive thinking (the synthesis is non-trivial).
- Prompt caching: the long, frozen role/discipline system prompt is marked
  cacheable; the per-day data goes in the user turn (after the cached prefix) so
  repeated runs reuse the prefix. (Opus caches prefixes ≥ ~4096 tokens; below
  that it silently won't cache — harmless here.)
- Streaming: the briefing can be long, so we stream and take get_final_message()
  to avoid HTTP timeouts.
- Robust: missing API key / artifacts / API errors degrade to a clear message and
  a non-zero exit so cron alerting can catch it; never crashes the daily job hard.

Run:  python scripts/briefing_llm.py --date 2026-06-02
Requires:  pip install anthropic ; ANTHROPIC_API_KEY in the environment.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Dict, Optional

log = logging.getLogger("finbot.briefing")

MODEL = "claude-opus-4-8"

# Frozen system prompt (cache prefix). Encodes the three agent charters and the
# product discipline. Keep this byte-stable across runs so the cache can hit.
SYSTEM_PROMPT = """\
你是 finbot 的首席投研编辑。你的任务：把下方程序化管线产出的结构化数据，整合成一份
**中文每日调仓简报**，融合三个角色的研判视角，但由你统一成一篇连贯、克制、可执行的简报。

# 三个角色视角（在同一篇简报里体现，不必分段标注角色）
- market-analyst（市场分析师）：解读 regime（risk-on/neutral/risk-off）与跨资产宏观
  （人民币/美元/商品/利差）对板块的传导，给出市场环境画像与主线/规避板块。区分事实与传闻。
- stock-picker（选股师）：基于因子排序候选与其驱动因子（drivers），叠加板块/催化逻辑，
  挑出值得关注的标的并说明理由；对“有分无逻辑”的标的主动降级。
- strategy-advisor（策略顾问）：以风控优先，审阅目标组合与调仓指令，指出是否过度集中、
  换手是否过高、敞口是否与 regime 匹配；可否决选股。

# 硬性纪律（必须遵守）
- 排序分是**横截面相对排序**，不是涨停、不是概率、不是收益保证。绝不出现“必涨/稳赚/高准确率”等表述。
- 现实目标是“小而稳定的正收益 + 控回撤”，靠一篮子分散与长期累积，不是押中单只。
- 风控线（止损/止盈/单仓上限/行业上限/总敞口/换手）是硬约束，风控优先于博弹性。
- 调仓执行口径：次日开盘、跳过已封涨停标的、尊重 T+1。
- 全篇结尾必须保留免责声明：本简报仅供研究参考，不构成投资建议；市场有风险，决策与风险自负。

# 输出格式（Markdown，简洁，不啰嗦）
1. **一句话市场判断**（regime + 敞口建议）
2. **市场环境与跨资产解读**（要点式，3-6 条）
3. **重点关注标的**（不超过 6 只：代码/名称/板块 + 一句逻辑 + 风险点；并说明哪些被你降级及原因）
4. **调仓执行计划**（基于给定 orders，列 BUY/SELL/TRIM/ADD，标注次日开盘/跳过封板）
5. **风险提示与执行纪律**
6. 免责声明

若某类数据缺失，明确写“（数据缺失）”，不要编造。
"""


def _load_portfolio_artifact(date: str, repo_root: Path) -> Optional[Dict]:
    path = repo_root / "artifacts" / date / "10_portfolio.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _load_news(date: str) -> list:
    """Pull the day's news from the local warehouse (best-effort)."""
    try:
        from finbot.config import load_config
        from finbot.data import Warehouse

        cfg = load_config()
        wh = Warehouse(root=str(cfg.path("data.warehouse_dir")),
                       benchmark=cfg.get("data.benchmark", "000985"))
        news = wh.news(start=date, end=date)
        if news.empty:
            news = wh.news()  # fall back to latest available
        return news.head(40).to_dict(orient="records")
    except Exception as exc:  # noqa: BLE001 - news is optional context
        log.warning("could not load news (%s); continuing without it", exc)
        return []


def build_user_content(date: str, portfolio: Dict, news: list) -> str:
    """The volatile, per-day payload — placed after the cached system prefix."""
    return (
        f"# 交易日: {date}\n\n"
        "## 市场状态 (regime)\n```json\n"
        + json.dumps(portfolio.get("regime", {}), ensure_ascii=False, indent=2)
        + "\n```\n\n## 目标组合 (target)\n```json\n"
        + json.dumps(portfolio.get("target", {}), ensure_ascii=False, indent=2)
        + "\n```\n\n## 调仓指令 (orders)\n```json\n"
        + json.dumps(portfolio.get("orders", []), ensure_ascii=False, indent=2)
        + "\n```\n\n## 当日财经新闻 (news)\n```json\n"
        + json.dumps(news, ensure_ascii=False, indent=2)
        + "\n```\n\n请据此撰写今日中文调仓简报。"
    )


def generate_briefing(date: str, repo_root: Optional[Path] = None) -> str:
    """Generate the briefing markdown. Raises on unrecoverable errors."""
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("anthropic SDK not installed; `pip install anthropic`") from exc

    repo_root = repo_root or Path(__file__).resolve().parents[1]
    portfolio = _load_portfolio_artifact(date, repo_root)
    if portfolio is None:
        raise FileNotFoundError(
            f"missing artifacts/{date}/10_portfolio.json — run `finbot run --date {date}` first"
        )
    news = _load_news(date)
    user_content = build_user_content(date, portfolio, news)

    # Credentials resolve from ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN in the env.
    client = anthropic.Anthropic()

    try:
        with client.messages.stream(
            model=MODEL,
            max_tokens=16000,
            thinking={"type": "adaptive"},
            system=[{
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},  # cache the frozen prefix
            }],
            messages=[{"role": "user", "content": user_content}],
        ) as stream:
            for _ in stream.text_stream:  # drain; we only need the final text
                pass
            message = stream.get_final_message()
    except anthropic.AuthenticationError as exc:
        raise RuntimeError("Claude auth failed — check ANTHROPIC_API_KEY") from exc
    except anthropic.RateLimitError as exc:
        raise RuntimeError("Claude rate limited; retry later") from exc
    except anthropic.APIStatusError as exc:
        raise RuntimeError(f"Claude API error {exc.status_code}: {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise RuntimeError("network error reaching Claude API") from exc

    text = "".join(b.text for b in message.content if b.type == "text").strip()
    u = message.usage
    log.info("briefing generated: in=%s cache_read=%s out=%s",
             u.input_tokens, getattr(u, "cache_read_input_tokens", 0), u.output_tokens)
    return text


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="finbot LLM daily briefing")
    parser.add_argument("--date", required=True, help="trading date YYYY-MM-DD")
    parser.add_argument("--out", default=None, help="output path (default reports/briefing_<date>.md)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    repo_root = Path(__file__).resolve().parents[1]
    try:
        briefing = generate_briefing(args.date, repo_root)
    except Exception as exc:  # noqa: BLE001 - surface a clean error + non-zero exit for cron alerting
        log.error("briefing failed: %s", exc)
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1

    out = Path(args.out) if args.out else repo_root / "reports" / f"briefing_{args.date}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(briefing, encoding="utf-8")
    print(json.dumps({"date": args.date, "briefing": str(out), "chars": len(briefing)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
