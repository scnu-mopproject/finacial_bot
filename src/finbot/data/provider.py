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


class TushareProvider(DataProvider):
    """Tushare Pro provider — token-based API, friendly to datacenter IPs.

    Recommended for server deployments: AkShare scrapes web endpoints that often
    block cloud IPs, whereas Tushare is a proper data API. Backfill is done
    by-trade-date (one call returns the whole market for a day) via ``daily_bars``
    + ``trading_dates``, which the warehouse uses to stay within rate limits.

    Macro/news need higher Tushare points, so they degrade to mock for now.
    """

    name = "tushare"

    def __init__(self, token: Optional[str] = None, fallback_to_mock: bool = True):
        import os
        import tushare as ts

        token = token or os.environ.get("TUSHARE_TOKEN")
        if not token:
            raise ValueError("TUSHARE_TOKEN not set (env or config data.tushare_token)")
        self.pro = ts.pro_api(token)
        self.fallback = fallback_to_mock
        self._mock = MockProvider()
        self._basic_cache = None

    @staticmethod
    def _ts_code(code: str) -> str:
        c = str(code).zfill(6)
        if c.startswith("6"):
            return c + ".SH"
        if c.startswith(("0", "3")):
            return c + ".SZ"
        return c + ".BJ"

    def _retry(self, fn, fallback_fn, tries: int = 3, base_sleep: float = 1.0):
        import time

        last = None
        for i in range(tries):
            try:
                return fn()
            except Exception as exc:  # noqa: BLE001 - retry on rate limit / transient
                last = exc
                time.sleep(base_sleep * (i + 1))
        if self.fallback:
            log.warning("tushare call failed (%s); using mock fallback", last)
            return fallback_fn()
        raise last  # type: ignore[misc]

    def _basics(self) -> pd.DataFrame:
        if self._basic_cache is None:
            try:
                self._basic_cache = self.pro.stock_basic(
                    exchange="", list_status="L", fields="ts_code,symbol,name,industry"
                )
            except Exception as exc:  # noqa: BLE001 - permission-gated; degrade
                log.warning("tushare stock_basic unavailable (%s); no name/sector", exc)
                self._basic_cache = pd.DataFrame(columns=["ts_code", "symbol", "name", "industry"])
        return self._basic_cache

    # -- bulk helpers used by the warehouse fast path --------------------
    def trading_dates(self, start: str, end: str):
        # Avoid trade_cal (often permission-gated). Derive the calendar from a
        # liquid reference stock's daily bars — only needs the `daily` interface.
        import datetime as _dt

        try:
            df = self.pro.daily(ts_code="000001.SZ",
                                start_date=start.replace("-", ""), end_date=end.replace("-", ""))
            ds = sorted(df["trade_date"].astype(str))
            if ds:
                return [f"{d[:4]}-{d[4:6]}-{d[6:]}" for d in ds]
        except Exception as exc:  # noqa: BLE001
            log.warning("tushare daily-calendar failed (%s); using weekdays", exc)
        # last resort: weekdays in range (akshare-free, offline-safe)
        s = _dt.datetime.strptime(start, "%Y-%m-%d")
        e = _dt.datetime.strptime(end, "%Y-%m-%d")
        out, cur = [], s
        while cur <= e:
            if cur.weekday() < 5:
                out.append(cur.strftime("%Y-%m-%d"))
            cur += _dt.timedelta(days=1)
        return out

    def daily_bars(self, date: str) -> pd.DataFrame:
        """Whole-market OHLCV for one trade date (one API call)."""
        def _real() -> pd.DataFrame:
            df = self.pro.daily(trade_date=date.replace("-", "")).rename(columns={"vol": "volume"})
            df["code"] = df["ts_code"].str.split(".").str[0]
            df["date"] = date
            return df[["date", "code", "open", "high", "low", "close", "volume"]]

        return self._retry(_real, lambda: pd.DataFrame())

    # -- DataProvider interface -----------------------------------------
    def universe(self, date: str) -> pd.DataFrame:
        def _real() -> pd.DataFrame:
            d = date.replace("-", "")
            daily = self.pro.daily(trade_date=d)            # essential (daily interface)
            daily["code"] = daily["ts_code"].str.split(".").str[0]
            daily["pct_chg"] = pd.to_numeric(daily["pct_chg"], errors="coerce")
            daily["amount_yi"] = pd.to_numeric(daily["amount"], errors="coerce") / 1e5  # 千元->亿元
            out = daily[["code", "ts_code", "close", "pct_chg", "amount_yi"]].copy()

            # optional: name / sector from stock_basic
            basic = self._basics()
            out = out.merge(basic[["ts_code", "name", "industry"]], on="ts_code", how="left") \
                if not basic.empty else out.assign(name="", industry="")

            # optional: turnover from daily_basic (permission-gated; best-effort)
            try:
                db = self.pro.daily_basic(trade_date=d, fields="ts_code,turnover_rate")
                out = out.merge(db, on="ts_code", how="left")
            except Exception as exc:  # noqa: BLE001
                log.warning("tushare daily_basic unavailable (%s); turnover=NaN", exc)
                out["turnover_rate"] = float("nan")

            out["name"] = out.get("name", "").fillna("")
            out["sector"] = out.get("industry", "").fillna("")
            out["turnover_rate"] = pd.to_numeric(out.get("turnover_rate"), errors="coerce")
            out["is_st"] = out["name"].astype(str).str.contains("ST", na=False)
            out["limit_up"] = out["pct_chg"] >= 9.8
            cols = ["code", "name", "sector", "close", "pct_chg",
                    "amount_yi", "turnover_rate", "is_st", "limit_up"]
            return out[cols].dropna(subset=["code"]).reset_index(drop=True)

        return self._retry(_real, lambda: self._mock.universe(date))

    def history(self, code: str, date: str, days: int = 120) -> pd.DataFrame:
        import datetime as _dt

        def _real() -> pd.DataFrame:
            end = date.replace("-", "")
            start = (_dt.datetime.strptime(date, "%Y-%m-%d")
                     - _dt.timedelta(days=days * 2 + 15)).strftime("%Y%m%d")
            df = self.pro.daily(ts_code=self._ts_code(code), start_date=start, end_date=end)
            df = df.sort_values("trade_date").tail(days).rename(columns={"vol": "volume"})
            df["date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d")
            return df[["date", "open", "high", "low", "close", "volume"]]

        return self._retry(_real, lambda: self._mock.history(code, date, days))

    def index_bars(self, code: str, date: str, days: int = 120) -> pd.DataFrame:
        def _real() -> pd.DataFrame:
            ts_code = code if "." in code else code + (".CSI" if code.startswith("000") else ".SH")
            df = self.pro.index_daily(ts_code=ts_code, end_date=date.replace("-", ""))
            df = df.sort_values("trade_date").tail(days)
            df["date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d")
            return df[["date", "close"]]

        return self._retry(_real, lambda: self._mock.index_bars(code, date, days))

    def macro(self, date: str, days: int = 120) -> pd.DataFrame:
        # Cross-asset macro needs higher Tushare points; degrade to mock for now.
        return self._mock.macro(date, days)

    def news(self, date: str) -> pd.DataFrame:
        # Tushare news needs higher points; degrade to mock for now.
        return self._mock.news(date)


def get_provider(source: str = "akshare", fallback_to_mock: bool = True,
                 token: Optional[str] = None) -> DataProvider:
    """Factory used by the pipeline / CLI."""
    if source == "mock":
        return MockProvider()
    if source == "tushare":
        try:
            return TushareProvider(token=token, fallback_to_mock=fallback_to_mock)
        except Exception as exc:  # noqa: BLE001 - no token / tushare not installed
            if fallback_to_mock:
                log.warning("tushare unavailable (%s); using mock provider", exc)
                return MockProvider()
            raise
    if source == "akshare":
        try:
            return AkShareProvider(fallback_to_mock=fallback_to_mock)
        except Exception as exc:  # noqa: BLE001 - akshare not installed, etc.
            if fallback_to_mock:
                log.warning("akshare unavailable (%s); using mock provider", exc)
                return MockProvider()
            raise
    raise ValueError(f"unknown data source: {source!r}")
