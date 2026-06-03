"""Deterministic mock data so the end-to-end skeleton runs without network access.

Everything is seeded off the trading date string, so a given date always yields
the same universe / quotes / news — handy for tests and reproducible demos.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Dict, List

import numpy as np
import pandas as pd

_SECTORS = ["半导体", "新能源", "医药", "白酒", "人工智能", "军工", "证券", "地产", "消费电子", "光伏"]

_NAMES = {
    "600519": ("贵州茅台", "白酒"),
    "300750": ("宁德时代", "新能源"),
    "688981": ("中芯国际", "半导体"),
    "002594": ("比亚迪", "新能源"),
    "601012": ("隆基绿能", "光伏"),
    "000858": ("五粮液", "白酒"),
    "300059": ("东方财富", "证券"),
    "002230": ("科大讯飞", "人工智能"),
    "688256": ("寒武纪", "半导体"),
    "600276": ("恒瑞医药", "医药"),
    "000063": ("中兴通讯", "消费电子"),
    "600893": ("航发动力", "军工"),
    "001979": ("招商蛇口", "地产"),
    "300033": ("同花顺", "证券"),
    "002475": ("立讯精密", "消费电子"),
    "603259": ("药明康德", "医药"),
    "688111": ("金山办公", "人工智能"),
    "601138": ("工业富联", "消费电子"),
    "300124": ("汇川技术", "新能源"),
    "002241": ("歌尔股份", "消费电子"),
}


def _seed(date: str, salt: str = "") -> int:
    h = hashlib.sha256(f"{date}:{salt}".encode()).hexdigest()
    return int(h[:8], 16)


def mock_universe(date: str) -> pd.DataFrame:
    """Return the tradable universe with same-day OHLCV-ish snapshot."""
    rng = np.random.default_rng(_seed(date, "universe"))
    rows: List[Dict] = []
    for code, (name, sector) in _NAMES.items():
        base = 20 + (int(code[-3:]) % 200)
        pct = float(rng.normal(0.5, 3.5))
        close = round(base * (1 + pct / 100), 2)
        amount_yi = float(abs(rng.normal(8, 6)) + 0.6)
        turnover = float(abs(rng.normal(3, 2)) + 0.2)
        rows.append(
            {
                "code": code,
                "name": name,
                "sector": sector,
                "close": close,
                "pct_chg": round(pct, 2),
                "amount_yi": round(amount_yi, 2),
                "turnover_rate": round(turnover, 2),
                "is_st": False,
                "limit_up": pct >= 9.8,
            }
        )
    return pd.DataFrame(rows)


def mock_history(code: str, date: str, days: int = 120) -> pd.DataFrame:
    """Return a synthetic daily OHLCV history ending at ``date``."""
    rng = np.random.default_rng(_seed(code, "hist"))
    end = datetime.strptime(date, "%Y-%m-%d")
    base = 20 + (int(code[-3:]) % 200)
    closes = [base]
    for _ in range(days - 1):
        closes.append(max(1.0, closes[-1] * (1 + rng.normal(0.001, 0.025))))
    dates = [(end - timedelta(days=days - 1 - i)).strftime("%Y-%m-%d") for i in range(days)]
    closes = np.array(closes)
    df = pd.DataFrame(
        {
            "date": dates,
            "open": np.round(closes * (1 + rng.normal(0, 0.005, days)), 2),
            "high": np.round(closes * (1 + abs(rng.normal(0, 0.012, days))), 2),
            "low": np.round(closes * (1 - abs(rng.normal(0, 0.012, days))), 2),
            "close": np.round(closes, 2),
            "volume": np.round(abs(rng.normal(5e6, 2e6, days))).astype(int),
        }
    )
    return df


def mock_news(date: str, n: int = 25) -> pd.DataFrame:
    """Return synthetic finance headlines tagged with a (rough) sector & polarity."""
    rng = np.random.default_rng(_seed(date, "news"))
    templates = [
        ("{sector}板块迎政策利好，多家龙头获机构调研", 0.8),
        ("{sector}行业景气度回升，订单超预期", 0.6),
        ("{sector}概念活跃，资金大幅流入", 0.5),
        ("{sector}龙头业绩预增，市场情绪升温", 0.7),
        ("{sector}遭遇获利回吐，短期承压", -0.5),
        ("监管关注{sector}炒作，提示风险", -0.7),
        ("{sector}出口数据走弱，需求存隐忧", -0.4),
        ("{sector}传出技术突破传闻，真伪待证实", 0.2),
    ]
    rows = []
    for i in range(n):
        sector = _SECTORS[int(rng.integers(0, len(_SECTORS)))]
        tmpl, polarity = templates[int(rng.integers(0, len(templates)))]
        rows.append(
            {
                "datetime": f"{date} {8 + i % 10:02d}:{(i * 7) % 60:02d}",
                "title": tmpl.format(sector=sector),
                "sector": sector,
                "polarity": round(float(polarity + rng.normal(0, 0.1)), 2),
                "source": ["eastmoney", "sina", "cls"][i % 3],
            }
        )
    return pd.DataFrame(rows)


# --- macro / cross-asset (commodities, FX, rates) ------------------------
# Series we synthesize for the cross-asset macro module (design §4/§5/§10).
MACRO_SERIES = {
    "USDCNY": 7.15,   # onshore RMB
    "USDCNH": 7.16,   # offshore RMB
    "DXY": 104.0,     # US dollar index
    "GOLD": 2300.0,   # COMEX-ish gold
    "OIL": 80.0,      # crude
    "COPPER": 4.2,    # copper
    "US10Y": 4.2,     # US 10y yield (%)
    "CN10Y": 2.3,     # CN 10y yield (%)
}


def _trading_dates(end: str, days: int) -> List[str]:
    """Approximate trading days ending at ``end`` (skips weekends; mock only)."""
    out: List[str] = []
    cur = datetime.strptime(end, "%Y-%m-%d")
    while len(out) < days:
        if cur.weekday() < 5:  # Mon-Fri
            out.append(cur.strftime("%Y-%m-%d"))
        cur -= timedelta(days=1)
    return list(reversed(out))


def mock_macro(end: str, days: int = 120) -> pd.DataFrame:
    """Synthetic cross-asset macro panel ending at ``end``.

    Long format: date, series, value. Deterministic per series.
    """
    dates = _trading_dates(end, days)
    rows: List[Dict] = []
    for series, base in MACRO_SERIES.items():
        rng = np.random.default_rng(_seed(series, "macro"))
        level = base
        for d in dates:
            # gentle random walk; rates move in smaller steps
            step = rng.normal(0, 0.004 if series not in ("US10Y", "CN10Y") else 0.01)
            level = max(0.01, level * (1 + step)) if base > 20 else max(0.01, level + step)
            rows.append({"date": d, "series": series, "value": round(float(level), 4)})
    return pd.DataFrame(rows)


def mock_index(code: str, end: str, days: int = 120) -> pd.DataFrame:
    """Synthetic benchmark index daily close ending at ``end``.

    Columns: date, close. ``code`` is the index code (e.g. 000985 中证全指).
    """
    dates = _trading_dates(end, days)
    rng = np.random.default_rng(_seed(code, "index"))
    closes = [3000.0]
    for _ in range(len(dates) - 1):
        closes.append(max(1.0, closes[-1] * (1 + rng.normal(0.0003, 0.012))))
    return pd.DataFrame({"date": dates, "close": np.round(closes, 2)})
