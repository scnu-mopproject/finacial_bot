"""Local incremental data warehouse (design doc §4).

Downloads market + macro data once and updates it incrementally, so the daily
pipeline reads from local Parquet in milliseconds instead of issuing thousands
of HTTP calls every run. This is also the prerequisite for backtesting (§8).

Tables (long/tidy format, stored under ``data/warehouse/``):
  - daily_bar   : date, code, open, high, low, close, volume        (per stock)
  - daily_basic : date, code, name, sector, amount_yi, turnover_rate, is_st
  - index_bar   : date, code, close                                 (benchmarks)
  - macro       : date, series, value                               (cross-asset)
  - news        : datetime, title, sector, polarity, source

Each table has a watermark (last ingested date) in ``_meta.json`` so updates are
incremental. Storage prefers Parquet and falls back to CSV when pyarrow is absent.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import pandas as pd

from .provider import DataProvider, get_provider

log = logging.getLogger(__name__)

# Merge keys used to de-duplicate when appending incremental pulls.
_TABLE_KEYS: Dict[str, List[str]] = {
    "daily_bar": ["date", "code"],
    "daily_basic": ["date", "code"],
    "index_bar": ["date", "code"],
    "macro": ["date", "series"],
    "news": ["datetime", "title"],
}


class Warehouse:
    """File-backed incremental store. All reads are local and fast."""

    def __init__(self, root: str | Path = "data/warehouse", benchmark: str = "000985"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.benchmark = benchmark  # default 中证全指

    # -- low-level storage ----------------------------------------------
    def _path(self, table: str, ext: str) -> Path:
        return self.root / f"{table}.{ext}"

    def _read_table(self, table: str) -> Optional[pd.DataFrame]:
        pq, csv = self._path(table, "parquet"), self._path(table, "csv")
        if pq.exists():
            try:
                return pd.read_parquet(pq)
            except Exception:  # noqa: BLE001
                pass
        if csv.exists():
            return pd.read_csv(csv, dtype={"code": str})
        return None

    def _write_table(self, table: str, df: pd.DataFrame) -> None:
        try:
            df.to_parquet(self._path(table, "parquet"), index=False)
        except Exception:  # noqa: BLE001 - no pyarrow; csv is always available
            df.to_csv(self._path(table, "csv"), index=False)

    def _merge(self, table: str, new: pd.DataFrame) -> pd.DataFrame:
        keys = _TABLE_KEYS[table]
        existing = self._read_table(table)
        combined = new if existing is None else pd.concat([existing, new], ignore_index=True)
        combined = combined.drop_duplicates(subset=keys, keep="last")
        if "date" in combined.columns:
            combined = combined.sort_values(["date", *[k for k in keys if k != "date"]])
        self._write_table(table, combined.reset_index(drop=True))
        return combined

    # -- watermarks ------------------------------------------------------
    @property
    def _meta_path(self) -> Path:
        return self.root / "_meta.json"

    def _meta(self) -> Dict[str, str]:
        if self._meta_path.exists():
            return json.loads(self._meta_path.read_text(encoding="utf-8"))
        return {}

    def watermark(self, table: str) -> Optional[str]:
        return self._meta().get(table)

    def _set_watermark(self, table: str, date: str) -> None:
        meta = self._meta()
        meta[table] = date
        self._meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    # -- incremental update ---------------------------------------------
    def update(
        self,
        asof: str,
        provider: Optional[DataProvider] = None,
        source: str = "akshare",
        history_days: int = 250,
        incremental_days: int = 10,
    ) -> Dict[str, int]:
        """Ingest data up to ``asof``.

        On first run (empty table) pulls ``history_days`` of history; afterwards
        only ``incremental_days`` to catch up cheaply. Returns row counts ingested.
        """
        provider = provider or get_provider(source)
        first_run = self.watermark("daily_bar") is None
        days = history_days if first_run else incremental_days
        log.info("warehouse update asof=%s source=%s first_run=%s days=%d",
                 asof, provider.name, first_run, days)

        universe = provider.universe(asof)
        counts: Dict[str, int] = {}

        # daily_basic: snapshot of the universe at asof (one row per stock per day)
        basic = universe.copy()
        basic.insert(0, "date", asof)
        keep = [c for c in ["date", "code", "name", "sector", "amount_yi", "turnover_rate", "is_st"]
                if c in basic.columns]
        counts["daily_basic"] = len(self._merge("daily_basic", basic[keep]))

        # daily_bar: per-stock OHLCV history (the expensive call; cheap once cached)
        bars: List[pd.DataFrame] = []
        for code in universe["code"]:
            h = provider.history(code, asof, days=days)
            if h is None or h.empty:
                continue
            h = h.copy()
            h["code"] = code
            bars.append(h)
        if bars:
            bar_df = pd.concat(bars, ignore_index=True)
            counts["daily_bar"] = len(self._merge("daily_bar", bar_df))

        # index_bar: benchmark
        idx = provider.index_bars(self.benchmark, asof, days=days).copy()
        idx["code"] = self.benchmark
        counts["index_bar"] = len(self._merge("index_bar", idx))

        # macro: cross-asset panel
        counts["macro"] = len(self._merge("macro", provider.macro(asof, days=days)))

        # news: append today's
        counts["news"] = len(self._merge("news", provider.news(asof)))

        for table in ("daily_bar", "daily_basic", "index_bar", "macro", "news"):
            self._set_watermark(table, asof)
        log.info("warehouse update done: %s", counts)
        return counts

    # -- reads (local, fast) --------------------------------------------
    def bars(self, codes: Optional[Iterable[str]] = None, start: str | None = None,
             end: str | None = None) -> pd.DataFrame:
        return self._read_range("daily_bar", codes, start, end)

    def basics(self, codes: Optional[Iterable[str]] = None, start: str | None = None,
               end: str | None = None) -> pd.DataFrame:
        return self._read_range("daily_basic", codes, start, end)

    def index_bar(self, start: str | None = None, end: str | None = None) -> pd.DataFrame:
        return self._read_range("index_bar", [self.benchmark], start, end)

    def macro(self, series: Optional[Iterable[str]] = None, start: str | None = None,
              end: str | None = None) -> pd.DataFrame:
        df = self._read_range("macro", None, start, end)
        if series is not None and not df.empty:
            df = df[df["series"].isin(list(series))]
        return df

    def news(self, start: str | None = None, end: str | None = None) -> pd.DataFrame:
        df = self._read_table("news")
        if df is None:
            return pd.DataFrame()
        if "datetime" in df.columns and (start or end):
            day = df["datetime"].astype(str).str.slice(0, 10)
            if start:
                df = df[day >= start]
            if end:
                df = df[day <= end]
        return df.reset_index(drop=True)

    def _read_range(self, table: str, codes, start, end) -> pd.DataFrame:
        df = self._read_table(table)
        if df is None:
            return pd.DataFrame()
        if codes is not None and "code" in df.columns:
            df = df[df["code"].astype(str).isin([str(c) for c in codes])]
        if "date" in df.columns:
            if start:
                df = df[df["date"] >= start]
            if end:
                df = df[df["date"] <= end]
        return df.reset_index(drop=True)

    def status(self) -> Dict[str, Dict]:
        """Summary of what's stored — used by the CLI / update-data skill."""
        out: Dict[str, Dict] = {}
        for table in _TABLE_KEYS:
            df = self._read_table(table)
            out[table] = {
                "rows": 0 if df is None else int(len(df)),
                "watermark": self.watermark(table),
            }
        return out
