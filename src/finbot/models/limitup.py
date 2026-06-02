"""Limit-up probability ranker.

Two modes:

1. ``lightgbm`` — loads a trained LightGBM model from ``model.store_dir`` and
   scores the feature matrix. Use :meth:`LimitUpRanker.train` to fit one from a
   labelled history (label = "did this stock hit limit-up the next day").
2. ``rule_score`` — a transparent weighted-factor fallback used when no trained
   model exists yet, so the pipeline produces sensible rankings on day one.

IMPORTANT: the output is a *relative ranking score in [0, 1]*, not a calibrated
probability and certainly not a guarantee. Limit-ups are driven by reflexive
capital and news flow that no factor model fully captures. Treat the top-N as a
research watchlist, never as a trade signal on its own.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

# Feature columns the model/score consumes. Missing columns are treated as 0.
FEATURE_COLUMNS: List[str] = [
    "mom_5d", "mom_20d", "volatility_20d", "above_ma20", "ma_bull_align",
    "vol_ratio_5d", "dist_from_high_60d", "amount_yi", "turnover_rate",
    "active_money", "limitups_20d", "is_limit_up_today", "pct_chg_today",
    "sector_sentiment", "sector_news_count", "sector_strength",
]

# Hand-tuned weights for the transparent fallback. Sign reflects the intuition:
# momentum / volume surge / bullish MA / sector heat raise the score; high
# realized volatility and being far below the recent high lower it.
_RULE_WEIGHTS = {
    "mom_5d": 1.5, "mom_20d": 0.6, "volatility_20d": -0.8, "above_ma20": 0.4,
    "ma_bull_align": 0.6, "vol_ratio_5d": 0.7, "dist_from_high_60d": 0.5,
    "active_money": 0.05, "limitups_20d": 0.25, "is_limit_up_today": 0.5,
    "sector_sentiment": 0.8, "sector_news_count": 0.06, "sector_strength": 0.12,
}


def _matrix(features: pd.DataFrame) -> pd.DataFrame:
    X = features.reindex(columns=FEATURE_COLUMNS).astype(float).fillna(0.0)
    return X


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


def rule_score(features: pd.DataFrame) -> np.ndarray:
    """Transparent fallback score in [0, 1] using standardized factor weights."""
    X = _matrix(features)
    z = (X - X.mean()) / (X.std(ddof=0) + 1e-9)  # standardize across the day's universe
    raw = np.zeros(len(X))
    for col, w in _RULE_WEIGHTS.items():
        if col in z.columns:
            raw += w * z[col].to_numpy()
    return _sigmoid(raw)


class LimitUpRanker:
    """Scores a feature matrix and returns a ranked candidate table."""

    def __init__(self, model_type: str = "lightgbm", store_dir: str = "models_store"):
        self.model_type = model_type
        self.store_dir = Path(store_dir)
        self._model = None
        if model_type == "lightgbm":
            self._try_load()

    # -- persistence -----------------------------------------------------
    @property
    def model_path(self) -> Path:
        return self.store_dir / "limitup_lgbm.txt"

    def _try_load(self) -> None:
        if not self.model_path.exists():
            log.info("no trained model at %s; will use rule_score fallback", self.model_path)
            return
        try:
            import lightgbm as lgb

            self._model = lgb.Booster(model_file=str(self.model_path))
            log.info("loaded LightGBM model from %s", self.model_path)
        except Exception as exc:  # noqa: BLE001
            log.warning("failed to load LightGBM model (%s); using rule_score", exc)
            self._model = None

    # -- inference -------------------------------------------------------
    def score(self, features: pd.DataFrame) -> np.ndarray:
        if self._model is not None:
            X = _matrix(features)
            return np.asarray(self._model.predict(X), dtype=float)
        return rule_score(features)

    def rank(self, features: pd.DataFrame, top_n: int = 20, min_score: float = 0.0) -> pd.DataFrame:
        """Return the top-N candidates as a tidy table with scores and key drivers."""
        if features.empty:
            return pd.DataFrame(columns=["code", "name", "sector", "score"])
        scores = self.score(features)
        out = features[["code", "name", "sector"]].copy()
        out["score"] = np.round(scores, 4)
        # Surface the strongest standardized drivers so the LLM agent can explain picks.
        out["drivers"] = _top_drivers(features)
        out = out[out["score"] >= min_score].sort_values("score", ascending=False)
        return out.head(top_n).reset_index(drop=True)

    # -- training (skeleton) --------------------------------------------
    def train(self, X: pd.DataFrame, y: pd.Series) -> "LimitUpRanker":
        """Fit a LightGBM classifier where ``y`` = next-day limit-up (1/0).

        This is the hook for offline training on a labelled factor history.
        Build that history with ``finbot.features.build_features`` over past dates
        and label each row with whether the stock hit limit-up the *next* session.
        """
        import lightgbm as lgb

        Xm = X.reindex(columns=FEATURE_COLUMNS).astype(float).fillna(0.0)
        dtrain = lgb.Dataset(Xm, label=y.astype(int))
        params = {
            "objective": "binary",
            "metric": "auc",
            "learning_rate": 0.05,
            "num_leaves": 31,
            "feature_fraction": 0.8,
            "bagging_fraction": 0.8,
            "verbose": -1,
        }
        self._model = lgb.train(params, dtrain, num_boost_round=300)
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self._model.save_model(str(self.model_path))
        log.info("trained and saved LightGBM model to %s", self.model_path)
        return self


def _top_drivers(features: pd.DataFrame, k: int = 3) -> List[str]:
    """For each row, name the k factors that most stand out vs the day's universe."""
    X = _matrix(features)
    z = (X - X.mean()) / (X.std(ddof=0) + 1e-9)
    drivers = []
    for _, r in z.iterrows():
        top = r.reindex(_RULE_WEIGHTS.keys()).abs().sort_values(ascending=False).head(k)
        drivers.append(", ".join(top.index.tolist()))
    return drivers
