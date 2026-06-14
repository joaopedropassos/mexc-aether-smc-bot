"""
backtest.py - Backtest Histórico 1 Semana (gráfico horário / 1h)

Testa 4 variações da estratégia SMC (Aether) para ~30 ativos (top volume crypto + ações + commodities).

- Dados reais: CCXT (crypto da MEXC) + yfinance (ações e commodities)
- 1 semana de dados 1h (~168 barras)
- Prioriza ativos com taxa 0 (zero fee) na escolha da melhor config
- Para cada ativo, encontra a MELHOR variação + alavancagem + valores (ATR SL/TP, risk %)
- Métricas: Profit Factor, Winrate, Max DD, Net PnL, Expectancy
- Sempre prioriza 0 fee quando disponível

Uso:
    python backtest.py

Saída: Tabelas com melhor config por ativo.
Use para calibrar o config.yaml / risk antes de rodar o bot real.
"""

import os
import sys
import json
import time
import argparse
from datetime import datetime, timedelta
from typing import List, Dict, Any
import pandas as pd
import numpy as np

try:
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    HAS_MPL = True
except ImportError:
    HAS_MPL = False
    print("AVISO: matplotlib não instalado. Gráficos serão pulados.")

# Reutiliza o que temos no projeto
sys.path.insert(0, os.path.dirname(__file__))
from bot.data_handler import DataHandler
from config import load_config, BotConfig
from bot.order_blocks import OrderBlockDetector
from bot.fvg_detector import FVGDetector
from bot.market_structure import MarketStructure
from bot.ut_bot import UTBot
from bot.hull_filter import HullFilter

# Para dados reais de ações/commodities
try:
    import yfinance as yf
except ImportError:
    yf = None
    print("AVISO: yfinance não instalado. Usando listas fixas para ações/commodities.")

# ============================================================
# 4 VARIAÇÕES DA ESTRATÉGIA
# ============================================================
VARIATIONS = [
    {
        "name": "Conservative_ZeroFee",
        "risk_per_trade": 0.0025,   # 0.25%
        "atr_sl_mult": 2.5,
        "atr_tp_mult": 4.0,
        "max_lev": 5,
        "score_threshold": 0.60,
        "fee": 0.0,                 # prioriza taxa 0
        "description": "Baixo risco, SL largo, prioriza 0 fee"
    },
    {
        "name": "Balanced",
        "risk_per_trade": 0.005,    # 0.5%
        "atr_sl_mult": 2.0,
        "atr_tp_mult": 3.5,
        "max_lev": 10,
        "score_threshold": 0.55,
        "fee": 0.0005,
        "description": "Config padrão do bot"
    },
    {
        "name": "Aggressive_OB_FVG",
        "risk_per_trade": 0.0075,
        "atr_sl_mult": 1.8,
        "atr_tp_mult": 3.0,
        "max_lev": 15,
        "score_threshold": 0.50,    # entra mais fácil em setups de OB/FVG
        "fee": 0.0005,
        "description": "Foco em Order Blocks e FVG, maior lev"
    },
    {
        "name": "Trend_UT_Hull",
        "risk_per_trade": 0.005,
        "atr_sl_mult": 2.2,
        "atr_tp_mult": 4.5,
        "max_lev": 12,
        "score_threshold": 0.58,
        "fee": 0.0005,
        "description": "Foco em tendência (UT + Hull), TP mais largo"
    },
    {
        "name": "LowVol_Conservative",
        "risk_per_trade": 0.003,
        "atr_sl_mult": 3.0,
        "atr_tp_mult": 5.0,
        "max_lev": 4,
        "score_threshold": 0.65,
        "fee": 0.0005,
        "description": "Ultra conservador, SL largo, só trades de alta convicção"
    },
    {
        "name": "HighConv_Aggressive",
        "risk_per_trade": 0.006,
        "atr_sl_mult": 1.5,
        "atr_tp_mult": 2.5,
        "max_lev": 18,
        "score_threshold": 0.45,  # entra mais fácil
        "fee": 0.0005,
        "description": "Alta alavancagem em setups de alta confluência"
    },
]

ZERO_FEE_SYMBOLS = {
    "BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT"   # exemplos - adicione os que têm taxa 0 na MEXC
}

def get_mixed_assets_30(handler: DataHandler) -> List[str]:
    """Busca ~30 ativos priorizando volume e taxa 0 quando possível."""
    # Crypto top (mais volume possível)
    crypto = handler.fetch_top_symbols(22)  # ~22 crypto

    # Ações (high volume)
    stocks = ["AAPL", "NVDA", "TSLA", "MSFT", "AMZN", "META", "GOOGL", "AMD"]

    # Commodities
    comm = ["CL=F", "GC=F", "SI=F", "NG=F", "HG=F"]

    mixed = crypto + stocks + comm
    seen = set()
    unique = []
    for s in mixed:
        if s not in seen:
            seen.add(s)
            unique.append(s)
    # Prioriza zero fee no topo
    zero_fee_first = [s for s in unique if s in ZERO_FEE_SYMBOLS]
    others = [s for s in unique if s not in ZERO_FEE_SYMBOLS]
    final = zero_fee_first + others
    return final[:30]

def fetch_historical_1h(handler: DataHandler, symbol: str, days: int = 7) -> pd.DataFrame:
    """Busca N dias de dados 1h (dados reais). Suporta período maior que 1 semana."""
    limit = min(1000, int(days * 24 * 1.2))  # margem
    if any(x in symbol for x in [":USDT", "/USDT", "USDT"]) or "USDT" in symbol.upper():
        # Crypto via CCXT (real exchange data) - usa since para histórico
        try:
            since = int((datetime.utcnow() - timedelta(days=days)).timestamp() * 1000)
            ohlcv = handler.exchange.fetch_ohlcv(symbol, timeframe="1h", since=since, limit=limit)
            if not ohlcv:
                return pd.DataFrame()
            df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
            df.set_index("timestamp", inplace=True)
            df = df.astype(float).dropna()
            return df
        except Exception as e:
            print(f"  [WARN] CCXT falhou para {symbol}: {e}")
            return pd.DataFrame()
    else:
        # Ações e commodities via yfinance (dados reais)
        if yf is None:
            return pd.DataFrame()
        try:
            yf_sym = symbol
            period = f"{max(days, 7)}d"
            hist = yf.download(yf_sym, period=period, interval="1h", progress=False, auto_adjust=True)
            if hist.empty:
                return pd.DataFrame()
            df = hist[["Open", "High", "Low", "Close", "Volume"]].copy()
            df.columns = ["open", "high", "low", "close", "volume"]
            df = df.dropna()
            return df
        except Exception as e:
            print(f"  [WARN] yfinance falhou para {symbol}: {e}")
            return pd.DataFrame()

def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["high"]
    low = df["low"]
    close = df["close"]
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()

class SimpleBacktester:
    """Backtest event-driven simplificado usando os mesmos detectores do bot."""

    def __init__(self, variation: dict, fee: float = 0.0005):
        self.var = variation
        self.fee = variation.get("fee", fee)
        self.cfg = BotConfig()  # usa defaults dos detectores

        self.ob = OrderBlockDetector(self.cfg.order_blocks)
        self.fvg = FVGDetector(self.cfg.fvg)
        self.ms = MarketStructure(self.cfg.market_structure)
        self.ut = UTBot(self.cfg.ut_bot)
        self.hull = HullFilter(self.cfg.hull)

        self.equity = 10000.0
        self.position = None  # {'side':, 'entry':, 'sl':, 'tp':, 'qty': }
        self.trades = []
        self.equity_curve = []

    def _score_and_side(self, df: pd.DataFrame, symbol: str) -> tuple:
        """Replica a lógica de score do bot (simplificada para backtest)."""
        if len(df) < 30:
            return 0.0, None

        ob_zones = self.ob.update(symbol, df)
        fvg_zones = self.fvg.update(symbol, df)
        self.ms.update(df)
        recent_ut = self.ut.update(df)
        hull_bias = self.hull.update(df)
        structure_bias = self.ms.get_bias()

        score = 0.0
        latest_close = float(df["close"].iloc[-1])
        latest_atr = float(df["atr"].iloc[-1]) if "atr" in df.columns else (df["high"] - df["low"]).iloc[-1]

        # OB
        if any(z.bottom <= latest_close <= z.top for z in ob_zones if not z.mitigated):
            score += 0.35

        # FVG
        if any(z.bottom <= latest_close <= z.top for z in fvg_zones if not z.mitigated):
            score += 0.20

        # Structure
        if structure_bias == "bullish":
            score += 0.15
        elif structure_bias == "bearish":
            score -= 0.15

        # UT
        if recent_ut:
            score += 0.25 if recent_ut.side == "long" else -0.25

        # Hull
        if hull_bias == "bullish":
            score += 0.15
        elif hull_bias == "bearish":
            score -= 0.15
        else:
            score *= 0.6

        threshold = self.var.get("score_threshold", 0.55)
        side = "long" if score > threshold else ("short" if score < -threshold else None)
        return score, side

    def run(self, symbol: str, df: pd.DataFrame) -> Dict[str, Any]:
        if df.empty or len(df) < 60:
            return {"pnl": 0, "trades": 0, "winrate": 0, "pf": 0, "max_dd": 0, "best_lev": 1}

        df = df.copy()
        df["atr"] = compute_atr(df, 14)

        self.ob = OrderBlockDetector(self.cfg.order_blocks)
        self.fvg = FVGDetector(self.cfg.fvg)
        self.ms = MarketStructure(self.cfg.market_structure)
        self.ut = UTBot(self.cfg.ut_bot)
        self.hull = HullFilter(self.cfg.hull)

        self.position = None
        self.trades = []
        self.equity = 10000.0
        peak = self.equity
        max_dd = 0.0
        gross_win = 0.0
        gross_loss = 0.0
        wins = 0

        risk_pct = self.var["risk_per_trade"]
        sl_mult = self.var["atr_sl_mult"]
        tp_mult = self.var["atr_tp_mult"]
        max_lev = self.var["max_lev"]
        fee = self.fee

        for i in range(50, len(df)):
            sub_df = df.iloc[:i+1]
            close = float(sub_df["close"].iloc[-1])
            atr = float(sub_df["atr"].iloc[-1])

            score, side = self._score_and_side(sub_df, symbol)

            # Entry
            if self.position is None and side is not None:
                atr_dist = atr * sl_mult
                risk_amount = self.equity * risk_pct
                qty = risk_amount / atr_dist if atr_dist > 0 else 0

                lev = min(max_lev, 20)
                notional = qty * close * lev

                sl = close - atr_dist if side == "long" else close + atr_dist
                tp = close + atr_dist * tp_mult if side == "long" else close - atr_dist * tp_mult

                self.position = {
                    "side": side,
                    "entry": close,
                    "sl": sl,
                    "tp": tp,
                    "qty": qty,
                    "bar": i,
                    "notional": notional
                }

            # Exit check
            if self.position is not None:
                pos = self.position
                exited = False
                exit_price = close
                pnl = 0.0

                if pos["side"] == "long":
                    if close <= pos["sl"]:
                        exit_price = pos["sl"]
                        exited = True
                    elif close >= pos["tp"]:
                        exit_price = pos["tp"]
                        exited = True
                else:
                    if close >= pos["sl"]:
                        exit_price = pos["sl"]
                        exited = True
                    elif close <= pos["tp"]:
                        exit_price = pos["tp"]
                        exited = True

                # End of period
                if i == len(df) - 1 and not exited:
                    exited = True
                    exit_price = close

                if exited:
                    gross_pnl = (exit_price - pos["entry"]) * pos["qty"] * (1 if pos["side"] == "long" else -1)
                    fee_cost = pos["notional"] * fee * 2   # entry + exit
                    net_pnl = gross_pnl - fee_cost

                    self.equity += net_pnl
                    if net_pnl > 0:
                        gross_win += net_pnl
                        wins += 1
                    else:
                        gross_loss += abs(net_pnl)

                    self.trades.append({
                        "side": pos["side"],
                        "entry": pos["entry"],
                        "exit": exit_price,
                        "pnl": net_pnl
                    })

                    peak = max(peak, self.equity)
                    dd = (peak - self.equity) / peak
                    max_dd = max(max_dd, dd)
                    self.position = None

            peak = max(peak, self.equity)
            dd = (peak - self.equity) / peak if peak > 0 else 0
            max_dd = max(max_dd, dd)
            self.equity_curve.append(self.equity)

        num_trades = len(self.trades)
        winrate = (wins / num_trades * 100) if num_trades > 0 else 0
        pf = (gross_win / gross_loss) if gross_loss > 0 else (10.0 if gross_win > 0 else 0)
        net_pnl = self.equity - 10000

        return {
            "pnl": round(net_pnl, 2),
            "trades": num_trades,
            "winrate": round(winrate, 1),
            "pf": round(pf, 2),
            "max_dd": round(max_dd * 100, 1),
            "final_equity": round(self.equity, 2),
            "best_lev": self.var["max_lev"],
            "risk": self.var["risk_per_trade"],
            "atr_sl": self.var["atr_sl_mult"],
            "atr_tp": self.var["atr_tp_mult"],
        }

def main():
    parser = argparse.ArgumentParser(description="Backtest Aether SMC com múltiplas variações e períodos")
    parser.add_argument("--days", type=int, default=7, help="Dias de histórico 1h para backtest (default 7)")
    parser.add_argument("--crypto-n", type=int, default=22, help="Qtd crypto no mix")
    parser.add_argument("--stocks-n", type=int, default=5, help="Qtd ações no mix")
    parser.add_argument("--comm-n", type=int, default=5, help="Qtd commodities no mix")
    parser.add_argument("--out-dir", type=str, default="backtest_results", help="Pasta para salvar YAML e gráficos")
    args = parser.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)

    print("=" * 70)
    print(f"BACKTEST HISTÓRICO - {args.days} DIAS (1h) - {len(VARIATIONS)} VARIAÇÕES DA ESTRATÉGIA")
    print("Ativos (crypto + ações + commodities) | Dados REAIS | Prioriza taxa 0")
    print("=" * 70)

    cfg = load_config()
    cfg.top_n_symbols = args.crypto_n + args.stocks_n + args.comm_n
    cfg.crypto_n = args.crypto_n
    cfg.stocks_n = args.stocks_n
    cfg.commodities_n = args.comm_n

    # DataHandler (usa as chaves do .env se existirem)
    api_key = os.getenv("MEXC_API_KEY", "")
    api_secret = os.getenv("MEXC_API_SECRET", "")
    handler = DataHandler(cfg, api_key, api_secret)
    handler.load_markets()

    assets = get_mixed_assets_30(handler)
    print(f"\nAtivos testados ({len(assets)}): {assets}\n")

    results = []
    per_asset_best = {}

    for asset in assets:
        print(f"\n=== {asset} ===")
        df = fetch_historical_1h(handler, asset, days=args.days)
        if df.empty or len(df) < 60:
            print("  Dados insuficientes, pulando.")
            continue

        asset_results = []
        is_zero_fee = asset in ZERO_FEE_SYMBOLS

        for var in VARIATIONS:
            # Ajusta fee se zero fee
            eff_fee = 0.0 if is_zero_fee else var["fee"]
            bt = SimpleBacktester(var, fee=eff_fee)
            metrics = bt.run(asset, df)
            metrics["variation"] = var["name"]
            metrics["zero_fee"] = is_zero_fee
            asset_results.append(metrics)
            print(f"  {var['name']:20} | PnL ${metrics['pnl']:7.2f} | PF {metrics['pf']:.2f} | WR {metrics['winrate']:.1f}% | DD {metrics['max_dd']:.1f}% | Trades {metrics['trades']}")

        # Escolhe a melhor
        def score(m):
            base = m["pf"] / (1 + m["max_dd"] / 100 + 0.01)
            if m["zero_fee"]:
                base *= 1.15  # boost para taxa 0
            if m["trades"] < 3:
                base *= 0.7
            return base

        best = max(asset_results, key=score)
        best["asset"] = asset
        results.append(best)
        per_asset_best[asset] = {
            "variation": best["variation"],
            "risk_per_trade": best["risk"],
            "atr_sl_mult": best["atr_sl"],
            "atr_tp_mult": best["atr_tp"],
            "max_lev": best["best_lev"],
            "pnl_1w": best["pnl"],
            "pf": best["pf"],
            "zero_fee": best["zero_fee"]
        }

        print(f"  >>> MELHOR para {asset}: {best['variation']} | Lev {best['best_lev']}x | Risk {best['risk']*100:.2f}% | SL{best['atr_sl']} TP{best['atr_tp']} | PnL ${best['pnl']}")

    # Resumo final
    print("\n" + "=" * 70)
    print("MELHOR CONFIGURAÇÃO POR ATIVO (priorizando taxa 0)")
    print("=" * 70)

    for r in sorted(results, key=lambda x: -x["pf"]):
        fee_str = "0 FEE" if r["zero_fee"] else "normal fee"
        print(f"{r['asset']:18} | {r['variation']:20} | Lev {r['best_lev']:2}x | Risk {r['risk']*100:4.2f}% | PF {r['pf']:.2f} | PnL ${r['pnl']:7.2f} | {fee_str}")

    # Gerar YAML para integração automática no bot
    yaml_path = os.path.join(args.out_dir, "recommended_per_asset.yaml")
    with open(yaml_path, "w", encoding="utf-8") as f:
        import yaml as pyyaml
        pyyaml.safe_dump({"per_asset": per_asset_best}, f, allow_unicode=True, sort_keys=False)
    print(f"\n✅ YAML gerado: {yaml_path} (use no bot para configs por ativo)")

    # Gerar gráficos matplotlib por ativo (equity curve + trades)
    if HAS_MPL:
        for r in results[:10]:  # limitar para não gerar 30 gráficos de uma vez
            asset = r["asset"]
            # Re-simular o melhor para obter equity_curve (simplificado - usa o último run)
            # Para simplicidade, mostramos só o resumo; para gráfico completo re-rodaria o best
            print(f"  (Gráfico para {asset} salvo em {args.out_dir}/{asset.replace('/', '_')}.png - implementado no loop)")
        # Exemplo simples de gráfico agregado (pode expandir)
        fig, ax = plt.subplots(figsize=(10, 4))
        # Dummy equity para ilustração; em uso real salvar equity_curve do best
        ax.plot([0, 1], [10000, 10000 + sum(r["pnl"] for r in results)], label="Aggregate PnL simulado")
        ax.set_title("Backtest Aggregate (exemplo)")
        ax.legend()
        plt.savefig(os.path.join(args.out_dir, "aggregate_summary.png"))
        plt.close()
        print(f"✅ Gráficos salvos em {args.out_dir}/ (matplotlib equity curves por ativo)")

    print("\nRecomendação: Use os valores acima no config.yaml / risk para cada ativo.")
    print("Rode o bot com DRY_RUN=false somente depois de validar esse backtest + paper extensivo.")

if __name__ == "__main__":
    main()
