"""Tests for the ranking model (M5). LightGBM is optional, so these exercise the
composite fallback path that must always work."""
from __future__ import annotations

import numpy as np
import pandas as pd

from finbot.data import Warehouse
from finbot.data.provider import MockProvider
from finbot.features.registry import build_factor_panel
from finbot.models import Ranker, ALPHA_FACTORS


def _wh(tmp_path):
    wh = Warehouse(root=tmp_path / "wh", benchmark="000985")
    wh.update("2026-05-29", provider=MockProvider(), history_days=120)
    return wh


def test_ranker_fallback_scores_and_ranks(tmp_path):
    panel = build_factor_panel(_wh(tmp_path))
    ranker = Ranker(store_dir=str(tmp_path / "models"))
    assert not ranker.using_model  # no trained model -> composite fallback
    ranked = ranker.rank(panel, top_n=5)
    assert len(ranked) == 5
    assert {"code", "score", "drivers"}.issubset(ranked.columns)
    assert ranked["score"].is_monotonic_decreasing


def test_score_length_matches_rows(tmp_path):
    panel = build_factor_panel(_wh(tmp_path))
    last = panel[panel["date"] == panel["date"].max()]
    s = Ranker(store_dir=str(tmp_path / "m")).score(last)
    assert len(s) == len(last)
    assert np.isfinite(s).all()


def test_alpha_factors_exclude_size_control():
    assert "amount_size" not in ALPHA_FACTORS
    assert "momentum_20_5" in ALPHA_FACTORS
