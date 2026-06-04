"""Speculative limit-up watch — DEMOTED feature (design §12 / M8).

The redesign abandons "predict today's limit-up" as a core objective (it is
low-signal, reflexive, and largely unexploitable under T+1 + sealed boards).
This module keeps a *clearly flagged* speculative watch for sentiment/theme
reference ONLY. It deliberately does NOT feed the portfolio construction chain.

Every output carries a prominent risk label. Heat is a momentum/attention proxy,
NOT a probability of limiting up.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from .data import Warehouse

_LIMIT_UP = 0.098

RISK_LABEL = (
    "⚠️ 高风险投机观察池：heat 是动量/关注度代理，不是涨停概率、不是建议。"
    "封板标的常因 T+1 与一字板无法稳定买入；本榜不进入组合优化主链路。"
)


def speculative_watch(wh: Warehouse, top_n: int = 15, date: Optional[str] = None) -> pd.DataFrame:
    """Rank a speculative 'heat' watch from recent strength / turnover / streaks.

    Columns: code, name, sector, pct_chg, turnover_rate, up_streak, heat.
    """
    bars = wh.bars()
    if bars.empty:
        return pd.DataFrame()
    close = bars.pivot_table(index="date", columns="code", values="close").sort_index()
    d = date or close.index.max()
    close = close.loc[:d]
    if len(close) < 6:
        return pd.DataFrame()

    daily = close.pct_change()
    last_ret = daily.iloc[-1]
    mom_5 = close.iloc[-1] / close.iloc[-6] - 1.0
    # consecutive up-day streak (last few sessions)
    up = (daily > 0).iloc[-5:]
    up_streak = up[::-1].cummin().sum()  # count of trailing consecutive up days

    basics = wh.basics()
    snap = (basics[basics["date"] == basics["date"].max()].set_index("code")
            if not basics.empty else pd.DataFrame())
    turnover = snap["turnover_rate"] if "turnover_rate" in snap.columns else pd.Series(dtype=float)

    df = pd.DataFrame({
        "code": close.columns,
        "pct_chg": (last_ret.values * 100).round(2),
        "mom_5": mom_5.values,
        "up_streak": up_streak.reindex(close.columns).fillna(0).astype(int).values,
    })
    df["turnover_rate"] = df["code"].map(turnover).astype(float)
    if not snap.empty:
        df["name"] = df["code"].map(snap.get("name", pd.Series(dtype=str)))
        df["sector"] = df["code"].map(snap.get("sector", pd.Series(dtype=str)))
    else:
        df["name"] = ""
        df["sector"] = ""

    # transparent heat score (z-scored blend; attention proxy only)
    def _z(s):
        s = s.astype(float)
        sd = s.std(ddof=0)
        return (s - s.mean()) / sd if sd else s * 0
    df["heat"] = (_z(df["mom_5"]) + 0.5 * _z(df["turnover_rate"].fillna(0)) + 0.5 * df["up_streak"]).round(3)
    # flag names already sealed up today (you usually cannot buy them)
    df["sealed_today"] = df["pct_chg"] >= _LIMIT_UP * 100
    return (df.sort_values("heat", ascending=False)
            .head(top_n)[["code", "name", "sector", "pct_chg", "turnover_rate",
                          "up_streak", "heat", "sealed_today"]]
            .reset_index(drop=True))
