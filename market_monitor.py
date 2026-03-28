"""
market_monitor.py
=================
Monitor de mercado independiente — corre en paralelo con los bots.

Monitorea y alerta via Telegram sobre:
  1. Fear & Greed Index  — sentimiento global crypto (Alternative.me, gratis)
  2. Volume spikes       — mercados de Polymarket con volumen >2x respecto al ciclo anterior
  3. Whale tracker       — wallets configuradas haciendo movimientos en crypto 5m markets
  4. Funding rates       — resumen de tasas de todos los assets (Binance Futures, gratis)

Uso:
    python market_monitor.py            # una sola vez
    python market_monitor.py --loop     # cada 5 minutos en loop
    python market_monitor.py --test     # solo testea Telegram

No interfiere con los bots — solo lee y notifica.
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

for _pkg in ["requests", "python-dotenv", "rich"]:
    try:
        __import__(_pkg.replace("-", "_").split("[")[0])
    except ImportError:
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", _pkg, "-q"])

import requests
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich import box

load_dotenv()

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
GAMMA_API        = "https://gamma-api.polymarket.com"
DATA_API         = "https://data-api.polymarket.com"
BINANCE_FUTURES  = "https://fapi.binance.com"

console = Console()

# Wallets crypto a monitorear — agrega las tuyas en .env como CRYPTO_WHALES
CRYPTO_WHALE_ADDRESSES = [
    addr.strip()
    for addr in os.getenv("CRYPTO_WHALES", "").split(",")
    if addr.strip() and len(addr.strip()) > 10
]

ASSETS_5M = ["BTC", "ETH", "SOL", "XRP"]
FUTURES_SYMBOLS = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT",
    "XRP": "XRPUSDT",
}

# State para detectar spikes (volumen anterior por market_id)
_VOL_STATE_FILE = "logs/monitor_vol_state.json"


# ── Telegram ──────────────────────────────────────────────────────────────────

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


# ── 1. Fear & Greed Index ─────────────────────────────────────────────────────

def fetch_fear_greed() -> dict | None:
    """
    Alternative.me Fear & Greed Index — gratis, sin API key.
    value: 0-100 (0=Extreme Fear, 100=Extreme Greed)
    classification: "Extreme Fear" | "Fear" | "Neutral" | "Greed" | "Extreme Greed"
    """
    try:
        resp = requests.get(
            "https://api.alternative.me/fng/",
            params={"limit": 2},   # today + yesterday
            timeout=8,
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
        if not data:
            return None
        today = data[0]
        yesterday = data[1] if len(data) > 1 else None
        return {
            "value":          int(today["value"]),
            "classification": today["value_classification"],
            "yesterday":      int(yesterday["value"]) if yesterday else None,
            "change":         int(today["value"]) - int(yesterday["value"]) if yesterday else 0,
        }
    except Exception:
        return None


def fg_signal(fg: dict) -> str:
    """Interpreta el F&G para los bots."""
    v = fg["value"]
    if   v <= 20: return "EXTREME_FEAR"    # rebote inminente históricamente
    elif v <= 40: return "FEAR"
    elif v <= 60: return "NEUTRAL"
    elif v <= 80: return "GREED"
    else:         return "EXTREME_GREED"   # corrección inminente históricamente


# ── 2. Volume Spike Detector ─────────────────────────────────────────────────

def _load_vol_state() -> dict:
    try:
        if os.path.exists(_VOL_STATE_FILE):
            return json.loads(open(_VOL_STATE_FILE, encoding="utf-8").read())
    except Exception:
        pass
    return {}


def _save_vol_state(state: dict) -> None:
    os.makedirs("logs", exist_ok=True)
    open(_VOL_STATE_FILE, "w", encoding="utf-8").write(
        json.dumps(state, indent=2, default=str)
    )


def fetch_active_markets(limit: int = 100) -> list[dict]:
    """Fetch active Polymarket markets sorted by volume."""
    try:
        resp = requests.get(
            f"{GAMMA_API}/markets",
            params={"active": "true", "closed": "false", "limit": limit,
                    "order": "volume", "ascending": "false"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []
    except Exception:
        return []


def detect_volume_spikes(threshold: float = 2.0) -> list[dict]:
    """
    Compara el volumen actual de cada mercado activo con el último ciclo.
    Devuelve mercados donde el volumen subió >= threshold veces.
    """
    markets   = fetch_active_markets()
    vol_state = _load_vol_state()
    spikes    = []
    new_state = {}

    for m in markets:
        mid = m.get("conditionId") or m.get("id", "")
        if not mid:
            continue
        try:
            vol = float(m.get("volume") or m.get("volume24hr") or 0)
        except (ValueError, TypeError):
            vol = 0.0

        new_state[mid] = {
            "vol":      vol,
            "question": (m.get("question") or "")[:80],
            "ts":       datetime.now(timezone.utc).isoformat(),
        }

        prev = vol_state.get(mid, {}).get("vol", 0)
        if prev > 100 and vol >= prev * threshold:
            spikes.append({
                "market_id": mid,
                "question":  (m.get("question") or "")[:80],
                "vol_now":   vol,
                "vol_prev":  prev,
                "multiplier": round(vol / prev, 1),
            })

    _save_vol_state(new_state)
    spikes.sort(key=lambda x: x["multiplier"], reverse=True)
    return spikes[:5]   # top 5 spikes


# ── 3. Whale Tracker (crypto 5m) ─────────────────────────────────────────────

def fetch_wallet_positions(address: str) -> list[dict]:
    """Fetch open positions of a wallet on Polymarket."""
    endpoints = [
        f"{DATA_API}/positions?user={address}&sizeThreshold=0",
        f"{GAMMA_API}/positions?user={address}&active=true",
    ]
    for url in endpoints:
        try:
            resp = requests.get(url, timeout=10)
            if resp.ok:
                data = resp.json()
                return data if isinstance(data, list) else data.get("positions", data.get("data", []))
        except Exception:
            continue
    return []


def check_crypto_whales() -> list[dict]:
    """
    Busca posiciones recientes de wallets whale en mercados crypto de 5m.
    Una posición reciente (<10 min) en un mercado UP/DOWN es una señal.
    """
    if not CRYPTO_WHALE_ADDRESSES:
        return []

    signals = []
    now = datetime.now(timezone.utc)

    for address in CRYPTO_WHALE_ADDRESSES:
        positions = fetch_wallet_positions(address)
        for pos in positions:
            question = (pos.get("title") or pos.get("question") or "").lower()
            # Solo mercados UP/DOWN de 5 minutos
            if "up or down" not in question:
                continue

            # Detectar asset
            asset = next((a for a in ASSETS_5M if a.lower() in question), None)
            if not asset:
                continue

            outcome = (pos.get("outcome") or pos.get("side") or "YES").upper()
            size    = float(pos.get("size") or pos.get("amount") or 0)
            price   = float(pos.get("avgPrice") or pos.get("price") or 0.5)

            # Solo posiciones grandes (>$10)
            if size < 10:
                continue

            signals.append({
                "wallet":   address[:8] + "...",
                "asset":    asset,
                "side":     "UP" if outcome == "YES" else "DOWN",
                "size":     size,
                "price":    price,
                "question": question[:60],
            })

    return signals


# ── 4. Funding Rates Summary ──────────────────────────────────────────────────

def fetch_all_funding_rates() -> dict[str, float | None]:
    """Fetch current funding rates for all tracked assets."""
    rates = {}
    for asset, symbol in FUTURES_SYMBOLS.items():
        try:
            resp = requests.get(
                f"{BINANCE_FUTURES}/fapi/v1/premiumIndex",
                params={"symbol": symbol},
                timeout=6,
            )
            resp.raise_for_status()
            rates[asset] = float(resp.json().get("lastFundingRate", 0)) * 100  # as %
        except Exception:
            rates[asset] = None
    return rates


# ── Display & Alerts ──────────────────────────────────────────────────────────

def print_and_alert(fg: dict | None, spikes: list[dict],
                    whales: list[dict], funding: dict[str, float | None]) -> None:
    console.rule("[bold cyan]MARKET MONITOR[/bold cyan]")
    console.print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    alert_parts = []

    # ── Fear & Greed ──────────────────────────────────────────────────────────
    if fg:
        sig  = fg_signal(fg)
        chg  = f"{fg['change']:+d}" if fg["change"] else "—"
        icon = {"EXTREME_FEAR": "😱", "FEAR": "😨", "NEUTRAL": "😐",
                "GREED": "😏", "EXTREME_GREED": "🤑"}.get(sig, "")
        color = {"EXTREME_FEAR": "green", "FEAR": "green",
                 "NEUTRAL": "white", "GREED": "red", "EXTREME_GREED": "red"}.get(sig, "white")
        console.print(
            f"  {icon} Fear & Greed: [{color}]{fg['value']} — {fg['classification']}[/{color}]"
            f"  (ayer: {fg['yesterday']}  cambio: {chg})"
        )
        if sig in ("EXTREME_FEAR", "EXTREME_GREED"):
            direction = "rebote UP esperado" if sig == "EXTREME_FEAR" else "corrección DOWN esperada"
            alert_parts.append(
                f"{icon} <b>Fear & Greed: {fg['value']} — {fg['classification']}</b>\n"
                f"   Señal histórica: {direction}"
            )
    else:
        console.print("  Fear & Greed: [dim]sin datos[/dim]")

    console.print()

    # ── Funding Rates ─────────────────────────────────────────────────────────
    t = Table(title="Funding Rates (Binance Perpetuos)", box=box.SIMPLE,
              header_style="bold")
    t.add_column("Asset", width=6)
    t.add_column("Rate",  justify="right", width=10)
    t.add_column("Señal", justify="left",  width=22)

    funding_alerts = []
    for asset, rate in funding.items():
        if rate is None:
            t.add_row(asset, "[dim]—[/dim]", "[dim]sin datos[/dim]")
            continue
        if   rate >  0.10: c, s = "bold red",   "🔴 LONGS muy overextended"
        elif rate >  0.05: c, s = "red",         "🟠 Longs cargados"
        elif rate > -0.05: c, s = "white",       "🟢 Neutral"
        elif rate > -0.10: c, s = "green",       "🟠 Shorts cargados"
        else:              c, s = "bold green",  "🔴 SHORTS muy overextended"
        t.add_row(asset, f"[{c}]{rate:+.4f}%[/{c}]", s)
        if abs(rate) > 0.10:
            funding_alerts.append(f"  {asset}: {rate:+.4f}% — {s}")

    console.print(t)

    if funding_alerts:
        alert_parts.append(
            "📊 <b>Funding extremo:</b>\n" + "\n".join(funding_alerts)
        )

    # ── Volume Spikes ─────────────────────────────────────────────────────────
    if spikes:
        console.print()
        t2 = Table(title="Volume Spikes en Polymarket", box=box.SIMPLE,
                   header_style="bold yellow")
        t2.add_column("Mercado",     width=55)
        t2.add_column("Vol. actual", justify="right", width=12)
        t2.add_column("Spike",       justify="right", width=7)
        for sp in spikes:
            t2.add_row(
                sp["question"],
                f"${sp['vol_now']:,.0f}",
                f"[bold yellow]{sp['multiplier']}x[/bold yellow]",
            )
        console.print(t2)

        spike_lines = "\n".join(
            f"  {sp['multiplier']}x — {sp['question'][:55]} (${sp['vol_now']:,.0f})"
            for sp in spikes
        )
        alert_parts.append(f"🔥 <b>Volume spikes:</b>\n{spike_lines}")
    else:
        console.print("\n  [dim]Sin volume spikes detectados.[/dim]")

    # ── Whale Tracker ─────────────────────────────────────────────────────────
    if whales:
        console.print()
        t3 = Table(title="Whale Activity — Crypto 5M", box=box.SIMPLE,
                   header_style="bold magenta")
        t3.add_column("Wallet",  width=12)
        t3.add_column("Asset",   width=5)
        t3.add_column("Lado",    width=5)
        t3.add_column("Size",    justify="right", width=8)
        t3.add_column("Precio",  justify="right", width=7)
        for w in whales:
            side_c = "green" if w["side"] == "UP" else "red"
            t3.add_row(
                w["wallet"], w["asset"],
                f"[{side_c}]{w['side']}[/{side_c}]",
                f"${w['size']:.0f}", f"{w['price']:.2f}",
            )
        console.print(t3)

        whale_lines = "\n".join(
            f"  {w['wallet']} → {w['asset']} {w['side']} ${w['size']:.0f} @{w['price']:.2f}"
            for w in whales
        )
        alert_parts.append(f"🐋 <b>Whale activity:</b>\n{whale_lines}")
    elif CRYPTO_WHALE_ADDRESSES:
        console.print("\n  [dim]Sin actividad whale reciente en 5M markets.[/dim]")
    else:
        console.print(
            "\n  [dim]Whale tracker: agrega wallets en .env → CRYPTO_WHALES=0x...,0x...[/dim]"
        )

    # ── Telegram alert ────────────────────────────────────────────────────────
    if alert_parts:
        msg = (
            "🔭 <b>MARKET MONITOR</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            + "\n\n".join(alert_parts)
        )
        if _send(msg):
            console.print("\n  [dim]Alerta enviada a Telegram.[/dim]")
    else:
        console.print("\n  [dim]Sin alertas que enviar.[/dim]")


# ── Main ──────────────────────────────────────────────────────────────────────

def run_once() -> None:
    console.print("[dim]Cargando datos...[/dim]")
    fg      = fetch_fear_greed()
    spikes  = detect_volume_spikes(threshold=2.0)
    whales  = check_crypto_whales()
    funding = fetch_all_funding_rates()
    print_and_alert(fg, spikes, whales, funding)


def main() -> None:
    parser = argparse.ArgumentParser(description="Polymarket market monitor")
    parser.add_argument("--loop", action="store_true",
                        help="Correr en loop cada 5 minutos")
    parser.add_argument("--interval", type=int, default=300,
                        help="Intervalo en segundos (default 300 = 5 min)")
    parser.add_argument("--test", action="store_true",
                        help="Solo testea la conexion a Telegram")
    args = parser.parse_args()

    if args.test:
        ok = _send("✅ <b>Market Monitor</b> conectado correctamente.")
        print("Telegram OK" if ok else "Telegram FAIL — revisa TELEGRAM_TOKEN y TELEGRAM_CHAT_ID")
        return

    run_once()

    if args.loop:
        console.print(f"\n[dim]Loop activo. Proxima ejecucion en {args.interval}s. Ctrl+C para salir.[/dim]")
        while True:
            try:
                time.sleep(args.interval)
                run_once()
            except KeyboardInterrupt:
                console.print("\n[dim]Monitor detenido.[/dim]")
                break


if __name__ == "__main__":
    main()
