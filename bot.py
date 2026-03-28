"""
POLYMARKET WEATHER BOT
======================
DRY_RUN mode: simula $20 con apuestas de $0.50
Copia a los mejores traders de temperatura + analisis climatico real

Ejecutar: python bot.py
"""

import os
import sys
import time
from datetime import datetime

# ─── Verificar e instalar dependencias ───────────────────────────────────────
def check_dependencies():
    missing = []
    for pkg in ["requests", "dotenv", "apscheduler", "rich", "colorama", "tabulate"]:
        try:
            __import__(pkg.replace("-", "_"))
        except ImportError:
            real_name = {"dotenv": "python-dotenv"}.get(pkg, pkg)
            missing.append(real_name)
    if missing:
        print(f"Instalando dependencias: {', '.join(missing)}")
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install"] + missing, stdout=subprocess.DEVNULL)
        print("Dependencias instaladas. Reiniciando...\n")

check_dependencies()

# ─── Imports post-install ─────────────────────────────────────────────────────
from rich.console import Console
from rich.table   import Table
from rich.panel   import Panel
from rich.text    import Text
from rich         import box
from apscheduler.schedulers.blocking import BlockingScheduler

from config       import DRY_RUN, INITIAL_BALANCE, BET_SIZE, MIN_EDGE
from signal_engine import generate_signals
from risk_manager  import RiskManager
from telegram_notifier import (
    notify_entrada, notify_resumen, notify_stop,
    notify_inicio, notify_sin_senales,
)

console = Console()
risk    = RiskManager()


# ─── Display helpers ──────────────────────────────────────────────────────────

def print_banner():
    mode_text = "[bold red]LIVE MODE[/bold red]" if not DRY_RUN else "[bold green]DRY RUN (simulacion)[/bold green]"
    console.print(Panel(
        f"""[bold cyan]POLYMARKET WEATHER BOT[/bold cyan]
Mode: {mode_text}
Balance inicial: [yellow]${INITIAL_BALANCE:.2f}[/yellow]
Tamaño apuesta:  [yellow]${BET_SIZE:.2f}[/yellow]
Edge minimo:     [yellow]{MIN_EDGE*100:.0f}%[/yellow]
""",
        box=box.DOUBLE,
        border_style="cyan",
    ))


def print_dashboard():
    s = risk.summary()
    pnl_color = "green" if s["pnl_total"] >= 0 else "red"

    console.print(Panel(
        f"Balance: [yellow]${s['balance']:.2f}[/yellow]  "
        f"PnL: [{pnl_color}]${s['pnl_total']:+.2f} ({s['pnl_pct']:+.1f}%)[/{pnl_color}]  "
        f"Apuestas hoy: [cyan]{s['bets_today']}/{s['max_daily']}[/cyan]  "
        f"Win rate: [magenta]{s['win_rate']*100:.0f}%[/magenta] ({s['total_won']}/{s['total_bets']})",
        title="[bold]DASHBOARD[/bold]",
        border_style="dim",
    ))


def print_signals_table(signals: list[dict]):
    if not signals:
        console.print("[dim]No hay señales que superen los filtros de riesgo.[/dim]")
        return

    table = Table(
        title=f"SEÑALES ENCONTRADAS ({len(signals)})",
        box=box.ROUNDED,
        header_style="bold cyan",
        show_lines=True,
    )
    table.add_column("Ciudad",     style="bold white", no_wrap=True)
    table.add_column("Condicion",  style="white",      max_width=40)
    table.add_column("Temp",       justify="right")
    table.add_column("Apuesta",    justify="center")
    table.add_column("Precio",     justify="right")
    table.add_column("P.Real",     justify="right")
    table.add_column("Edge",       justify="right")
    table.add_column("Confianza",  justify="center")
    table.add_column("Copy",       justify="center")
    table.add_column("Dias",       justify="right")

    for s in signals:
        edge_str  = f"[green]{s['best_edge']*100:.1f}%[/green]"
        price_str = f"{(s['price_yes'] if s['best_side']=='YES' else s['price_no']):.2f}"
        prob_str  = f"[cyan]{s['prob_real']*100:.0f}%[/cyan]"
        side_str  = f"[green]SI ocurre[/green]" if s["best_side"] == "YES" else f"[red]NO ocurre[/red]"
        conf_col  = {"HIGH": "[green]HIGH[/green]", "MEDIUM": "[yellow]MED[/yellow]", "LOW": "[red]LOW[/red]"}
        copy_str  = "[bold green]SI[/bold green]" if s["copy_aligned"] else "[dim]--[/dim]"
        cond_str  = f">={s['temp_threshold']:.0f}C" if s["condition"] == "gte" else f"<={s['temp_threshold']:.0f}C"

        table.add_row(
            s["city"],
            s["question"][:38] + "..." if len(s["question"]) > 40 else s["question"],
            cond_str,
            side_str,
            price_str,
            prob_str,
            edge_str,
            conf_col.get(s["confidence"], s["confidence"]),
            copy_str,
            str(s["days_to_resolve"]),
        )

    console.print(table)


def print_bet_placed(signal: dict, bet: dict):
    price = signal["price_yes"] if signal["best_side"] == "YES" else signal["price_no"]
    potential = round(BET_SIZE / price, 2)
    mode_tag  = "[DRY RUN]" if bet["dry_run"] else "[REAL]"

    console.print(Panel(
        f"{mode_tag} [bold]APUESTA COLOCADA[/bold]\n"
        f"Mercado: [white]{signal['question'][:60]}[/white]\n"
        f"Ciudad:  [cyan]{signal['city']}[/cyan]  "
        f"Lado: [bold]{'[green]YES' if signal['best_side']=='YES' else '[red]NO'}[/bold]  "
        f"Precio: {price:.2f}\n"
        f"Monto: [yellow]${BET_SIZE:.2f}[/yellow]  "
        f"Ganancia potencial: [green]${potential:.2f}[/green]  "
        f"Edge: [cyan]{signal['best_edge']*100:.1f}%[/cyan]\n"
        f"Clima: temp_avg={signal['temp_avg_max']}°C  "
        f"Modelos={signal['models_agree']}/{signal['models_total']}  "
        f"Confianza={signal['confidence']}\n"
        f"Copy aligned: {'SI ✓' if signal['copy_aligned'] else 'NO'}",
        border_style="green" if not bet["dry_run"] else "yellow",
    ))


def print_history():
    history = risk.get_history(last_n=15)
    if not history:
        console.print("[dim]Sin historial aun.[/dim]")
        return

    from datetime import timezone

    table = Table(title="HISTORIAL RECIENTE", box=box.SIMPLE, header_style="bold")
    table.add_column("Hora",      no_wrap=True)
    table.add_column("Ciudad",    style="white")
    table.add_column("Apuesta",   justify="center")
    table.add_column("Monto",     justify="right")
    table.add_column("Resuelve",  justify="center")
    table.add_column("Estado",    justify="center")
    table.add_column("PnL",       justify="right")

    now = datetime.now(timezone.utc)

    for b in reversed(history):
        ts   = b["timestamp"][:16].replace("T", " ")
        city = b.get("city", "?")
        side_label = "SI ocurre" if b["side"] == "YES" else "NO ocurre"
        side = f"[green]{side_label}[/green]" if b["side"] == "YES" else f"[red]{side_label}[/red]"
        status_map = {
            "PENDING": "[yellow]PENDING[/yellow]",
            "WON":     "[green]WON ✓[/green]",
            "LOST":    "[red]LOST ✗[/red]",
        }
        pnl_str = ""
        if b["pnl"] is not None:
            c = "green" if b["pnl"] >= 0 else "red"
            pnl_str = f"[{c}]${b['pnl']:+.2f}[/{c}]"

        # Calcular tiempo restante
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
                resolve_str = b.get("days_to_resolve", "?")

        table.add_row(ts, city, side, f"${b['bet_size']:.2f}", resolve_str, status_map.get(b["status"], b["status"]), pnl_str)

    console.print(table)


# ─── Ciclo principal ──────────────────────────────────────────────────────────

def run_cycle():
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    console.rule(f"[dim]Ciclo: {ts}[/dim]")

    # Dashboard
    print_dashboard()

    # Verificar si podemos apostar
    can, reason = risk.can_bet()
    if not can:
        console.print(f"[red]STOP:[/red] {reason}")
        notify_stop(reason)
        return

    # Generar señales
    console.print("\n[bold cyan]Analizando mercados de temperatura...[/bold cyan]")
    signals = generate_signals(verbose=True)

    if not signals:
        console.print("[yellow]Sin señales validas en este ciclo.[/yellow]")
        notify_sin_senales()
        return

    # Mostrar todas las señales encontradas
    print_signals_table(signals)

    # Apostar solo la mejor señal por ciclo que no esté ya apostada
    best = None
    for sig in signals:
        if risk.already_bet(sig["market_id"]):
            console.print(f"[dim]Saltando {sig['city']} — ya hay apuesta PENDING en este mercado.[/dim]")
            continue
        best = sig
        break

    if best is None:
        console.print("[yellow]Todas las señales ya tienen apuesta PENDING.[/yellow]")
        return

    console.print(f"\n[bold]Mejor señal: {best['city']} | Edge={best['best_edge']*100:.1f}% | Confianza={best['confidence']}[/bold]")

    if DRY_RUN:
        bet = risk.record_bet(best, dry_run=True)
        print_bet_placed(best, bet)
        notify_entrada(best, BET_SIZE, dry_run=True)
    else:
        # TODO: implementar ejecucion real con py-clob-client
        console.print("[red]LIVE mode: ejecutar orden real aqui (requiere credenciales)[/red]")
        bet = risk.record_bet(best, dry_run=False)
        print_bet_placed(best, bet)
        notify_entrada(best, BET_SIZE, dry_run=False)

    notify_resumen(risk.summary())

    # Historial
    console.print()
    print_history()


# ─── Entry point ──────────────────────────────────────────────────────────────

def main():
    print_banner()

    # Crear archivo .env si no existe
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    env_example = os.path.join(os.path.dirname(__file__), ".env.example")
    if not os.path.exists(env_path) and os.path.exists(env_example):
        import shutil
        shutil.copy(env_example, env_path)
        console.print("[dim]Archivo .env creado desde .env.example[/dim]\n")

    console.print(f"[dim]DRY_RUN={DRY_RUN} | Balance actual: ${risk.balance:.2f}[/dim]\n")
    notify_inicio(risk.balance, DRY_RUN)

    # Primera ejecucion inmediata
    run_cycle()

    # Scheduler: cada 30 minutos
    console.print("\n[dim]Scheduler activo. Proxima ejecucion en 30 min. Ctrl+C para salir.[/dim]")
    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(run_cycle, "interval", minutes=30)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        console.print("\n[yellow]Bot detenido por el usuario.[/yellow]")
        print_dashboard()
        print_history()


if __name__ == "__main__":
    main()
