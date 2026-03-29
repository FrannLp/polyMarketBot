"""
cryp_signal_5minutes.py
========================
5-minute UP/DOWN bot for BTC, ETH, SOL, XRP, DOGE and HYPE on Polymarket.

Cycle runs every 5 minutes, 30 seconds after each window opens:
  :00:30, :05:30, :10:30, :15:30 … past each hour

Usage:
    python cryp_signal_5minutes.py

Config (via .env):
    CRYP5M_BET_SIZE          default 1.00
    CRYP5M_INITIAL_BALANCE   default 50.00
    CRYP5M_MIN_EDGE          default 0.04   (4%)
    CRYP5M_MAX_DAILY_BETS    default 100
    CRYP5M_DAILY_STOP_LOSS   default 15.00
    CRYP5M_MAX_BETS_PER_CYCLE default 2     (how many assets to bet per 5-min cycle)
    DRY_RUN                  default true
"""

import io
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

# ── Windows UTF-8 fix — previene crash con caracteres especiales (≥ → ▲ ▼ etc.) ──
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ── Auto-install dependencies ─────────────────────────────────────────────────
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
from cryp_5m_analyzer import analyze_asset_5m
from telegram_notifier import notify_stop, notify_sin_senales

# ── Polymarket CLOB client (solo en LIVE mode) ────────────────────────────────
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
        print(f"[CLOB] Error inicializando cliente: {e}")
    return _clob_client


def place_real_order(token_id: str, price: float, bet_size: float, side: str) -> dict | None:
    """
    Ejecuta una orden REAL en Polymarket CLOB.
    side: "UP" o "DOWN"
    Retorna la respuesta del CLOB o None si falla.
    """
    try:
        from py_clob_client.clob_types import OrderArgs, PartialCreateOrderOptions
        from py_clob_client.order_builder.constants import BUY

        client = _get_clob_client()
        if not client:
            raise RuntimeError("CLOB client no disponible")

        # Calcular shares: bet_size USDC / precio por share
        # Mínimo 5 shares requerido por el CLOB de Polymarket
        MIN_SHARES = 5.0
        shares = max(MIN_SHARES, round(bet_size / price, 2))

        # Los mercados 5-min usan tick_size=0.01 y neg_risk=False
        options = PartialCreateOrderOptions(tick_size="0.01", neg_risk=False)

        resp = client.create_and_post_order(
            OrderArgs(
                token_id=token_id,
                price=price,
                size=shares,
                side=BUY,
            ),
            options=options,
        )
        return resp
    except Exception as e:
        print(f"[CLOB] Error colocando orden: {e}")
        return None

# ── Config ────────────────────────────────────────────────────────────────────
DRY_RUN          = os.getenv("DRY_RUN",                  "true").lower() == "true"
# Nombre único por instancia — aparece en todas las alertas de Telegram
# Configura en .env: BOT_NAME=SIM o BOT_NAME=PROD
BOT_NAME         = os.getenv("BOT_NAME", "SIM" if os.getenv("DRY_RUN", "true").lower() == "true" else "PROD")
BET_SIZE         = float(os.getenv("CRYP5M_BET_SIZE",         "1.00"))
INITIAL_BALANCE  = float(os.getenv("CRYP5M_INITIAL_BALANCE",  "50.00"))
MIN_EDGE         = float(os.getenv("CRYP5M_MIN_EDGE",          "0.04"))
MAX_DAILY_BETS   = int(os.getenv("CRYP5M_MAX_DAILY_BETS",     "100"))
DAILY_STOP_LOSS  = float(os.getenv("CRYP5M_DAILY_STOP_LOSS",  "15.00"))
MAX_PER_CYCLE    = int(os.getenv("CRYP5M_MAX_BETS_PER_CYCLE",  "2"))
# EV protection: token price above this threshold means negative expected value
# Research: at $0.60 entry EV is already -$0.027; higher prices are worse.
MAX_PRICE        = float(os.getenv("CRYP5M_MAX_PRICE",         "0.60"))
# Only bet when there is a real directional signal (avoid flat/lateral markets)
MIN_MOMENTUM     = float(os.getenv("CRYP5M_MIN_MOMENTUM",      "0.05"))  # 0.05%
# Edge cap: very high edge often means our model is wrong, not that the market is.
# Data: 15% edge bet lost; 6-10% edge bets won 100%. Cap avoids overconfident signals.
MAX_EDGE         = float(os.getenv("CRYP5M_MAX_EDGE",           "0.12"))  # 12%
# Correlation guard: max 1 bet per direction per active time window.
# Betting DOWN on both SOL and ETH in the same window = double exposure to one move.
MAX_BETS_PER_DIRECTION_PER_WINDOW = int(os.getenv("CRYP5M_MAX_SAME_DIR", "1"))

# ── Per-asset signal requirements (derived from backtest analysis) ─────────────
# Backtest results (24h, MIN_EDGE=4%):
#   BTC: MACD cross WR=59.6%  CVD div WR=34.6%  → require MACD cross
#   XRP: MACD cross WR=54.4%  CVD div WR=65.6%  → require CVD divergence
#   ETH: MACD cross WR=46.9%  CVD div WR=26.3%  → no reliable signal, skip
#   SOL: MACD cross WR=46.5%  CVD div WR=45.0%  → no reliable signal, skip
#
# "macd_cross" = bet only when MACD(3/15/3) just crossed (strongest directional signal)
# "cvd_div"    = bet only when price/CVD divergence detected (smart money signal)
# None         = no reliable signal found in backtest — asset is paused
#
# Override via env: CRYP5M_SIGNAL_BTC=macd_cross  CRYP5M_SIGNAL_ETH=none
SIGNAL_RULES: dict[str, str | None] = {
    # Backtest results: BTC 59.6% WR with MACD cross, XRP 65.6% WR with CVD div
    # ETH 46.9%/26.3% — sin señal confiable, requiere edge alto.
    # SOL 46.5%/45.0% — sin señal confiable, requiere edge alto.
    # DOGE/HYPE: sin backtest aun — recolectando datos con "any".
    "BTC":  os.getenv("CRYP5M_SIGNAL_BTC",  "macd_cross"),
    "ETH":  os.getenv("CRYP5M_SIGNAL_ETH",  "any"),
    "SOL":  os.getenv("CRYP5M_SIGNAL_SOL",  "any"),
    "XRP":  os.getenv("CRYP5M_SIGNAL_XRP",  "cvd_div"),
    "DOGE": os.getenv("CRYP5M_SIGNAL_DOGE", "any"),
    "HYPE": os.getenv("CRYP5M_SIGNAL_HYPE", "any"),
}

# Edge mínimo por asset — activos con señal débil requieren más edge para apostar
# BTC/XRP tienen señal probada → MIN_EDGE global. ETH/SOL/DOGE/HYPE → 12% mínimo.
MIN_EDGE_PER_ASSET: dict[str, float] = {
    "BTC":  float(os.getenv("CRYP5M_MIN_EDGE_BTC",  str(MIN_EDGE))),
    "ETH":  float(os.getenv("CRYP5M_MIN_EDGE_ETH",  "0.12")),
    "SOL":  float(os.getenv("CRYP5M_MIN_EDGE_SOL",  "0.12")),
    "XRP":  float(os.getenv("CRYP5M_MIN_EDGE_XRP",  str(MIN_EDGE))),
    "DOGE": float(os.getenv("CRYP5M_MIN_EDGE_DOGE", "0.12")),
    "HYPE": float(os.getenv("CRYP5M_MIN_EDGE_HYPE", "0.12")),
}

STATE_FILE = Path("logs/cryp5m_state.json")
console    = Console()

# ── State helpers ─────────────────────────────────────────────────────────────

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
        return False, f"Balance insuficiente ${state['balance']:.2f} (mín ${BET_SIZE})"
    if state["bets_today"] >= MAX_DAILY_BETS:
        return False, f"Límite diario alcanzado ({MAX_DAILY_BETS} apuestas)"
    if state["loss_today"] >= DAILY_STOP_LOSS:
        return False, f"Stop-loss diario ${state['loss_today']:.2f} >= ${DAILY_STOP_LOSS}"
    return True, ""


def record_bet(state: dict, market: dict, side: str, price: float, edge: float,
               prob_win: float) -> dict:
    bet = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "dry_run":   DRY_RUN,
        "asset":     market["asset"],
        "market_id": market["market_id"],
        "question":  market["question"],
        "side":      side,           # "UP" or "DOWN"
        "bet_size":  BET_SIZE,
        "price":     round(price, 4),
        "edge":      round(edge, 4),
        "prob_win":  round(prob_win, 4),
        "end_date":  market["end_date"].isoformat() if market.get("end_date") else None,
        "status":    "PENDING",
        "pnl":       None,
    }
    state["balance"]    -= BET_SIZE
    state["bets_today"] += 1
    state["total_bets"] += 1
    state["history"].append(bet)
    _save(state)
    return bet


# ── Resolution checker ────────────────────────────────────────────────────────

def _notify_resolved(bet: dict, pnl: float) -> None:
    """Send Telegram notification when a bet resolves."""
    try:
        from telegram_notifier import _send
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
        _send(msg)
    except Exception:
        pass


def _parse_outcome_prices(raw) -> list:
    """Parse outcomePrices which may arrive as a JSON string or a list."""
    import json as _json
    if isinstance(raw, str):
        try:
            raw = _json.loads(raw)
        except Exception:
            return []
    return raw if isinstance(raw, list) else []


def _fetch_market_outcome(asset: str, end_date_str: str) -> str | None:
    """
    Fetch the resolved outcome ('UP' or 'DOWN') for a 5-minute market by
    reconstructing its event slug from asset + end_date.
    Returns 'UP', 'DOWN', or None if not yet resolved.
    """
    import requests as _req
    GAMMA_API = "https://gamma-api.polymarket.com"

    try:
        end_dt = datetime.fromisoformat(end_date_str)
        if end_dt.tzinfo is None:
            end_dt = end_dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None

    from datetime import timedelta
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
        # Fast path: check resolved flag directly
        if mkt.get("closed") or mkt.get("resolved"):
            winner = mkt.get("winner") or mkt.get("winningOutcome") or ""
            if winner in ("UP", "Yes", "up"):
                return "UP"
            if winner in ("DOWN", "No", "down"):
                return "DOWN"

        ops = _parse_outcome_prices(mkt.get("outcomePrices", []))
        if len(ops) >= 2:
            try:
                p0, p1 = float(ops[0]), float(ops[1])
                # Use >= 0.99 / <= 0.01 instead of exact == to handle float precision
                if p0 >= 0.99 and p1 <= 0.01:
                    return "UP"
                if p0 <= 0.01 and p1 >= 0.99:
                    return "DOWN"
            except (ValueError, TypeError):
                pass
    return None


def check_resolutions(state: dict) -> int:
    """
    Query Polymarket for PENDING bets that are past their end_date.
    Uses the events/slug endpoint (outcomePrices: ["1","0"] or ["0","1"]).
    Fetches all pending bets in parallel for speed.
    Returns number of resolved bets.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    now = datetime.now(timezone.utc)

    # Collect bets eligible for resolution
    eligible = []
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
        # Only resolve after window has closed (+ 5s buffer for Polymarket to publish)
        if (now - end_dt).total_seconds() < 5:
            continue
        eligible.append(bet)

    if not eligible:
        return 0

    # Fetch outcomes in parallel
    outcomes: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=len(eligible)) as ex:
        future_to_bet = {
            ex.submit(_fetch_market_outcome, bet["asset"], bet["end_date"]): bet
            for bet in eligible
        }
        for future in as_completed(future_to_bet):
            bet = future_to_bet[future]
            result = future.result()
            if result is not None:
                outcomes[id(bet)] = result

    resolved = 0
    for bet in eligible:
        market_outcome = outcomes.get(id(bet))
        if market_outcome is None:
            continue

        won = (market_outcome == "UP" and bet["side"] == "UP") or \
              (market_outcome == "DOWN" and bet["side"] == "DOWN")

        bet_size = bet.get("bet_size", BET_SIZE)
        if won:
            payout = bet_size / bet["price"]
            pnl    = round(payout - bet_size, 2)
            state["balance"]   = round(state["balance"] + payout, 2)
            state["total_won"] += 1
            bet["status"] = "WON"
        else:
            pnl   = -bet_size
            state["loss_today"] = round(state["loss_today"] + bet_size, 2)
            bet["status"] = "LOST"

        bet["pnl"]         = pnl
        state["total_pnl"] = round(state["total_pnl"] + pnl, 2)
        _notify_resolved(bet, pnl)
        resolved += 1

    if resolved > 0:
        _save(state)
    return resolved


def resolve_job() -> None:
    """Dedicated resolution job — runs every minute to catch settled markets fast."""
    state    = _load()
    resolved = check_resolutions(state)
    if resolved:
        state = _load()
        console.print(f"[dim]resolve_job: {resolved} apuesta(s) resuelta(s)[/dim]")


# ── Display ───────────────────────────────────────────────────────────────────

def print_banner() -> None:
    mode = "[yellow]SIM (DRY RUN)[/yellow]" if DRY_RUN else "[bold red]LIVE[/bold red]"
    console.rule(f"[bold cyan]CRYPTO 5M BOT[/bold cyan] — {mode}")
    console.print(
        f"  Assets: BTC · ETH · SOL · XRP · DOGE · HYPE  |  "
        f"Bet: ${BET_SIZE}  Min edge: {MIN_EDGE:.0%}  "
        f"Balance inicial: ${INITIAL_BALANCE}  |  Ciclo: cada 5 minutos"
    )


def print_dashboard(state: dict) -> None:
    total    = state["total_bets"]
    won      = state["total_won"]
    win_rate = (won / total * 100) if total > 0 else 0.0
    pnl      = state["total_pnl"]
    pnl_c    = "green" if pnl >= 0 else "red"
    mode_c   = "yellow" if DRY_RUN else "red"
    console.print(
        f"  [{mode_c}]{'SIM' if DRY_RUN else 'LIVE'}[/{mode_c}]  "
        f"Balance: [bold]${state['balance']:.2f}[/bold]  "
        f"PnL: [{pnl_c}]${pnl:+.2f}[/{pnl_c}]  "
        f"Hoy: {state['bets_today']}/{MAX_DAILY_BETS}  "
        f"Stop-loss: ${state['loss_today']:.2f}/${DAILY_STOP_LOSS}  "
        f"Win: {win_rate:.0f}% ({won}/{total})"
    )


def print_signals(signals: list[dict]) -> None:
    """Print analysis table for all 4 assets."""
    t = Table(title=f"ANALISIS 5M  (MAX_PRICE={MAX_PRICE}  MIN_MOM={MIN_MOMENTUM}%)",
              box=box.SIMPLE, header_style="bold cyan")
    t.add_column("Asset",   width=5)
    t.add_column("Trigger", justify="center", width=12)  # required signal status
    t.add_column("Precio",  justify="right",  width=10)
    t.add_column("RSI",     justify="right",  width=5)
    t.add_column("MACD",    justify="center", width=7)
    t.add_column("VWAP",    justify="center", width=6)
    t.add_column("CVD",     justify="center", width=7)
    t.add_column("WinMom",  justify="right",  width=9)
    t.add_column("Mom 5m",  justify="right",  width=8)
    t.add_column("P(UP)",   justify="right",  width=7)
    t.add_column("Mkt UP",  justify="right",  width=7)
    t.add_column("Edge UP", justify="right",  width=8)
    t.add_column("EdgeDWN", justify="right",  width=8)
    t.add_column("BET",     justify="center", width=10)

    for s in signals:
        a   = s["analysis"] or {}
        mkt = s.get("market")

        # RSI
        rsi_val = a.get("rsi")
        rsi_s   = f"{rsi_val:.0f}" if rsi_val else "—"
        if rsi_val:
            if rsi_val >= 70:   rsi_s = f"[red]{rsi_s}[/red]"
            elif rsi_val <= 30: rsi_s = f"[green]{rsi_s}[/green]"

        # MACD — crossover shown prominently
        macd_cross = a.get("macd_cross")
        macd_hist  = a.get("macd_hist")
        if macd_cross == "BULL":
            macd_s = "[bold green]XBULL[/bold green]"
        elif macd_cross == "BEAR":
            macd_s = "[bold red]XBEAR[/bold red]"
        elif macd_hist is not None:
            mc     = "green" if macd_hist > 0 else "red"
            macd_s = f"[{mc}]{macd_hist:+.4f}[/{mc}]"
        else:
            macd_s = "[dim]—[/dim]"

        # VWAP position
        vwap_pos = a.get("vwap_pos", "AT")
        if   vwap_pos == "ABOVE": vwap_s = "[green]ABOVE[/green]"
        elif vwap_pos == "BELOW": vwap_s = "[red]BELOW[/red]"
        else:                     vwap_s = "[dim]AT[/dim]"

        # CVD divergence
        cvd_div = a.get("cvd_divergence")
        cvd_l5  = a.get("cvd_last5", 0)
        if   cvd_div == "BULL":  cvd_s = "[bold green]DIV-B[/bold green]"
        elif cvd_div == "BEAR":  cvd_s = "[bold red]DIV-S[/bold red]"
        elif cvd_l5 > 0:         cvd_s = "[green]+buy[/green]"
        elif cvd_l5 < 0:         cvd_s = "[red]-sell[/red]"
        else:                    cvd_s = "[dim]—[/dim]"

        # Within-window momentum
        win_mom   = a.get("window_momentum", 0.0)
        wm_c      = "green" if win_mom > 0 else ("red" if win_mom < 0 else "dim")
        wm_flag   = " *" if abs(win_mom) >= MIN_MOMENTUM else ""
        win_mom_s = f"[{wm_c}]{win_mom:+.3f}%{wm_flag}[/{wm_c}]"

        mom5    = a.get("momentum_5m", 0.0)
        mom5_c  = "green" if mom5 > 0 else "red"
        mom5_s  = f"[{mom5_c}]{mom5:+.3f}%[/{mom5_c}]"

        price_s = f"${a['current_price']:,.2f}" if a.get("current_price") else "—"

        if mkt:
            up_price  = mkt["price_up"]
            dn_price  = mkt["price_down"]
            up_c      = "red" if up_price > MAX_PRICE else "white"
            dn_c      = "red" if dn_price > MAX_PRICE else "white"
            mkt_up_s  = f"[{up_c}]{up_price:.2f}[/{up_c}]"
            mkt_dn_s  = f"[{dn_c}]{dn_price:.2f}[/{dn_c}]"
            edge_up   = s.get("edge_up", 0)
            edge_dn   = s.get("edge_down", 0)
            eu_c      = "green" if edge_up >= MIN_EDGE else ("yellow" if edge_up > 0 else "red")
            ed_c      = "green" if edge_dn >= MIN_EDGE else ("yellow" if edge_dn > 0 else "red")
            edge_up_s = f"[{eu_c}]{edge_up:+.1%}[/{eu_c}]"
            edge_dn_s = f"[{ed_c}]{edge_dn:+.1%}[/{ed_c}]"
        else:
            mkt_up_s = mkt_dn_s = edge_up_s = edge_dn_s = "[dim]—[/dim]"

        # Trigger status column
        required = s.get("required")
        sig_ok   = s.get("signal_active", True)
        reason   = s.get("signal_reason", "")
        if required is None:
            trigger_s = "[dim]PAUSED[/dim]"
        elif not sig_ok:
            short = reason.replace("waiting ", "").replace(" cross", "").upper()[:8]
            trigger_s = f"[yellow]wait {short}[/yellow]"
        else:
            trigger_s = f"[bold green]{str(required).upper()[:8]} OK[/bold green]"

        bet_rec = s.get("best_bet")
        rec_s   = f"[green]{bet_rec[0]} {bet_rec[1]:+.1%}[/green]" if bet_rec else "[dim]—[/dim]"

        t.add_row(
            s["asset"], trigger_s, price_s, rsi_s,
            macd_s, vwap_s, cvd_s,
            win_mom_s, mom5_s,
            f"{a.get('prob_up', 0.5):.1%}",
            mkt_up_s,
            edge_up_s, edge_dn_s, rec_s,
        )

    console.print(t)


def print_history(state: dict, n: int = 12) -> None:
    history = state["history"][-n:]
    if not history:
        return

    now = datetime.now(timezone.utc)
    t   = Table(title="HISTORIAL BTC/ETH/SOL/XRP/DOGE/HYPE 5M", box=box.SIMPLE, header_style="bold")
    t.add_column("Hora",    no_wrap=True, width=16)
    t.add_column("Asset",   width=4)
    t.add_column("Lado",    justify="center", width=5)
    t.add_column("Precio",  justify="right",  width=6)
    t.add_column("Edge",    justify="right",  width=7)
    t.add_column("Tiempo",  justify="right",  width=9)
    t.add_column("Estado",  justify="center", width=7)
    t.add_column("PnL",     justify="right",  width=8)

    status_map = {
        "PENDING": "[yellow]PEND[/yellow]",
        "WON":     "[green]WON[/green]",
        "LOST":    "[red]LOST[/red]",
    }

    for b in reversed(history):
        # Convert UTC timestamp to local system time for display
        try:
            ts_utc = datetime.fromisoformat(b["timestamp"])
            if ts_utc.tzinfo is None:
                ts_utc = ts_utc.replace(tzinfo=timezone.utc)
            ts = ts_utc.astimezone().strftime("%Y-%m-%d %H:%M")
        except Exception:
            ts = b["timestamp"][:16].replace("T", " ")
        side_c  = "green" if b["side"] == "UP" else "red"
        side_s  = f"[{side_c}]{b['side']}[/{side_c}]"
        edge_s  = f"{b['edge']*100:.1f}%"

        time_s = "?"
        end_str = b.get("end_date")
        if end_str:
            try:
                end_dt = datetime.fromisoformat(end_str)
                if end_dt.tzinfo is None:
                    end_dt = end_dt.replace(tzinfo=timezone.utc)
                secs = int((end_dt - now).total_seconds())
                time_s = f"{secs//60}m {secs%60}s" if secs > 0 else "[dim]cerrado[/dim]"
            except Exception:
                pass

        pnl_s = ""
        if b["pnl"] is not None:
            c     = "green" if b["pnl"] >= 0 else "red"
            pnl_s = f"[{c}]${b['pnl']:+.2f}[/{c}]"

        t.add_row(
            ts, b["asset"], side_s,
            f"{b['price']:.2f}", edge_s, time_s,
            status_map.get(b["status"], b["status"]), pnl_s,
        )

    console.print(t)


# ── Telegram helpers ──────────────────────────────────────────────────────────

_NOTIFIED_BETS_FILE = Path("logs/notified_bets.json")

def _load_notified() -> set:
    """Load set of already-notified market_ids to prevent duplicate alerts."""
    try:
        if _NOTIFIED_BETS_FILE.exists():
            return set(json.load(_NOTIFIED_BETS_FILE.open()))
    except Exception:
        pass
    return set()

def _save_notified(notified: set) -> None:
    try:
        _NOTIFIED_BETS_FILE.parent.mkdir(exist_ok=True)
        json.dump(list(notified)[-200:], _NOTIFIED_BETS_FILE.open("w"))  # keep last 200
    except Exception:
        pass

def _notify_bet(asset: str, side: str, price: float, edge: float,
                prob_win: float, question: str, market_id: str = "") -> None:
    """Send a Telegram notification for a new bet. Deduplicates by market_id."""
    # Dedup: skip if this market+side+bot already notified
    dedup_key = f"{BOT_NAME}:{market_id}:{side}"
    notified = _load_notified()
    if dedup_key in notified:
        return
    notified.add(dedup_key)
    _save_notified(notified)

    from telegram_notifier import _send
    mode    = f"🟡 SIM [{BOT_NAME}]" if DRY_RUN else f"🟢 REAL [{BOT_NAME}]"
    side_ic = "⬆️" if side == "UP" else "⬇️"
    gain    = round(BET_SIZE / price, 2)
    msg = (
        f"{mode} — <b>5M ENTRADA</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📊 Asset: <b>{asset}</b>\n"
        f"📌 {question[:60]}\n"
        f"{side_ic} Lado: <b>{side}</b>\n"
        f"💰 Monto: <b>${BET_SIZE:.2f}</b>\n"
        f"📈 Precio: {price:.2f}  |  Edge: {edge*100:.1f}%\n"
        f"🎯 Ganancia potencial: <b>${gain:.2f}</b>\n"
        f"🔮 Prob win: {prob_win:.1%}"
    )
    try:
        _send(msg)
    except Exception:
        pass


# ── PnL alert (every 10 minutes) ─────────────────────────────────────────────

def pnl_alert() -> None:
    """Print a PnL summary and send Telegram alert every 10 minutes."""
    state    = _load()
    resolved = check_resolutions(state)
    if resolved:
        console.print(f"[dim]OK {resolved} apuesta(s) resuelta(s)[/dim]")
        state = _load()

    pnl     = state["total_pnl"]
    balance = state["balance"]
    won     = state["total_won"]
    total   = state["total_bets"]

    if total == 0:
        return  # Sin apuestas aún — no spamear

    pending = sum(1 for b in state["history"] if b["status"] == "PENDING")
    pnl_c   = "green" if pnl >= 0 else "red"
    arrow   = "+" if pnl >= 0 else "-"

    console.print(
        f"\n  [bold cyan]RESUMEN PnL (10MIN)[/bold cyan]  "
        f"Balance: [bold]${balance:.2f}[/bold]  "
        f"PnL: [{pnl_c}]{arrow} ${pnl:+.2f}[/{pnl_c}]  "
        f"Pendientes: {pending}  Win: {won}/{total}\n"
    )

    try:
        from telegram_notifier import _send
        mode = "SIM" if DRY_RUN else "REAL"

        # Build per-bet lines (most recent first, up to 10)
        recent = state["history"][-10:][::-1]
        bet_lines = []
        for b in recent:
            st = b.get("status", "PENDING")
            if st == "WON":
                ic = "✅"
                pnl_txt = f"+${b['pnl']:.2f}"
            elif st == "LOST":
                ic = "❌"
                pnl_txt = f"-${abs(b['pnl']):.2f}"
            else:
                ic = "⏳"
                pnl_txt = "pendiente"
            side_ic = "⬆️" if b.get("side") == "UP" else "⬇️"
            # Extract time range from question e.g. "XRP Up or Down - March 26, 12:55AM-1:00AM ET"
            import re as _re
            q = b.get("question", "")
            m = _re.search(r',\s*(\d+:\d+[AP]M-\d+:\d+[AP]M\s*ET)', q)
            time_range = m.group(1) if m else ""
            time_str = f" | <i>{time_range}</i>" if time_range else ""
            bet_lines.append(
                f"{ic} {b.get('asset','?')} {side_ic} @{b.get('price',0):.2f}{time_str} → <b>{pnl_txt}</b>"
            )

        bets_block = "\n".join(bet_lines) if bet_lines else "Sin apuestas aún."

        msg = (
            f"⏰ <b>RESUMEN 10MIN — 5M Bot [{mode}]</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"💰 Balance: <b>${balance:.2f}</b>\n"
            f"📊 PnL: <b>{arrow} ${pnl:+.2f}</b>\n"
            f"🎯 Win: {won}/{total}  |  ⏳ Pendientes: {pending}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"{bets_block}"
        )
        _send(msg)
    except Exception:
        pass


# ── Main cycle ────────────────────────────────────────────────────────────────

def run_cycle() -> None:
    ts_str = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    console.rule(f"[dim]Ciclo 5M — {ts_str}[/dim]")

    state = _load()
    state = _reset_daily(state)

    # 1. Resolve pending bets
    resolved = check_resolutions(state)
    if resolved:
        console.print(f"[dim]OK {resolved} apuesta(s) resuelta(s)[/dim]")
        state = _load()

    print_dashboard(state)

    # 2. Check daily limits
    ok, reason = can_bet(state)
    if not ok:
        console.print(f"[red]STOP:[/red] {reason}")
        # Only notify once per pause reason — avoid spamming every 5 min
        last_stop = state.get("_last_stop_reason", "")
        if reason != last_stop:
            notify_stop(reason)
            state["_last_stop_reason"] = reason
            _save(state)
        print_history(state)
        return
    # Clear stop reason when bot is active again
    if state.get("_last_stop_reason"):
        state["_last_stop_reason"] = ""

    # 3. Fetch active markets for all assets
    markets = fetch_5m_markets()
    market_map: dict[str, dict] = {m["asset"]: m for m in markets}

    # 4. Analyze all assets in parallel — pass window_start so analyzer can compute
    #    within-window momentum (strongest signal for 5-min markets)
    def _analyze(asset: str) -> tuple[str, dict]:
        cfg         = ASSETS[asset]
        win_start   = market_map.get(asset, {}).get("window_start", 0)
        return asset, analyze_asset_5m(cfg["binance"], window_start_ts=win_start)

    analyses: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(_analyze, a): a for a in ASSETS}
        for fut in as_completed(futures):
            try:
                asset, result = fut.result()
                analyses[asset] = result
            except Exception:
                pass

    # 5. Build signal list with edges
    now = datetime.now(timezone.utc)
    signals: list[dict] = []
    pending_ids = {b["market_id"] for b in state["history"] if b["status"] == "PENDING"}

    for asset in ASSETS:
        a   = analyses.get(asset) or {}
        mkt = market_map.get(asset)

        edge_up = edge_dn = 0.0
        best_bet = None
        signal_active = True
        signal_reason = ""
        required = SIGNAL_RULES.get(asset)

        if mkt:
            # Skip: already have a PENDING bet in this exact market
            if mkt["market_id"] in pending_ids:
                mkt = None
            else:
                # Verify enough time left to bet (≥ 60s)
                if mkt["end_date"]:
                    secs_left = (mkt["end_date"] - now).total_seconds()
                    if secs_left < 60:
                        mkt = None

        if mkt and a:
            edge_up = a.get("prob_up", 0.5) - mkt["price_up"]
            edge_dn = a.get("prob_down", 0.5) - mkt["price_down"]

            win_mom  = abs(a.get("window_momentum", 0.0))
            mom_5m   = abs(a.get("momentum_5m", 0.0))
            has_momentum = (win_mom >= MIN_MOMENTUM) or (mom_5m >= MIN_MOMENTUM)

            # Per-asset signal gate (backtest-derived):
            # Only bet when the asset's required trigger is active.
            # This filters ~75% of low-quality setups and improves win rate.
            required = SIGNAL_RULES.get(asset)
            signal_active = True
            signal_reason = ""

            if required is None or required == "none":
                # Asset paused — no reliable signal found in backtest
                signal_active = False
                signal_reason = "paused (no edge signal in backtest)"
            elif required != "any":
                if required == "macd_cross":
                    # Require a fresh MACD(3/15/3) crossover this candle
                    cross = a.get("macd_cross")
                    if cross is None:
                        signal_active = False
                        signal_reason = "waiting MACD cross"
                    # Align cross direction with intended bet direction (checked below)
                elif required == "cvd_div":
                    # Require CVD divergence (price vs buy pressure split)
                    div = a.get("cvd_divergence")
                    if div is None:
                        signal_active = False
                        signal_reason = "waiting CVD divergence"

            asset_min_edge = MIN_EDGE_PER_ASSET.get(asset, MIN_EDGE)

            if signal_active and edge_up >= asset_min_edge and edge_up >= edge_dn:
                if mkt["price_up"] <= MAX_PRICE and has_momentum:
                    # For directional signals, ensure the signal agrees with bet direction
                    if required == "macd_cross" and a.get("macd_cross") != "BULL":
                        pass  # MACD says DOWN, don't bet UP
                    elif required == "cvd_div" and a.get("cvd_divergence") != "BULL":
                        pass  # CVD says DOWN (or neutral), don't bet UP
                    else:
                        best_bet = ("UP", edge_up)

            elif signal_active and edge_dn >= asset_min_edge and edge_dn > edge_up:
                if mkt["price_down"] <= MAX_PRICE and has_momentum:
                    if required == "macd_cross" and a.get("macd_cross") != "BEAR":
                        pass  # MACD says UP, don't bet DOWN
                    elif required == "cvd_div" and a.get("cvd_divergence") != "BEAR":
                        pass  # CVD says UP (or neutral), don't bet DOWN
                    else:
                        best_bet = ("DOWN", edge_dn)

        signals.append({
            "asset":         asset,
            "analysis":      a,
            "market":        mkt,
            "edge_up":       round(edge_up, 4),
            "edge_down":     round(edge_dn, 4),
            "best_bet":      best_bet,
            "signal_active": signal_active,
            "signal_reason": signal_reason,
            "required":      required,
        })

    # Sort by best edge descending
    signals.sort(key=lambda x: max(x["edge_up"], x["edge_down"]), reverse=True)

    print_signals(signals)

    # 6. Place bets (up to MAX_PER_CYCLE per cycle)
    # Correlation guard: track how many bets we've placed per (end_date, side) this cycle.
    # Avoids doubling correlated risk (e.g., DOWN on SOL + DOWN on ETH same window = same bet).
    window_dir_count: dict[tuple, int] = {}

    bets_placed = 0
    for sig in signals:
        if bets_placed >= MAX_PER_CYCLE:
            break
        ok_now, reason = can_bet(state)
        if not ok_now:
            console.print(f"[red]STOP:[/red] {reason}")
            break

        if not sig["best_bet"] or not sig["market"]:
            continue

        side, edge = sig["best_bet"]
        mkt        = sig["market"]
        a          = sig["analysis"]

        # Edge cap: skip if our model is too far from market (likely over-confident)
        if edge > MAX_EDGE:
            console.print(
                f"[dim]Saltando {sig['asset']} {side} — edge {edge:.1%} supera cap "
                f"{MAX_EDGE:.0%} (modelo posiblemente sobreconfiado)[/dim]"
            )
            continue

        # Correlation guard: only MAX_BETS_PER_DIRECTION_PER_WINDOW bets per (window, direction)
        end_date_str = mkt["end_date"].isoformat() if mkt.get("end_date") else ""
        corr_key     = (end_date_str, side)
        if window_dir_count.get(corr_key, 0) >= MAX_BETS_PER_DIRECTION_PER_WINDOW:
            console.print(
                f"[dim]Saltando {sig['asset']} {side} — ya hay apuesta {side} en esta ventana "
                f"(correlacion, riesgo doble)[/dim]"
            )
            continue
        window_dir_count[corr_key] = window_dir_count.get(corr_key, 0) + 1

        price    = mkt["price_up"] if side == "UP" else mkt["price_down"]
        prob_win = a.get("prob_up", 0.5) if side == "UP" else a.get("prob_down", 0.5)

        # ── LIVE: ejecutar orden real en Polymarket ────────────────────────────
        if not DRY_RUN:
            token_id = mkt.get("token_id_up") if side == "UP" else mkt.get("token_id_down")
            if not token_id:
                console.print(f"[red]  Sin token_id para {sig['asset']} {side} - saltando[/red]")
                continue

            console.print(f"[bold red]  >> Enviando orden REAL a Polymarket...[/bold red]")
            resp = place_real_order(token_id, price, BET_SIZE, side)

            if resp is None:
                console.print(f"[red]  ORDEN FALLIDA - {sig['asset']} {side} no se ejecuto[/red]")
                continue

            order_id = resp.get("orderID") or resp.get("order_id") or str(resp)[:24]
            console.print(f"[bold green]  ORDEN EJECUTADA OK  ID: {order_id}[/bold green]")

        # ── Registrar apuesta en estado local ─────────────────────────────────
        bet = record_bet(state, mkt, side, price, edge, prob_win)
        gain = BET_SIZE / price

        mode_tag = "[yellow]SIM[/yellow]" if DRY_RUN else "[bold green]REAL OK[/bold green]"
        console.print(
            f"\n  {mode_tag} [{sig['asset']}] >> [bold]{side}[/bold]  "
            f"Precio: {price:.2f}  Edge: {edge:+.1%}  "
            f"Apuesta: ${BET_SIZE}  Ganancia potencial: ${gain:.2f}\n"
            f"  {mkt['question'][:70]}"
        )

        _notify_bet(sig["asset"], side, price, edge, prob_win, mkt["question"], mkt.get("market_id", ""))
        bets_placed += 1

    if bets_placed == 0:
        console.print("[dim]Sin senales con edge suficiente en este ciclo[/dim]")
        notify_sin_senales()

    print_history(state)
    console.print()


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    # ── Single-instance lock: prevent multiple bots on same state file ──────────
    lock_file = Path("logs/bot.lock")
    lock_file.parent.mkdir(exist_ok=True)
    try:
        import msvcrt
        _lock_fd = open(lock_file, "w")
        msvcrt.locking(_lock_fd.fileno(), msvcrt.LK_NBLCK, 1)
    except (OSError, IOError):
        console.print("[bold red]ERROR: Ya hay una instancia del bot corriendo. Ciérrala antes de iniciar otra.[/bold red]")
        raise SystemExit(1)

    print_banner()
    console.print()

    # Immediate first cycle
    run_cycle()

    # Schedule: every 5 minutes, 30 seconds after each window opens
    # Windows open at :00, :05, :10 … so we run at :00:30, :05:30, :10:30 …
    sched = BlockingScheduler(timezone="UTC")
    sched.add_job(
        run_cycle,
        trigger="cron",
        minute="0,5,10,15,20,25,30,35,40,45,50,55",
        second=10,
        misfire_grace_time=20,
    )
    sched.add_job(
        pnl_alert,
        trigger="cron",
        minute="0,10,20,30,40,50",
        second=0,
        misfire_grace_time=30,
    )
    sched.add_job(
        resolve_job,
        trigger="interval",
        seconds=30,
        misfire_grace_time=15,
    )

    console.print("[dim]Scheduler activo - ciclo cada 5 minutos (:10s) | resolucion cada 30s | alerta PnL cada 10 minutos[/dim]")
    try:
        sched.start()
    except KeyboardInterrupt:
        console.print("\n[dim]Bot detenido.[/dim]")


if __name__ == "__main__":
    main()
