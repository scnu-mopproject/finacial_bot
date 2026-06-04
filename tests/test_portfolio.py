"""Tests for portfolio construction & order generation (M6)."""
from __future__ import annotations

import pandas as pd

from finbot.portfolio import build_target_portfolio, rebalance_orders, Portfolio


def _scored(n=30, seed=1):
    import numpy as np
    rng = np.random.default_rng(seed)
    return pd.DataFrame({
        "code": [f"{i:06d}" for i in range(n)],
        "sector": ["A", "B", "C"] * (n // 3),
        "score": rng.normal(size=n),
    })


def test_target_respects_size_and_count_caps():
    t = build_target_portfolio(_scored(), n_holdings=10, max_position_pct=0.15,
                               max_total_exposure=0.9)
    assert len(t["weights"]) <= 10
    assert all(w <= 0.15 + 1e-9 for w in t["weights"].values())
    assert sum(t["weights"].values()) <= 0.9 + 1e-9
    assert abs(sum(t["weights"].values()) + t["cash"] - 1.0) < 1e-6


def test_hysteresis_keeps_borderline_holdings():
    scored = _scored(30, seed=2).sort_values("score", ascending=False).reset_index(drop=True)
    # a holding ranked at ~25% should be kept (within hold 30%) even if outside enter 15%
    borderline = scored.iloc[7]["code"]   # ~ top 25%
    prev = {borderline: 0.1}
    t = build_target_portfolio(scored, prev_weights=prev, n_holdings=12,
                               enter_pct=0.15, hold_pct=0.30)
    assert borderline in t["weights"]


def test_turnover_cap_limits_change():
    scored = _scored(30, seed=3)
    prev = {f"{i:06d}": 0.075 for i in range(12)}  # fully invested elsewhere
    t = build_target_portfolio(scored, prev_weights=prev, n_holdings=12, max_turnover=0.30)
    assert t["turnover"] <= 0.30 + 1e-6


def test_rebalance_orders_buy_sell_classification():
    pf = Portfolio.from_dict({
        "cash": 50000,
        "positions": [{"code": "600519", "name": "A", "shares": 100,
                       "cost_price": 100, "current_price": 100}],
    })
    # target drops the held name and buys a new one
    target = {"000001": 0.2}
    orders = rebalance_orders(pf, target, prices={"000001": 10.0})
    actions = {o["code"]: o["action"] for o in orders}
    assert actions["600519"] == "SELL"
    assert actions["000001"] == "BUY"
