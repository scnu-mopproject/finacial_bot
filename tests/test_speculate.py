"""Tests for the demoted speculative watch (M8)."""
from __future__ import annotations

from finbot.data import Warehouse
from finbot.data.provider import MockProvider
from finbot.speculate import RISK_LABEL, speculative_watch


def test_speculative_watch_ranks_with_risk_flag(tmp_path):
    wh = Warehouse(root=tmp_path / "wh", benchmark="000985")
    wh.update("2026-05-29", provider=MockProvider(), history_days=120)
    watch = speculative_watch(wh, top_n=10)
    assert not watch.empty
    assert len(watch) <= 10
    assert {"code", "heat", "sealed_today"}.issubset(watch.columns)
    # heat sorted descending
    assert watch["heat"].is_monotonic_decreasing
    # the module ships a prominent risk label
    assert "投机" in RISK_LABEL and "不是涨停概率" in RISK_LABEL
