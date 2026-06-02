"""Strategy construction from candidates + the user's live portfolio.

Produces a *deterministic, risk-bounded* action plan:

- manage existing positions (stop-loss / take-profit / hold based on P&L)
- propose new entries from the ranked candidates, sized by the risk config
- respect exposure / position-count / cash limits

This is the quantitative scaffold. The ``strategy-advisor`` Claude agent layers
judgement on top: it reads this plan plus the news context and writes the final
narrative, flags conflicts, and can veto entries. Nothing here is investment
advice; sizes are illustrative and bounded by your own config.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd


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


_STYLE_FACTOR = {"conservative": 0.6, "balanced": 1.0, "aggressive": 1.4}


def _manage_existing(portfolio: Portfolio, risk: Dict, candidate_codes: set) -> List[Dict]:
    actions: List[Dict] = []
    stop = risk.get("stop_loss_pct", -0.07)
    take = risk.get("take_profit_pct", 0.15)
    for p in portfolio.positions:
        if p.pnl_pct <= stop:
            action, reason = "SELL", f"触发止损线 ({p.pnl_pct:.1%} <= {stop:.0%})"
        elif p.pnl_pct >= take:
            action, reason = "TRIM", f"触发止盈线 ({p.pnl_pct:.1%} >= {take:.0%})，建议减仓锁定利润"
        elif p.code in candidate_codes:
            action, reason = "ADD", "已持仓且今日仍在候选榜，趋势延续可考虑加仓"
        else:
            action, reason = "HOLD", f"当前盈亏 {p.pnl_pct:.1%}，未触发风控线"
        actions.append(
            {
                "code": p.code,
                "name": p.name,
                "action": action,
                "pnl_pct": round(p.pnl_pct, 4),
                "market_value": round(p.market_value, 2),
                "reason": reason,
            }
        )
    return actions


def _propose_entries(
    portfolio: Portfolio, candidates: pd.DataFrame, risk: Dict, style: str
) -> List[Dict]:
    equity = portfolio.equity or 1.0
    max_pos_pct = risk.get("max_position_pct", 0.15)
    max_new = int(risk.get("max_new_positions", 3))
    max_expo = risk.get("max_total_exposure", 0.90)
    style_factor = _STYLE_FACTOR.get(style, 1.0)

    held = {p.code for p in portfolio.positions}
    current_expo = sum(p.market_value for p in portfolio.positions) / equity
    available_expo = max(0.0, max_expo - current_expo)
    cash = portfolio.cash

    proposals: List[Dict] = []
    for _, c in candidates.iterrows():
        if len(proposals) >= max_new or available_expo <= 0 or cash <= 0:
            break
        if c["code"] in held:
            continue
        # Size = base cap, scaled by conviction (score) and style, bounded by
        # remaining exposure and cash.
        target_pct = min(max_pos_pct * float(c["score"]) * style_factor, max_pos_pct, available_expo)
        budget = min(target_pct * equity, cash)
        if budget < 1000:  # too small to bother
            continue
        proposals.append(
            {
                "code": c["code"],
                "name": c.get("name", ""),
                "sector": c.get("sector", ""),
                "action": "BUY",
                "score": float(c["score"]),
                "suggested_pct": round(budget / equity, 4),
                "suggested_amount": round(budget, 2),
                "drivers": c.get("drivers", ""),
            }
        )
        cash -= budget
        available_expo -= budget / equity
    return proposals


def build_strategy(
    candidates: pd.DataFrame,
    portfolio: Portfolio,
    risk: Optional[Dict] = None,
    style: str = "balanced",
) -> Dict:
    """Build the full action plan. Returns a JSON-serializable dict."""
    risk = risk or {}
    candidate_codes = set(candidates["code"]) if not candidates.empty else set()
    existing = _manage_existing(portfolio, risk, candidate_codes)
    entries = _propose_entries(portfolio, candidates, risk, style)

    return {
        "summary": {
            "equity": round(portfolio.equity, 2),
            "cash": round(portfolio.cash, 2),
            "n_positions": len(portfolio.positions),
            "realized_pnl_ytd": round(portfolio.realized_pnl_ytd, 2),
            "style": style,
            "n_new_buys": len(entries),
        },
        "manage_existing": existing,
        "new_entries": entries,
        "disclaimer": (
            "以上为基于因子模型与风控参数的程序化建议，仅供研究参考，不构成投资建议。"
            "市场有风险，决策与风险由使用者自行承担。"
        ),
    }
