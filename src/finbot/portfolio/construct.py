"""Target-portfolio construction (design §9 / §9A).

Turns a scored cross-section into target weights, with the dynamic-rotation
discipline from §9A:
- **hysteresis**: enter only if ranked in the top ``enter_pct``; keep an existing
  holding until it drops out of the top ``hold_pct`` (avoids churn at the边界);
- **diversification**: cap basket size, per-name weight, and per-sector weight;
- **exposure**: scale to ``max_total_exposure`` (keep a cash buffer);
- **turnover cap**: limit how much the book changes per rebalance to save costs.

Weights are equal by default; pass ``vol`` for inverse-volatility sizing.
"""
from __future__ import annotations

from typing import Dict, Optional

import pandas as pd

from .risk import inverse_vol_weights


def _percentile_rank(scored: pd.DataFrame) -> pd.DataFrame:
    s = scored.sort_values("score", ascending=False).reset_index(drop=True)
    s["pct"] = (s.index + 1) / len(s)  # top name -> smallest pct
    return s


def _select(scored: pd.DataFrame, prev: Dict[str, float], n: int,
            enter_pct: float, hold_pct: float) -> list:
    s = _percentile_rank(scored)
    enter = list(s[s["pct"] <= enter_pct]["code"])
    hold = set(s[s["pct"] <= hold_pct]["code"])
    # keep prior holdings still inside the (wider) hold band, ordered by score
    keep = [c for c in s["code"] if c in prev and c in hold]
    # then add new high-rank entrants until we reach n
    additions = [c for c in enter if c not in keep]
    selected = (keep + additions)[:n]
    # if still short (early days / tiny universe), top up from best-ranked
    if len(selected) < n:
        for c in s["code"]:
            if c not in selected:
                selected.append(c)
            if len(selected) >= n:
                break
    return selected


def _apply_caps(weights: Dict[str, float], sectors: Dict[str, str],
                max_name: float, max_sector: float) -> Dict[str, float]:
    w = {c: min(v, max_name) for c, v in weights.items()}
    # sector cap: scale down any over-cap sector, then renormalize overall
    by_sector: Dict[str, float] = {}
    for c, v in w.items():
        by_sector[sectors.get(c, "")] = by_sector.get(sectors.get(c, ""), 0.0) + v
    for sec, tot in by_sector.items():
        if tot > max_sector and tot > 0:
            scale = max_sector / tot
            for c in w:
                if sectors.get(c, "") == sec:
                    w[c] *= scale
    return w


def _limit_turnover(target: Dict[str, float], prev: Dict[str, float], max_turnover: float) -> Dict[str, float]:
    codes = set(target) | set(prev)
    turnover = sum(abs(target.get(c, 0.0) - prev.get(c, 0.0)) for c in codes) / 2.0
    if turnover <= max_turnover or turnover == 0:
        return target
    alpha = max_turnover / turnover  # move only part-way toward target
    return {c: prev.get(c, 0.0) + alpha * (target.get(c, 0.0) - prev.get(c, 0.0)) for c in codes}


def build_target_portfolio(
    scored: pd.DataFrame,
    prev_weights: Optional[Dict[str, float]] = None,
    n_holdings: int = 12,
    enter_pct: float = 0.15,
    hold_pct: float = 0.30,
    max_total_exposure: float = 0.90,
    max_position_pct: float = 0.15,
    max_sector_pct: float = 0.40,
    max_turnover: float = 0.30,
    vol: Optional[Dict[str, float]] = None,
) -> Dict:
    """Return target weights + metadata for one rebalance date.

    ``scored`` has columns code, sector, score (one row per stock, single date).
    ``prev_weights`` are the current target weights (code -> weight); defaults empty.
    """
    prev = dict(prev_weights or {})
    if scored.empty:
        return {"weights": {}, "selected": [], "turnover": 0.0, "cash": 1.0}

    selected = _select(scored, prev, n_holdings, enter_pct, hold_pct)
    sectors = dict(zip(scored["code"], scored.get("sector", pd.Series(index=scored.index, dtype=str)).fillna("")))

    # base weights
    if vol:
        raw = inverse_vol_weights({c: vol.get(c, 0.0) for c in selected})
    else:
        raw = {c: 1.0 / len(selected) for c in selected} if selected else {}
    # scale to target exposure, then apply name/sector caps and renormalize
    weights = {c: w * max_total_exposure for c, w in raw.items()}
    weights = _apply_caps(weights, sectors, max_position_pct, max_sector_pct)
    tot = sum(weights.values())
    if tot > max_total_exposure and tot > 0:
        weights = {c: w * max_total_exposure / tot for c, w in weights.items()}

    weights = _limit_turnover(weights, prev, max_turnover)
    # Hard risk caps win over turnover smoothing: re-clip to the per-name cap so a
    # large legacy position is always trimmed toward the limit (only lowers risk).
    weights = {c: min(w, max_position_pct) for c, w in weights.items()}
    weights = {c: round(w, 4) for c, w in weights.items() if w > 1e-4}

    turnover = sum(abs(weights.get(c, 0.0) - prev.get(c, 0.0))
                   for c in set(weights) | set(prev)) / 2.0
    return {
        "weights": weights,
        "selected": list(weights.keys()),
        "turnover": round(turnover, 4),
        "cash": round(max(0.0, 1.0 - sum(weights.values())), 4),
    }
