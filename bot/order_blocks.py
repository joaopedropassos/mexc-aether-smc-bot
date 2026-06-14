"""
order_blocks.py - LuxAlgo-style Order Block detection and mitigation.

Key concepts implemented:
- Volume Pivot: Look for swing points where volume is significantly higher.
- Bullish OB: Last bearish candle before a strong impulsive bullish move from a volume pivot low.
- Bearish OB: Symmetric for shorts.
- Mitigation: Price wicks through or closes inside the OB zone (configurable).
- Limited memory: Keep only the most relevant recent OBs.

This is one of the highest confluence zones in the system.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional, Literal
import pandas as pd
import numpy as np

from config import OrderBlockConfig


@dataclass
class OrderBlock:
    symbol: str
    side: Literal["bullish", "bearish"]
    top: float          # High of the block zone
    bottom: float       # Low of the block zone
    volume: float
    strength: float     # Volume ratio vs average
    creation_bar: int   # Bar index when created
    mitigated: bool = False
    mitigation_price: Optional[float] = None
    mitigation_time: Optional[pd.Timestamp] = None


class OrderBlockDetector:
    def __init__(self, cfg: OrderBlockConfig):
        self.cfg = cfg
        self.active_bullish: List[OrderBlock] = []
        self.active_bearish: List[OrderBlock] = []

    def _find_volume_pivots(self, df: pd.DataFrame, length: int) -> pd.Series:
        """Simple volume pivot: local max volume in rolling window."""
        vol = df["volume"]
        return (vol == vol.rolling(length * 2 + 1, center=True).max())

    def _is_impulse_move(self, df: pd.DataFrame, idx: int, direction: int, lookforward: int = 3) -> bool:
        """Check if after the pivot we had a strong directional move."""
        if idx + lookforward >= len(df):
            return False
        closes = df["close"].iloc[idx: idx + lookforward + 1]
        body = abs(closes.iloc[-1] - closes.iloc[0])
        atr = df["atr"].iloc[idx] if "atr" in df.columns else (df["high"] - df["low"]).rolling(14).mean().iloc[idx]
        return body > 1.8 * atr

    def update(self, symbol: str, df: pd.DataFrame) -> List[OrderBlock]:
        """
        Process latest candles and maintain active OBs.
        Expects df with columns: open, high, low, close, volume, and preferably 'atr'.
        """
        if len(df) < 30:
            return []

        df = df.copy()
        if "atr" not in df.columns:
            tr = np.maximum(df["high"] - df["low"],
                            np.maximum(abs(df["high"] - df["close"].shift(1)),
                                       abs(df["low"] - df["close"].shift(1))))
            df["atr"] = tr.rolling(14).mean()

        volume_pivots = self._find_volume_pivots(df, self.cfg.volume_pivot_length)

        new_obs: List[OrderBlock] = []

        for i in range(5, len(df) - 2):
            if not volume_pivots.iloc[i]:
                continue

            # Bullish OB candidate: bearish candle + strong bullish follow-through
            if df["close"].iloc[i] < df["open"].iloc[i]:  # bearish candle
                if self._is_impulse_move(df, i, direction=1):
                    ob_top = df["high"].iloc[i]
                    ob_bottom = df["low"].iloc[i]
                    vol = df["volume"].iloc[i]
                    avg_vol = df["volume"].iloc[max(0, i-20):i].mean()
                    strength = vol / max(avg_vol, 1)

                    if strength >= self.cfg.min_ob_strength:
                        ob = OrderBlock(
                            symbol=symbol,
                            side="bullish",
                            top=ob_top,
                            bottom=ob_bottom,
                            volume=vol,
                            strength=strength,
                            creation_bar=i,
                        )
                        new_obs.append(ob)

            # Bearish OB
            if df["close"].iloc[i] > df["open"].iloc[i]:
                if self._is_impulse_move(df, i, direction=-1):
                    ob_top = df["high"].iloc[i]
                    ob_bottom = df["low"].iloc[i]
                    vol = df["volume"].iloc[i]
                    avg_vol = df["volume"].iloc[max(0, i-20):i].mean()
                    strength = vol / max(avg_vol, 1)

                    if strength >= self.cfg.min_ob_strength:
                        ob = OrderBlock(
                            symbol=symbol,
                            side="bearish",
                            top=ob_top,
                            bottom=ob_bottom,
                            volume=vol,
                            strength=strength,
                            creation_bar=i,
                        )
                        new_obs.append(ob)

        # Merge new with existing and mitigate
        self.active_bullish = self._merge_and_mitigate(self.active_bullish + [o for o in new_obs if o.side == "bullish"], df, "bullish")
        self.active_bearish = self._merge_and_mitigate(self.active_bearish + [o for o in new_obs if o.side == "bearish"], df, "bearish")

        # Prune old / weak
        self.active_bullish = self.active_bullish[-self.cfg.max_active_obs:]
        self.active_bearish = self.active_bearish[-self.cfg.max_active_obs:]

        return self.active_bullish + self.active_bearish

    def _merge_and_mitigate(self, obs: List[OrderBlock], df: pd.DataFrame, side: str) -> List[OrderBlock]:
        active = []
        latest_close = float(df["close"].iloc[-1])
        latest_high = float(df["high"].iloc[-1])
        latest_low = float(df["low"].iloc[-1])

        for ob in obs:
            if ob.mitigated:
                continue

            # Mitigation logic
            if self.cfg.mitigation_method == "close":
                if side == "bullish" and latest_close < ob.top and latest_close > ob.bottom:
                    ob.mitigated = True
                    ob.mitigation_price = latest_close
                elif side == "bearish" and latest_close > ob.bottom and latest_close < ob.top:
                    ob.mitigated = True
                    ob.mitigation_price = latest_close
            else:  # wick
                if side == "bullish" and latest_low < ob.top and latest_high > ob.bottom:
                    ob.mitigated = True
                    ob.mitigation_price = latest_low
                elif side == "bearish" and latest_high > ob.bottom and latest_low < ob.top:
                    ob.mitigated = True
                    ob.mitigation_price = latest_high

            if not ob.mitigated:
                active.append(ob)

        # Sort by strength descending
        active.sort(key=lambda x: x.strength, reverse=True)
        return active

    def get_active_zones(self, side: Optional[str] = None) -> List[OrderBlock]:
        if side == "bullish":
            return [o for o in self.active_bullish if not o.mitigated]
        if side == "bearish":
            return [o for o in self.active_bearish if not o.mitigated]
        return [o for o in (self.active_bullish + self.active_bearish) if not o.mitigated]

    def is_price_in_ob(self, price: float, side: Optional[str] = None) -> bool:
        zones = self.get_active_zones(side)
        for z in zones:
            if z.bottom <= price <= z.top:
                return True
        return False
