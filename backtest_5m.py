"""
backtest_5m.py
==============
Backtesta la estrategia del bot de 5 minutos contra datos historicos de Binance.

Metodologia:
  1. Descarga N horas de 1m candles desde Binance (gratis, sin API key).
  2. Simula cada ventana de 5 minutos: llama al analyzer con los datos del
     pasado disponibles en ese momento.
  3. El "resultado" es si el precio subio o bajo al cierre de la ventana.
  4. Asume precio de entrada siempre en 0.50 (mercado justo, sin spread).
  5. Reporta: win rate, profit factor, max drawdown, Sharpe ratio.

Uso:
    python backtest_5m.py                    # BTC, 24h de historia
    python backtest_5m.py --asset ETH        # ETH
    python backtest_5m.py --hours 72         # 72h de historia
    python backtest_5m.py --min-edge 0.06    # filtro de edge mas estricto

Benchmarks de estrategia rentable (del articulo RBI):
    Win rate     > 55%
    Profit factor > 1.5
    Max drawdown < 20%
    Muestra       >= 100 trades
"""

import argparse
import sys
import time
from datetime import datetime, timezone, timedelta

# ── Auto-install ───────────────────────────────────────────────────────────────
for _pkg in ["requests", "rich"]:
    try:
        __import__(_pkg)
    except ImportError:
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", _pkg, "-q"])

import requests
from rich.console import Console
from rich.table import Table
from rich import box

from cryp_5m_analyzer import _ema, _macd, _vwap, _cvd, _rsi

BINANCE_API = "https://api.binance.com"
console     = Console()

ASSETS = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT",
    "XRP": "XRPUSDT",
}


# ── Data fetching ──────────────────────────────────────────────────────────────

def fetch_history(symbol: str, hours: int = 24) -> list[dict]:
    """
    Download 1-minute candles for the last N hours from Binance.
    Binance allows up to 1000 candles per request.
    """
    limit_per_req = 1000
    total_minutes = hours * 60
    all_candles: list[dict] = []

    end_ms   = int(time.time() * 1000)
    start_ms = end_ms - total_minutes * 60 * 1000

    console.print(f"  Descargando {total_minutes} candles de {symbol}...", end="")

    while start_ms < end_ms:
        try:
            resp = requests.get(
                f"{BINANCE_API}/api/v3/klines",
                params={
                    "symbol":    symbol,
                    "interval":  "1m",
                    "startTime": start_ms,
                    "endTime":   end_ms,
                    "limit":     limit_per_req,
                },
                timeout=15,
            )
            resp.raise_for_status()
            rows = resp.json()
            if not rows:
                break
            for row in rows:
                all_candles.append({
                    "ts":            int(row[0]),
                    "open":          float(row[1]),
                    "high":          float(row[2]),
                    "low":           float(row[3]),
                    "close":         float(row[4]),
                    "volume":        float(row[5]),
                    "taker_buy_vol": float(row[9]),
                })
            start_ms = int(rows[-1][0]) + 60_000   # next minute
            time.sleep(0.1)   # gentle rate limiting
        except Exception as e:
            console.print(f" [red]Error: {e}[/red]")
            break

    console.print(f" {len(all_candles)} candles OK")
    return all_candles


# ── Analyzer on historical slice ───────────────────────────────────────────────

def analyze_slice(candles: list[dict]) -> dict:
    """
    Run the same probability model as cryp_5m_analyzer on a historical candle slice.
    candles[-1] is the most recent candle AT decision time (window just opened).
    """
    if len(candles) < 20:
        return {"prob_up": 0.5, "prob_down": 0.5}

    closes  = [c["close"]  for c in candles]
    volumes = [c["volume"] for c in candles]
    last5   = candles[-5:]

    current_price = closes[-1]
    mom_5m = (closes[-1] - closes[-6]) / closes[-6] if closes[-6] != 0 else 0.0
    mom_1m = (closes[-1] - closes[-2]) / closes[-2] if closes[-2] != 0 else 0.0

    rsi = _rsi(closes)
    _, _, macd_hist, macd_hist_prev = _macd(closes, fast=3, slow=15, signal=3)
    vwap    = _vwap(candles[-20:])
    cvd_total, cvd_last5 = _cvd(candles)

    # MACD crossover
    macd_cross = None
    if macd_hist is not None and macd_hist_prev is not None:
        if macd_hist > 0 and macd_hist_prev <= 0:
            macd_cross = "BULL"
        elif macd_hist < 0 and macd_hist_prev >= 0:
            macd_cross = "BEAR"

    # VWAP position
    vwap_pos = "AT"
    if vwap and current_price:
        diff = (current_price - vwap) / vwap
        if   diff >  0.001: vwap_pos = "ABOVE"
        elif diff < -0.001: vwap_pos = "BELOW"

    # CVD divergence
    cvd_divergence = None
    if len(candles) >= 10:
        cvd_prev   = sum((c.get("taker_buy_vol", 0) - (c["volume"] - c.get("taker_buy_vol", 0)))
                        for c in candles[-10:-5])
        cvd_rising  = cvd_last5 > cvd_prev
        cvd_falling = cvd_last5 < cvd_prev
        if mom_5m < -0.001 and cvd_rising:
            cvd_divergence = "BULL"
        elif mom_5m > 0.001 and cvd_falling:
            cvd_divergence = "BEAR"

    avg_vol   = sum(volumes[-10:]) / 10
    vol_ratio = volumes[-1] / avg_vol if avg_vol > 0 else 1.0
    greens    = sum(1 for c in last5 if c["close"] >= c["open"])

    # ── Probability model (mirrors cryp_5m_analyzer) ──────────────────────────
    prob_up = 0.50

    # Window momentum not available in backtest (we're AT window open), skip

    # MACD(3/15/3)
    if macd_cross == "BULL":
        prob_up += 0.08
    elif macd_cross == "BEAR":
        prob_up -= 0.08
    elif macd_hist is not None:
        if   macd_hist >  0.0005: prob_up += 0.04
        elif macd_hist >  0:      prob_up += 0.02
        elif macd_hist < -0.0005: prob_up -= 0.04
        elif macd_hist <  0:      prob_up -= 0.02

    # CVD divergence
    if cvd_divergence == "BULL":
        prob_up += 0.07
    elif cvd_divergence == "BEAR":
        prob_up -= 0.07
    elif cvd_last5 > 0:
        prob_up += 0.03
    elif cvd_last5 < 0:
        prob_up -= 0.03

    # VWAP
    if   vwap_pos == "ABOVE": prob_up += 0.03
    elif vwap_pos == "BELOW": prob_up -= 0.03

    # Pre-window 5m momentum
    if   mom_5m >  0.003:  prob_up += 0.06
    elif mom_5m >  0.0015: prob_up += 0.04
    elif mom_5m >  0.0005: prob_up += 0.02
    elif mom_5m < -0.003:  prob_up -= 0.06
    elif mom_5m < -0.0015: prob_up -= 0.04
    elif mom_5m < -0.0005: prob_up -= 0.02

    # Last-candle momentum
    if   mom_1m >  0.002:  prob_up += 0.04
    elif mom_1m >  0.0008: prob_up += 0.02
    elif mom_1m < -0.002:  prob_up -= 0.04
    elif mom_1m < -0.0008: prob_up -= 0.02

    # RSI
    if rsi is not None:
        if   rsi >= 78: prob_up -= 0.10
        elif rsi >= 65: prob_up -= 0.04
        elif rsi <= 22: prob_up += 0.10
        elif rsi <= 35: prob_up += 0.04

    # Candle pattern
    if   greens >= 5: prob_up += 0.04
    elif greens >= 4: prob_up += 0.02
    elif greens <= 0: prob_up -= 0.04
    elif greens <= 1: prob_up -= 0.02

    # Volume
    if vol_ratio > 1.8:
        if   mom_5m > 0: prob_up += 0.02
        elif mom_5m < 0: prob_up -= 0.02

    prob_up = max(0.20, min(0.80, prob_up))
    return {
        "prob_up":        round(prob_up, 4),
        "prob_down":      round(1 - prob_up, 4),
        "macd_cross":     macd_cross,
        "cvd_divergence": cvd_divergence,
        "vwap_pos":       vwap_pos,
    }


# ── Backtest engine ────────────────────────────────────────────────────────────

def run_backtest(symbol: str, candles: list[dict], min_edge: float = 0.04,
                 bet_size: float = 1.0, entry_price: float = 0.50) -> dict:
    """
    Simulate the strategy on historical candles.

    For each 5-minute window:
      - Use candles up to window start as context
      - Decide UP/DOWN/NO BET based on prob and edge
      - Resolve: price went UP if candles[window_end].close > candles[window_start].close

    Assumes market price is always entry_price (0.50) = fair market.
    """
    if len(candles) < 35:
        return {}

    WINDOW = 5   # minutes

    trades     = []
    balance    = 100.0
    peak_bal   = 100.0
    max_dd     = 0.0

    # Group candles into 5-minute windows
    # Start from candle 30 to have enough history for MACD
    i = 30
    while i + WINDOW < len(candles):
        window_start = candles[i]
        window_end   = candles[i + WINDOW - 1]

        # Context: all candles up to (not including) window start
        context = candles[max(0, i - 30):i]

        analysis = analyze_slice(context)
        prob_up   = analysis["prob_up"]
        prob_down = analysis["prob_down"]

        # Decide bet
        edge_up = prob_up   - entry_price
        edge_dn = prob_down - entry_price
        best_edge = 0.0
        side      = None

        if edge_up >= min_edge and edge_up >= edge_dn:
            side      = "UP"
            best_edge = edge_up
        elif edge_dn >= min_edge and edge_dn > edge_up:
            side      = "DOWN"
            best_edge = edge_dn

        if side is None:
            i += WINDOW
            continue

        # Resolve: did price go UP or DOWN over the window?
        price_start = window_start["close"]
        price_end   = window_end["close"]
        actual_up   = price_end > price_start

        won = (side == "UP" and actual_up) or (side == "DOWN" and not actual_up)
        payout = bet_size / entry_price
        pnl    = round(payout - bet_size, 4) if won else -bet_size

        balance = round(balance + pnl, 4)
        if balance > peak_bal:
            peak_bal = balance
        dd = (peak_bal - balance) / peak_bal
        if dd > max_dd:
            max_dd = dd

        trades.append({
            "ts":       datetime.utcfromtimestamp(window_start["ts"] / 1000).strftime("%Y-%m-%d %H:%M"),
            "side":     side,
            "edge":     best_edge,
            "won":      won,
            "pnl":      pnl,
            "balance":  balance,
            "macd":     analysis.get("macd_cross", ""),
            "cvd":      analysis.get("cvd_divergence", ""),
            "vwap":     analysis.get("vwap_pos", ""),
            "price_chg": round((price_end - price_start) / price_start * 100, 3),
        })

        i += WINDOW

    if not trades:
        return {}

    total  = len(trades)
    wins   = sum(1 for t in trades if t["won"])
    losses = total - wins
    wr     = wins / total

    gross_profit = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    gross_loss   = abs(sum(t["pnl"] for t in trades if t["pnl"] < 0))
    pf           = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    pnls    = [t["pnl"] for t in trades]
    avg_pnl = sum(pnls) / len(pnls)
    std_pnl = (sum((p - avg_pnl) ** 2 for p in pnls) / len(pnls)) ** 0.5
    sharpe  = (avg_pnl / std_pnl * (252 * 288) ** 0.5) if std_pnl > 0 else 0  # annualized

    # Signal breakdown
    macd_cross_trades = [t for t in trades if t["macd"] in ("BULL", "BEAR")]
    cvd_div_trades    = [t for t in trades if t["cvd"] in ("BULL", "BEAR")]

    macd_wr = sum(1 for t in macd_cross_trades if t["won"]) / len(macd_cross_trades) if macd_cross_trades else 0
    cvd_wr  = sum(1 for t in cvd_div_trades    if t["won"]) / len(cvd_div_trades)    if cvd_div_trades    else 0

    return {
        "symbol":      symbol,
        "total":       total,
        "wins":        wins,
        "losses":      losses,
        "win_rate":    wr,
        "pnl_total":   round(balance - 100.0, 2),
        "profit_factor": round(pf, 2),
        "max_drawdown": round(max_dd * 100, 1),
        "sharpe":      round(sharpe, 2),
        "macd_trades": len(macd_cross_trades),
        "macd_wr":     macd_wr,
        "cvd_trades":  len(cvd_div_trades),
        "cvd_wr":      cvd_wr,
        "trades":      trades,
    }


# ── Report ─────────────────────────────────────────────────────────────────────

def print_report(results: list[dict], min_edge: float) -> None:
    console.print()
    console.rule("[bold cyan]BACKTEST RESULTS — 5M BOT[/bold cyan]")
    console.print(f"  Entry price assumed: 0.50  |  Min edge: {min_edge:.0%}  |  Bet size: $1.00\n")

    # Summary table
    t = Table(title="Summary", box=box.ROUNDED, header_style="bold")
    t.add_column("Asset",       width=6)
    t.add_column("Trades",      justify="right", width=7)
    t.add_column("Win Rate",    justify="right", width=9)
    t.add_column("PnL",         justify="right", width=9)
    t.add_column("Profit F.",   justify="right", width=9)
    t.add_column("Max DD",      justify="right", width=8)
    t.add_column("Sharpe",      justify="right", width=7)
    t.add_column("Verdict",     justify="center", width=12)

    BENCHMARKS = {"win_rate": 0.55, "profit_factor": 1.5, "max_drawdown": 20.0}

    for r in results:
        if not r:
            continue
        wr_c   = "green" if r["win_rate"] >= BENCHMARKS["win_rate"]    else "red"
        pf_c   = "green" if r["profit_factor"] >= BENCHMARKS["profit_factor"] else "red"
        dd_c   = "green" if r["max_drawdown"]  <= BENCHMARKS["max_drawdown"]  else "red"
        pnl_c  = "green" if r["pnl_total"] >= 0 else "red"

        passes = (r["win_rate"] >= BENCHMARKS["win_rate"] and
                  r["profit_factor"] >= BENCHMARKS["profit_factor"] and
                  r["max_drawdown"] <= BENCHMARKS["max_drawdown"] and
                  r["total"] >= 20)
        verdict = "[green]PASS[/green]" if passes else "[red]FAIL[/red]"

        t.add_row(
            r["symbol"][:6],
            str(r["total"]),
            f"[{wr_c}]{r['win_rate']:.1%}[/{wr_c}]",
            f"[{pnl_c}]${r['pnl_total']:+.2f}[/{pnl_c}]",
            f"[{pf_c}]{r['profit_factor']:.2f}[/{pf_c}]",
            f"[{dd_c}]{r['max_drawdown']:.1f}%[/{dd_c}]",
            str(r["sharpe"]),
            verdict,
        )

    console.print(t)

    # Benchmarks legend
    console.print(
        "[dim]  Benchmarks: Win Rate >55%  |  Profit Factor >1.5  |  Max Drawdown <20%  |  Trades >=20[/dim]\n"
    )

    # Signal breakdown table
    t2 = Table(title="Signal breakdown", box=box.SIMPLE, header_style="bold")
    t2.add_column("Asset",        width=6)
    t2.add_column("MACD cross",   justify="right", width=12)
    t2.add_column("MACD WR",      justify="right", width=9)
    t2.add_column("CVD diverge",  justify="right", width=12)
    t2.add_column("CVD WR",       justify="right", width=9)

    for r in results:
        if not r:
            continue
        mwr_c = "green" if r["macd_wr"] >= 0.55 else ("yellow" if r["macd_wr"] >= 0.50 else "red")
        cwr_c = "green" if r["cvd_wr"]  >= 0.55 else ("yellow" if r["cvd_wr"]  >= 0.50 else "red")
        t2.add_row(
            r["symbol"][:6],
            str(r["macd_trades"]),
            f"[{mwr_c}]{r['macd_wr']:.1%}[/{mwr_c}]" if r["macd_trades"] else "[dim]—[/dim]",
            str(r["cvd_trades"]),
            f"[{cwr_c}]{r['cvd_wr']:.1%}[/{cwr_c}]"  if r["cvd_trades"]  else "[dim]—[/dim]",
        )

    console.print(t2)
    console.print()
    console.print("[dim]Tip: run with --hours 72 or --hours 168 (1 week) for more significant results.[/dim]")
    console.print("[dim]     run with --min-edge 0.06 to see if stricter filtering improves win rate.[/dim]")
    console.print()


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest the 5m crypto bot strategy")
    parser.add_argument("--asset",    default="ALL",  help="BTC | ETH | SOL | XRP | ALL")
    parser.add_argument("--hours",    type=int, default=24, help="Hours of history (default 24)")
    parser.add_argument("--min-edge", type=float, default=0.04, help="Min edge to bet (default 0.04)")
    parser.add_argument("--bet",      type=float, default=1.0,  help="Bet size in dollars (default 1.0)")
    args = parser.parse_args()

    assets = list(ASSETS.items()) if args.asset == "ALL" else \
             [(args.asset, ASSETS[args.asset])] if args.asset in ASSETS else []

    if not assets:
        console.print(f"[red]Asset desconocido: {args.asset}. Opciones: BTC ETH SOL XRP ALL[/red]")
        return

    console.rule("[bold cyan]BACKTEST 5M BOT[/bold cyan]")
    console.print(f"  Assets: {[a for a, _ in assets]}  |  Horas: {args.hours}  |  Min edge: {args.min_edge:.0%}\n")

    results = []
    for asset, symbol in assets:
        candles = fetch_history(symbol, hours=args.hours)
        if len(candles) < 40:
            console.print(f"  [red]{asset}: datos insuficientes[/red]")
            continue
        result = run_backtest(symbol, candles, min_edge=args.min_edge, bet_size=args.bet)
        if result:
            result["symbol"] = asset
            results.append(result)

    if results:
        print_report(results, args.min_edge)
    else:
        console.print("[red]Sin resultados. Verifica conexion a internet.[/red]")


if __name__ == "__main__":
    main()
