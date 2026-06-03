"""Economically-grounded factor library + neutralization (design §5 / M3).

Each factor has an *economic prior* (a documented risk premium or behavioral
bias) and a direction. Factors are computed across the whole warehouse panel
(vectorized over a date×code close matrix), then per-date **neutralized**:
winsorize -> z-score -> residualize against industry dummies + log size.
Neutralization is what separates relative stock-selection skill (alpha) from
simply loading on size/industry/beta risk premia.

Add a factor: write a builder, register it in ``FACTOR_REGISTRY`` with its
economic note + direction. The cross-asset family (FX/commodity sensitivity)
follows the §5 anti-look-ahead discipline (uses only info up to date t).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd

from ..data import Warehouse

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class FactorSpec:
    name: str
    family: str
    direction: int          # +1: higher is better, -1: lower is better, 0: regime/contextual
    economic_note: str


# Catalog (metadata). Builders live below; this drives docs + the registry view.
FACTOR_REGISTRY: Dict[str, FactorSpec] = {
    "momentum_20_5": FactorSpec("momentum_20_5", "momentum", +1, "中期动量延续(剔除最近5日反转噪声)；行为：反应不足"),
    "momentum_60_5": FactorSpec("momentum_60_5", "momentum", +1, "中期动量(60日)"),
    "reversal_5": FactorSpec("reversal_5", "reversal", +1, "短期反转(取负的5日收益)；A股散户主导"),
    "low_vol_20": FactorSpec("low_vol_20", "low_vol", +1, "低波动异象(取负的20日波动)；彩票偏好致高波高估"),
    "turnover": FactorSpec("turnover", "liquidity", -1, "高换手常伴随散户过度交易/拥挤"),
    "illiquidity": FactorSpec("illiquidity", "liquidity", +1, "Amihud非流动性溢价"),
    "amount_size": FactorSpec("amount_size", "size", 0, "规模代理；作为中性化对象，不直接当alpha"),
    "news_sentiment": FactorSpec("news_sentiment", "sentiment", +1, "板块新闻情绪；情绪扩散，短期动量(易反转)"),
    "fx_sensitivity": FactorSpec("fx_sensitivity", "cross_asset", 0, "对人民币(USDCNY)的beta×汇率趋势；出口/外资传导"),
    "commodity_sensitivity": FactorSpec("commodity_sensitivity", "cross_asset", 0, "对油/金的beta×商品趋势；资源链成本/收入传导"),
    # value / quality require fundamentals; specs declared, builders return NaN
    # under mock until a fundamentals source is wired (graceful no-op).
    "ep": FactorSpec("ep", "value", +1, "盈利收益率(E/P)；估值均值回归(需基本面数据)"),
    "roe": FactorSpec("roe", "quality", +1, "ROE；优质企业长期跑赢(需基本面数据)"),
}

# Factors used as neutralization controls rather than alpha signals.
NEUTRALIZE_CONTROLS = ["amount_size"]
# Macro series and the trend window for the cross-asset family.
_FX_SERIES, _COMMO_SERIES = "USDCNY", "OIL"
_BETA_WINDOW, _TREND_WINDOW = 60, 20


# --- helpers -------------------------------------------------------------
def _pivot_close(bars: pd.DataFrame) -> pd.DataFrame:
    return bars.pivot_table(index="date", columns="code", values="close").sort_index()


def _rolling_beta(ret: pd.DataFrame, factor_ret: pd.Series, window: int) -> pd.DataFrame:
    """Vectorized rolling beta of every column in ``ret`` onto ``factor_ret``."""
    f = factor_ret.reindex(ret.index)
    cov = (ret.mul(f, axis=0)).rolling(window).mean().sub(
        ret.rolling(window).mean().mul(f.rolling(window).mean(), axis=0)
    )
    var = f.rolling(window).var()
    return cov.div(var, axis=0)


def _winsorize(s: pd.Series, k: float = 3.0) -> pd.Series:
    med = s.median()
    mad = (s - med).abs().median()
    if mad == 0 or np.isnan(mad):
        lo, hi = s.quantile(0.01), s.quantile(0.99)
    else:
        lo, hi = med - k * 1.4826 * mad, med + k * 1.4826 * mad
    return s.clip(lo, hi)


def _zscore(s: pd.Series) -> pd.Series:
    sd = s.std(ddof=0)
    return (s - s.mean()) / sd if sd and not np.isnan(sd) else s * 0.0


def neutralize_cross_section(df: pd.DataFrame, factor_cols: List[str]) -> pd.DataFrame:
    """Per-date: winsorize -> z-score -> residualize on industry dummies + log size.

    ``df`` is a single date's slice with columns: code, sector, amount_size, factors.
    Returns the same frame with neutralized factor columns.
    """
    out = df.copy()
    # design matrix: industry dummies + log size
    X_parts = []
    if "sector" in out.columns and out["sector"].notna().any():
        X_parts.append(pd.get_dummies(out["sector"].fillna("NA"), prefix="ind", dtype=float))
    size = out.get("amount_size")
    if size is not None:
        X_parts.append(pd.DataFrame({"log_size": np.log(size.clip(lower=1e-6))}, index=out.index))
    X = pd.concat(X_parts, axis=1) if X_parts else pd.DataFrame(index=out.index)
    X = X.assign(_const=1.0).fillna(0.0).to_numpy()

    for col in factor_cols:
        s = _zscore(_winsorize(out[col].astype(float).fillna(out[col].median())))
        if X.shape[1] > 1 and np.isfinite(s).all():
            beta, *_ = np.linalg.lstsq(X, s.to_numpy(), rcond=None)
            resid = s.to_numpy() - X @ beta
            out[col] = _zscore(pd.Series(resid, index=out.index))
        else:
            out[col] = s
    return out


# --- panel builder -------------------------------------------------------
def build_factor_panel(
    wh: Warehouse,
    start: str | None = None,
    end: str | None = None,
    neutralize: bool = True,
) -> pd.DataFrame:
    """Compute the neutralized factor panel for all (date, code) in the warehouse.

    Returns a long frame: date, code, sector, <factor columns>.
    Value/quality columns are present but NaN until fundamentals are wired.
    """
    bars = wh.bars(start=start, end=end)
    if bars.empty:
        return pd.DataFrame()
    close = _pivot_close(bars)
    ret = close.pct_change()

    # --- price/volume factors (vectorized over the wide matrix) ---
    feats: Dict[str, pd.DataFrame] = {}
    feats["momentum_20_5"] = close.shift(5) / close.shift(25) - 1.0
    feats["momentum_60_5"] = close.shift(5) / close.shift(65) - 1.0
    feats["reversal_5"] = -(close / close.shift(5) - 1.0)
    feats["low_vol_20"] = -ret.rolling(20).std()

    # --- cross-asset macro sensitivity (beta × trend), anti-look-ahead ---
    macro = wh.macro(start=start, end=end)
    if not macro.empty:
        mw = macro.pivot_table(index="date", columns="series", values="value").sort_index()
        for fac_name, series in (("fx_sensitivity", _FX_SERIES), ("commodity_sensitivity", _COMMO_SERIES)):
            if series in mw.columns:
                m_ret = mw[series].pct_change()
                beta = _rolling_beta(ret, m_ret, _BETA_WINDOW)
                trend = m_ret.rolling(_TREND_WINDOW).mean()           # macro trend up to t
                feats[fac_name] = beta.mul(trend.reindex(beta.index), axis=0)

    # melt price/macro factors to long
    long_frames = []
    for name, wide in feats.items():
        lf = wide.stack().rename(name).reset_index()
        lf.columns = ["date", "code", name]
        long_frames.append(lf)
    panel = long_frames[0]
    for lf in long_frames[1:]:
        panel = panel.merge(lf, on=["date", "code"], how="outer")

    # --- basics-derived factors (turnover, size, illiquidity) ---
    basics = wh.basics(start=start, end=end)
    if not basics.empty:
        b = basics.copy()
        b["amount_size"] = pd.to_numeric(b.get("amount_yi"), errors="coerce")
        b["turnover"] = pd.to_numeric(b.get("turnover_rate"), errors="coerce")
        panel = panel.merge(b[["date", "code", "sector", "amount_size", "turnover"]],
                            on=["date", "code"], how="left")
        # Amihud illiquidity proxy: |daily ret| / amount (higher = less liquid)
        amt = panel["amount_size"].replace(0, np.nan)
        rr = panel.merge(ret.stack().rename("r").reset_index().rename(columns={"level_1": "code"}),
                         on=["date", "code"], how="left")["r"]
        panel["illiquidity"] = (rr.abs() / amt).to_numpy()
    else:
        panel["sector"], panel["amount_size"], panel["turnover"], panel["illiquidity"] = "", np.nan, np.nan, np.nan

    # --- news sentiment by sector (joined on date+sector) ---
    news = wh.news(start=start, end=end)
    if not news.empty and "sector" in news.columns:
        news = news.copy()
        news["d"] = news["datetime"].astype(str).str.slice(0, 10)
        news["polarity"] = pd.to_numeric(news["polarity"], errors="coerce")
        sent = news.groupby(["d", "sector"])["polarity"].mean().rename("news_sentiment").reset_index()
        panel = panel.merge(sent, left_on=["date", "sector"], right_on=["d", "sector"], how="left").drop(columns=["d"])
    panel["news_sentiment"] = panel.get("news_sentiment", pd.Series(index=panel.index, dtype=float)).fillna(0.0)

    # value/quality placeholders (need fundamentals)
    for col in ("ep", "roe"):
        panel[col] = np.nan

    factor_cols = [c for c in FACTOR_REGISTRY if c in panel.columns]
    panel = panel.dropna(subset=["momentum_20_5"]).reset_index(drop=True)

    if neutralize:
        alpha_cols = [c for c in factor_cols if c not in NEUTRALIZE_CONTROLS
                      and panel[c].notna().any()]
        # Explicit loop (not groupby.apply) so the 'date' column is preserved
        # across pandas versions.
        parts = [neutralize_cross_section(g, alpha_cols) for _, g in panel.groupby("date")]
        panel = pd.concat(parts, ignore_index=True)
    return panel


def registry_view() -> pd.DataFrame:
    """Tabular view of the factor catalog (for docs / the analyze-market skill)."""
    return pd.DataFrame(
        [{"name": s.name, "family": s.family, "direction": s.direction, "economic_note": s.economic_note}
         for s in FACTOR_REGISTRY.values()]
    )
