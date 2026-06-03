"""Tests for the local incremental data warehouse (M1)."""
from __future__ import annotations

from finbot.data import Warehouse
from finbot.data.provider import MockProvider


def _wh(tmp_path):
    return Warehouse(root=tmp_path / "wh", benchmark="000985")


def test_first_update_backfills_all_tables(tmp_path):
    wh = _wh(tmp_path)
    provider = MockProvider()
    counts = wh.update("2026-05-29", provider=provider, history_days=120, incremental_days=10)
    assert counts["daily_bar"] > 0
    assert counts["daily_basic"] > 0
    assert counts["macro"] > 0
    assert counts["index_bar"] > 0
    # watermarks set for every table
    for table in ("daily_bar", "daily_basic", "index_bar", "macro", "news"):
        assert wh.watermark(table) == "2026-05-29"


def test_incremental_update_is_additive_and_dedupes(tmp_path):
    wh = _wh(tmp_path)
    provider = MockProvider()
    wh.update("2026-05-29", provider=provider, history_days=120)
    rows_before = wh.status()["daily_bar"]["rows"]

    wh.update("2026-06-01", provider=provider, incremental_days=10)
    rows_after = wh.status()["daily_bar"]["rows"]

    # strictly grew (new dates) but did not duplicate existing (date, code) keys
    assert rows_after > rows_before
    bars = wh.bars()
    assert not bars.duplicated(subset=["date", "code"]).any()
    assert wh.watermark("daily_bar") == "2026-06-01"


def test_reads_filter_by_code_and_date(tmp_path):
    wh = _wh(tmp_path)
    wh.update("2026-05-29", provider=MockProvider(), history_days=120)

    one = wh.bars(codes=["600519"], start="2026-05-01")
    assert not one.empty
    assert set(one["code"].unique()) == {"600519"}
    assert (one["date"] >= "2026-05-01").all()

    macro = wh.macro(series=["USDCNY"], start="2026-05-01")
    assert not macro.empty
    assert set(macro["series"].unique()) == {"USDCNY"}
