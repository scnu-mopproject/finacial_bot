"""Tests for forward-return labels (M2): correctness + no look-ahead."""
from __future__ import annotations

import numpy as np
import pandas as pd

from finbot.data import Warehouse
from finbot.data.provider import MockProvider
from finbot.labels import _forward_return, forward_returns


def test_forward_return_math():
    # closes for consecutive sessions; H=2, entry=t+1, exit=t+1+H
    close = pd.Series([10.0, 11.0, 12.0, 13.2, 14.0, 15.0])
    fr = _forward_return(close, h=2)
    # at t=0: entry=close[1]=11, exit=close[3]=13.2 -> 0.2
    assert np.isclose(fr.iloc[0], 13.2 / 11.0 - 1.0)
    # last (H+1) entries cannot have a full forward window -> NaN
    assert fr.iloc[-1] != fr.iloc[-1]  # NaN
    assert fr.iloc[-2] != fr.iloc[-2]  # NaN


def _populated_wh(tmp_path):
    wh = Warehouse(root=tmp_path / "wh", benchmark="000985")
    wh.update("2026-05-29", provider=MockProvider(), history_days=120)
    return wh


def test_forward_returns_have_excess_and_no_lookahead(tmp_path):
    wh = _populated_wh(tmp_path)
    h = 5
    fr = forward_returns(wh, h=h)
    assert {"date", "code", "fwd_ret", "bench_fwd_ret", "excess_ret"}.issubset(fr.columns)
    # excess = stock - benchmark
    sample = fr.dropna().iloc[0]
    assert np.isclose(sample["excess_ret"], sample["fwd_ret"] - sample["bench_fwd_ret"])

    # No look-ahead: for each code, the most recent H+1 dates must be unlabeled.
    last_dates = sorted(fr["date"].unique())[-(h + 1):]
    tail = fr[fr["date"].isin(last_dates)]
    assert tail["fwd_ret"].isna().all()


def test_build_dataset_drops_unlabeled(tmp_path):
    from finbot.labels import build_dataset

    wh = _populated_wh(tmp_path)
    panel = build_dataset(wh, h=5, out_dir=tmp_path / "datasets")
    assert not panel.empty
    assert panel["excess_ret"].notna().all()  # unlabeled rows dropped
