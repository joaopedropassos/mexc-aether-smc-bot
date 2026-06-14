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
from bot.signal_engine import SignalEngine


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

    def _sync_real_positions(self):
        """When in real mode, query the exchange for actual open positions and sync to risk state.
        This way the panel and bot detect manual or previous real positions.
        """
        if self.cfg.dry_run:
            return
        try:
            # MEXC futures via CCXT
            positions = self.data.exchange.fetch_positions() or []
            real_open = {}
            for p in positions:
                sym = p.get('symbol')
                contracts = float(p.get('contracts') or p.get('info', {}).get('positionAmt', 0) or 0)
                if sym and contracts != 0:
                    real_open[sym] = {
                        'side': p.get('side'),
                        'qty': contracts,
                        'entry': p.get('entryPrice'),
                        'unrealizedPnl': p.get('unrealizedPnl'),
                    }
            self.risk.open_positions.update(real_open)
            if real_open:
                self.logger.info(f"Synced {len(real_open)} real open positions from MEXC exchange")
        except Exception as e:
            self.logger.warning(f"Could not sync real positions from exchange (check API key permissions for futures): {e}")

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

        latest_atr = float(df["atr"].iloc[-1]) if "atr" in df.columns else 0.0

        # === Usar o novo SignalEngine v2 para confluencia com tiers ===
        engine = SignalEngine(
            det["structure"],
            det["ob"],
            det["fvg"],
            det["hull"],
            det["ut"],
        )
        setup = engine.evaluate(symbol, df)

        if not setup:
            # log analysis even without trade (usar dados do df para log basico)
            latest_close = float(df["close"].iloc[-1])
            self.logger.info(
                f"[{symbol}] CLOSE={latest_close:.4f} ATR%={latest_atr/latest_close*100 if latest_close > 0 else 0:.2f} "
                f"SCORE=0.00 BIAS=neutral/neutral OB=0 FVG=0 UT=none ACTION=hold REASONS=['sem confluencia tier']"
            )
            return

        latest_close = setup.entry_price
        side = setup.side

        # Log detalhado do setup (para o painel)
        ob_count = 1 if setup.ob_zone else 0
        fvg_count = 1 if setup.fvg_zone else 0
        ut_side = setup.ut_signal.side if setup.ut_signal else "none"
        self.logger.info(
            f"[{symbol}] CLOSE={latest_close:.4f} ATR%={latest_atr/latest_close*100 if latest_close > 0 else 0:.2f} "
            f"SCORE={setup.confluence_score:.2f} BIAS={setup.hull_trend}/{'bullish' if side=='long' else 'bearish'} "
            f"OB={ob_count} FVG={fvg_count} UT={ut_side} ACTION={side} TIER={setup.tier} "
            f"REASONS=[{setup.notes}] components={setup.components}"
        )

        # === Risk & Execution (usando components para o RiskManager v2) ===
        try:
            plan = self.risk.calculate_position_size(
                symbol=symbol,
                entry_price=latest_close,
                atr=latest_atr,
                side=side,
                confluence_components=setup.components,
                market_info=self.data.get_contract_info(symbol),
            )

            # Ajustar pelo risk_multiplier do tier (do SignalEngine)
            plan.risk_usdt *= setup.risk_multiplier
            plan.quantity *= setup.risk_multiplier
            plan.notional_usdt *= setup.risk_multiplier
            plan.reason = f"TIER_{setup.tier} | " + setup.notes + f" | score={setup.confluence_score:.2f}"

            # Final safety gate (v2 suporta symbol)
            can, why = self.risk.can_trade(symbol)
            if not can:
                self.logger.info(f"{symbol} signal blocked by risk: {why}")
                return

            # Execute (real ou dry)
            market_info = self.data.get_contract_info(symbol)
            result = self.executor.place_order_from_plan(plan, market_info)

            if result.get("status") in ("live", "dry_run_simulated"):
                self.risk.register_fill(symbol, side, plan.quantity, plan.entry_price)
                self._save_state()
                self.logger.info(f"POSITION REGISTERED: {symbol} {side} risk=${plan.risk_usdt:.2f} TIER={setup.tier}")

        except Exception as e:
            self.logger.error(f"Error processing signal for {symbol}: {e}\n{traceback.format_exc()}")

    def run(self) -> None:
        self.data.load_markets()

        while self.running:
            try:
                self._sync_real_positions()  # detect real open positions when in live mode

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
                        # Emit a basic detailed line so the panel can show something for this symbol too
                        self.logger.info(f"[{symbol}] CLOSE=N/A ATR%=N/A SCORE=0.00 BIAS=n/a/n/a OB=0 FVG=0 UT=none ACTION=hold REASONS=['fetch error: {sym_err}']")
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
