r"""
dashboard.py - Painel em tempo real para o Aether SMC Bot (MEXC Futures)

Execute em outro terminal / PowerShell enquanto o bot está rodando:

    cd C:/Users/55639/mexc_aether_bot
    .\.venv\Scripts\activate
    python dashboard.py

Ele atualiza automaticamente a cada poucos segundos lendo:
- state/bot_state.json
- O log mais recente em logs/
- As últimas linhas de atividade

Dependência leve: rich (instale com: pip install rich)
"""

import os
import json
import time
import glob
from datetime import datetime
from collections import deque

try:
    from rich.console import Console
    from rich.live import Live
    from rich.table import Table
    from rich.panel import Panel
    from rich.layout import Layout
    from rich.text import Text
except ImportError:
    print("Por favor instale o rich: pip install rich")
    exit(1)

console = Console()

STATE_FILE = "state/bot_state.json"
LOGS_DIR = "logs"


def get_latest_log_file() -> str:
    logs = sorted(glob.glob(os.path.join(LOGS_DIR, "aether_*.log")))
    return logs[-1] if logs else ""


def read_state() -> dict:
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def tail_log(path: str, max_lines: int = 25) -> list[str]:
    if not path or not os.path.exists(path):
        return ["(nenhum log encontrado ainda)"]
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        return [line.rstrip() for line in lines[-max_lines:]]
    except Exception:
        return ["(erro ao ler log)"]


def parse_recent_activity(log_lines: list[str]) -> list[str]:
    """Filtra linhas interessantes para o painel."""
    interesting = []
    for line in log_lines:
        lower = line.lower()
        if any(kw in lower for kw in ["top ", "analyzing ", "dry-run", "sinal", "score", "confluence", "risk", "position", "error", "cooldown"]):
            # Limpa timestamp se muito longo
            if "|" in line and len(line) > 20:
                parts = line.split("|", 3)
                if len(parts) >= 3:
                    interesting.append(parts[-1].strip())
                else:
                    interesting.append(line[-120:])
            else:
                interesting.append(line[-120:])
    return interesting[-12:]  # últimas relevantes


def build_dashboard() -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="main"),
        Layout(name="risk", size=5),
        Layout(name="log", size=12),
    )

    # Header
    header_text = Text("AETHER FLOW SYSTEM - PAINEL EM TEMPO REAL (MEXC Futures)", style="bold cyan")
    header_text.append("\nDRY-RUN / PAPER TRADING  |  Atualiza a cada ~5s  |  Pressione Ctrl+C para sair", style="dim")
    layout["header"].update(Panel(header_text, style="cyan"))

    state = read_state()
    log_path = get_latest_log_file()
    log_lines = tail_log(log_path, 100)  # more lines to capture detailed [SYM] CLOSE= logs for all symbols
    recent = parse_recent_activity(log_lines)

    # Try structured status first (written by bot for dashboard) - now with real symbols_data for values
    symbols = []
    last_update = "N/A"
    symbols_data = {}
    try:
        if os.path.exists("state/dashboard_status.json"):
            with open("state/dashboard_status.json", "r", encoding="utf-8") as f:
                dstatus = json.load(f)
                symbols = dstatus.get("top_symbols", [])
                symbols_data = dstatus.get("symbols_data", {})
                last_update = dstatus.get("timestamp", "N/A")
                is_real = not dstatus.get("dry_run", True)
    except Exception:
        is_real = False
        pass

    # Fallback to parsing the log
    if not symbols:
        for line in reversed(log_lines):
            if "Top 10 symbols:" in line:
                try:
                    start = line.find("[")
                    end = line.rfind("]") + 1
                    symbols = eval(line[start:end])
                    last_update = line[:19]
                    break
                except Exception:
                    pass

    # Main table - Symbols & Activity WITH LOGS AND DETAILS
    table = Table(title="Símbolos Monitorados (Top Volume 24h) - DETALHES DO PAINEL", show_header=True, header_style="bold magenta")
    table.add_column("Símbolo", style="bold")
    table.add_column("Close", justify="right")
    table.add_column("ATR%", justify="right")
    table.add_column("Score", justify="right")
    table.add_column("Bias", justify="center")
    table.add_column("OB/FVG", justify="center")
    table.add_column("UT", justify="center")
    table.add_column("Action", justify="center")
    table.add_column("Status")

    # Parse detailed logs from bot: [SYM] CLOSE=... ATR%=... SCORE=... BIAS=... OB=... FVG=... UT=... ACTION=...
    symbol_details = {}
    for l in reversed(log_lines):
        if l.startswith("[") and "] CLOSE=" in l:
            try:
                sym = l.split("]")[0][1:]
                # crude parse key=value
                d = {}
                for token in l.split():
                    if "=" in token:
                        k, v = token.split("=", 1)
                        d[k] = v
                symbol_details[sym] = d
            except Exception:
                pass

    if symbols:
        for sym in symbols:
            last_anal = "aguardando..."
            for l in reversed(log_lines):
                if f"Analyzing {sym}" in l:
                    last_anal = l[:19]
                    break
            # Prefer symbols_data from status (real values from bot dump)
            d = symbols_data.get(sym, symbol_details.get(sym, {}))
            close = d.get("CLOSE", d.get("close", "-"))
            atrp = d.get("ATR%", d.get("atr_pct", "-"))
            score = d.get("SCORE", d.get("score", "-"))
            bias = d.get("BIAS", d.get("bias", "-"))
            obfvg = d.get("OB/FVG", d.get("ob_fvg", f"{d.get('OB', '?')}/{d.get('FVG', '?')}"))
            ut = d.get("UT", d.get("ut", "-"))
            action = d.get("ACTION", d.get("action", "hold"))
            status = "Em observação"
            for l in reversed(log_lines[-30:]):
                low = l.lower()
                if sym in l and ("dry-run" in low or "position registered" in low or "sinal" in low or "live" in low):
                    status = Text("SINAL/TRADE", style="bold yellow")
                    break
            if any(sym in l and "ERROR" in l for l in log_lines[-5:]):
                status = Text("SKIP (não suportado)", style="red")
            table.add_row(sym, close, atrp, score, bias, obfvg, ut, action, status)
    else:
        table.add_row("Aguardando...", "-", "-", "-", "-", "-", "-", "-", "Iniciando...")

    layout["main"].update(Panel(table, title="Análise por Par", border_style="blue"))

    # Risk Panel - now shows REAL if not dry_run
    open_pos = state.get("open_positions", [])
    is_dry = state.get("dry_run", True)
    risk_text = Text()
    risk_text.append(f"Posições abertas: {len(open_pos)}\n", style="bold")
    risk_text.append(f"Último update: {state.get('timestamp', 'N/A')}\n", style="dim")
    risk_text.append("Status de Risco: ", style="bold")
    mode_str = "OK (DRY-RUN / PAPER)" if is_dry else "OK (REAL TRADER - LIVE)"
    mode_style = "green" if is_dry else "red"
    risk_text.append(mode_str, style=mode_style)
    if open_pos:
        risk_text.append(f"\nOpen: {', '.join(open_pos)}")
    risk_text.append("\n\nLembrete: Risco por trade configurado em 0.5% | Limite diário 3%")

    layout["risk"].update(Panel(risk_text, title="Gestão de Risco", border_style="green"))

    # Log tail
    log_text = Text("\n".join(recent), style="dim")
    layout["log"].update(Panel(log_text, title=f"Log Recente ({os.path.basename(log_path) if log_path else 'N/A'})", border_style="yellow"))

    return layout


def main():
    console.clear()
    console.print("[bold green]Iniciando painel do Aether Bot...[/bold green]")
    console.print("Lendo state/ e logs/ a cada 5 segundos.\n")

    try:
        with Live(build_dashboard(), refresh_per_second=0.5, screen=True) as live:
            while True:
                live.update(build_dashboard())
                time.sleep(5)
    except KeyboardInterrupt:
        console.print("\n[bold red]Painel encerrado.[/bold red]")


if __name__ == "__main__":
    main()
