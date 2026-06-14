"""
market_structure.py - Simplified but functional Market Structure (SMC).

Detects:
- Swing Highs / Lows (HH, HL, LH, LL)
- Break of Structure (BoS)
- Change of Character (CHoCH)

Gives a bias: "bullish", "bearish", or "neutral".
Used as a major filter for direction.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import List, Literal, Optional
import pandas as pd
import numpy as np

from config import MarketStructureConfig


@dataclass
class Swing:
    index: int
    price: float
    kind: Literal["high", "low"]


@dataclass
class StructureEvent:
    type: Literal["BOS", "CHoCH"]
    direction: Literal["bullish", "bearish"]
    price: float
    bar: int


class MarketStructure:
    def __init__(self, cfg: MarketStructureConfig):
        self.cfg = cfg
        self.swings: List[Swing] = []
        self.events: List[StructureEvent] = []
        self.bias: Literal["bullish", "bearish", "neutral"] = "neutral"

    def _find_swings(self, df: pd.DataFrame) -> List[Swing]:
        length = self.cfg.swing_length
        highs = df["high"]
        lows = df["low"]
        swings = []

        for i in range(length, len(df) - length):
            # Higher high / lower low style pivot
            if highs.iloc[i] == highs.iloc[i-length:i+length+1].max():
                swings.append(Swing(i, float(highs.iloc[i]), "high"))
            if lows.iloc[i] == lows.iloc[i-length:i+length+1].min():
                swings.append(Swing(i, float(lows.iloc[i]), "low"))
        return swings[-12:]  # keep recent

    def update(self, df: pd.DataFrame) -> StructureEvent | None:
        if len(df) < 20:
            return None

        self.swings = self._find_swings(df)
        if len(self.swings) < 3:
            return None

        latest = self.swings[-1]
        prev = self.swings[-2]
        prev2 = self.swings[-3]

        event = None

        if latest.kind == "high":
            # Potential bullish BOS or CHoCH
            if latest.price > prev.price and prev.kind == "high":
                # Break of previous high
                if self.bias != "bullish":
                    event = StructureEvent("CHoCH", "bullish", latest.price, latest.index)
                    self.bias = "bullish"
                else:
                    event = StructureEvent("BOS", "bullish", latest.price, latest.index)
            elif latest.price > prev2.price and prev.kind == "low":
                self.bias = "bullish"

        elif latest.kind == "low":
            if latest.price < prev.price and prev.kind == "low":
                if self.bias != "bearish":
                    event = StructureEvent("CHoCH", "bearish", latest.price, latest.index)
                    self.bias = "bearish"
                else:
                    event = StructureEvent("BOS", "bearish", latest.price, latest.index)
            elif latest.price < prev2.price and prev.kind == "high":
                self.bias = "bearish"

        if event:
            self.events.append(event)
            self.events = self.events[-8:]

        return event

    def get_bias(self) -> str:
        return self.bias
