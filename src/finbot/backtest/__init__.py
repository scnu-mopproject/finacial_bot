"""Backtesting & factor-validation layer (design §8 / M4).

The only way to know whether a signal has edge. Provides factor-level metrics
(rank IC / ICIR / quantile monotonicity) and a walk-forward portfolio backtest
that accounts for A-share frictions (T+1, sealed limit-up untradeable, costs).
"""
from .metrics import rank_ic, ic_summary, quantile_returns, perf_metrics
from .engine import backtest_signal

__all__ = ["rank_ic", "ic_summary", "quantile_returns", "perf_metrics", "backtest_signal"]
