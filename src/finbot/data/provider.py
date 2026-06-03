"""Data provider abstraction.

The rest of the codebase only depends on this interface. Today there are two
implementations: an AkShare-backed provider and a deterministic mock provider.
Swapping in Tushare later only means adding another subclass.
"""
from __future__ import annotations

import logging
from typing import Optional

import pandas as pd

from . import mock

log = logging.getLogger(__name__)


class DataProvider:
    """Interface every concrete provider implements.

    All methods return tidy :class:`pandas.DataFrame` objects with stable column
    names so downstream feature code never has to special-case the source.
    """

    name = "base"

    def universe(self, date: str) -> pd.DataFrame:
        """Tradable A-share snapshot for ``date``.

        Columns: code, name, sector, close, pct_chg, amount_yi, turnover_rate,
        is_st, limit_up.
        """
        raise NotImplementedError

    def history(self, code: str, date: str, days: int = 120) -> pd.DataFrame:
        """Daily OHLCV for ``code`` ending at ``date``.

        Columns: date, open, high, low, close, volume.
        """
        raise NotImplementedError

    def news(self, date: str) -> pd.DataFrame:
        """Finance news for ``date``.

        Columns: datetime, title, sector, polarity, source.
        ``polarity`` may be NaN when the source provides no sentiment; the
        sentiment feature builder fills it.
        """
        raise NotImplementedError

    def macro(self, date: str, days: int = 120) -> pd.DataFrame:
        """Cross-asset macro panel ending at ``date`` (commodities/FX/rates).

        Long format columns: date, series, value. See ``mock.MACRO_SERIES``
        for the series catalog (USDCNY, USDCNH, DXY, GOLD, OIL, COPPER, yields).
        """
        raise NotImplementedError

    def index_bars(self, code: str, date: str, days: int = 120) -> pd.DataFrame:
        """Benchmark index daily history ending at ``date``.

        Columns: date, close.
        """
        raise NotImplementedError


class MockProvider(DataProvider):
    name = "mock"

    def universe(self, date: str) -> pd.DataFrame:
        return mock.mock_universe(date)

    def history(self, code: str, date: str, days: int = 120) -> pd.DataFrame:
        return mock.mock_history(code, date, days)

    def news(self, date: str) -> pd.DataFrame:
        return mock.mock_news(date)

    def macro(self, date: str, days: int = 120) -> pd.DataFrame:
        return mock.mock_macro(date, days)

    def index_bars(self, code: str, date: str, days: int = 120) -> pd.DataFrame:
        return mock.mock_index(code, date, days)


class AkShareProvider(DataProvider):
    """AkShare-backed provider.

    Each method is wrapped so a network / interface failure degrades to mock
    data (when ``fallback_to_mock`` is on) instead of crashing the daily run.
    The AkShare interface names are pinned in one place for easy maintenance.
    """

    name = "akshare"

    def __init__(self, fallback_to_mock: bool = True):
        import akshare as ak  # imported lazily; only needed for this provider

        self.ak = ak
        self.fallback = fallback_to_mock
        self._mock = MockProvider()

    # -- helpers ---------------------------------------------------------
    def _guard(self, fn, fallback_fn):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - degrade gracefully on any failure
            if self.fallback:
                log.warning("akshare call failed (%s); using mock fallback", exc)
                return fallback_fn()
            raise

    # -- interface -------------------------------------------------------
    def universe(self, date: str) -> pd.DataFrame:
        def _real() -> pd.DataFrame:
            # Real-time spot board for the whole market.
            spot = self.ak.stock_zh_a_spot_em()
            df = spot.rename(
                columns={
                    "代码": "code",
                    "名称": "name",
                    "最新价": "close",
                    "涨跌幅": "pct_chg",
                    "成交额": "amount",
                    "换手率": "turnover_rate",
                }
            )
            df["amount_yi"] = pd.to_numeric(df["amount"], errors="coerce") / 1e8
            df["is_st"] = df["name"].str.contains("ST", na=False)
            df["limit_up"] = pd.to_numeric(df["pct_chg"], errors="coerce") >= 9.8
            df["sector"] = ""  # enriched separately by the board-linkage feature
            cols = [
                "code", "name", "sector", "close", "pct_chg",
                "amount_yi", "turnover_rate", "is_st", "limit_up",
            ]
            return df[cols].dropna(subset=["code"]).reset_index(drop=True)

        return self._guard(_real, lambda: self._mock.universe(date))

    def history(self, code: str, date: str, days: int = 120) -> pd.DataFrame:
        def _real() -> pd.DataFrame:
            end = date.replace("-", "")
            df = self.ak.stock_zh_a_hist(
                symbol=code, period="daily", end_date=end, adjust="qfq"
            )
            df = df.rename(
                columns={
                    "日期": "date", "开盘": "open", "最高": "high",
                    "最低": "low", "收盘": "close", "成交量": "volume",
                }
            )
            return df[["date", "open", "high", "low", "close", "volume"]].tail(days)

        return self._guard(_real, lambda: self._mock.history(code, date, days))

    def news(self, date: str) -> pd.DataFrame:
        def _real() -> pd.DataFrame:
            # Global finance fast-news feed; sentiment is filled downstream.
            df = self.ak.stock_info_global_cls(symbol="全部")
            df = df.rename(columns={"标题": "title", "发布时间": "datetime"})
            df["source"] = "cls"
            df["sector"] = ""
            df["polarity"] = float("nan")
            keep = [c for c in ["datetime", "title", "sector", "polarity", "source"] if c in df.columns]
            return df[keep]

        return self._guard(_real, lambda: self._mock.news(date))

    def macro(self, date: str, days: int = 120) -> pd.DataFrame:
        # Real cross-asset pulls vary a lot by AkShare version / availability, so
        # this is intentionally best-effort and degrades to deterministic mock.
        # TODO(M1+): wire concrete interfaces, e.g.
        #   USDCNY/USDCNH -> ak.currency_boc_sina / ak.fx_spot_quote
        #   DXY/GOLD/OIL  -> ak.futures_foreign_hist / ak.macro_*
        #   CN10Y/US10Y   -> ak.bond_zh_us_rate
        return self._guard(lambda: self._mock.macro(date, days), lambda: self._mock.macro(date, days))

    def index_bars(self, code: str, date: str, days: int = 120) -> pd.DataFrame:
        def _real() -> pd.DataFrame:
            end = date.replace("-", "")
            df = self.ak.index_zh_a_hist(symbol=code, period="daily", end_date=end)
            df = df.rename(columns={"日期": "date", "收盘": "close"})
            return df[["date", "close"]].tail(days)

        return self._guard(_real, lambda: self._mock.index_bars(code, date, days))


def get_provider(source: str = "akshare", fallback_to_mock: bool = True) -> DataProvider:
    """Factory used by the pipeline / CLI."""
    if source == "mock":
        return MockProvider()
    if source == "akshare":
        try:
            return AkShareProvider(fallback_to_mock=fallback_to_mock)
        except Exception as exc:  # noqa: BLE001 - akshare not installed, etc.
            if fallback_to_mock:
                log.warning("akshare unavailable (%s); using mock provider", exc)
                return MockProvider()
            raise
    raise ValueError(f"unknown data source: {source!r}")
