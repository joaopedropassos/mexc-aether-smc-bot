"""
x_sentiment.py - Optional X (Twitter) sentiment filter for confluence.

IMPORTANT:
- This module is OPTIONAL and disabled by default (see config).
- Requires a valid X API v2 Bearer Token (Essential or higher access).
- Rate limits are strict. We cache results aggressively.
- If the module fails or is disabled, the bot continues without it (graceful degradation).

Sentiment is used as an additional confluence:
- Longs: prefer neutral-to-bullish overall crypto futures sentiment
- Shorts: neutral-to-bearish

Implementation options:
1. Primary: tweepy with Bearer token (Client)
2. Fallback: simple keyword heuristic on recent public search (not reliable)
3. Advanced: replace with LLM-based analysis of posts (recommended for production)

Replace / extend this module as needed. Do not let sentiment override strong SMC confluences.
"""

from __future__ import annotations
import os
import time
from typing import Dict, List
from dataclasses import dataclass
import logging

try:
    import tweepy
except ImportError:
    tweepy = None

from config import XSentimentConfig


@dataclass
class SentimentResult:
    symbol: str
    score: float          # -1.0 (very bearish) to +1.0 (very bullish)
    label: str            # bullish / bearish / neutral
    sample_size: int
    fetched_at: float


class XSentimentAnalyzer:
    def __init__(self, cfg: XSentimentConfig, logger: logging.Logger):
        self.cfg = cfg
        self.logger = logger
        self.cache: Dict[str, SentimentResult] = {}
        self.client = None

        if cfg.enabled:
            token = os.getenv(cfg.bearer_token_env)
            if token and tweepy:
                try:
                    self.client = tweepy.Client(bearer_token=token)
                    self.logger.info("X sentiment client initialized.")
                except Exception as e:
                    self.logger.warning(f"Failed to init X client: {e}")
            else:
                self.logger.warning("X sentiment enabled but no valid token/tweepy. Module will be passive.")

    def _simple_keyword_score(self, texts: List[str]) -> float:
        """Very crude fallback sentiment."""
        bullish = {"bull", "bullish", "long", "pump", "moon", "breakout", "higher", "buy"}
        bearish = {"bear", "bearish", "short", "dump", "crash", "lower", "sell", "liquidation"}
        score = 0.0
        for t in texts:
            t_lower = t.lower()
            b = sum(1 for w in bullish if w in t_lower)
            be = sum(1 for w in bearish if w in t_lower)
            score += (b - be) / max(1, len(texts))
        return max(-1.0, min(1.0, score))

    def analyze_symbol(self, symbol: str, base: str) -> SentimentResult:
        """Main entry point. Returns cached or fresh result."""
        now = time.time()
        cached = self.cache.get(symbol)
        if cached and (now - cached.fetched_at) < (self.cfg.cache_minutes * 60):
            return cached

        if not self.cfg.enabled or not self.client:
            # Neutral when disabled
            return SentimentResult(symbol, 0.0, "neutral", 0, now)

        query = f"({base} OR ${base} OR {symbol}) (futures OR perp OR perpetual) -is:retweet lang:en"
        try:
            tweets = self.client.search_recent_tweets(
                query=query,
                max_results=min(100, self.cfg.max_tweets_per_symbol),
                tweet_fields=["text"]
            )
            texts = [t.text for t in (tweets.data or [])]
            if not texts:
                score = 0.0
            else:
                score = self._simple_keyword_score(texts)

            label = "bullish" if score > self.cfg.sentiment_threshold else ("bearish" if score < -self.cfg.sentiment_threshold else "neutral")

            result = SentimentResult(symbol, score, label, len(texts), now)
            self.cache[symbol] = result
            self.logger.debug(f"X sentiment {symbol}: {label} ({score:.2f}) from {len(texts)} tweets")
            return result
        except Exception as e:
            self.logger.warning(f"X sentiment fetch failed for {symbol}: {e}")
            return SentimentResult(symbol, 0.0, "neutral", 0, now)

    def get_overall_crypto_futures_sentiment(self, symbols: List[str]) -> float:
        """Average score across watched symbols. Used as global filter."""
        scores = []
        for s in symbols[:5]:  # limit cost
            base = s.split("/")[0].replace("USDT", "")
            res = self.analyze_symbol(s, base)
            scores.append(res.score)
        return sum(scores) / max(1, len(scores)) if scores else 0.0
