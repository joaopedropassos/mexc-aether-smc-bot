"""
config.py - Central Configuration with Validation
All parameters for the Aether SMC bot. Load from env + yaml or defaults.
Emphasizes safety: dry_run=True by default.
"""

from __future__ import annotations
import os
from dataclasses import dataclass, field
from typing import List, Optional
from dotenv import load_dotenv
import yaml

load_dotenv()  # Load .env if present

@dataclass
class RiskConfig:
    risk_per_trade_pct: float = 0.005          # 0.5% of equity per trade (very important - keep low)
    atr_period: int = 14
    atr_sl_multiplier: float = 2.0             # SL distance = ATR * mult
    atr_tp_multiplier: float = 3.5             # Minimum RR target
    daily_loss_limit_pct: float = 0.03         # Stop trading for the day after 3% loss
    max_open_positions: int = 3                # Hard cap on concurrent positions
    max_leverage: int = 20                     # Global safety cap (bot targets ~14x average)
    min_leverage: int = 3
    cooldown_minutes_after_loss: int = 60      # Pause after stopped out or big loss
    position_size_step: float = 0.0001         # Minimum precision for qty adjustment

@dataclass
class OrderBlockConfig:
    volume_pivot_length: int = 5               # Lookback for volume pivot (LuxAlgo style)
    max_active_obs: int = 8                    # Limit memory per side (bull/bear)
    mitigation_method: str = "wick"            # "wick" or "close"
    require_volume_confirmation: bool = True
    min_ob_strength: float = 1.2               # Volume ratio threshold

@dataclass
class FVGConfig:
    min_gap_pct: float = 0.0015                # Ignore tiny FVGs (<0.15%)
    max_active_fvgs: int = 12
    mitigation_on_close: bool = True           # Mitigate only on candle close inside
    only_unmitigated: bool = True

@dataclass
class MarketStructureConfig:
    swing_length: int = 5                      # Pivot strength for swings
    use_internal_structure: bool = True        # Look at lower timeframe bias if needed
    bos_confirmation_bars: int = 1

@dataclass
class UTBotConfig:
    atr_period: int = 10
    sensitivity: float = 1.0                   # Key parameter from original UT Bot
    max_bars_since_signal: int = 3             # Only consider fresh signals

@dataclass
class HullConfig:
    hma_length: int = 21                       # Main Hull length
    use_as_filter: bool = True                 # Require trend alignment
    confirm_bars: int = 2

@dataclass
class XSentimentConfig:
    enabled: bool = False                      # Default OFF for stability
    bearer_token_env: str = "X_BEARER_TOKEN"
    max_tweets_per_symbol: int = 15
    sentiment_threshold: float = 0.15          # net bullish score needed for long bias etc.
    cache_minutes: int = 45                    # Avoid hammering API

@dataclass
class ExecutionConfig:
    exchange: str = "mexc"
    default_type: str = "swap"                 # USDT perpetual futures
    timeframe: str = "1h"
    poll_interval_seconds: int = 60 * 5        # Check every 5 min (but align to 1h candles)
    max_slippage_bps: int = 8
    post_only_preference: bool = True          # Try maker when possible

@dataclass
class BotConfig:
    dry_run: bool = True                       # SAFETY: Paper trading by default. Set False only when ready.
    top_n_symbols: int = 10
    crypto_n: int = 4                          # Top crypto by volume (real data from exchange)
    stocks_n: int = 3                          # Top ações/stocks by volume (real data via yfinance)
    commodities_n: int = 3                     # Top comodites/commodities by volume (real data via yfinance)
    refresh_top_symbols_hours: int = 6
    preferred_zero_fee_symbols: List[str] = field(default_factory=lambda: [])  # e.g. ["BTC/USDT:USDT"]
    risk: RiskConfig = field(default_factory=RiskConfig)
    order_blocks: OrderBlockConfig = field(default_factory=OrderBlockConfig)
    fvg: FVGConfig = field(default_factory=FVGConfig)
    market_structure: MarketStructureConfig = field(default_factory=MarketStructureConfig)
    ut_bot: UTBotConfig = field(default_factory=UTBotConfig)
    hull: HullConfig = field(default_factory=HullConfig)
    x_sentiment: XSentimentConfig = field(default_factory=XSentimentConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    log_level: str = "INFO"
    state_file: str = "state/bot_state.json"
    per_asset_file: str = "recommended_per_asset.yaml"  # gerado pelo backtest

    # Per-asset overrides (carregado automaticamente do YAML do backtest)
    per_asset: dict = field(default_factory=dict)  # { "BTC/USDT:USDT": {"max_lev": 5, "risk_per_trade": 0.0025, ...}, ... }

    # --- Validation ---
    def validate(self) -> None:
        assert 0.001 <= self.risk.risk_per_trade_pct <= 0.02, "Risk per trade must be between 0.1% and 2%"
        assert self.risk.max_open_positions >= 1
        assert self.top_n_symbols <= 20, "Too many symbols can overload rate limits"
        assert self.order_blocks.mitigation_method in ("wick", "close")
        if self.x_sentiment.enabled:
            token = os.getenv(self.x_sentiment.bearer_token_env)
            if not token:
                print("WARNING: X sentiment enabled but no X_BEARER_TOKEN found in env. Module will be disabled at runtime.")


def load_config(config_path: Optional[str] = None) -> BotConfig:
    """Load config, allowing optional YAML override for advanced users."""
    cfg = BotConfig()

    if config_path and os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
            # Simple merge (production would use deeper merge)
            for section, values in data.items():
                if hasattr(cfg, section):
                    for k, v in values.items():
                        setattr(getattr(cfg, section), k, v)

    # Load per-asset overrides from backtest YAML (automatic integration)
    per_asset_path = getattr(cfg, 'per_asset_file', 'recommended_per_asset.yaml')
    if os.path.exists(per_asset_path):
        try:
            with open(per_asset_path, "r", encoding="utf-8") as f:
                pa_data = yaml.safe_load(f) or {}
                cfg.per_asset = pa_data.get("per_asset", {})
            print(f"[config] Carregado per-asset config de {per_asset_path} ({len(cfg.per_asset)} ativos)")
        except Exception as e:
            print(f"[config] Erro ao carregar per-asset: {e}")

    # Environment overrides (useful for CI or quick tests)
    if os.getenv("DRY_RUN", "").lower() == "false":
        cfg.dry_run = False
    if os.getenv("DRY_RUN", "").lower() == "true":
        cfg.dry_run = True

    cfg.validate()
    return cfg
