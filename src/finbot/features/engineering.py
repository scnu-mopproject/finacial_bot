"""Factor engineering.

Turns raw (universe snapshot + per-stock history + news) into a tidy feature
matrix, one row per stock, that the limit-up ranker consumes. Each factor family
maps to a config toggle in ``features.*`` so it can be enabled/disabled.

These are deliberately transparent, well-known factors — the point of the
skeleton is a clean, extensible pipeline, not a secret alpha. Add your own
factors as new ``_build_*`` helpers and register them in ``FACTOR_BUILDERS``.
"""
from __future__ import annotations

from typing import Dict, List

import numpy as np
import pandas as pd

from ..config import Config
from ..data import DataProvider


# --- technical factors ---------------------------------------------------
def _build_technical(hist: pd.DataFrame) -> Dict[str, float]:
    close = hist["close"].astype(float)
    vol = hist["volume"].astype(float)
    if len(close) < 25:
        return {}
    ret = close.pct_change()
    ma5, ma10, ma20 = close.rolling(5).mean(), close.rolling(10).mean(), close.rolling(20).mean()
    last = close.iloc[-1]
    return {
        "mom_5d": float(close.iloc[-1] / close.iloc[-6] - 1),
        "mom_20d": float(close.iloc[-1] / close.iloc[-21] - 1),
        "volatility_20d": float(ret.tail(20).std()),
        "above_ma20": float(last > ma20.iloc[-1]),
        "ma_bull_align": float(ma5.iloc[-1] > ma10.iloc[-1] > ma20.iloc[-1]),
        "vol_ratio_5d": float(vol.iloc[-1] / (vol.tail(5).mean() + 1e-9)),
        "dist_from_high_60d": float(last / close.tail(60).max() - 1),
    }


# --- capital-flow factors (proxied on the snapshot in the skeleton) ------
def _build_capital_flow(row: pd.Series) -> Dict[str, float]:
    return {
        "amount_yi": float(row.get("amount_yi", np.nan)),
        "turnover_rate": float(row.get("turnover_rate", np.nan)),
        # Strong turnover with a positive day is a crude main-force-interest proxy.
        "active_money": float(row.get("turnover_rate", 0) * max(row.get("pct_chg", 0), 0)),
    }


# --- limit-up "gene" factors --------------------------------------------
def _build_limitup_genes(hist: pd.DataFrame, row: pd.Series) -> Dict[str, float]:
    close = hist["close"].astype(float)
    daily_ret = close.pct_change()
    near_limit = (daily_ret >= 0.095).tail(20).sum()
    return {
        "limitups_20d": float(near_limit),
        "is_limit_up_today": float(bool(row.get("limit_up", False))),
        "pct_chg_today": float(row.get("pct_chg", np.nan)),
    }


# --- sentiment factors (news joined by sector) ---------------------------
def _sector_sentiment(news: pd.DataFrame) -> Dict[str, float]:
    if news.empty or "sector" not in news.columns:
        return {}
    pol = news.copy()
    pol["polarity"] = pd.to_numeric(pol["polarity"], errors="coerce")
    grp = pol.groupby("sector")["polarity"].agg(["mean", "count"])
    return {s: float(r["mean"] if not np.isnan(r["mean"]) else 0.0) for s, r in grp.iterrows()}


def _sector_counts(news: pd.DataFrame) -> Dict[str, int]:
    if news.empty or "sector" not in news.columns:
        return {}
    return news["sector"].value_counts().to_dict()


# --- board-linkage factors ----------------------------------------------
def _sector_strength(universe: pd.DataFrame) -> Dict[str, float]:
    if "sector" not in universe.columns or universe["sector"].eq("").all():
        return {}
    return universe.groupby("sector")["pct_chg"].mean().to_dict()


FACTOR_BUILDERS = ("technical", "capital_flow", "sentiment", "board_linkage", "limitup_genes")


def build_features(
    provider: DataProvider,
    universe: pd.DataFrame,
    news: pd.DataFrame,
    date: str,
    cfg: Config,
    max_names: int | None = None,
) -> pd.DataFrame:
    """Build the per-stock feature matrix for ``date``.

    Parameters mirror the pipeline stages so this can be called standalone in
    tests. ``max_names`` caps how many stocks we pull history for (history is the
    expensive call); the daily run pre-filters the universe before this.
    """
    sector_sent = _sector_sentiment(news) if cfg.get("features.sentiment", True) else {}
    sector_news_n = _sector_counts(news) if cfg.get("features.sentiment", True) else {}
    sector_strength = _sector_strength(universe) if cfg.get("features.board_linkage", True) else {}

    rows: List[Dict] = []
    work = universe if max_names is None else universe.head(max_names)
    for _, row in work.iterrows():
        code = row["code"]
        feat: Dict[str, float] = {"code": code, "name": row.get("name", ""), "sector": row.get("sector", "")}

        if cfg.get("features.technical", True) or cfg.get("features.limitup_genes", True):
            hist = provider.history(code, date)
            if cfg.get("features.technical", True):
                feat.update(_build_technical(hist))
            if cfg.get("features.limitup_genes", True):
                feat.update(_build_limitup_genes(hist, row))

        if cfg.get("features.capital_flow", True):
            feat.update(_build_capital_flow(row))

        if cfg.get("features.sentiment", True):
            sec = row.get("sector", "")
            feat["sector_sentiment"] = float(sector_sent.get(sec, 0.0))
            feat["sector_news_count"] = float(sector_news_n.get(sec, 0))

        if cfg.get("features.board_linkage", True):
            feat["sector_strength"] = float(sector_strength.get(row.get("sector", ""), 0.0))

        rows.append(feat)

    df = pd.DataFrame(rows)
    return df.replace([np.inf, -np.inf], np.nan).fillna(0.0)
