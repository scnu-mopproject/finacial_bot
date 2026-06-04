"""Risk model helpers (design §9).

A light, dependency-free risk layer: a shrinkage covariance estimator
(Ledoit-Wolf style toward a diagonal target) and inverse-volatility weights.
The constructor can use these for risk-aware sizing; equal-weight remains the
robust default.
"""
from __future__ import annotations

from typing import Dict

import numpy as np
import pandas as pd


def shrink_cov(returns: pd.DataFrame, shrinkage: float = 0.3) -> pd.DataFrame:
    """Sample covariance shrunk toward its diagonal (reduces estimation error).

    ``returns`` is a date×code return matrix. ``shrinkage`` in [0,1]: 0 = sample
    cov, 1 = pure diagonal.
    """
    r = returns.dropna(how="all", axis=1).fillna(0.0)
    sample = r.cov()
    target = pd.DataFrame(np.diag(np.diag(sample)), index=sample.index, columns=sample.columns)
    return (1 - shrinkage) * sample + shrinkage * target


def inverse_vol_weights(vol: Dict[str, float]) -> Dict[str, float]:
    """Risk-parity-ish weights ∝ 1/volatility, normalized to sum to 1."""
    inv = {c: (1.0 / v) for c, v in vol.items() if v and v > 0}
    s = sum(inv.values())
    if s <= 0:
        n = len(vol)
        return {c: 1.0 / n for c in vol} if n else {}
    return {c: w / s for c, w in inv.items()}
