"""
hull_filter.py - Hull Moving Average (HMA) Suite as trend filter.

HMA reacts faster than SMA/EMA with less lag.
We use slope direction + color (classic LuxAlgo style) as bias confirmation.
Longs preferred only when HMA slope is up (and price above), etc.
"""

from __future__ import annotations
import pandas as pd
import numpy as np

from config import HullConfig


def wma(series: pd.Series, length: int) -> pd.Series:
    weights = np.arange(1, length + 1)
    return series.rolling(length).apply(lambda x: np.dot(x, weights) / weights.sum(), raw=True)


def hull_moving_average(close: pd.Series, length: int = 21) -> pd.Series:
    """Standard HMA formula."""
    half = int(length / 2)
    sqrt_len = int(np.sqrt(length))
    wma_half = wma(close, half)
    wma_full = wma(close, length)
    hma = wma(2 * wma_half - wma_full, sqrt_len)
    return hma


class HullFilter:
    def __init__(self, cfg: HullConfig):
        self.cfg = cfg
        self.hma: pd.Series | None = None
        self.slope: pd.Series | None = None

    def update(self, df: pd.DataFrame) -> str:
        if len(df) < self.cfg.hma_length + 5:
            return "neutral"

        close = df["close"]
        self.hma = hull_moving_average(close, self.cfg.hma_length)
        self.slope = self.hma.diff(self.cfg.confirm_bars)

        latest_hma = self.hma.iloc[-1]
        latest_slope = self.slope.iloc[-1]
        latest_price = close.iloc[-1]

        if pd.isna(latest_slope) or pd.isna(latest_hma):
            return "neutral"

        if latest_slope > 0 and latest_price > latest_hma:
            return "bullish"
        elif latest_slope < 0 and latest_price < latest_hma:
            return "bearish"
        return "neutral"

    def get_trend(self) -> str:
        if self.slope is None or len(self.slope) == 0 or pd.isna(self.slope.iloc[-1]):
            return "neutral"
        s = self.slope.iloc[-1]
        if s > 0:
            return "bullish"
        if s < 0:
            return "bearish"
        return "neutral"
