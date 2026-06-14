"""
main.py - Aether Flow System (MEXC Futures SMC Bot)

Production-oriented, modular, "hard to break" architecture.

Default: DRY RUN (paper trading). Set dry_run=False in config ONLY after extensive testing.
"""

import os
import json
import time
import traceback
from datetime import datetime, timezone
from typing import Dict

import pandas as pd

from config import load_config, BotConfig
from bot.logger import setup_logger
from bot.data_handler import DataHandler
from bot.risk_manager import RiskManager
from bot.order_blocks import OrderBlockDetector
from bot.fvg_detector import FVGDetector
from bot.market_structure import MarketStructure
from bot.ut_bot import UTBot
from bot.hull_filter import HullFilter
from bot.executor import Executor
from bot.x_sentiment import XSentimentAnalyzer


class AetherBot:
    def __init__(self, config_path: str | None = None):
        self.cfg: BotConfig = load_config(config_path)
        self.logger = setup_logger(level=self.cfg.log_level)

        self.logger.info("=" * 60)
        self.logger.info("AETHER FLOW SYSTEM - MEXC Futures SMC Bot starting")
        self.logger.info(f"DRY RUN MODE: {self.cfg.dry_run}  (set to False only after paper validation)")
        self.logger.info("=" * 60)

        # Secrets
        api_key = os.getenv("MEXC_API_KEY")
        api_secret = os.getenv("MEXC_API_SECRET")
        if not api_key or not api_secret:
            raise RuntimeError("MEXC_API_KEY and MEXC_API_SECRET must be set in .env")

        # Core components
        self.data = DataHandler(self.cfg, api_key, api_secret)
        self.risk = RiskManager(self.cfg.risk, self.cfg, per_asset=self.cfg.per_asset)
        self.executor = Executor(self.data.exchange, self.cfg, self.logger)

        # Per-symbol detectors (we keep state per symbol)
        self.detectors: Dict[str, dict] = {}

        # Optional sentiment
        self.sentiment = XSentimentAnalyzer(self.cfg.x_sentiment, self.logger)

        # State
        self.state_path = self.cfg.state_file
        self._load_state()

        self.running = True

    def _get_detectors(self, symbol: str):
        if symbol not in self.detectors:
            self.detectors[symbol] = {
                "ob": OrderBlockDetector(self.cfg.order_blocks),
                "fvg": FVGDetector(self.cfg.fvg),
                "structure": MarketStructure(self.cfg.market_structure),
                "ut": UTBot(self.cfg.ut_bot),
                "hull": HullFilter(self.cfg.hull),
            }
        return self.detectors[symbol]

    def _load_state(self) -> None:
        try:
            if os.path.exists(self.state_path):
                with open(self.state_path, "r") as f:
                    saved = json.load(f)
                    self.logger.info(f"Loaded persistent state from {self.state_path}")
                    # Restore minimal state if needed (positions, active OBs etc. can be rehydrated from detectors)
        except Exception as e:
            self.logger.warning(f"Could not load state: {e}")

    def _save_state(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.state_path) or ".", exist_ok=True)
            state = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "open_positions": list(self.risk.open_positions.keys()),
                "dry_run": self.cfg.dry_run,
            }
            with open(self.state_path, "w") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            self.logger.error(f"Failed to save state: {e}")

    def _compute_atr(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        high = df["high"]
        low = df["low"]
        close = df["close"]
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs()
        ], axis=1).max(axis=1)
        return tr.rolling(period).mean()

    def process_symbol(self, symbol: str, df: pd.DataFrame) -> None:
        if len(df) < 50:
            return

        det = self._get_detectors(symbol)

        # Add ATR for convenience
        df = df.copy()
        df["atr"] = self._compute_atr(df, self.cfg.risk.atr_period)

        latest_close = float(df["close"].iloc[-1])
        latest_atr = float(df["atr"].iloc[-1]) if "atr" in df.columns else 0.0

        # === Run all detectors ===
        ob_zones = det["ob"].update(symbol, df)
        fvgs = det["fvg"].update(symbol, df)
        structure_event = det["structure"].update(df)
        ut_signal = det["ut"].update(df)
        hull_trend = det["hull"].update(df)

        structure_bias = det["structure"].get_bias()
        hull_bias = hull_trend

        # === Confluence scoring (0.0 - 1.0+) ===
        score = 0.0
        reasons = []

        # Order Block confluence (strongest)
        if det["ob"].is_price_in_ob(df["close"].iloc[-1]):
            score += 0.35
            reasons.append("IN_OB")

        # FVG
        if det["fvg"].price_in_fvg(df["close"].iloc[-1]):
            score += 0.20
            reasons.append("IN_FVG")

        # Market Structure
        if structure_bias == "bullish":
            score += 0.15
            reasons.append("BULL_STRUCTURE")
        elif structure_bias == "bearish":
            score -= 0.15

        # UT Bot fresh signal
        recent_ut = det["ut"].get_recent_signal()
        if recent_ut:
            score += 0.25 if recent_ut.side == "long" else -0.25
            reasons.append(f"UT_{recent_ut.side.upper()}")

        # Hull trend filter (mandatory-ish)
        if self.cfg.hull.use_as_filter:
            if hull_bias == "bullish":
                score += 0.15
            elif hull_bias == "bearish":
                score -= 0.15
            else:
                score *= 0.6  # penalize neutral

        # X Sentiment (optional global filter)
        if self.cfg.x_sentiment.enabled:
            overall_sent = self.sentiment.get_overall_crypto_futures_sentiment([symbol])
            if overall_sent > 0.1:
                score += 0.1
            elif overall_sent < -0.1:
                score -= 0.1
            reasons.append(f"SENT_{overall_sent:.2f}")

        side = "long" if score > 0.55 else ("short" if score < -0.55 else None)
        if not side:
            # log analysis even without trade
            ob_count = len(det["ob"].get_active_zones())
            fvg_count = len(det["fvg"].get_unmitigated_zones())
            ut_side = recent_ut.side if recent_ut else "none"
            self.logger.info(
                f"[{symbol}] CLOSE={latest_close:.4f} ATR%={latest_atr/latest_close*100 if latest_close > 0 else 0:.2f} "
                f"SCORE={score:.2f} BIAS={structure_bias}/{hull_bias} OB={ob_count} FVG={fvg_count} "
                f"UT={ut_side} ACTION=hold REASONS={reasons}"
            )
            return

        # DETAILED LOG FOR PANEL (with action)
        ob_count = len(det["ob"].get_active_zones())
        fvg_count = len(det["fvg"].get_unmitigated_zones())
        ut_side = recent_ut.side if recent_ut else "none"
        self.logger.info(
            f"[{symbol}] CLOSE={latest_close:.4f} ATR%={latest_atr/latest_close*100 if latest_close > 0 else 0:.2f} "
            f"SCORE={score:.2f} BIAS={structure_bias}/{hull_bias} OB={ob_count} FVG={fvg_count} "
            f"UT={ut_side} ACTION={side} REASONS={reasons}"
        )

        # === Risk & Execution ===
        try:
            latest_close = float(df["close"].iloc[-1])
            latest_atr = float(df["atr"].iloc[-1])

            plan = self.risk.calculate_position_size(
                symbol=symbol,
                entry_price=latest_close,
                atr=latest_atr,
                side=side,
                confluence_score=min(1.8, max(0.6, abs(score))),
                market_info=self.data.get_contract_info(symbol),
            )

            plan.reason = " | ".join(reasons) + f" | score={score:.2f}"

            # Final safety gate
            can, why = self.risk.can_trade()
            if not can:
                self.logger.info(f"{symbol} signal blocked by risk: {why}")
                return

            # Execute (or dry-run log)
            market_info = self.data.get_contract_info(symbol)
            result = self.executor.place_order_from_plan(plan, market_info)

            if result.get("status") in ("live", "dry_run_simulated"):
                self.risk.register_fill(symbol, side, plan.quantity, plan.entry_price)
                self._save_state()
                self.logger.info(f"POSITION REGISTERED: {symbol} {side} risk=${plan.risk_usdt:.2f}")

        except Exception as e:
            self.logger.error(f"Error processing signal for {symbol}: {e}\n{traceback.format_exc()}")

    def run(self) -> None:
        self.data.load_markets()

        while self.running:
            try:
                if self.data.should_refresh_symbols() or not self.data.top_symbols:
                    # Use mixed top volume across crypto + stocks (ações) + commodities (comodites)
                    # Real data from exchange (crypto) + yfinance (stocks/commodities)
                    symbols = self.data.get_top_symbols_mixed()
                    self.logger.info(f"Top {len(symbols)} symbols (crypto + ações + comodites): {symbols}")

                # Dump structured status for the real-time dashboard
                try:
                    os.makedirs("state", exist_ok=True)
                    status = {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "top_symbols": symbols,
                        "dry_run": self.cfg.dry_run,
                    }
                    with open("state/dashboard_status.json", "w", encoding="utf-8") as f:
                        json.dump(status, f, indent=2)
                except Exception:
                    pass

                for symbol in self.data.top_symbols:
                    self.logger.info(f"Analyzing {symbol}...")
                    try:
                        df = self.data.fetch_ohlcv(symbol, limit=220)
                        self.process_symbol(symbol, df)
                    except Exception as sym_err:
                        self.logger.error(f"Error on {symbol}: {sym_err}")
                        time.sleep(3)
                        continue

                self._save_state()
                # Sleep aligned roughly to 1h but poll more frequently for responsiveness
                time.sleep(self.cfg.execution.poll_interval_seconds)

            except KeyboardInterrupt:
                self.logger.info("Shutdown requested by user.")
                self.running = False
            except Exception as e:
                self.logger.error(f"Top level error: {e}\n{traceback.format_exc()}")
                time.sleep(30)  # Backoff on catastrophic failure


if __name__ == "__main__":
    bot = AetherBot()
    bot.run()
