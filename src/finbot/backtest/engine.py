"""Walk-forward portfolio backtest with A-share frictions (design §8).

Given a panel with a ranking ``score`` and the forward excess-return label, this
simulates a periodic top-N equal-weight long book and reports net performance.

Frictions modeled:
- **T+1**: the label already measures entry at t+1's close (see finbot.labels).
- **Sealed limit-up untradeable**: names whose entry-day move ≈ limit-up are
  skipped (you usually cannot buy a sealed board) — applied when a warehouse is
  supplied so the entry-day return is known.
- **Costs**: per-rebalance turnover × round-trip cost (commission+stamp+slippage).

Use ``rebalance_days == holding_period H`` for non-overlapping, unbiased periods.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from ..data import Warehouse
from .metrics import ic_summary, perf_metrics, quantile_returns, rank_ic

log = logging.getLogger(__name__)

_LIMIT_UP = 0.098  # close-to-close proxy for a sealed limit-up board


def _entry_day_returns(wh: Warehouse) -> pd.DataFrame:
    """Per-(date, code) next-session return — used to flag untradeable limit-ups.

    Keyed by *decision date* t: value = close[t+1]/close[t]-1 (the move on the
    day you would actually buy).
    """
    bars = wh.bars().sort_values(["code", "date"])
    bars["next_ret"] = bars.groupby("code")["close"].transform(lambda s: s.shift(-1) / s - 1.0)
    return bars[["date", "code", "next_ret"]]


def backtest_signal(
    panel: pd.DataFrame,
    wh: Optional[Warehouse] = None,
    score_col: str = "score",
    label_col: str = "excess_ret",
    n_holdings: int = 12,
    rebalance_days: int = 5,
    cost_roundtrip: float = 0.0026,   # ~0.13% per side, round trip
    q: int = 5,
) -> Dict:
    """Run the backtest and factor-validation suite.

    Returns a dict with IC summary, quantile monotonicity, net performance, and
    the per-period return series.
    """
    panel = panel.dropna(subset=[score_col, label_col]).copy()
    if panel.empty:
        return {"error": "empty panel after dropping NaN score/label"}

    # Untradeable mask: skip names sealed at limit-up on the entry day.
    if wh is not None:
        eret = _entry_day_returns(wh)
        panel = panel.merge(eret, on=["date", "code"], how="left")
        panel["tradeable"] = ~(panel["next_ret"] >= _LIMIT_UP)
    else:
        panel["tradeable"] = True

    dates = sorted(panel["date"].unique())
    rebal_dates = dates[::rebalance_days]

    period_rets: List[float] = []
    period_index: List[str] = []
    prev_book: set = set()
    for d in rebal_dates:
        day = panel[(panel["date"] == d) & panel["tradeable"]]
        if len(day) < q:
            continue
        top = day.nlargest(n_holdings, score_col)
        gross = float(top[label_col].mean())                  # equal-weight excess return
        book = set(top["code"])
        turnover = 1.0 if not prev_book else len(book ^ prev_book) / (2 * max(len(book), 1))
        cost = turnover * cost_roundtrip
        period_rets.append(gross - cost)
        period_index.append(str(d))
        prev_book = book

    ret_series = pd.Series(period_rets, index=period_index)
    ppy = 252.0 / rebalance_days

    ic = rank_ic(panel, score_col=score_col, label_col=label_col)
    return {
        "config": {
            "n_holdings": n_holdings, "rebalance_days": rebalance_days,
            "cost_roundtrip": cost_roundtrip, "n_rebalances": len(ret_series),
            "date_range": [str(dates[0]), str(dates[-1])] if dates else [],
        },
        "ic_summary": ic_summary(ic),
        "quantile_returns": quantile_returns(panel, q=q, score_col=score_col, label_col=label_col)
            .to_dict(orient="records"),
        "performance": perf_metrics(ret_series, periods_per_year=ppy),
        "period_returns": {k: round(v, 5) for k, v in ret_series.items()},
        "note": ("净值基于前瞻超额收益的等权多头组合，已扣换手成本并剔除封板不可买入；"
                 "仅为研究回测，非实盘收益承诺。"),
    }
