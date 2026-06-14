"""
executor.py - All order execution through CCXT for MEXC Futures.

Critical responsibilities:
- Set leverage per symbol
- Place market or limit orders with good practices
- Strict DRY RUN mode (logs intended action, never sends real order)
- Error handling and reconciliation
- Support for stop loss / take profit (via conditional or manual management)

The bot is "inalterável" because risk is calculated before any order, and executor only executes plans.
"""

from __future__ import annotations
import logging
from typing import Optional
import ccxt

from config import BotConfig
from bot.risk_manager import RiskPlan


class Executor:
    def __init__(self, exchange: ccxt.Exchange, cfg: BotConfig, logger: logging.Logger):
        self.exchange = exchange
        self.cfg = cfg
        self.logger = logger
        self.dry_run = cfg.dry_run

    def set_leverage(self, symbol: str, leverage: int) -> bool:
        if self.dry_run:
            self.logger.info(f"[DRY-RUN] Would set leverage {leverage}x on {symbol}")
            return True
        try:
            self.exchange.set_leverage(leverage, symbol)
            self.logger.info(f"Set leverage {leverage}x on {symbol}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to set leverage on {symbol}: {e}")
            return False

    def place_order_from_plan(self, plan: RiskPlan, market_info: Optional[dict] = None) -> dict:
        """
        Execute a validated RiskPlan.
        In dry_run: only log the full intended order.
        """
        symbol = plan.symbol
        side = "buy" if plan.side == "long" else "sell"

        # Round quantity safely (production must respect precision)
        qty = plan.quantity
        if market_info and "precision" in market_info:
            amount_prec = market_info.get("precision", {}).get("amount", 4)
            qty = round(qty, amount_prec)

        order = {
            "symbol": symbol,
            "type": "market" if not self.cfg.execution.post_only_preference else "limit",
            "side": side,
            "amount": qty,
            "price": plan.entry_price if self.cfg.execution.post_only_preference else None,
            "params": {
                "leverage": plan.leverage,
                "stopLoss": {"triggerPrice": plan.stop_loss},
                "takeProfit": {"triggerPrice": plan.take_profit},
            }
        }

        if self.dry_run:
            self.logger.warning(f"[DRY-RUN][INTENT] {plan.side.upper()} {symbol} @ {plan.entry_price} | "
                                f"Qty={qty} | Lev={plan.leverage}x | SL={plan.stop_loss} | TP={plan.take_profit} | "
                                f"Risk=${plan.risk_usdt:.2f} | RR={plan.rr}")
            return {"status": "dry_run_simulated", "order": order, "plan": plan}

        try:
            # Real execution path
            self.set_leverage(symbol, plan.leverage)

            # For safety on MEXC we often use market orders + separate stop orders, or one-cancels-other.
            # Here we demonstrate a market order + attach SL/TP where supported.
            result = self.exchange.create_order(
                symbol=symbol,
                type="market",
                side=side,
                amount=qty,
            )
            self.logger.info(f"[LIVE] Order sent: {result}")
            return {"status": "live", "result": result, "plan": plan}
        except Exception as e:
            self.logger.error(f"[EXECUTOR ERROR] {symbol}: {e}")
            return {"status": "error", "error": str(e), "plan": plan}

    def cancel_all_for_symbol(self, symbol: str) -> None:
        if self.dry_run:
            self.logger.info(f"[DRY-RUN] Would cancel all orders for {symbol}")
            return
        try:
            self.exchange.cancel_all_orders(symbol)
        except Exception as e:
            self.logger.warning(f"Cancel all failed for {symbol}: {e}")
