"""Market regime classification (design §10 / M7).

Combines market breadth, benchmark volatility, and cross-asset macro (RMB / DXY /
commodities) into a risk-on / neutral / risk-off read, plus a sector tilt. This
modulates the strategy's exposure and factor emphasis, and is the quantitative
input the ``market-analyst`` agent interprets qualitatively.

Note (design §10): northbound real-time flow was discontinued in 2024, so RMB
and DXY trends proxy foreign-capital risk appetite here.
"""
from __future__ import annotations

import logging
from typing import Dict, List

import numpy as np
import pandas as pd

from .data import Warehouse

log = logging.getLogger(__name__)


def _trend(series: pd.Series, window: int = 20) -> float:
    """Recent fractional change over ``window`` observations."""
    s = series.dropna()
    if len(s) < window + 1:
        return float("nan")
    return float(s.iloc[-1] / s.iloc[-1 - window] - 1.0)


def classify(wh: Warehouse, breadth_lookback: int = 1) -> Dict:
    """Return the regime read for the latest available date in the warehouse."""
    bars = wh.bars()
    signals: Dict[str, float] = {}
    votes: List[int] = []  # +1 risk-on, -1 risk-off

    # --- breadth: fraction of stocks up on the latest session ---
    if not bars.empty:
        close = bars.pivot_table(index="date", columns="code", values="close").sort_index()
        ret = close.pct_change().iloc[-1]
        breadth = float((ret > 0).mean())
        signals["breadth_up_frac"] = round(breadth, 3)
        votes.append(1 if breadth > 0.55 else (-1 if breadth < 0.45 else 0))

        # --- benchmark trend & volatility ---
    idx = wh.index_bar().sort_values("date")
    if not idx.empty:
        bench_ret = idx["close"].pct_change()
        vol = float(bench_ret.tail(20).std())
        trend = _trend(idx["close"].reset_index(drop=True), 20)
        signals["bench_trend_20"] = round(trend, 4) if trend == trend else None
        signals["bench_vol_20"] = round(vol, 4)
        if trend == trend:
            votes.append(1 if trend > 0 else -1)
        votes.append(-1 if vol > bench_ret.std() * 1.3 else 0)  # vol spike => risk-off

    # --- cross-asset macro (RMB / DXY / commodities) ---
    macro = wh.macro()
    sector_tilt: List[str] = []
    if not macro.empty:
        mw = macro.pivot_table(index="date", columns="series", values="value").sort_index()
        cny = _trend(mw["USDCNY"], 20) if "USDCNY" in mw else float("nan")
        dxy = _trend(mw["DXY"], 20) if "DXY" in mw else float("nan")
        oil = _trend(mw["OIL"], 20) if "OIL" in mw else float("nan")
        signals["usdcny_trend_20"] = round(cny, 4) if cny == cny else None
        signals["dxy_trend_20"] = round(dxy, 4) if dxy == dxy else None
        signals["oil_trend_20"] = round(oil, 4) if oil == oil else None
        # RMB depreciation (USDCNY up) and strong USD => foreign risk-off
        if cny == cny:
            votes.append(-1 if cny > 0.005 else (1 if cny < -0.005 else 0))
            sector_tilt.append("出口链(贬值受益)" if cny > 0.005 else "进口/航空(升值受益)" if cny < -0.005 else "")
        if dxy == dxy:
            votes.append(-1 if dxy > 0.01 else (1 if dxy < -0.01 else 0))
        if oil == oil and oil > 0.03:
            sector_tilt.append("资源链(油价上行)")

    score = int(np.nansum(votes)) if votes else 0
    regime = "risk-on" if score >= 2 else "risk-off" if score <= -2 else "neutral"
    # exposure suggestion scales with regime
    exposure = {"risk-on": 0.90, "neutral": 0.70, "risk-off": 0.45}[regime]
    factor_emphasis = {
        "risk-on": "动量/景气", "neutral": "均衡", "risk-off": "低波/质量/反转",
    }[regime]

    return {
        "date": str(bars["date"].max()) if not bars.empty else None,
        "regime": regime,
        "score": score,
        "suggested_exposure": exposure,
        "factor_emphasis": factor_emphasis,
        "sector_tilt": [s for s in sector_tilt if s],
        "signals": signals,
        "note": "regime 为定量初判，应由 market-analyst 智能体结合新闻/政策做定性复核。",
    }
