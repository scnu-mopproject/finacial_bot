"""Factor-validation & performance metrics (design §8).

All metrics operate on a panel with at least: date, code, <score>, excess_ret
(the forward H-day excess return label from finbot.labels).
"""
from __future__ import annotations

from typing import Dict

import numpy as np
import pandas as pd


def rank_ic(panel: pd.DataFrame, score_col: str = "score", label_col: str = "excess_ret") -> pd.Series:
    """Per-date cross-sectional rank IC (Spearman) between score and forward label.

    A small but consistently positive mean IC (≈0.03–0.08) is the realistic edge.
    """
    def _ic(g: pd.DataFrame) -> float:
        s, y = g[score_col], g[label_col]
        if s.notna().sum() < 5 or y.notna().sum() < 5:
            return np.nan
        # Spearman = Pearson on ranks (avoids a scipy dependency).
        return s.rank().corr(y.rank())

    return panel.groupby("date").apply(_ic).dropna()


def ic_summary(ic: pd.Series) -> Dict[str, float]:
    """Summarize an IC series: mean, std, ICIR, t-stat, hit rate."""
    ic = ic.dropna()
    if ic.empty:
        return {"n": 0, "ic_mean": None}
    mean, std = float(ic.mean()), float(ic.std(ddof=0))
    icir = mean / std if std else float("nan")
    return {
        "n": int(len(ic)),
        "ic_mean": round(mean, 4),
        "ic_std": round(std, 4),
        "icir": round(icir, 3),                       # IR of the IC series
        "t_stat": round(icir * np.sqrt(len(ic)), 2),  # significance of mean IC
        "hit_rate": round(float((ic > 0).mean()), 3), # share of days IC>0
    }


def quantile_returns(panel: pd.DataFrame, q: int = 5, score_col: str = "score",
                     label_col: str = "excess_ret") -> pd.DataFrame:
    """Mean forward excess return by score quantile (monotonicity check).

    Q1 = lowest score, Q{q} = highest. A monotonic increase, with Q{q}>Q1>0
    spread, indicates the signal sorts future returns.
    """
    df = panel.dropna(subset=[score_col, label_col]).copy()

    def _assign(g: pd.DataFrame) -> pd.Series:
        if g[score_col].notna().sum() < q:
            return pd.Series(np.nan, index=g.index)
        return pd.qcut(g[score_col].rank(method="first"), q, labels=range(1, q + 1)).astype(float)

    df["quantile"] = df.groupby("date", group_keys=False).apply(_assign)
    df = df.dropna(subset=["quantile"])
    if df.empty:
        return pd.DataFrame()
    out = (df.groupby("quantile", observed=True)[label_col]
           .agg(["mean", "count"]).reset_index())
    out.columns = ["quantile", "mean_excess_ret", "count_obs"]
    return out


def perf_metrics(period_returns: pd.Series, periods_per_year: float) -> Dict[str, float]:
    """Annualized performance of a per-rebalance return series."""
    r = period_returns.dropna()
    if r.empty:
        return {"n_periods": 0}
    equity = (1 + r).cumprod()
    total = float(equity.iloc[-1] - 1)
    ann_ret = float((1 + total) ** (periods_per_year / len(r)) - 1) if len(r) else 0.0
    ann_vol = float(r.std(ddof=0) * np.sqrt(periods_per_year))
    sharpe = ann_ret / ann_vol if ann_vol else float("nan")
    dd = equity / equity.cummax() - 1
    return {
        "n_periods": int(len(r)),
        "total_return": round(total, 4),
        "ann_return": round(ann_ret, 4),
        "ann_vol": round(ann_vol, 4),
        "sharpe": round(sharpe, 3),
        "max_drawdown": round(float(dd.min()), 4),
        "win_rate": round(float((r > 0).mean()), 3),
    }
