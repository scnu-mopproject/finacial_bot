"""Tests for regime classification and the end-to-end portfolio pipeline (M7)."""
from __future__ import annotations

from finbot import regime as regime_mod
from finbot.config import load_config
from finbot import pipeline
from finbot.data import Warehouse
from finbot.data.provider import MockProvider
from finbot.portfolio import Portfolio


def _mock_cfg(tmp_path):
    cfg = load_config()
    cfg.raw.setdefault("data", {})["source"] = "mock"
    cfg.raw["data"]["warehouse_dir"] = str(tmp_path / "wh")
    cfg.raw.setdefault("report", {})["output_dir"] = str(tmp_path / "reports")
    return cfg


def test_regime_classifies(tmp_path):
    wh = Warehouse(root=tmp_path / "wh", benchmark="000985")
    wh.update("2026-05-29", provider=MockProvider(), history_days=120)
    reg = regime_mod.classify(wh)
    assert reg["regime"] in ("risk-on", "neutral", "risk-off")
    assert 0 < reg["suggested_exposure"] <= 0.9
    assert "signals" in reg and "breadth_up_frac" in reg["signals"]


def test_run_portfolio_end_to_end(tmp_path):
    cfg = _mock_cfg(tmp_path)
    summary = pipeline.run_portfolio("2026-05-29", cfg, portfolio=Portfolio.empty(100000))
    assert "report" in summary
    assert summary["n_holdings"] > 0
    assert summary["regime"] in ("risk-on", "neutral", "risk-off")
    # report file written
    from pathlib import Path
    assert Path(summary["report"]).exists()
