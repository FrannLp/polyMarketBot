"""
POLYMARKET WEATHER BOT — v2
============================
Upgrades vs v1:
  - 3 forecast sources: ECMWF, HRRR (US), METAR
  - Expected Value filter (WEATHER_MIN_EV) instead of fixed edge
  - Fractional Kelly Criterion for position sizing
  - Auto-resolution: checks Gamma API every 30 min for WON/LOST
  - Stop-loss monitoring: checks prices every 10 min
  - Per-market data snapshots in data/markets/
  - Telegram: entry, result, stop-loss, daily summary
  - State: logs/weather_state.json (separate from other bots)

Run: python bot.py
"""

import io
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ── Auto-install dependencies ─────────────────────────────────────────────────
def _check_deps():
    missing = []
    for pkg in ["requests", "dotenv", "apscheduler", "rich", "colorama"]:
        try:
            __import__(pkg.replace("-", "_"))
        except ImportError:
            missing.append({"dotenv": "python-dotenv"}.get(pkg, pkg))
    if missing:
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install"] + missing, stdout=subprocess.DEVNULL)

_check_deps()

import requests
from rich.console import Console
from rich.table   import Table
from rich.panel   import Panel
from rich         import box
from apscheduler.schedulers.blocking import BlockingScheduler

from config import (
    WEATHER_DRY_RUN, WEATHER_INITIAL_BALANCE, WEATHER_BET_SIZE, WEATHER_MIN_EV,
    WEATHER_MAX_DAILY_BETS, WEATHER_DAILY_STOP_LOSS, WEATHER_KELLY_FRACTION,
    WEATHER_STOP_LOSS_PCT, GAMMA_API,
    TELEGRAM_TOKEN, TELEGRAM_CHAT_ID,
)
from signal_engine   import generate_signals
from risk_manager    import WeatherRiskManager
from telegram_notifier import (
    notify_weather_entrada, notify_weather_result,
    notify_weather_stop_loss, notify_weather_resumen,
    notify_stop, notify_inicio,
)

DRY_RUN = WEATHER_DRY_RUN
console = Console()
risk    = WeatherRiskManager()

DATA_DIR = Path("data/markets")
DATA_DIR.mkdir(parents=True, exist_ok=True)


# ── Market data snapshots (for calibration) ────────────────────────────────────

def _market_file(market_id: str) -> Path:
    return DATA_DIR / f"{market_id[:12]}.json"


def _load_market_data(market_id: str) -> dict:
    f = _market_file(market_id)
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_market_data(market_id: str, data: dict):
    _market_file(market_id).write_text(
        json.dumps(data, indent=2, default=str), encoding="utf-8"
    )


def _record_forecast_snapshot(signal: dict):
    """Append forecast snapshot to per-market file (for calibration later)."""
    mid  = signal["market_id"]
    data = _load_market_data(mid)
    if "snapshots" not in data:
        data["snapshots"] = []
        data["city"]      = signal["city"]
        data["question"]  = signal["question"]
        data["created_at"] = datetime.now(timezone.utc).isoformat()
    data["snapshots"].append({
        "ts":             datetime.now(timezone.utc).isoformat(),
        "hours_left":     signal.get("hours_left"),
        "primary_temp":   signal.get("primary_temp"),
        "primary_source": signal.get("primary_source"),
        "ecmwf":          signal.get("ecmwf_temp"),
        "hrrr":           signal.get("hrrr_temp"),
        "metar":          signal.get("metar_obs"),
        "prob_real":      signal.get("prob_real"),
        "confidence":     signal.get("confidence"),
        "ev":             signal.get("ev"),
    })
    _save_market_data(mid, data)


# ── Resolution checker ─────────────────────────────────────────────────────────

def check_resolution():
    """Check Gamma API for resolved markets and update PENDING bets."""
    pending = [b for b in risk.state["history"] if b["status"] == "PENDING"]
    if not pending:
        return

    console.print(f"[dim]Verificando resolución de {len(pending)} apuesta(s) pendiente(s)...[/dim]")
    now = datetime.now(timezone.utc)

    for bet in pending:
        mid = bet.get("market_id", "")
        if not mid:
            continue

        # Skip if market hasn't reached end_date yet (add buffer of 30 min)
        end_str = bet.get("end_date")
        if end_str:
            try:
                end_dt = datetime.fromisoformat(end_str)
                if end_dt.tzinfo is None:
                    end_dt = end_dt.replace(tzinfo=timezone.utc)
                if now < end_dt + timedelta(minutes=30):
                    continue
            except Exception:
                pass

        try:
            resp = requests.get(
                f"{GAMMA_API}/markets",
                params={"condition_id": mid},
                timeout=12,
            )
            resp.raise_for_status()
            data = resp.json()
            mkt  = data[0] if isinstance(data, list) and data else data

            # Only resolve if market is closed/resolved
            if not (mkt.get("closed") or mkt.get("resolved")):
                continue

            prices = mkt.get("outcomePrices") or []
            if not prices or len(prices) < 2:
                continue

            price_yes = float(prices[0])

            won = None
            if bet["side"] == "YES":
                if price_yes >= 0.95:
                    won = True
                elif price_yes <= 0.05:
                    won = False
            else:  # NO side
                price_no = float(prices[1]) if len(prices) > 1 else (1 - price_yes)
                if price_no >= 0.95:
                    won = True
                elif price_no <= 0.05:
                    won = False

            if won is None:
                continue  # not yet fully resolved

            pnl = risk.record_result(mid, won, close_reason="resolved")
            if pnl is not None:
                bet["_balance_after"] = f"${risk.balance:.2f}"
                notify_weather_result(bet, won)
                status = "WON ✓" if won else "LOST ✗"
                color  = "green" if won else "red"
                console.print(
                    f"[{color}]Resuelto: {bet.get('city')} | {bet['side']} | "
                    f"{status} | PnL ${pnl:+.2f}[/{color}]"
                )

                # Update per-market data with actual outcome
                mdata = _load_market_data(mid)
                mdata["status"]   = "WON" if won else "LOST"
                mdata["resolved_price_yes"] = price_yes
                mdata["resolved_at"] = datetime.now(timezone.utc).isoformat()
                _save_market_data(mid, mdata)

        except Exception as e:
            console.print(f"[dim]Error verificando {mid[:12]}...: {e}[/dim]")


# ── Stop-loss monitor ──────────────────────────────────────────────────────────

def check_stop_losses():
    """Close positions if price dropped WEATHER_STOP_LOSS_PCT from entry."""
    pending = [b for b in risk.state["history"] if b["status"] == "PENDING"]
    if not pending:
        return

    for bet in pending:
        mid = bet.get("market_id", "")
        if not mid:
            continue
        try:
            resp = requests.get(
                f"{GAMMA_API}/markets",
                params={"condition_id": mid},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            mkt  = data[0] if isinstance(data, list) and data else data

            if mkt.get("closed") or mkt.get("resolved"):
                continue  # let check_resolution handle this

            prices = mkt.get("outcomePrices") or []
            if not prices or len(prices) < 2:
                continue

            price_yes = float(prices[0])
            current   = price_yes if bet["side"] == "YES" else float(prices[1])
            entry     = bet["price"]

            if entry <= 0:
                continue

            pct_drop = (entry - current) / entry
            if pct_drop >= WEATHER_STOP_LOSS_PCT:
                # Close position as LOST
                pnl = risk.record_result(mid, False, close_reason="stop_loss_20pct")
                if pnl is not None:
                    notify_weather_stop_loss(bet, current, "stop_loss_20pct")
                    console.print(
                        f"[red]STOP LOSS: {bet.get('city')} | "
                        f"Precio {current:.2f} (entrada {entry:.2f}, -{pct_drop*100:.1f}%) | "
                        f"PnL ${pnl:+.2f}[/red]"
                    )

        except Exception:
            pass


# ── Display helpers ────────────────────────────────────────────────────────────

def print_banner():
    mode_text = "[bold red]LIVE MODE[/bold red]" if not DRY_RUN else "[bold green]DRY RUN (simulación)[/bold green]"
    console.print(Panel(
        f"""[bold cyan]POLYMARKET WEATHER BOT v2[/bold cyan]
Mode: {mode_text}
Balance inicial: [yellow]${WEATHER_INITIAL_BALANCE:.2f}[/yellow]
Kelly fraction:  [yellow]{WEATHER_KELLY_FRACTION*100:.0f}%[/yellow]  |  Max bet: [yellow]${WEATHER_BET_SIZE:.2f}[/yellow]
Min EV:          [yellow]{WEATHER_MIN_EV:.2f}[/yellow]
Sources:         [cyan]ECMWF + HRRR (US) + METAR[/cyan]
""",
        box=box.DOUBLE, border_style="cyan",
    ))


def print_dashboard():
    s = risk.summary()
    pnl_color = "green" if s["pnl_total"] >= 0 else "red"
    console.print(Panel(
        f"Balance: [yellow]${s['balance']:.2f}[/yellow]  "
        f"PnL: [{pnl_color}]${s['pnl_total']:+.2f} ({s['pnl_pct']:+.1f}%)[/{pnl_color}]  "
        f"Hoy: [cyan]{s['bets_today']}/{s['max_daily']}[/cyan]  "
        f"Win rate: [magenta]{s['win_rate']*100:.0f}%[/magenta] "
        f"({s['total_won']}/{s['total_bets']})",
        title="[bold]DASHBOARD — WEATHER BOT[/bold]", border_style="dim",
    ))


def print_signals_table(signals: list[dict]):
    if not signals:
        console.print("[dim]No hay señales que superen los filtros.[/dim]")
        return

    table = Table(
        title=f"SEÑALES ENCONTRADAS ({len(signals)})",
        box=box.ROUNDED, header_style="bold cyan", show_lines=True,
    )
    table.add_column("Ciudad",     style="bold white", no_wrap=True)
    table.add_column("Condición",  max_width=30)
    table.add_column("Pronóst.",   justify="right")
    table.add_column("Fuente",     justify="center")
    table.add_column("Lado",       justify="center")
    table.add_column("Precio",     justify="right")
    table.add_column("P(win)",     justify="right")
    table.add_column("EV",         justify="right")
    table.add_column("Kelly%",     justify="right")
    table.add_column("Conf.",      justify="center")
    table.add_column("Horas",      justify="right")

    for s in signals:
        unit      = s.get("unit", "C")
        cond_sym  = ">=" if s["condition"] == "gte" else "<="
        cond_str  = f"{cond_sym}{s['temp_threshold']:.0f}°{unit}"
        ptemp     = s.get("primary_temp")
        ptemp_str = f"{ptemp:.1f}°{unit}" if ptemp is not None else "?"
        psource   = (s.get("primary_source") or "?").upper()
        price     = s["price_yes"] if s["best_side"] == "YES" else s["price_no"]
        conf_map  = {"HIGH": "[green]HIGH[/green]", "MEDIUM": "[yellow]MED[/yellow]", "LOW": "[red]LOW[/red]"}
        side_str  = "[green]YES[/green]" if s["best_side"] == "YES" else "[red]NO[/red]"
        ev_str    = f"[green]{s['ev']:+.2f}[/green]" if s["ev"] > 0 else f"[red]{s['ev']:+.2f}[/red]"
        kelly_str = f"{s['kelly_frac']*100:.1f}%"

        table.add_row(
            s["city"],
            cond_str,
            ptemp_str,
            psource,
            side_str,
            f"{price:.2f}",
            f"{s['prob_win']*100:.0f}%",
            ev_str,
            kelly_str,
            conf_map.get(s["confidence"], s["confidence"]),
            f"{s.get('hours_left', '?'):.0f}h",
        )
    console.print(table)


def print_bet_placed(signal: dict, bet: dict):
    price     = signal["price_yes"] if signal["best_side"] == "YES" else signal["price_no"]
    bet_size  = bet["bet_size"]
    potential = round(bet_size / price, 2)
    mode_tag  = "[DRY RUN]" if bet["dry_run"] else "[REAL]"
    unit      = signal.get("unit", "C")
    ptemp     = signal.get("primary_temp", "?")
    psource   = (signal.get("primary_source") or "?").upper()
    console.print(Panel(
        f"{mode_tag} [bold]APUESTA COLOCADA[/bold]\n"
        f"Mercado: [white]{signal['question'][:65]}[/white]\n"
        f"Ciudad:  [cyan]{signal['city']}[/cyan]  "
        f"Lado: {'[green]YES' if signal['best_side']=='YES' else '[red]NO'}[/bold]  "
        f"Precio: {price:.2f}\n"
        f"Apuesta: [yellow]${bet_size:.2f}[/yellow] (Kelly {signal['kelly_frac']*100:.1f}%)  "
        f"Potencial: [green]${potential:.2f}[/green]  "
        f"EV: [cyan]{signal['ev']:+.2f}[/cyan]\n"
        f"Pronóstico: {ptemp}°{unit} ({psource})  "
        f"Confianza: {signal['confidence']}",
        border_style="green" if not bet["dry_run"] else "yellow",
    ))


def print_history():
    history = risk.get_history(last_n=15)
    if not history:
        console.print("[dim]Sin historial aún.[/dim]")
        return

    table = Table(title="HISTORIAL RECIENTE", box=box.SIMPLE, header_style="bold")
    table.add_column("Hora",     no_wrap=True)
    table.add_column("Ciudad",   style="white")
    table.add_column("Cond.",    justify="center")
    table.add_column("Lado",     justify="center")
    table.add_column("Monto",    justify="right")
    table.add_column("EV",       justify="right")
    table.add_column("Resuelve", justify="center")
    table.add_column("Estado",   justify="center")
    table.add_column("PnL",      justify="right")

    now = datetime.now(timezone.utc)
    for b in reversed(history):
        ts       = b["timestamp"][:16].replace("T", " ")
        unit     = b.get("unit", "C")
        cond_sym = ">=" if b.get("condition") == "gte" else "<="
        thresh   = b.get("temp_threshold", "?")
        cond_str = f"{cond_sym}{thresh:.0f}°{unit}" if isinstance(thresh, (int, float)) else "?"
        side     = f"[green]{b['side']}[/green]" if b["side"] == "YES" else f"[red]{b['side']}[/red]"
        ev_str   = f"{b.get('ev', 0):+.2f}"
        status_map = {
            "PENDING": "[yellow]PENDING[/yellow]",
            "WON":     "[green]WON ✓[/green]",
            "LOST":    "[red]LOST ✗[/red]",
        }
        pnl_str  = ""
        if b.get("pnl") is not None:
            c = "green" if b["pnl"] >= 0 else "red"
            pnl_str = f"[{c}]${b['pnl']:+.2f}[/{c}]"

        # Time to resolution
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

        table.add_row(ts, b.get("city", "?"), cond_str, side,
                      f"${b['bet_size']:.2f}", ev_str, resolve_str,
                      status_map.get(b["status"], b["status"]), pnl_str)
    console.print(table)


# ── Main cycle ─────────────────────────────────────────────────────────────────

def run_cycle():
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    console.rule(f"[dim]Ciclo temperatura: {ts}[/dim]")
    print_dashboard()

    # Check if we can bet
    can, reason = risk.can_bet()
    if not can:
        console.print(f"[red]STOP:[/red] {reason}")
        notify_stop(reason)
        return

    # Generate signals
    console.print("\n[bold cyan]Analizando mercados de temperatura...[/bold cyan]")
    signals = generate_signals(verbose=True)

    if not signals:
        console.print("[yellow]Sin señales válidas (EV insuficiente o baja confianza).[/yellow]")
        return

    print_signals_table(signals)

    # Record forecast snapshot for ALL signals (calibration data)
    for sig in signals[:5]:
        _record_forecast_snapshot(sig)

    # Select best signal not already bet
    best = None
    for sig in signals:
        if risk.already_bet(sig["market_id"]):
            console.print(f"[dim]Saltando {sig['city']} — ya hay apuesta PENDING.[/dim]")
            continue
        best = sig
        break

    if best is None:
        console.print("[yellow]Todos los mercados con señal ya tienen apuesta PENDING.[/yellow]")
        return

    console.print(
        f"\n[bold]Mejor señal: {best['city']} | EV={best['ev']:+.2f} | "
        f"Kelly={best['kelly_frac']*100:.1f}% | Confianza={best['confidence']}[/bold]"
    )

    # Kelly-sized bet, capped at WEATHER_BET_SIZE
    kelly_bet = min(best["kelly_frac"] * risk.balance, WEATHER_BET_SIZE)
    kelly_bet = max(kelly_bet, 0.10)  # minimum $0.10

    bet = risk.record_bet(best, dry_run=DRY_RUN, bet_size=kelly_bet)
    print_bet_placed(best, bet)
    notify_weather_entrada(best, kelly_bet, dry_run=DRY_RUN)
    notify_weather_resumen(risk.summary())

    console.print()
    print_history()


# ── Monitoring cycle (every 10 min) ───────────────────────────────────────────

def run_monitor():
    """Quick pass: stop-loss check + resolution check."""
    check_stop_losses()
    check_resolution()


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    print_banner()

    # Create .env if missing
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    env_example = os.path.join(os.path.dirname(__file__), ".env.example")
    if not os.path.exists(env_path) and os.path.exists(env_example):
        import shutil
        shutil.copy(env_example, env_path)
        console.print("[dim]Archivo .env creado desde .env.example[/dim]\n")

    console.print(f"[dim]DRY_RUN={DRY_RUN} | Balance: ${risk.balance:.2f} | "
                  f"Kelly={WEATHER_KELLY_FRACTION*100:.0f}% | Min EV={WEATHER_MIN_EV:.2f}[/dim]\n")

    notify_inicio(risk.balance, DRY_RUN)

    # Run immediately
    run_cycle()

    # Schedule jobs
    console.print("\n[dim]Scheduler activo:[/dim]")
    console.print("[dim]  - Ciclo completo (señales + apuestas): cada 60 min[/dim]")
    console.print("[dim]  - Stop-loss + resolución: cada 10 min[/dim]")
    console.print("[dim]Ctrl+C para salir.[/dim]\n")

    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(run_cycle,  "interval", minutes=60)
    scheduler.add_job(run_monitor, "interval", minutes=10)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        console.print("\n[yellow]Bot detenido por el usuario.[/yellow]")
        print_dashboard()
        print_history()


if __name__ == "__main__":
    main()
