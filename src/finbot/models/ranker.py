"""Cross-sectional forward-return ranking model (design §7 / M5).

A LightGBM ranker over the neutralized factor panel. When no trained model
exists (or LightGBM isn't installed), it transparently falls back to the
equal-weight ``composite_score`` so the pipeline always produces a ranking.

Training is **walk-forward**: fit on a trailing window, predict the next
out-of-sample block, roll forward — this is how we estimate honest IC and avoid
look-ahead. The final model (fit on all history) is persisted for live scoring.

Output is a relative cross-sectional ranking score, NOT a probability or a
guarantee. See design §3 and §15.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from ..features.registry import FACTOR_REGISTRY, NEUTRALIZE_CONTROLS, composite_score

log = logging.getLogger(__name__)

# Alpha factors fed to the model (exclude pure neutralization controls like size).
ALPHA_FACTORS: List[str] = [n for n in FACTOR_REGISTRY if n not in NEUTRALIZE_CONTROLS]


def _matrix(panel: pd.DataFrame) -> pd.DataFrame:
    return panel.reindex(columns=ALPHA_FACTORS).astype(float).fillna(0.0)


class Ranker:
    """Scores a factor panel and returns ranked candidates."""

    def __init__(self, store_dir: str = "models_store", model_file: str = "ranker_lgbm.txt"):
        self.store_dir = Path(store_dir)
        self.model_path = self.store_dir / model_file
        self._model = None
        self._load()

    def _load(self) -> None:
        if not self.model_path.exists():
            return
        try:
            import lightgbm as lgb

            self._model = lgb.Booster(model_file=str(self.model_path))
            log.info("loaded ranker model from %s", self.model_path)
        except Exception as exc:  # noqa: BLE001
            log.warning("failed to load ranker (%s); using composite fallback", exc)

    @property
    def using_model(self) -> bool:
        return self._model is not None

    def score(self, panel: pd.DataFrame) -> np.ndarray:
        if self._model is not None:
            return np.asarray(self._model.predict(_matrix(panel)), dtype=float)
        return composite_score(panel).to_numpy()

    def rank(self, panel: pd.DataFrame, top_n: int = 20, date: Optional[str] = None) -> pd.DataFrame:
        """Return top-N for a single date (latest if ``date`` is None)."""
        if panel.empty:
            return pd.DataFrame(columns=["code", "name", "sector", "score"])
        d = date or panel["date"].max()
        day = panel[panel["date"] == d].copy()
        day["score"] = self.score(day)
        day["drivers"] = _top_drivers(day)
        cols = [c for c in ["code", "sector", "score", "drivers"] if c in day.columns]
        return day.sort_values("score", ascending=False)[cols].head(top_n).reset_index(drop=True)

    # -- walk-forward training ------------------------------------------
    def train_walkforward(
        self, panel: pd.DataFrame, label_col: str = "excess_ret",
        train_window: int = 120, step: int = 20, min_train: int = 60,
    ) -> Dict:
        """Walk-forward fit; returns OOS rank-IC summary and persists final model.

        Requires LightGBM. ``panel`` must carry ALPHA_FACTORS + label_col + date.
        """
        try:
            import lightgbm as lgb
        except ImportError:
            return {"error": "lightgbm not installed; `pip install lightgbm`. "
                             "Until then the ranker uses the composite fallback."}
        from ..backtest.metrics import ic_summary, rank_ic

        data = panel.dropna(subset=[label_col]).copy()
        dates = sorted(data["date"].unique())
        oos_frames: List[pd.DataFrame] = []
        params = {"objective": "regression", "metric": "l2", "learning_rate": 0.05,
                  "num_leaves": 31, "feature_fraction": 0.8, "bagging_fraction": 0.8,
                  "min_data_in_leaf": 50, "verbose": -1}

        i = max(min_train, train_window)
        while i < len(dates):
            train_dates = dates[max(0, i - train_window):i]
            test_dates = dates[i:i + step]
            tr = data[data["date"].isin(train_dates)]
            te = data[data["date"].isin(test_dates)]
            if tr.empty or te.empty:
                break
            booster = lgb.train(params, lgb.Dataset(_matrix(tr), label=tr[label_col]),
                                num_boost_round=200)
            pred = te[["date", "code", label_col]].copy()
            pred["score"] = booster.predict(_matrix(te))
            oos_frames.append(pred)
            i += step

        if not oos_frames:
            return {"error": "insufficient history for walk-forward; ingest more data"}
        oos = pd.concat(oos_frames, ignore_index=True)
        ic = rank_ic(oos, score_col="score", label_col=label_col)

        # Final model on all data, persisted for live scoring.
        self.store_dir.mkdir(parents=True, exist_ok=True)
        final = lgb.train(params, lgb.Dataset(_matrix(data), label=data[label_col]),
                          num_boost_round=200)
        final.save_model(str(self.model_path))
        self._model = final
        return {"oos_ic_summary": ic_summary(ic), "n_oos_obs": int(len(oos)),
                "model_path": str(self.model_path),
                "note": "OOS rank-IC from walk-forward; score is relative ranking, not a guarantee."}


def _top_drivers(day: pd.DataFrame, k: int = 3) -> List[str]:
    """Name the k factors that stand out most for each row (already z-scored)."""
    X = day.reindex(columns=ALPHA_FACTORS).astype(float).fillna(0.0)
    out = []
    for _, r in X.iterrows():
        top = r.abs().sort_values(ascending=False).head(k)
        out.append(", ".join(top.index.tolist()))
    return out
