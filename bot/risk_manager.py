"""
risk_manager.py - The heart of the "inalterável" bot.
Handles:
- Position sizing using ATR (fixed fractional risk)
- Dynamic SL/TP
- Daily loss limit circuit breaker
- Max concurrent positions
- Smart leverage calculation (targets ~14x average but respects volatility)
- Cooldowns and safety checks

All calculations must be deterministic and auditable.
Never allow position size that would risk more than configured %.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timedelta, date
from typing import Optional, Dict, Tuple
import numpy as np

from config import RiskConfig, BotConfig


@dataclass
class RiskPlan:
    symbol: str
    side: str                    # "long" or "short"
    entry_price: float
    stop_loss: float
    take_profit: float
    quantity: float              # In base asset (e.g. 0.001 BTC)
    notional_usdt: float
    risk_usdt: float             # Absolute $ at risk
    leverage: int
    rr: float                    # Achieved risk-reward
    reason: str = ""
    confidence: float = 1.0      # 0-1 confluence score from signals


@dataclass
class RiskState:
    day: Optional[date] = None
    realized_pnl_today: float = 0.0
    peak_equity_today: float = 0.0
    stopped_out_today: bool = False
    last_loss_time: Optional[datetime] = None
    consecutive_losses: int = 0


class RiskManager:
    def __init__(self, cfg: RiskConfig, bot_cfg: BotConfig, initial_equity: float = 1000.0, per_asset: dict = None):
        self.cfg = cfg
        self.bot_cfg = bot_cfg
        self.state = RiskState()
        self.initial_equity = initial_equity
        self.equity = initial_equity
        self.open_positions: Dict[str, dict] = {}   # symbol -> position info
        self.per_asset = per_asset or {}  # overrides from backtest YAML: {symbol: {"risk_per_trade": x, "max_lev": y, ...}}

    def update_equity(self, new_equity: float) -> None:
        """Call this with real or paper equity after each cycle or fill."""
        self.equity = new_equity
        today = datetime.utcnow().date()
        if self.state.day != today:
            self._rollover_day(today, new_equity)
        self.state.peak_equity_today = max(self.state.peak_equity_today, new_equity)

    def _rollover_day(self, today: date, equity: float) -> None:
        self.state.day = today
        self.state.realized_pnl_today = 0.0
        self.state.peak_equity_today = equity
        self.state.stopped_out_today = False
        # We keep consecutive_losses across days for extra caution (can be reset if wanted)

    def can_trade(self) -> Tuple[bool, str]:
        """Global circuit breakers."""
        if self.state.stopped_out_today:
            return False, "DAILY_LOSS_LIMIT_REACHED"

        today = datetime.utcnow().date()
        if self.state.day != today:
            self._rollover_day(today, self.equity)

        if self.state.realized_pnl_today < -self.equity * self.cfg.daily_loss_limit_pct:
            self.state.stopped_out_today = True
            return False, "DAILY_LOSS_LIMIT_REACHED"

        if len(self.open_positions) >= self.cfg.max_open_positions:
            return False, "MAX_OPEN_POSITIONS"

        if self.state.last_loss_time:
            cooldown = timedelta(minutes=self.cfg.cooldown_minutes_after_loss)
            if datetime.utcnow() - self.state.last_loss_time < cooldown:
                return False, "COOLDOWN_ACTIVE"

        return True, "OK"

    def register_fill(self, symbol: str, side: str, qty: float, entry: float, realized_pnl: float = 0.0) -> None:
        """Track open position and daily PnL."""
        self.open_positions[symbol] = {
            "side": side,
            "qty": qty,
            "entry": entry,
            "timestamp": datetime.utcnow(),
        }
        self.state.realized_pnl_today += realized_pnl
        if realized_pnl < -0.0001:  # small threshold
            self.state.consecutive_losses += 1
            self.state.last_loss_time = datetime.utcnow()
        else:
            self.state.consecutive_losses = max(0, self.state.consecutive_losses - 1)

    def close_position(self, symbol: str, realized_pnl: float) -> None:
        if symbol in self.open_positions:
            del self.open_positions[symbol]
        self.state.realized_pnl_today += realized_pnl
        if realized_pnl < 0:
            self.state.consecutive_losses += 1
            self.state.last_loss_time = datetime.utcnow()
        else:
            self.state.consecutive_losses = 0

    def calculate_position_size(
        self,
        symbol: str,
        entry_price: float,
        atr: float,
        side: str,
        confluence_score: float = 1.0,
        market_info: Optional[dict] = None,
    ) -> RiskPlan:
        """
        Core sizing logic.
        Risk amount = equity * risk_per_trade_pct * min(1.0, confluence_score)
        SL distance = ATR * atr_sl_multiplier
        Quantity = risk_amount / (SL_distance)
        Then adjust for contract specifications and desired leverage.

        Per-asset overrides from backtest are automatically applied here.
        """
        can, reason = self.can_trade()
        if not can:
            raise RuntimeError(f"Cannot open position: {reason}")

        # Apply per-asset overrides (from backtest recommended YAML) - automatic integration
        sym_over = self.per_asset.get(symbol, {})
        risk_pct = sym_over.get("risk_per_trade", self.cfg.risk_per_trade_pct)
        sl_mult = sym_over.get("atr_sl_mult", getattr(self.cfg, "atr_sl_multiplier", 2.0))
        tp_mult = sym_over.get("atr_tp_mult", getattr(self.cfg, "atr_tp_multiplier", 3.5))
        max_lev = sym_over.get("max_lev", self.cfg.max_leverage)

        risk_amount = self.equity * risk_pct * min(1.0, max(0.5, confluence_score))

        sl_distance = max(atr * sl_mult, entry_price * 0.0015)  # safety floor
        if sl_distance <= 0:
            raise ValueError("Invalid SL distance")

        # Raw quantity in base currency (for BTCUSDT: BTC amount)
        raw_qty = risk_amount / sl_distance

        # Leverage calculation (smart, volatility aware) + per-asset cap
        vol_factor = max(0.6, min(1.4, 0.012 / max(atr / entry_price, 0.0005)))
        target_lev = int(round(14 * vol_factor))
        target_lev = max(self.cfg.min_leverage, min(target_lev, max_lev))

        # Notional desired
        desired_notional = raw_qty * entry_price * target_lev

        # Respect exchange min/max if market_info provided (from CCXT)
        if market_info:
            min_cost = market_info.get("limits", {}).get("cost", {}).get("min", 5)
            if desired_notional < min_cost:
                target_lev = min(max_lev, target_lev + 3)

        quantity = raw_qty

        stop_loss = entry_price - sl_distance if side == "long" else entry_price + sl_distance
        take_profit = entry_price + (sl_distance * tp_mult) if side == "long" else entry_price - (sl_distance * tp_mult)

        rr = abs(take_profit - entry_price) / max(abs(stop_loss - entry_price), 1e-9)

        plan = RiskPlan(
            symbol=symbol,
            side=side,
            entry_price=round(entry_price, 8),
            stop_loss=round(stop_loss, 8),
            take_profit=round(take_profit, 8),
            quantity=round(quantity, 8),
            notional_usdt=quantity * entry_price,
            risk_usdt=risk_amount,
            leverage=target_lev,
            rr=round(rr, 2),
            reason="ATR_RISK_SIZING" + (f" (per-asset override)" if sym_over else ""),
            confidence=confluence_score,
        )
        return plan

    def get_current_leverage_for_symbol(self, symbol: str) -> int:
        """Can be extended to query exchange position."""
        if symbol in self.open_positions:
            # In real system we would query leverage from exchange
            return 10  # placeholder
        return 10

    def is_symbol_in_cooldown(self, symbol: str) -> bool:
        # Per-symbol cooldowns can be added here
        return False
