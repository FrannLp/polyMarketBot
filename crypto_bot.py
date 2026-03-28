"""
POLYMARKET CRYPTO BOT
=====================
Analiza mercados crypto de Polymarket usando precio real (CoinGecko)
+ indicadores técnicos (SMA, RSI, tendencia) + copy trading.

Ejecutar: python crypto_bot.py
"""

import os
import sys
import time
from datetime import datetime, timezone

# ─── Verificar e instalar dependencias ───────────────────────────────────────
def check_dependencies():
    missing = []
    for pkg in ["requests", "dotenv", "apscheduler", "rich", "colorama"]:
        try:
            __import__(pkg.replace("-", "_"))
        except ImportError:
            real_name = {"dotenv": "python-dotenv"}.get(pkg, pkg)
            missing.append(real_name)
    if missing:
        print(f"Instalando dependencias: {', '.join(missing)}")
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install"] + missing,
                              stdout=subprocess.DEVNULL)
        print("Dependencias instaladas.\n")

check_dependencies()

from rich.console import Console
from rich.table   import Table
from rich.panel   import Panel
from rich         import box
from apscheduler.schedulers.blocking import BlockingScheduler

from config              import DRY_RUN, CRYPTO_INITIAL_BALANCE, CRYPTO_BET_SIZE, CRYPTO_MIN_EDGE
from crypto_signal_engine import generate_crypto_signals
from risk_manager        import CryptoRiskManager
from telegram_notifier   import (
    notify_entrada, notify_resumen, notify_stop,
    notify_inicio, notify_sin_senales,
)

console = Console()
risk    = CryptoRiskManager()

SCAN_INTERVAL_MINUTES = 45  # crypto se mueve más rápido que el clima


# ─── Display helpers ──────────────────────────────────────────────────────────

def print_banner():
    mode_text = "[bold red]LIVE MODE[/bold red]" if not DRY_RUN else "[bold green]DRY RUN (simulacion)[/bold green]"
    console.print(Panel(
        f"""[bold yellow]POLYMARKET CRYPTO BOT[/bold yellow]
Mode: {mode_text}
Balance inicial: [yellow]${CRYPTO_INITIAL_BALANCE:.2f}[/yellow]
Tamaño apuesta:  [yellow]${CRYPTO_BET_SIZE:.2f}[/yellow]
Edge minimo:     [yellow]{CRYPTO_MIN_EDGE*100:.0f}%[/yellow]
Fuente precio:   [cyan]CoinGecko (gratis, sin API key)[/cyan]
Indicadores:     [cyan]SMA7, SMA20, RSI14, Tendencia[/cyan]
""",
        box=box.DOUBLE,
        border_style="yellow",
    ))


def print_dashboard():
    s = risk.summary()
    pnl_color = "green" if s["pnl_total"] >= 0 else "red"
    console.print(Panel(
        f"Balance: [yellow]${s['balance']:.2f}[/yellow]  "
        f"PnL: [{pnl_color}]${s['pnl_total']:+.2f} ({s['pnl_pct']:+.1f}%)[/{pnl_color}]  "
        f"Apuestas hoy: [cyan]{s['bets_today']}/{s['max_daily']}[/cyan]  "
        f"Win rate: [magenta]{s['win_rate']*100:.0f}% ({s['total_won']}/{s['total_bets']})[/magenta]",
        title="[bold]DASHBOARD CRYPTO[/bold]",
        border_style="dim",
    ))


def _market_desc(sig: dict) -> str:
    """Descripción legible del tipo de mercado."""
    t = sig["market_type"]
    asset = sig["asset"]
    if t == "above_below":
        cond = "encima de" if sig["condition"] == "above" else "debajo de"
        target = sig["price_target"]
        return f"{asset} {cond} ${target:,.0f}"
    elif t == "range":
        return f"{asset} en rango ${sig['price_lo']:,.0f}–${sig['price_hi']:,.0f}"
    elif t == "direction":
        return f"{asset} {'SUBE' if sig['condition'] == 'up' else 'BAJA'}"
    return asset


def print_signals_table(signals: list[dict]):
    if not signals:
        console.print("[dim]No hay señales que superen los filtros.[/dim]")
        return

    table = Table(
        title=f"SEÑALES CRYPTO ({len(signals)})",
        box=box.ROUNDED,
        header_style="bold yellow",
        show_lines=True,
    )
    table.add_column("Asset",      style="bold white", no_wrap=True)
    table.add_column("Mercado",    style="white", max_width=34)
    table.add_column("Precio act.", justify="right")
    table.add_column("RSI",        justify="right")
    table.add_column("Tendencia",  justify="center")
    table.add_column("Apuesta",    justify="center")
    table.add_column("Precio",     justify="right")
    table.add_column("P.Real",     justify="right")
    table.add_column("Edge",       justify="right")
    table.add_column("Confianza",  justify="center")
    table.add_column("Copy",       justify="center")
    table.add_column("Dias",       justify="right")

    for s in signals:
        rsi = s["rsi"]
        rsi_color = "red" if rsi > 70 else ("green" if rsi < 30 else "white")
        trend = s["trend_score"]
        trend_str = "↑↑" if trend >= 0.8 else ("↑" if trend >= 0.5 else ("↓↓" if trend <= 0.2 else "↓"))
        trend_color = "green" if trend >= 0.5 else "red"

        price_mkt = s["price_yes"] if s["best_side"] == "YES" else s["price_no"]
        side_str  = "[green]SI ocurre[/green]" if s["best_side"] == "YES" else "[red]NO ocurre[/red]"
        conf_col  = {"HIGH": "[green]HIGH[/green]", "MEDIUM": "[yellow]MED[/yellow]", "LOW": "[red]LOW[/red]"}
        copy_str  = "[bold green]SI[/bold green]" if s["copy_aligned"] else "[dim]--[/dim]"

        cur = s["current_price"]
        cur_str = f"${cur:,.2f}" if cur < 10 else f"${cur:,.0f}"

        table.add_row(
            s["asset"],
            _market_desc(s),
            cur_str,
            f"[{rsi_color}]{rsi:.0f}[/{rsi_color}]",
            f"[{trend_color}]{trend_str}[/{trend_color}]",
            side_str,
            f"{price_mkt:.2f}",
            f"[cyan]{s['prob_win']*100:.0f}%[/cyan]",
            f"[green]{s['best_edge']*100:.1f}%[/green]",
            conf_col.get(s["confidence"], s["confidence"]),
            copy_str,
            str(s["days_to_resolve"]),
        )

    console.print(table)


def print_bet_placed(signal: dict, bet: dict):
    price = signal["price_yes"] if signal["best_side"] == "YES" else signal["price_no"]
    potential = round(CRYPTO_BET_SIZE / price, 2)
    mode_tag  = "[DRY RUN]" if bet["dry_run"] else "[REAL]"

    cur = signal["current_price"]
    cur_str = f"${cur:,.2f}" if cur < 10 else f"${cur:,.0f}"

    console.print(Panel(
        f"{mode_tag} [bold]APUESTA COLOCADA[/bold]\n"
        f"Mercado: [white]{signal['question'][:65]}[/white]\n"
        f"Asset:   [yellow]{signal['asset']}[/yellow]  "
        f"Precio actual: [cyan]{cur_str}[/cyan]  "
        f"Cambio 24h: {'[green]' if signal['change_24h']>=0 else '[red]'}{signal['change_24h']:+.2f}%{'[/green]' if signal['change_24h']>=0 else '[/red]'}\n"
        f"RSI: [white]{signal['rsi']:.0f}[/white]  "
        f"Tendencia: {signal['trend_score']:.2f}  "
        f"SMA7: {signal['sma7']:,.0f}  SMA20: {signal['sma20']:,.0f}\n"
        f"Apuesta: [bold]{'[green]SI ocurre' if signal['best_side']=='YES' else '[red]NO ocurre'}[/bold]  "
        f"Precio: {price:.2f}  Edge: [cyan]{signal['best_edge']*100:.1f}%[/cyan]\n"
        f"Monto: [yellow]${CRYPTO_BET_SIZE:.2f}[/yellow]  "
        f"Ganancia potencial: [green]${potential:.2f}[/green]  "
        f"Confianza: {signal['confidence']}\n"
        f"Copy aligned: {'SI ✓' if signal['copy_aligned'] else 'NO'}",
        border_style="yellow" if bet["dry_run"] else "green",
    ))


def print_history():
    history = risk.get_history(last_n=15)
    if not history:
        console.print("[dim]Sin historial aun.[/dim]")
        return

    now = datetime.now(timezone.utc)
    table = Table(title="HISTORIAL RECIENTE", box=box.SIMPLE, header_style="bold")
    table.add_column("Hora",     no_wrap=True)
    table.add_column("Asset",    style="yellow")
    table.add_column("Apuesta",  justify="center")
    table.add_column("Monto",    justify="right")
    table.add_column("Resuelve", justify="center")
    table.add_column("Estado",   justify="center")
    table.add_column("PnL",      justify="right")

    status_map = {
        "PENDING": "[yellow]PENDING[/yellow]",
        "WON":     "[green]WON ✓[/green]",
        "LOST":    "[red]LOST ✗[/red]",
    }

    for b in reversed(history):
        ts   = b["timestamp"][:16].replace("T", " ")
        city = b.get("city") or b.get("asset", "?")
        side_label = "SI ocurre" if b["side"] == "YES" else "NO ocurre"
        side = f"[green]{side_label}[/green]" if b["side"] == "YES" else f"[red]{side_label}[/red]"

        pnl_str = ""
        if b["pnl"] is not None:
            c = "green" if b["pnl"] >= 0 else "red"
            pnl_str = f"[{c}]${b['pnl']:+.2f}[/{c}]"

        resolve_str = "?"
        end_str = b.get("end_date")
        if end_str:
            try:
                end_dt = datetime.fromisoformat(end_str)
                if end_dt.tzinfo is None:
                    end_dt = end_dt.replace(tzinfo=timezone.utc)
                delta = end_dt - now
                if delta.total_seconds() <= 0:
                    resolve_str = "[dim]vencido[/dim]"
                else:
                    h = int(delta.total_seconds() // 3600)
                    m = int((delta.total_seconds() % 3600) // 60)
                    resolve_str = f"{h}h {m}m"
            except Exception:
                resolve_str = str(b.get("days_to_resolve", "?"))

        table.add_row(ts, city.upper(), side, f"${b['bet_size']:.2f}",
                      resolve_str, status_map.get(b["status"], b["status"]), pnl_str)

    console.print(table)


# ─── Ciclo principal ──────────────────────────────────────────────────────────

def run_cycle():
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    console.rule(f"[dim]Ciclo crypto: {ts}[/dim]")

    print_dashboard()

    can, reason = risk.can_bet()
    if not can:
        console.print(f"[red]STOP:[/red] {reason}")
        notify_stop(reason)
        return

    console.print("\n[bold yellow]Analizando mercados crypto...[/bold yellow]")
    signals = generate_crypto_signals(verbose=True)

    if not signals:
        console.print("[yellow]Sin señales válidas en este ciclo.[/yellow]")
        notify_sin_senales()
        return

    print_signals_table(signals)

    # Mejor señal sin apuesta PENDING duplicada
    best = None
    for sig in signals:
        if risk.already_bet(sig["market_id"]):
            console.print(f"[dim]Saltando {sig['asset']} — apuesta PENDING ya existe.[/dim]")
            continue
        best = sig
        break

    if best is None:
        console.print("[yellow]Todas las señales ya tienen apuesta PENDING.[/yellow]")
        return

    console.print(
        f"\n[bold]Mejor señal: {best['asset']} | {_market_desc(best)} | "
        f"Edge={best['best_edge']*100:.1f}% | RSI={best['rsi']:.0f} | "
        f"Confianza={best['confidence']}[/bold]"
    )

    if DRY_RUN:
        bet = risk.record_bet(best, dry_run=True)
        print_bet_placed(best, bet)
        notify_entrada(best, CRYPTO_BET_SIZE, dry_run=True)
    else:
        console.print("[red]LIVE mode: ejecutar orden real aqui (requiere credenciales)[/red]")
        bet = risk.record_bet(best, dry_run=False)
        print_bet_placed(best, bet)
        notify_entrada(best, CRYPTO_BET_SIZE, dry_run=False)

    notify_resumen(risk.summary())

    console.print()
    print_history()


# ─── Entry point ──────────────────────────────────────────────────────────────

def main():
    print_banner()
    console.print(f"[dim]DRY_RUN={DRY_RUN} | Balance actual: ${risk.balance:.2f}[/dim]\n")
    notify_inicio(risk.balance, DRY_RUN)

    run_cycle()

    console.print(f"\n[dim]Scheduler activo. Próximo ciclo en {SCAN_INTERVAL_MINUTES} min. Ctrl+C para salir.[/dim]")
    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(run_cycle, "interval", minutes=SCAN_INTERVAL_MINUTES)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        console.print("\n[yellow]Bot detenido por el usuario.[/yellow]")
        print_dashboard()
        print_history()


if __name__ == "__main__":
    main()
