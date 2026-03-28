"""
telegram_notifier.py
====================
Envía notificaciones al Telegram del usuario sobre entradas, salidas y resumen diario.
"""

import requests
from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID


def _send(msg: str) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=10,
        )
        return resp.ok
    except Exception:
        return False


def notify_entrada(signal: dict, bet_size: float, dry_run: bool) -> None:
    mode = "🟡 DRY RUN" if dry_run else "🟢 REAL"
    side = "✅ YES" if signal["best_side"] == "YES" else "❌ NO"
    price = signal["price_yes"] if signal["best_side"] == "YES" else signal["price_no"]
    potential = round(bet_size / price, 2)
    days = signal.get("days_to_resolve", "?")

    if "temp_threshold" in signal:
        cond = f">={signal['temp_threshold']:.0f}°C" if signal["condition"] == "gte" else f"<={signal['temp_threshold']:.0f}°C"
        market_line = f"🌆 Ciudad: <b>{signal['city'].title()}</b>\n🌡 Condicion: temp {cond}\n"
    else:
        t = signal.get("market_type", "")
        asset = signal.get("asset", "?")
        if t == "above_below":
            cond_word = "encima de" if signal.get("condition") == "above" else "debajo de"
            cond = f"{asset} {cond_word} ${signal['price_target']:,.0f}"
        elif t == "range":
            cond = f"{asset} en rango ${signal['price_lo']:,.0f}–${signal['price_hi']:,.0f}"
        elif t == "direction":
            cond = f"{asset} {'SUBE' if signal.get('condition') == 'up' else 'BAJA'}"
        else:
            cond = asset
        market_line = f"📊 Asset: <b>{asset}</b>\n📌 Mercado: {cond}\n"

    msg = (
        f"{mode} — <b>ENTRADA</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{market_line}"
        f"📌 Lado: {side}\n"
        f"💰 Monto: <b>${bet_size:.2f}</b>\n"
        f"📈 Precio: {price:.2f}  |  Edge: {signal['best_edge']*100:.1f}%\n"
        f"🎯 Ganancia potencial: <b>${potential:.2f}</b>\n"
        f"⏳ Resuelve en: {days} dia(s)\n"
        f"🔍 Confianza: {signal['confidence']}"
    )
    _send(msg)


def notify_resumen(summary: dict) -> None:
    pnl = summary["pnl_total"]
    pnl_icon = "📈" if pnl >= 0 else "📉"
    msg = (
        f"📊 <b>RESUMEN DEL CICLO</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💵 Balance: <b>${summary['balance']:.2f}</b>\n"
        f"{pnl_icon} PnL total: <b>${pnl:+.2f} ({summary['pnl_pct']:+.1f}%)</b>\n"
        f"🎰 Apuestas hoy: {summary['bets_today']}/{summary['max_daily']}\n"
        f"🏆 Win rate: {summary['win_rate']*100:.0f}% "
        f"({summary['total_won']}/{summary['total_bets']})"
    )
    _send(msg)


def notify_stop(reason: str) -> None:
    msg = (
        f"🛑 <b>BOT PAUSADO</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Motivo: {reason}"
    )
    _send(msg)


def notify_inicio(balance: float, dry_run: bool) -> None:
    mode = "🟡 DRY RUN" if dry_run else "🟢 LIVE"
    msg = (
        f"🤖 <b>BOT INICIADO</b> — {mode}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💵 Balance: <b>${balance:.2f}</b>\n"
        f"⏱ Ciclos cada 30 minutos"
    )
    _send(msg)


def notify_sin_senales() -> None:
    _send("🔎 Sin señales válidas en este ciclo.")
