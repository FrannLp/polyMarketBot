"""
poly5m_bot.py
=============
5-minute UP/DOWN bot using ONLY Polymarket data (no Binance).

Signals derived from:
  - CLOB orderbook imbalance (bids vs asks on UP token)
  - Price deviation from 0.50 (market mispricing)

Runs simultaneously alongside cryp_signal_5minutes.py.
Uses its own state file: logs/poly5m_state.json

Usage:
    PYTHONUTF8=1 PYTHONIOENCODING=utf-8 python poly5m_bot.py

Config (.env):
    POLY5M_BET_SIZE         default 2.50
    POLY5M_INITIAL_BALANCE  default 25.00
    POLY5M_MIN_EDGE         default 0.06   (6% — lower since signal is cleaner)
    POLY5M_MAX_DAILY_BETS   default 200
    POLY5M_DAILY_STOP_LOSS  default 300.00
    POLY5M_MAX_PRICE        default 0.65
    POLY5M_MIN_BOOK_DEPTH   default 500    (min USDC in book to trust signal)
    DRY_RUN                 from .env (shared with other bots)
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

for _pkg in ["requests", "apscheduler", "rich", "python-dotenv"]:
    try:
        __import__(_pkg.replace("-", "_").split("[")[0])
    except ImportError:
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", _pkg, "-q"])

from dotenv import load_dotenv
load_dotenv()

from apscheduler.schedulers.blocking import BlockingScheduler
from rich.console import Console
from rich.table import Table
from rich import box

from cryp_5m_scraper  import fetch_5m_markets, ASSETS
from poly5m_analyzer  import analyze_market_poly, get_best_ask

# ── Config ────────────────────────────────────────────────────────────────────
DRY_RUN          = os.getenv("DRY_RUN",                       "true").lower() == "true"
BOT_NAME         = os.getenv("POLY5M_BOT_NAME",               "SIM" if os.getenv("DRY_RUN","true").lower()=="true" else "PROD")
BET_SIZE         = float(os.getenv("POLY5M_BET_SIZE",         "2.50"))
INITIAL_BALANCE  = float(os.getenv("POLY5M_INITIAL_BALANCE",  "25.00"))
MIN_EDGE         = float(os.getenv("POLY5M_MIN_EDGE",          "0.06"))
MAX_DAILY_BETS   = int(os.getenv("POLY5M_MAX_DAILY_BETS",      "200"))
DAILY_STOP_LOSS  = float(os.getenv("POLY5M_DAILY_STOP_LOSS",   "300.00"))
MAX_PRICE        = float(os.getenv("POLY5M_MAX_PRICE",          "0.65"))
MIN_BOOK_DEPTH   = float(os.getenv("POLY5M_MIN_BOOK_DEPTH",    "500"))
MAX_PER_CYCLE    = int(os.getenv("POLY5M_MAX_BETS_PER_CYCLE",  "2"))
TG_TOKEN         = os.getenv("POLY5M_TELEGRAM_TOKEN",  "") or os.getenv("TELEGRAM_TOKEN", "")
TG_CHAT          = os.getenv("POLY5M_TELEGRAM_CHAT_ID", "") or os.getenv("TELEGRAM_CHAT_ID", "")

# Per-asset MIN_EDGE overrides and on/off switch (none = skip asset)
MIN_EDGE_PER_ASSET: dict[str, float] = {
    "BTC":  float(os.getenv("POLY5M_MIN_EDGE_BTC",  str(MIN_EDGE))),
    "ETH":  float(os.getenv("POLY5M_MIN_EDGE_ETH",  str(MIN_EDGE))),
    "SOL":  float(os.getenv("POLY5M_MIN_EDGE_SOL",  "0.10")),   # raised: 46% WR
    "XRP":  float(os.getenv("POLY5M_MIN_EDGE_XRP",  str(MIN_EDGE))),
    "DOGE": float(os.getenv("POLY5M_MIN_EDGE_DOGE", str(MIN_EDGE))),
    "HYPE": float(os.getenv("POLY5M_MIN_EDGE_HYPE", "99.0")),   # effectively disabled: 33% WR
}

# ── CLOB client (solo para LIVE) ──────────────────────────────────────────────
_clob_client = None

def _get_clob_client():
    global _clob_client
    if _clob_client is not None:
        return _clob_client
    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.constants import POLYGON
        from py_clob_client.clob_types import ApiCreds
        proxy_wallet = os.getenv("POLYMARKET_PROXY_WALLET", "")
        _clob_client = ClobClient(
            "https://clob.polymarket.com",
            key=os.getenv("POLYMARKET_PRIVATE_KEY"),
            chain_id=POLYGON,
            signature_type=1,
            funder=proxy_wallet if proxy_wallet else None,
            creds=ApiCreds(
                api_key=os.getenv("POLYMARKET_API_KEY"),
                api_secret=os.getenv("POLYMARKET_API_SECRET"),
                api_passphrase=os.getenv("POLYMARKET_PASSPHRASE"),
            ),
        )
    except Exception as e:
        console.print(f"[red][CLOB] Error inicializando cliente: {e}[/red]")
    return _clob_client


def _place_real_order(token_id: str, price: float, bet_size: float) -> dict | None:
    """Ejecuta orden real BUY en Polymarket CLOB. Retorna respuesta o None."""
    try:
        from py_clob_client.clob_types import OrderArgs, PartialCreateOrderOptions
        from py_clob_client.order_builder.constants import BUY
        client = _get_clob_client()
        if not client:
            raise RuntimeError("CLOB client no disponible")
        shares  = max(5.0, round(bet_size / price, 2))   # mínimo 5 shares
        options = PartialCreateOrderOptions(tick_size="0.01", neg_risk=False)
        return client.create_and_post_order(
            OrderArgs(token_id=token_id, price=price, size=shares, side=BUY),
            options=options,
        )
    except Exception as e:
        console.print(f"[red][CLOB] Error colocando orden: {e}[/red]")
        return None

STATE_FILE         = Path("logs/poly5m_state.json")
_NOTIFIED_FILE     = Path("logs/poly5m_notified.json")
console    = Console()


# ── Telegram ──────────────────────────────────────────────────────────────────

def _tg(text: str) -> None:
    if not TG_TOKEN or not TG_CHAT:
        return
    try:
        import requests as _req
        _req.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": text, "parse_mode": "HTML"},
            timeout=6,
        )
    except Exception:
        pass


def _load_notified() -> set:
    try:
        return set(json.loads(_NOTIFIED_FILE.read_text(encoding="utf-8")))
    except Exception:
        return set()


def _save_notified(keys: set) -> None:
    try:
        _NOTIFIED_FILE.write_text(json.dumps(list(keys)), encoding="utf-8")
    except Exception:
        pass


def _notify_bet(market_id: str, asset: str, side: str, price: float,
                edge: float, prob_win: float, question: str,
                bid_up: float, bid_down: float) -> None:
    key = f"{BOT_NAME}:{market_id}:{side}"
    notified = _load_notified()
    if key in notified:
        return
    notified.add(key)
    _save_notified(notified)
    mode    = f"🟡 SIM [{BOT_NAME}]" if DRY_RUN else f"🟢 REAL [{BOT_NAME}]"
    side_ic = "⬆️" if side == "UP" else "⬇️"
    gain    = round(BET_SIZE / price, 2)
    _tg(
        f"{mode} — <b>5M ENTRADA (PM Signal)</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📊 Asset: <b>{asset}</b>\n"
        f"📌 {question[:60]}\n"
        f"{side_ic} Lado: <b>{side}</b>\n"
        f"💰 Monto: <b>${BET_SIZE:.2f}</b>\n"
        f"📈 Precio: {price:.2f}  |  Edge: {edge*100:.1f}%\n"
        f"🎯 Ganancia potencial: <b>${gain:.2f}</b>\n"
        f"🔮 Prob win: {prob_win:.1%}"
    )


def _notify_resolved(bet: dict, pnl: float) -> None:
    mode     = f"SIM [{BOT_NAME}]" if DRY_RUN else f"REAL [{BOT_NAME}]"
    status   = bet["status"]
    ic       = "✅ WON" if status == "WON" else "❌ LOST"
    side_ic  = "⬆️" if bet.get("side") == "UP" else "⬇️"
    pnl_str  = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
    question = bet.get("question", "")[:55]
    msg = (
        f"{ic} — <b>5M [{mode}]</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📊 Asset: <b>{bet.get('asset','?')}</b>  {side_ic} {bet.get('side','?')}\n"
        f"📌 {question}\n"
        f"📈 Entrada: {bet.get('price',0):.2f}  |  PnL: <b>{pnl_str}</b>"
    )
    if status == "WON":
        payout = round(bet.get("bet_size", 0) / bet.get("price", 1), 2)
        msg += f"\n💰 Pago: <b>${payout:.2f}</b>  (apuesta ${bet.get('bet_size',0):.2f})"
        msg += "\n\n👆 <a href='https://polymarket.com/portfolio'>Reclamar ganancias en Polymarket</a>"
    _tg(msg)


def pnl_alert() -> None:
    """Telegram RESUMEN 10MIN — mismo formato que el bot Binance."""
    import re as _re
    state    = _load()
    n = check_resolutions(state)
    if n:
        state = _load()

    pnl     = state["total_pnl"]
    balance = state["balance"]
    won     = state["total_won"]
    total   = state["total_bets"]

    if total == 0:
        return  # Sin apuestas aún — no spamear
    pending = sum(1 for b in state["history"] if b["status"] == "PENDING")
    arrow   = "+" if pnl >= 0 else "-"
    mode    = "SIM" if DRY_RUN else "REAL"

    recent = state["history"][-10:][::-1]
    bet_lines = []
    for b in recent:
        st = b.get("status", "PENDING")
        if st == "WON":
            ic      = "✅"
            pnl_txt = f"+${b['pnl']:.2f}"
        elif st == "LOST":
            ic      = "❌"
            pnl_txt = f"-${abs(b['pnl']):.2f}"
        else:
            ic      = "⏳"
            pnl_txt = "pendiente"
        side_ic = "⬆️" if b.get("side") == "UP" else "⬇️"
        q = b.get("question", "")
        m = _re.search(r',\s*(\d+:\d+[AP]M-\d+:\d+[AP]M\s*ET)', q)
        time_range = m.group(1) if m else ""
        time_str   = f" | <i>{time_range}</i>" if time_range else ""
        bet_lines.append(
            f"{ic} {b.get('asset','?')} {side_ic} @{b.get('price',0):.2f}{time_str} → <b>{pnl_txt}</b>"
        )

    # Slippage stats (SIM: ask vs mid spread measurement)
    slippages = [b["slippage"] for b in state["history"] if b.get("slippage") is not None]
    slip_line = ""
    if slippages:
        avg_slip  = sum(slippages) / len(slippages)
        max_slip  = max(slippages)
        slip_cost = avg_slip * total  # approx total cost in USDC per share
        slip_line = (
            f"\n📉 Spread avg: <b>{avg_slip:+.4f}</b>  max: {max_slip:+.4f}"
            f"  (≈${slip_cost:.2f} acum en {len(slippages)} apuestas)"
        )

    bets_block = "\n".join(bet_lines) if bet_lines else "Sin apuestas aún."
    _tg(
        f"⏰ <b>RESUMEN 10MIN — 5M Bot [{mode}]</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💰 Balance: <b>${balance:.2f}</b>\n"
        f"📊 PnL: <b>{arrow} ${pnl:+.2f}</b>\n"
        f"🎯 Win: {won}/{total}  |  ⏳ Pendientes: {pending}"
        f"{slip_line}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{bets_block}"
    )


# ── State ─────────────────────────────────────────────────────────────────────

def _load() -> dict:
    STATE_FILE.parent.mkdir(exist_ok=True)
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "balance":    INITIAL_BALANCE,
        "initial":    INITIAL_BALANCE,
        "bets_today": 0,
        "loss_today": 0.0,
        "total_bets": 0,
        "total_won":  0,
        "total_pnl":  0.0,
        "last_reset": datetime.now(timezone.utc).date().isoformat(),
        "history":    [],
    }


def _save(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")


def _reset_daily(state: dict) -> dict:
    today = datetime.now(timezone.utc).date().isoformat()
    if state.get("last_reset") != today:
        state["bets_today"] = 0
        state["loss_today"] = 0.0
        state["last_reset"] = today
    return state


def can_bet(state: dict) -> tuple[bool, str]:
    if state["balance"] < BET_SIZE:
        return False, f"Balance insuficiente ${state['balance']:.2f}"
    if state["bets_today"] >= MAX_DAILY_BETS:
        return False, f"Limite diario ({MAX_DAILY_BETS})"
    if state["loss_today"] >= DAILY_STOP_LOSS:
        return False, f"Stop-loss ${state['loss_today']:.2f} >= ${DAILY_STOP_LOSS}"
    return True, ""


def record_bet(state: dict, market: dict, side: str, price: float, edge: float,
               analysis: dict, fill_price: float | None = None,
               slippage: float | None = None, order_id: str | None = None) -> dict:
    bet = {
        "timestamp":  datetime.now(timezone.utc).isoformat(),
        "dry_run":    DRY_RUN,
        "asset":      market["asset"],
        "market_id":  market["market_id"],
        "question":   market["question"],
        "side":       side,
        "bet_size":   BET_SIZE,
        "price":      price,
        "fill_price": round(fill_price, 4) if fill_price is not None else None,
        "slippage":   round(slippage, 4)   if slippage  is not None else None,
        "order_id":   order_id,
        "edge":       round(edge, 4),
        "demand_imbalance": analysis.get("demand_imbalance", 0),
        "bid_up":           analysis.get("bid_up", 0),
        "bid_down":         analysis.get("bid_down", 0),
        "end_date":   market["end_date"].isoformat() if market.get("end_date") else None,
        "status":     "PENDING",
        "pnl":        None,
    }
    state["history"].append(bet)
    state["balance"]    -= BET_SIZE
    state["bets_today"] += 1
    state["loss_today"] += BET_SIZE
    state["total_bets"] += 1
    _save(state)
    return bet


# ── Resolution ────────────────────────────────────────────────────────────────

def _parse_outcome_prices(raw) -> list:
    if isinstance(raw, list):
        return raw
    try:
        import json as _j
        return _j.loads(raw)
    except Exception:
        return []


def _fetch_market_outcome(asset: str, end_date_str: str):
    import requests as _req
    GAMMA_API = "https://gamma-api.polymarket.com"
    try:
        end_dt = datetime.fromisoformat(end_date_str)
        if end_dt.tzinfo is None:
            end_dt = end_dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None
    window_start_ts = int((end_dt - timedelta(minutes=5)).timestamp())
    slug = f"{asset.lower()}-updown-5m-{window_start_ts}"
    try:
        resp = _req.get(f"{GAMMA_API}/events", params={"slug": slug}, timeout=4)
        if resp.status_code != 200:
            return None
        data = resp.json()
        event = data[0] if isinstance(data, list) and data else {}
    except Exception:
        return None
    for mkt in event.get("markets", []):
        ops = _parse_outcome_prices(mkt.get("outcomePrices", []))
        if len(ops) >= 2:
            try:
                p0, p1 = float(ops[0]), float(ops[1])
                if p0 >= 0.99 and p1 <= 0.01:
                    return "UP"
                if p0 <= 0.01 and p1 >= 0.99:
                    return "DOWN"
            except (ValueError, TypeError):
                pass
    return None


def check_resolutions(state: dict) -> int:
    from concurrent.futures import ThreadPoolExecutor, as_completed
    now = datetime.now(timezone.utc)

    pending = []
    for bet in state["history"]:
        if bet["status"] != "PENDING":
            continue
        end_str = bet.get("end_date")
        if not end_str:
            continue
        try:
            end_dt = datetime.fromisoformat(end_str)
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if (now - end_dt).total_seconds() < 5:   # 5s buffer (was 30s)
            continue
        pending.append((bet, end_str))

    if not pending:
        return 0

    # Fetch outcomes in parallel
    def _fetch(item):
        bet, end_str = item
        return bet, _fetch_market_outcome(bet["asset"], end_str)

    resolved = 0
    with ThreadPoolExecutor(max_workers=min(len(pending), 6)) as ex:
        futures = {ex.submit(_fetch, item): item for item in pending}
        for fut in as_completed(futures, timeout=10):
            try:
                bet, outcome = fut.result()
            except Exception:
                continue
            if outcome is None:
                continue
            won = (outcome == "UP"   and bet["side"] == "UP") or \
                  (outcome == "DOWN" and bet["side"] == "DOWN")
            bet_size = bet.get("bet_size", BET_SIZE)
            if won:
                payout = bet_size / bet["price"]
                pnl    = payout - bet_size
                state["balance"]   += payout
                state["loss_today"] = max(0.0, state["loss_today"] - bet_size)
                bet["status"] = "WON"
                bet["pnl"]    = round(pnl, 4)
                state["total_won"] += 1
                state["total_pnl"] = round(state["total_pnl"] + pnl, 4)
                console.print(f"  [green]POLY WON[/green]  [{BOT_NAME}] {bet['asset']} {bet['side']} +${pnl:.2f}")
                _notify_resolved(bet, pnl)
            else:
                pnl = -bet_size
                bet["status"] = "LOST"
                bet["pnl"]    = round(pnl, 4)
                state["total_pnl"] = round(state["total_pnl"] + pnl, 4)
                console.print(f"  [red]POLY LOST[/red] [{BOT_NAME}] {bet['asset']} {bet['side']} -${bet_size:.2f}")
                _notify_resolved(bet, pnl)
            resolved += 1
            _save(state)
    return resolved


# ── Display ───────────────────────────────────────────────────────────────────

def print_banner():
    mode = "[yellow]SIM (DRY RUN)[/yellow]" if DRY_RUN else "[bold red]LIVE[/bold red]"
    console.rule(f"[bold magenta]POLY 5M BOT[/bold magenta] — {mode} — [dim]Solo Polymarket CLOB[/dim]")
    console.print(
        f"  Assets: {' - '.join(ASSETS)}  |  "
        f"Bet: ${BET_SIZE}  Min edge: {MIN_EDGE:.0%}  "
        f"Min book: ${MIN_BOOK_DEPTH:.0f}  Balance inicial: ${INITIAL_BALANCE}"
    )
    console.print()


def print_dashboard(state: dict):
    total = state["total_bets"]
    won   = state["total_won"]
    pnl   = state["total_pnl"]
    pnl_c = "green" if pnl >= 0 else "red"
    mode_c = "yellow" if DRY_RUN else "red"
    console.print(
        f"  [{mode_c}]{'SIM' if DRY_RUN else 'LIVE'}[/{mode_c}]  "
        f"Balance: [bold]${state['balance']:.2f}[/bold]  "
        f"PnL: [{pnl_c}]${pnl:+.2f}[/{pnl_c}]  "
        f"Hoy: {state['bets_today']}/{MAX_DAILY_BETS}  "
        f"Win: {(won/total*100):.0f}% ({won}/{total})" if total > 0 else
        f"  [{mode_c}]{'SIM' if DRY_RUN else 'LIVE'}[/{mode_c}]  "
        f"Balance: [bold]${state['balance']:.2f}[/bold]  Sin apuestas aun"
    )


def print_signals(signals: list):
    t = Table(box=box.SIMPLE, show_header=True, header_style="bold dim")
    t.add_column("Asset",     style="bold", width=6)
    t.add_column("Price UP",  width=8)
    t.add_column("Price DN",  width=8)
    t.add_column("Bid UP$",   width=9)
    t.add_column("Bid DN$",   width=9)
    t.add_column("DemImb",    width=9)
    t.add_column("Edge UP",   width=8)
    t.add_column("Edge DN",   width=8)
    t.add_column("BET",       width=8)

    for s in signals:
        a   = s["analysis"]
        bet = s["best_bet"]
        mkt = s["market"]

        if not mkt:
            t.add_row(s["asset"], "-", "-", "-", "-", "-", "-", "-", "[dim]no mkt[/dim]")
            continue

        bid_d = f"${a.get('bid_up', 0):,.0f}"
        ask_d = f"${a.get('bid_down', 0):,.0f}"
        bk    = a.get("demand_imbalance", 0)
        bk_s  = f"[green]+{bk:.0%}[/green]" if bk > 0.05 else (f"[red]{bk:.0%}[/red]" if bk < -0.05 else f"{bk:.0%}")
        eu    = a.get("edge_up", 0)
        ed    = a.get("edge_down", 0)
        eu_s  = f"[green]+{eu:.0%}[/green]" if eu >= MIN_EDGE else f"[dim]{eu:.0%}[/dim]"
        ed_s  = f"[green]+{ed:.0%}[/green]" if ed >= MIN_EDGE else f"[dim]{ed:.0%}[/dim]"
        bet_s = f"[bold green]{bet[0]}[/bold green]" if bet else "[dim]-[/dim]"

        t.add_row(
            s["asset"],
            f"{mkt['price_up']:.3f}",
            f"{mkt['price_down']:.3f}",
            bid_d,
            ask_d,
            bk_s,
            eu_s,
            ed_s,
            bet_s,
        )
    console.print(t)


# ── Main cycle ────────────────────────────────────────────────────────────────

def run_cycle():
    ts_str = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    console.rule(f"[dim]POLY Ciclo 5M — {ts_str}[/dim]")

    state = _load()
    state = _reset_daily(state)

    check_resolutions(state)
    print_dashboard(state)

    ok, reason = can_bet(state)
    if not ok:
        console.print(f"[red]STOP:[/red] {reason}")
        last_stop = state.get("_last_stop_reason", "")
        if reason != last_stop:
            _tg(f"🛑 <b>POLY BOT PAUSADO [{BOT_NAME}]</b>\n━━━━━━━━━━━━━━━━━━\nMotivo: {reason}")
            state["_last_stop_reason"] = reason
        _save(state)
        return
    if state.get("_last_stop_reason"):
        state["_last_stop_reason"] = ""

    # Fetch active markets
    markets = fetch_5m_markets()
    market_map = {m["asset"]: m for m in markets}
    pending_ids = {b["market_id"] for b in state["history"] if b["status"] == "PENDING"}

    signals = []
    for asset in ASSETS:
        mkt  = market_map.get(asset)
        best = None

        if mkt:
            if mkt["market_id"] in pending_ids:
                mkt = None
            else:
                # Thin book guard
                a = analyze_market_poly(mkt)
                total_book = a.get("bid_up", 0) + a.get("bid_down", 0)

                imbalance  = a.get("demand_imbalance", 0)
                asset_edge = MIN_EDGE_PER_ASSET.get(asset, MIN_EDGE)

                if total_book < MIN_BOOK_DEPTH:
                    # Book too thin — skip
                    a["edge_up"] = 0
                    a["edge_down"] = 0
                elif (mkt["price_up"] <= MAX_PRICE and a["edge_up"] >= asset_edge
                      and imbalance >= 0.10):
                    # CLOB must confirm UP: >= 10% more bids on UP
                    best = ("UP", a["edge_up"])
                elif (mkt["price_down"] <= MAX_PRICE and a["edge_down"] >= asset_edge
                      and imbalance <= -0.10):
                    # CLOB must confirm DOWN: >= 10% more bids on DOWN
                    best = ("DOWN", a["edge_down"])
        else:
            a = {}

        signals.append({
            "asset":    asset,
            "market":   mkt,
            "analysis": a if mkt else {},
            "best_bet": best,
        })

    signals.sort(key=lambda x: max(
        x["analysis"].get("edge_up", 0), x["analysis"].get("edge_down", 0)
    ), reverse=True)

    print_signals(signals)

    bets_placed = 0
    sides_used: set[str] = set()   # correlation guard: max 1 UP + 1 DOWN per cycle
    for sig in signals:
        if bets_placed >= MAX_PER_CYCLE:
            break
        ok_now, reason = can_bet(state)
        if not ok_now:
            console.print(f"[red]STOP:[/red] {reason}")
            break
        if not sig["best_bet"] or not sig["market"]:
            continue

        mkt  = sig["market"]
        side, edge = sig["best_bet"]

        # Correlation guard: only 1 bet per direction per cycle
        if side in sides_used:
            continue
        sides_used.add(side)

        mid_price = mkt["price_up"] if side == "UP" else mkt["price_down"]
        token_id  = mkt.get("token_id_up") if side == "UP" else mkt.get("token_id_down")
        a         = sig["analysis"]

        # ── Fetch real ask price (spread measurement) ─────────────────────────
        ask_price = get_best_ask(token_id) if token_id else None
        order_id  = None

        # Skip if ask price is above MAX_PRICE — spread too wide, edge would be negative in LIVE
        if ask_price is not None and ask_price > MAX_PRICE:
            console.print(f"  [yellow]SKIP[/yellow] {sig['asset']} {side} — ask {ask_price:.3f} > MAX_PRICE {MAX_PRICE}")
            continue

        if DRY_RUN:
            # SIM: use mid-price for bet, record ask as fill_price to track spread
            price      = mid_price
            fill_price = ask_price
            slippage   = round(ask_price - mid_price, 4) if ask_price is not None else None
        else:
            # LIVE: place real order at ask price (limit order, immediate fill)
            order_price = ask_price if ask_price else mid_price
            order_resp  = _place_real_order(token_id, order_price, BET_SIZE)
            if order_resp is None:
                console.print(f"[red][LIVE] Error colocando orden para {sig['asset']} {side} — skipping[/red]")
                continue
            price      = order_price
            fill_price = order_price
            slippage   = round(order_price - mid_price, 4)
            order_id   = str(order_resp.get("orderID") or order_resp.get("id") or "")

        bet  = record_bet(state, mkt, side, price, edge, a,
                          fill_price=fill_price, slippage=slippage, order_id=order_id)
        gain = BET_SIZE / price

        slip_str = f"  Slip: {slippage:+.4f}" if slippage is not None else ""
        fill_str = f"  Fill: {fill_price:.3f}" if fill_price is not None else ""
        mode_tag = "[yellow]SIM[/yellow]" if DRY_RUN else "[bold green]REAL[/bold green]"
        console.print(
            f"\n  {mode_tag} [POLY/{BOT_NAME}] [{sig['asset']}] >> [bold]{side}[/bold]  "
            f"Mid: {mid_price:.3f}{fill_str}{slip_str}  Edge: {edge:+.1%}  "
            f"Book: ${a.get('bid_up',0):,.0f}↑/${a.get('bid_down',0):,.0f}↓  "
            f"Ganancia pot.: ${gain:.2f}\n"
            f"  {mkt['question'][:70]}"
        )
        prob_win = a.get("prob_up", 0.5) if side == "UP" else a.get("prob_down", 0.5)
        _notify_bet(
            mkt["market_id"], sig["asset"], side, price, edge,
            prob_win, mkt.get("question", ""),
            a.get("bid_up", 0), a.get("bid_down", 0),
        )
        bets_placed += 1

    if bets_placed == 0:
        console.print("[dim]Sin senales con edge suficiente (POLY)[/dim]")
    console.print()


def _resolve_job():
    state = _load()
    n = check_resolutions(state)
    if n:
        _save(state)


def main():
    # Single-instance lock (Windows)
    if sys.platform == "win32":
        import msvcrt
        _lock_path = Path("logs/poly5m_bot.lock")
        _lock_path.parent.mkdir(exist_ok=True)
        try:
            _lock_fh = open(_lock_path, "w")
            msvcrt.locking(_lock_fh.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            console.print("[red]POLY BOT ya está corriendo (lock activo). Saliendo.[/red]")
            sys.exit(1)

    print_banner()
    run_cycle()

    sched = BlockingScheduler(timezone="UTC")
    sched.add_job(
        run_cycle,
        trigger="cron",
        minute="0,5,10,15,20,25,30,35,40,45,50,55",
        second=10,
    )
    sched.add_job(
        _resolve_job,
        trigger="interval",
        seconds=30,
    )
    sched.add_job(
        pnl_alert,
        trigger="cron",
        minute="0,10,20,30,40,50",
        second=30,
    )
    console.print(
        f"[dim]POLY Bot [{BOT_NAME}] activo — ciclo cada 5 min (:10s) | resolución cada 30s | resumen cada 10 min[/dim]"
    )
    sched.start()


if __name__ == "__main__":
    main()
