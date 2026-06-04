"""Account reconciliation: target weights -> concrete rebalance orders (design §9 / §9B).

Compares the constructed target portfolio against the user's live holdings and
emits the buy/sell/trim/add order list they actually execute (at next-day open,
respecting T+1). Reuses the Portfolio/Position model.
"""
from __future__ import annotations

from typing import Dict, List, Optional

# Reuse the existing account model (consolidated in M9).
from ..strategy.portfolio import Portfolio, Position

MIN_TRADE_VALUE = 1000.0  # skip dust trades


def rebalance_orders(
    portfolio: Portfolio,
    target_weights: Dict[str, float],
    prices: Optional[Dict[str, float]] = None,
    names: Optional[Dict[str, str]] = None,
) -> List[Dict]:
    """Produce orders to move the portfolio toward ``target_weights``.

    ``prices`` supplies a reference price for names not currently held (for share
    sizing); held names use their position's current price.
    """
    prices = prices or {}
    names = names or {}
    equity = portfolio.equity or 1.0
    held = {p.code: p for p in portfolio.positions}
    cur_w = {p.code: p.market_value / equity for p in portfolio.positions}

    orders: List[Dict] = []
    for code in sorted(set(cur_w) | set(target_weights)):
        tgt = target_weights.get(code, 0.0)
        cur = cur_w.get(code, 0.0)
        delta_value = (tgt - cur) * equity
        if abs(delta_value) < MIN_TRADE_VALUE and not (tgt == 0 and cur > 0):
            action = "HOLD"
        elif cur == 0 and tgt > 0:
            action = "BUY"
        elif tgt == 0 and cur > 0:
            action = "SELL"          # full exit
        elif delta_value > 0:
            action = "ADD"
        else:
            action = "TRIM"

        price = held[code].current_price if code in held else prices.get(code)
        shares = round(delta_value / price) if price else None
        orders.append({
            "code": code,
            "name": names.get(code, held[code].name if code in held else ""),
            "action": action,
            "current_weight": round(cur, 4),
            "target_weight": round(tgt, 4),
            "delta_value": round(delta_value, 2),
            "ref_price": price,
            "delta_shares": shares,
        })
    # actionable orders first
    order_rank = {"SELL": 0, "TRIM": 1, "BUY": 2, "ADD": 3, "HOLD": 4}
    orders.sort(key=lambda o: order_rank.get(o["action"], 9))
    return orders
