"""Tests for the economically-grounded factor library + neutralization (M3)."""
from __future__ import annotations

import numpy as np

from finbot.data import Warehouse
from finbot.data.provider import MockProvider
from finbot.features.registry import FACTOR_REGISTRY, build_factor_panel, registry_view


def _wh(tmp_path):
    wh = Warehouse(root=tmp_path / "wh", benchmark="000985")
    wh.update("2026-05-29", provider=MockProvider(), history_days=120)
    return wh


def test_factor_panel_has_expected_columns(tmp_path):
    panel = build_factor_panel(_wh(tmp_path))
    assert not panel.empty
    assert {"date", "code", "sector"}.issubset(panel.columns)
    for fam_factor in ("momentum_20_5", "reversal_5", "low_vol_20",
                       "fx_sensitivity", "commodity_sensitivity"):
        assert fam_factor in panel.columns


def test_neutralized_factors_are_standardized(tmp_path):
    panel = build_factor_panel(_wh(tmp_path), neutralize=True)
    last = panel[panel["date"] == panel["date"].max()]
    for col in ("momentum_20_5", "reversal_5", "low_vol_20", "news_sentiment",
                "fx_sensitivity", "commodity_sensitivity"):
        # cross-sectional mean ~ 0 after z-scoring/residualizing
        assert abs(last[col].mean()) < 1e-6
        assert last[col].std() > 0


def test_registry_view_matches_registry():
    view = registry_view()
    assert len(view) == len(FACTOR_REGISTRY)
    assert {"name", "family", "direction", "economic_note"}.issubset(view.columns)
