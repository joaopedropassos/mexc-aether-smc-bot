"""
ut_bot.py - UT Bot (ATR Trailing Stop) signals.

Classic implementation:
- ATR trailing stop that flips direction.
- Buy signal when price crosses above the trailing line from below.
- Sell signal when price crosses below from above.
- We keep only very recent signals for confluence.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Literal
import pandas as pd
import numpy as np

from config import UTBotConfig


@dataclass
class UTSignal:
    side: Literal["long", "short"]
    price: float
    bar_index: int
    atr_value: float


class UTBot:
    def __init__(self, cfg: UTBotConfig):
        self.cfg = cfg
        self.last_signals: list[UTSignal] = []

    def _calculate_trailing(self, df: pd.DataFrame) -> pd.Series:
        """Core UT Bot trailing stop line."""
        n = self.cfg.atr_period
        sens = self.cfg.sensitivity

        atr = (df["high"] - df["low"]).rolling(n).mean() * sens
        # Simple trailing (can be made more sophisticated with EMA on close)
        up = df["close"] - atr
        dn = df["close"] + atr

        trend = pd.Series(1, index=df.index)
        for i in range(1, len(df)):
            if df["close"].iloc[i] > up.iloc[i-1]:
                trend.iloc[i] = 1
            elif df["close"].iloc[i] < dn.iloc[i-1]:
                trend.iloc[i] = -1
            else:
                trend.iloc[i] = trend.iloc[i-1]

            if trend.iloc[i] > 0:
                up.iloc[i] = max(up.iloc[i], up.iloc[i-1])
            else:
                dn.iloc[i] = min(dn.iloc[i], dn.iloc[i-1])

        trailing = pd.Series(np.where(trend > 0, up, dn), index=df.index)
        return trailing, trend

    def update(self, df: pd.DataFrame) -> Optional[UTSignal]:
        if len(df) < self.cfg.atr_period + 5:
            return None

        trailing, trend = self._calculate_trailing(df)

        i = len(df) - 1
        prev_trend = trend.iloc[i-1]
        curr_trend = trend.iloc[i]

        signal = None

        if prev_trend <= 0 and curr_trend > 0:
            signal = UTSignal("long", float(df["close"].iloc[i]), i, float(trailing.iloc[i]))
        elif prev_trend >= 0 and curr_trend < 0:
            signal = UTSignal("short", float(df["close"].iloc[i]), i, float(trailing.iloc[i]))

        if signal:
            self.last_signals.append(signal)
            # Keep only recent
            self.last_signals = [s for s in self.last_signals if (i - s.bar_index) <= self.cfg.max_bars_since_signal]

        return signal

    def get_recent_signal(self, side: Optional[str] = None) -> Optional[UTSignal]:
        if not self.last_signals:
            return None
        if side:
            for s in reversed(self.last_signals):
                if s.side == side:
                    return s
            return None
        return self.last_signals[-1]
