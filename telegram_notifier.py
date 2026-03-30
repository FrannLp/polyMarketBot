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


# ── Weather Bot Notifications ─────────────────────────────────────────────────

def notify_weather_entrada(signal: dict, bet_size: float, dry_run: bool) -> None:
    """Alert de entrada — temperature bot con EV, Kelly, fuente del pronóstico."""
    mode      = "🟡 DRY RUN" if dry_run else "🟢 REAL"
    side      = "✅ YES" if signal["best_side"] == "YES" else "❌ NO"
    price     = signal["price_yes"] if signal["best_side"] == "YES" else signal["price_no"]
    potential = round(bet_size / price, 2)
    unit      = signal.get("unit", "C")
    cond_sym  = ">=" if signal.get("condition") == "gte" else "<="
    threshold = signal.get("temp_threshold", "?")
    ptemp     = signal.get("primary_temp", "?")
    psource   = (signal.get("primary_source") or "?").upper()
    kelly_pct = round(signal.get("kelly_frac", 0) * 100, 1)
    metar_obs = signal.get("metar_obs")
    metar_str = f"\n🔭 METAR obs: {metar_obs:.1f}°{unit}" if metar_obs else ""

    msg = (
        f"{mode} — 🌡 <b>TEMPERATURA ENTRADA</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📍 Ciudad: <b>{signal['city']}</b>\n"
        f"❓ Condición: temp {cond_sym}{threshold:.0f}°{unit}\n"
        f"📌 Lado: {side}\n"
        f"💰 Apuesta: <b>${bet_size:.2f}</b> (Kelly {kelly_pct}%)\n"
        f"📈 Precio: {price:.2f}  |  EV: <b>{signal.get('ev', 0):+.2f}</b>\n"
        f"🎯 Ganancia potencial: <b>${potential:.2f}</b>\n"
        f"🌡 Pronóstico: {ptemp}°{unit} ({psource})"
        f"{metar_str}\n"
        f"🔍 Confianza: {signal['confidence']}  |  "
        f"P(ganar): {signal.get('prob_win', 0)*100:.0f}%\n"
        f"⏳ Resuelve en: {signal.get('hours_left', '?'):.0f}h"
    )
    _send(msg)


def notify_weather_result(bet: dict, won: bool) -> None:
    """Alert de resolución WON/LOST para un bet de temperatura."""
    icon  = "✅ WON" if won else "❌ LOST"
    pnl   = bet.get("pnl", 0) or 0
    unit  = bet.get("unit", "C")
    cond_sym = ">=" if bet.get("condition") == "gte" else "<="
    threshold = bet.get("temp_threshold", "?")
    msg = (
        f"🌡 <b>RESULTADO — {icon}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📍 Ciudad: <b>{bet.get('city', '?')}</b>\n"
        f"❓ Condición: temp {cond_sym}{threshold:.0f}°{unit}\n"
        f"📌 Lado: {bet.get('side')} | Precio entrada: {bet.get('price', 0):.2f}\n"
        f"💵 PnL: <b>{'+'if pnl>=0 else ''}{pnl:.2f}</b>\n"
        f"📊 Balance: ${bet.get('_balance_after', '?')}"
    )
    _send(msg)


def notify_weather_stop_loss(bet: dict, current_price: float, reason: str) -> None:
    """Alert cuando se activa stop-loss en una posición de temperatura."""
    unit     = bet.get("unit", "C")
    entry    = bet.get("price", 0)
    pct_drop = round((entry - current_price) / entry * 100, 1) if entry > 0 else 0
    threshold = bet.get("temp_threshold", "?")
    cond_sym  = ">=" if bet.get("condition") == "gte" else "<="
    reason_label = {
        "stop_loss_20pct":   "Precio -20% desde entrada",
        "forecast_moved":    "Pronóstico salió del bucket",
        "trailing_stop":     "Trailing stop activado",
    }.get(reason, reason)
    msg = (
        f"⛔ <b>STOP LOSS — {bet.get('city', '?')}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"❓ {cond_sym}{threshold:.0f}°{unit} | Lado: {bet.get('side')}\n"
        f"📉 Precio actual: {current_price:.2f} (entrada: {entry:.2f}, -{pct_drop}%)\n"
        f"💸 PnL: -${bet.get('bet_size', 0):.2f}\n"
        f"📋 Motivo: {reason_label}"
    )
    _send(msg)


def notify_weather_resumen(summary: dict) -> None:
    """Resumen del ciclo para el weather bot."""
    pnl      = summary["pnl_total"]
    pnl_icon = "📈" if pnl >= 0 else "📉"
    wr       = summary["win_rate"] * 100
    msg = (
        f"🌡 <b>RESUMEN TEMPERATURA</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💵 Balance: <b>${summary['balance']:.2f}</b> "
        f"({'+' if summary['pnl_pct']>=0 else ''}{summary['pnl_pct']:.1f}%)\n"
        f"{pnl_icon} PnL total: <b>${pnl:+.2f}</b>\n"
        f"🎰 Apuestas hoy: {summary['bets_today']}/{summary['max_daily']}\n"
        f"🏆 Win rate: {wr:.0f}% ({summary['total_won']}/{summary['total_bets']})"
    )
    _send(msg)
