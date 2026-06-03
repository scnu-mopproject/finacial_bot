"""Tests for the backtest & factor-validation layer (M4)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from finbot.backtest.metrics import ic_summary, perf_metrics, quantile_returns, rank_ic
from finbot.backtest import backtest_signal


def _toy_panel(n_dates=20, n_codes=30, seed=0):
    """Panel where score is intentionally predictive of the label (positive IC)."""
    rng = np.random.default_rng(seed)
    rows = []
    for di in range(n_dates):
        date = f"2026-01-{di+1:02d}"
        for c in range(n_codes):
            score = rng.normal()
            # label = signal + noise -> score should sort future returns
            label = 0.02 * score + rng.normal(0, 0.03)
            rows.append({"date": date, "code": f"{c:06d}", "score": score, "excess_ret": label})
    return pd.DataFrame(rows)


def test_rank_ic_detects_predictive_signal():
    panel = _toy_panel()
    ic = rank_ic(panel)
    s = ic_summary(ic)
    assert s["ic_mean"] > 0.1          # constructed to be predictive
    assert s["hit_rate"] > 0.7


def test_quantile_returns_monotonic_for_predictive_signal():
    qr = quantile_returns(_toy_panel(), q=5)
    assert len(qr) == 5
    # top quantile should out-return bottom quantile
    top = qr.loc[qr["quantile"] == 5, "mean_excess_ret"].iloc[0]
    bot = qr.loc[qr["quantile"] == 1, "mean_excess_ret"].iloc[0]
    assert top > bot


def test_perf_metrics_basic():
    r = pd.Series([0.01, -0.005, 0.02, 0.0, 0.015])
    m = perf_metrics(r, periods_per_year=50)
    assert m["n_periods"] == 5
    assert "sharpe" in m and "max_drawdown" in m
    assert m["max_drawdown"] <= 0


def test_backtest_signal_runs_and_reports():
    res = backtest_signal(_toy_panel(), n_holdings=8, rebalance_days=5)
    assert "performance" in res and "ic_summary" in res
    assert res["config"]["n_rebalances"] >= 1
    assert res["ic_summary"]["ic_mean"] > 0
