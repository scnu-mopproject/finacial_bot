"""Forward-return labels & panel dataset construction (design doc §6 / M2).

The redesign predicts **cross-sectional forward excess return** over a holding
horizon H, not limit-up events. This module turns warehouse prices into labels
with a strict no-look-ahead convention:

    decision date t      -> features use info available up to and including t
    enter at  t+1 (close)  (respect A-share T+1: you act next session)
    exit  at  t+1+H (close)
    label = R_stock(t+1 -> t+1+H) - R_benchmark(t+1 -> t+1+H)   (excess return)

So the most recent H+1 trading days have NaN labels (their future hasn't
happened yet) — those rows are for live prediction, never for training.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from .data import Warehouse

log = logging.getLogger(__name__)


def _forward_return(close: pd.Series, h: int) -> pd.Series:
    """R(t+1 -> t+1+H) for a date-sorted close series.

    entry = next session's close (T+1), exit = close H sessions after entry.
    """
    entry = close.shift(-1)
    exit_ = close.shift(-(1 + h))
    return exit_ / entry - 1.0


def forward_returns(wh: Warehouse, h: int = 5, start: str | None = None,
                    end: str | None = None) -> pd.DataFrame:
    """Compute per-(date, code) forward excess return over horizon ``h``.

    Returns columns: date, code, fwd_ret, bench_fwd_ret, excess_ret.
    Rows whose future is not yet available carry NaN (and are dropped by
    :func:`build_dataset` when assembling a training panel).
    """
    bars = wh.bars(start=start, end=end)
    if bars.empty:
        return pd.DataFrame(columns=["date", "code", "fwd_ret", "bench_fwd_ret", "excess_ret"])
    bars = bars.sort_values(["code", "date"])

    # Per-stock forward return.
    bars["fwd_ret"] = bars.groupby("code")["close"].transform(lambda s: _forward_return(s, h))

    # Benchmark forward return, keyed by decision date t.
    idx = wh.index_bar(start=start, end=end).sort_values("date")
    bench = pd.DataFrame({"date": idx["date"].to_numpy()})
    bench["bench_fwd_ret"] = _forward_return(idx["close"].reset_index(drop=True), h).to_numpy()

    out = bars.merge(bench, on="date", how="left")
    out["excess_ret"] = out["fwd_ret"] - out["bench_fwd_ret"]
    return out[["date", "code", "fwd_ret", "bench_fwd_ret", "excess_ret"]].reset_index(drop=True)


def attach_labels(features: pd.DataFrame, labels: pd.DataFrame) -> pd.DataFrame:
    """Left-join a feature matrix (with date+code) onto forward-return labels."""
    if "date" not in features.columns:
        raise ValueError("features must carry a 'date' column to align with labels")
    return features.merge(labels, on=["date", "code"], how="left")


def build_dataset(
    wh: Warehouse,
    h: int = 5,
    features: Optional[pd.DataFrame] = None,
    drop_unlabeled: bool = True,
    out_dir: str | Path = "data/datasets",
) -> pd.DataFrame:
    """Assemble and persist a labelled panel for horizon ``h``.

    If ``features`` is provided (one row per date+code), they are joined; otherwise
    only the label table is produced (features land with the M3 factor library).
    Unlabeled rows (no future yet) are dropped from the training panel by default.
    """
    labels = forward_returns(wh, h=h)
    panel = attach_labels(features, labels) if features is not None else labels
    if drop_unlabeled:
        panel = panel.dropna(subset=["excess_ret"]).reset_index(drop=True)

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"panel_h{h}.parquet"
    try:
        panel.to_parquet(path, index=False)
    except Exception:  # noqa: BLE001 - csv fallback when pyarrow missing
        path = out_dir / f"panel_h{h}.csv"
        panel.to_csv(path, index=False)
    log.info("wrote labelled panel %s (%d rows)", path, len(panel))
    return panel
