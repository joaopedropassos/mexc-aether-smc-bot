"""
data_handler.py - Data fetching layer using CCXT.

Responsibilities:
- Connect to MEXC Futures (swap)
- Discover top N USDT perpetual contracts by 24h quote volume
- Fetch 1h OHLCV with sufficient history for indicators (150+ bars recommended)
- Basic normalization and caching
"""

from __future__ import annotations
import time
from typing import List, Dict, Tuple, Optional
import pandas as pd
import ccxt
from tenacity import retry, stop_after_attempt, wait_exponential

from config import BotConfig


class DataHandler:
    def __init__(self, cfg: BotConfig, api_key: str, api_secret: str):
        self.cfg = cfg
        self.exchange = ccxt.mexc({
            "apiKey": api_key,
            "secret": api_secret,
            "options": {"defaultType": cfg.execution.default_type},
            "enableRateLimit": True,
        })
        self.exchange.set_sandbox_mode(False)  # Set True only if you have MEXC testnet
        self.top_symbols: List[str] = []
        self.last_top_refresh = 0

    def load_markets(self) -> None:
        self.exchange.load_markets()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def fetch_top_symbols(self, n: int = 10) -> List[str]:
        """Return top N USDT perpetual futures by 24h volume."""
        tickers = self.exchange.fetch_tickers()
        usdt_perps = []

        for symbol, t in tickers.items():
            if not symbol.endswith(":USDT") and "/USDT" not in symbol:
                continue
            # Prefer perpetuals
            info = t.get("info", {})
            quote_vol = t.get("quoteVolume") or info.get("quote_volume") or 0
            if quote_vol and quote_vol > 100000:  # minimum liquidity filter
                usdt_perps.append((symbol, float(quote_vol)))

        usdt_perps.sort(key=lambda x: x[1], reverse=True)
        top = [s[0] for s in usdt_perps[:n]]

        # Prefer zero fee pairs if known (user can populate in config)
        zero_fee = set(self.cfg.preferred_zero_fee_symbols)
        if zero_fee:
            top = sorted(top, key=lambda s: (0 if s in zero_fee else 1, -usdt_perps[[x[0] for x in usdt_perps].index(s)][1] if s in [x[0] for x in usdt_perps] else 0))

        self.top_symbols = top
        self.last_top_refresh = time.time()
        return top

    def should_refresh_symbols(self) -> bool:
        hours = self.cfg.refresh_top_symbols_hours
        return (time.time() - self.last_top_refresh) > (hours * 3600)

    @retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=1, min=3, max=15))
    def fetch_ohlcv(self, symbol: str, limit: int = 200) -> pd.DataFrame:
        """Fetch 1h candles. Returns DataFrame indexed by time."""
        ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe=self.cfg.execution.timeframe, limit=limit)
        df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df.set_index("timestamp", inplace=True)
        df = df.astype(float)
        return df

    def get_contract_info(self, symbol: str) -> dict:
        """Useful for min qty, precision, fees."""
        try:
            market = self.exchange.market(symbol)
            return {
                "symbol": symbol,
                "precision": market.get("precision", {}),
                "limits": market.get("limits", {}),
                "maker": market.get("maker", 0.0001),
                "taker": market.get("taker", 0.0005),
            }
        except Exception:
            return {"symbol": symbol}
