"""End-to-end smoke test on the deterministic mock data source.

Runs the full pipeline for a fixed date and asserts each stage produces a
non-empty, well-formed result. No network or trained model required.
"""
from __future__ import annotations

import pandas as pd

from finbot.config import load_config
from finbot import pipeline
from finbot.strategy import Portfolio, build_strategy

DATE = "2026-06-02"


def _mock_cfg():
    cfg = load_config()
    cfg.raw.setdefault("data", {})["source"] = "mock"
    return cfg


def test_crawl_produces_universe_and_news():
    cfg = _mock_cfg()
    out = pipeline.stage_crawl(DATE, cfg)
    assert out["data_source"] == "mock"
    assert out["n_universe"] > 0
    assert out["n_news"] > 0


def test_features_and_predict():
    cfg = _mock_cfg()
    pipeline.stage_crawl(DATE, cfg)
    feats = pipeline.stage_features(DATE, cfg)
    assert not feats.empty
    assert "code" in feats.columns

    candidates = pipeline.stage_predict(DATE, cfg)
    assert not candidates.empty
    assert {"code", "name", "score"}.issubset(candidates.columns)
    # scores are bounded ranking scores in [0, 1]
    assert candidates["score"].between(0, 1).all()
    # sorted descending
    assert candidates["score"].is_monotonic_decreasing


def test_strategy_respects_position_count_limit():
    cfg = _mock_cfg()
    candidates = pipeline.stage_predict(DATE, cfg)
    portfolio = Portfolio.empty(cash=100000.0)
    plan = build_strategy(
        candidates, portfolio,
        risk={"max_new_positions": 2, "max_position_pct": 0.15, "max_total_exposure": 0.9},
        style="balanced",
    )
    assert len(plan["new_entries"]) <= 2
    assert "disclaimer" in plan


def test_full_run_writes_report(tmp_path):
    cfg = _mock_cfg()
    summary = pipeline.run_daily(DATE, cfg)
    assert summary["n_candidates"] > 0
    report = pd.io.common.get_handle  # noqa: F841 - touch pandas to ensure import
    assert summary["report"].endswith(".md")
