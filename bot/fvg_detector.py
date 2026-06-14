"""
fvg_detector.py - Fair Value Gap detection and mitigation.

Bullish FVG: Candle 1 high < Candle 3 low → gap between them.
Bearish FVG: Candle 1 low > Candle 3 high.

Mitigation: When price closes inside (or wicks significantly) the gap.
We keep only unmitigated + recent FVGs for confluence.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional, Literal
import pandas as pd
import numpy as np

from config import FVGConfig


@dataclass
class FVG:
    symbol: str
    side: Literal["bullish", "bearish"]
    top: float
    bottom: float
    creation_bar: int
    gap_size: float
    gap_pct: float
    mitigated: bool = False
    mitigation_price: Optional[float] = None


class FVGDetector:
    def __init__(self, cfg: FVGConfig):
        self.cfg = cfg
        self.active_fvgs: List[FVG] = []

    def update(self, symbol: str, df: pd.DataFrame) -> List[FVG]:
        if len(df) < 10:
            return []

        new_fvgs: List[FVG] = []
        n = len(df)

        for i in range(2, n - 1):
            c1_high = df["high"].iloc[i-2]
            c1_low = df["low"].iloc[i-2]
            c3_high = df["high"].iloc[i]
            c3_low = df["low"].iloc[i]
            close = df["close"].iloc[i]

            # Bullish FVG
            if c1_high < c3_low:
                gap = c3_low - c1_high
                gap_pct = gap / c1_high
                if gap_pct >= self.cfg.min_gap_pct:
                    fvg = FVG(
                        symbol=symbol,
                        side="bullish",
                        top=c3_low,
                        bottom=c1_high,
                        creation_bar=i,
                        gap_size=gap,
                        gap_pct=gap_pct,
                    )
                    new_fvgs.append(fvg)

            # Bearish FVG
            if c1_low > c3_high:
                gap = c1_low - c3_high
                gap_pct = gap / c1_low
                if gap_pct >= self.cfg.min_gap_pct:
                    fvg = FVG(
                        symbol=symbol,
                        side="bearish",
                        top=c1_low,
                        bottom=c3_high,
                        creation_bar=i,
                        gap_size=gap,
                        gap_pct=gap_pct,
                    )
                    new_fvgs.append(fvg)

        # Add new and mitigate existing
        self.active_fvgs.extend(new_fvgs)
        self._apply_mitigation(df)

        # Prune
        if self.cfg.only_unmitigated:
            self.active_fvgs = [f for f in self.active_fvgs if not f.mitigated]

        self.active_fvgs = sorted(
            self.active_fvgs,
            key=lambda x: (x.creation_bar, -x.gap_pct)
        )[-self.cfg.max_active_fvgs:]

        return self.active_fvgs

    def _apply_mitigation(self, df: pd.DataFrame) -> None:
        latest_close = float(df["close"].iloc[-1])
        latest_high = float(df["high"].iloc[-1])
        latest_low = float(df["low"].iloc[-1])

        for fvg in self.active_fvgs:
            if fvg.mitigated:
                continue

            if fvg.side == "bullish":
                # Price entered the gap
                if self.cfg.mitigation_on_close:
                    if fvg.bottom <= latest_close <= fvg.top:
                        fvg.mitigated = True
                        fvg.mitigation_price = latest_close
                else:
                    if fvg.bottom <= latest_low <= fvg.top or fvg.bottom <= latest_high <= fvg.top:
                        fvg.mitigated = True
                        fvg.mitigation_price = latest_low

            else:  # bearish
                if self.cfg.mitigation_on_close:
                    if fvg.bottom <= latest_close <= fvg.top:
                        fvg.mitigated = True
                        fvg.mitigation_price = latest_close
                else:
                    if fvg.bottom <= latest_low <= fvg.top or fvg.bottom <= latest_high <= fvg.top:
                        fvg.mitigated = True
                        fvg.mitigation_price = latest_high

    def get_unmitigated_zones(self, side: Optional[str] = None) -> List[FVG]:
        res = [f for f in self.active_fvgs if not f.mitigated]
        if side:
            res = [f for f in res if f.side == side]
        return res

    def price_in_fvg(self, price: float, side: Optional[str] = None) -> bool:
        for f in self.get_unmitigated_zones(side):
            if f.bottom <= price <= f.top:
                return True
        return False
