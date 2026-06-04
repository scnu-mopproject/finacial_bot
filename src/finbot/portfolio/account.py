"""Account model + reconciliation: target weights -> rebalance orders (design §9 / §9B).

Owns the live-holdings model (Portfolio/Position) and compares a constructed
target portfolio against current holdings, emitting the buy/sell/trim/add order
list the user executes at next-day open (respecting T+1).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

MIN_TRADE_VALUE = 1000.0  # skip dust trades


@dataclass
class Position:
    code: str
    name: str
    shares: float
    cost_price: float
    current_price: float

    @property
    def market_value(self) -> float:
        return self.shares * self.current_price

    @property
    def pnl_pct(self) -> float:
        if self.cost_price <= 0:
            return 0.0
        return self.current_price / self.cost_price - 1.0


@dataclass
class Portfolio:
    cash: float = 0.0
    positions: List[Position] = field(default_factory=list)
    risk_tolerance: str = "balanced"
    realized_pnl_ytd: float = 0.0

    @property
    def equity(self) -> float:
        return self.cash + sum(p.market_value for p in self.positions)

    @classmethod
    def from_file(cls, path: str | Path) -> "Portfolio":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: Dict) -> "Portfolio":
        positions = [
            Position(
                code=str(p["code"]),
                name=p.get("name", ""),
                shares=float(p["shares"]),
                cost_price=float(p["cost_price"]),
                current_price=float(p.get("current_price", p["cost_price"])),
            )
            for p in data.get("positions", [])
        ]
        return cls(
            cash=float(data.get("cash", 0.0)),
            positions=positions,
            risk_tolerance=data.get("risk_tolerance", "balanced"),
            realized_pnl_ytd=float(data.get("realized_pnl_ytd", 0.0)),
        )

    @classmethod
    def empty(cls, cash: float = 100000.0) -> "Portfolio":
        return cls(cash=cash)


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
