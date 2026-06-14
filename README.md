# AETHER FLOW SYSTEM – MEXC Futures SMC Bot (USDT Perpetual)

**High-quality, modular, production-oriented Smart Money Concepts (SMC) trading bot** for MEXC USDT Futures (1h timeframe).

Inspired by "AETHER FLOW SYSTEM – Mxwll Fusion Engine (LuxAlgo OB Edition)" Pine Script concepts, fully re-engineered for robustness, testability, and safety in live environments.

## Core Philosophy: "Inalterável" (Hard to Break)

- **Dry-run / Paper Trading enabled by default**.
- Risk management is the **single most important module** — all position sizing, leverage, and stops are calculated **before** any order is considered.
- Heavy error handling, automatic retries, state persistence, and graceful degradation.
- No hardcoded secrets. Everything configurable.
- Clear separation of concerns across 10+ modules.

## Key Features Implemented

### SMC / LuxAlgo-inspired Logic
- **Order Blocks** (volume-pivot based detection + configurable mitigation "wick" or "close")
- **Fair Value Gaps (FVG)** with mitigation tracking
- **Market Structure** (BoS / CHoCH + swing detection with HH/HL/LH/LL bias)
- **UT Bot** (ATR trailing stop signals, fresh signals only)
- **Hull Moving Average Suite** (trend filter / directional bias)

### Professional Risk Management (ATR-based)
- Fixed fractional risk per trade (default 0.5%)
- Dynamic ATR stop-loss and minimum RR take-profit
- Daily loss circuit breaker (auto-pause trading)
- Hard cap on simultaneous open positions
- Smart leverage targeting (~14x average, volatility-adjusted, with hard caps)
- Cooldown periods after losses

### Execution & Data
- Full **CCXT** integration for MEXC Futures (swap/perpetual)
- Dynamically selects **Top 10 highest volume USDT perpetual pairs** (refreshed periodically)
- Prefers zero-fee pairs when you configure them
- Proper leverage setting per symbol
- Clean dry-run simulation of every order

### Optional X (Twitter) Sentiment Filter
- `x_sentiment.py` module (disabled by default)
- Uses X API v2 Bearer token + tweepy
- Simple keyword + score-based sentiment
- Acts as **additional confluence**, never overrides strong SMC signals
- Graceful fallback: if API fails or is not configured, bot continues normally

## Project Structure

```
mexc_aether_bot/
├── main.py                 # Orchestrator + main loop
├── config.py               # All parameters + validation
├── requirements.txt
├── .env.example
├── README.md
├── .gitignore
├── bot/
│   ├── __init__.py
│   ├── risk_manager.py     # ★ MOST CRITICAL MODULE
│   ├── order_blocks.py
│   ├── fvg_detector.py
│   ├── market_structure.py
│   ├── ut_bot.py
│   ├── hull_filter.py
│   ├── executor.py         # CCXT orders + dry-run enforcement
│   ├── x_sentiment.py      # Optional X sentiment
│   ├── data_handler.py     # Top volume discovery + OHLCV via CCXT
│   └── logger.py
├── logs/                   # Auto-created
└── state/                  # Persistent bot state (JSON)
```

## Setup Instructions

### 1. Get MEXC API Keys (Futures)

1. Go to MEXC → Profile → API Management
2. Create a new API key **specifically for Futures / Perpetual**
3. **Enable** "Order Placement" / Futures trading permissions
4. Bind your IP address if possible (highly recommended for production)
5. Copy Key + Secret

Your provided credentials file contained:
```
MEXC_API_KEY=mx0vgluRtZgfoCtwFH
MEXC_API_SECRET=b38338a4be8d456ea5e9d14d1fd5b409
```
**Replace these with your real keys.**

### 2. Environment & Installation

```powershell
cd C:\Users\55639\mexc_aether_bot

# Copy and edit environment
copy .env.example .env
notepad .env   # Paste your real MEXC_API_KEY and MEXC_API_SECRET

# Create/activate venv and install
python -m venv .venv
.\ .venv\Scripts\activate
pip install -r requirements.txt
```

For X sentiment (optional):
- Get a Bearer Token from https://developer.twitter.com
- Add to `.env` as `X_BEARER_TOKEN=...`
- In `config.py` or by editing `BotConfig`, set `x_sentiment.enabled = True`

### 3. First Runs — Always Start in Paper Mode

The bot starts in `dry_run: true` (see `config.py`).

```powershell
.\ .venv\Scripts\activate
python main.py
```

Watch the logs carefully. It will:
- Fetch current top volume symbols
- Calculate all SMC confluences
- Log exactly what it **would** do in live mode (including exact qty, SL, TP, leverage)

Let it run for days/weeks in dry-run. Compare signals vs actual price action.

### 4. Going Live (Extremely Carefully)

1. Paper trade successfully for a long period (minimum 2-4 weeks recommended).
2. Start with very small capital.
3. Edit `config.py` (or pass overrides) and set `dry_run = False`.
4. **Double-check** your risk parameters (0.5% or lower per trade is advised).
5. Run with live keys only on a machine you control 24/7 with monitoring.

There is a manual confirmation-like safety in spirit (dry_run flag + daily loss limit + position caps).

### 5. Monitoring & Logs

- Logs go to `logs/aether_YYYYMMDD.log` (rotated)
- State (open positions etc.) saved to `state/bot_state.json`
- Console shows high-level activity.

## Configuration Highlights (config.py)

- `risk.risk_per_trade_pct`: Start at 0.003–0.005 (0.3–0.5%)
- `risk.daily_loss_limit_pct`: 2–3% → full day pause
- `risk.max_open_positions`: 2–4 recommended
- Order Block / FVG parameters tuned for 1h
- `hull.use_as_filter: true` (strongly recommended)

All detectors are designed to be tweakable without breaking the risk layer.

## Risk Warnings & Limitations

**This is high-risk automated trading.** Most retail traders lose money with leveraged futures.

- Past performance (even in backtests or paper) does **not** predict future results.
- MEXC futures have specific quirks (funding rates, liquidation engines, API rate limits, occasional maintenance).
- The X sentiment module is basic (keyword-based). Real sentiment analysis benefits from better NLP/LLM.
- No backtester is included in v1 (you should build or integrate one before live).
- The bot targets confluence but **cannot** guarantee winning trades.
- Never risk money you cannot afford to lose completely.

**Recommended Capital & Risk Discipline**
- Begin with small test allocation.
- Never exceed 1% risk per trade until you have 3+ months of verified positive expectancy in paper + small live.
- Monitor funding rates manually at first.

## Next Steps / Improvements You Can Add

- Proper vectorized backtester + walk-forward
- Per-symbol performance tracking + auto blacklisting
- Telegram / Discord alerts
- More sophisticated position management (trailing via exchange, breakeven, partials)
- Funding rate filter before entry
- Multi-timeframe confirmation (e.g. 4h structure on 1h signals)
- Replace simple X sentiment with LLM (e.g. call local model or Grok API for tweet summary)

## Support & Philosophy

The goal of this architecture is **long-term stability and auditability**, not maximum feature count.

Every order that could be sent goes through:
1. Detectors → Confluence score
2. RiskManager (sizing + global checks)
3. Executor (only executes validated plans, respects dry_run)

If anything looks suspicious in logs, the dry_run flag gives you an immediate off-switch.

Trade responsibly.

---

**Project created for you on 2026-06-14. All core modules implemented with production mindset.**
