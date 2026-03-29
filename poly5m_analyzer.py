"""
poly5m_analyzer.py
==================
Analyzes Polymarket 5-min UP/DOWN markets using ONLY Polymarket data.
No Binance candles — signals derived from the CLOB orderbook and price data.

Signals:
  1. Demand imbalance  — bids on UP token vs bids on DOWN token
                         (where is real money actively buying?)
  2. Price deviation   — how far price_up is from 0.50

Why bids-only comparison (not bids vs asks):
  In Polymarket's CLOB, market makers post massive ask walls on BOTH sides
  as structural liquidity — they carry no directional signal.
  Only BIDS reflect real traders actively choosing a direction.
  bid_up >> bid_down → crowd buying UP → bullish signal
  bid_down >> bid_up → crowd buying DOWN → bearish signal

API used:
  CLOB: https://clob.polymarket.com/book?token_id={token_id}  (public, no auth)
"""

import requests
import time as _time

CLOB_API = "https://clob.polymarket.com"

_BOOK_CACHE: dict[str, dict] = {}
_BOOK_TTL = 15   # seconds


def _fetch_orderbook(token_id: str) -> dict:
    if not token_id:
        return {}
    cached = _BOOK_CACHE.get(token_id)
    if cached and (_time.time() - cached["ts"]) < _BOOK_TTL:
        return cached["data"]
    try:
        resp = requests.get(
            f"{CLOB_API}/book",
            params={"token_id": token_id},
            timeout=6,
        )
        resp.raise_for_status()
        data = resp.json()
        _BOOK_CACHE[token_id] = {"ts": _time.time(), "data": data}
        return data
    except Exception:
        return {}


def _bid_depth(book: dict) -> float:
    """Sum total USDC value of all bids (active buyers)."""
    total = 0.0
    for e in book.get("bids", []):
        try:
            total += float(e.get("size", 0)) * float(e.get("price", 0))
        except (ValueError, TypeError):
            pass
    return total


def get_best_ask(token_id: str) -> float | None:
    """Return the best (lowest) ask price from the cached orderbook."""
    book = _fetch_orderbook(token_id)
    asks = book.get("asks", [])
    if not asks:
        return None
    try:
        prices = [float(e["price"]) for e in asks if float(e.get("size", 0)) > 0]
        return min(prices) if prices else None
    except (ValueError, TypeError):
        return None


def analyze_market_poly(market: dict) -> dict:
    """
    Analyze a 5-min market comparing bid demand on UP vs DOWN tokens.

    Returns
    -------
    dict with:
        prob_up         float   estimated win probability for UP
        prob_down       float   estimated win probability for DOWN
        edge_up         float   prob_up - price_up
        edge_down       float   prob_down - price_down
        demand_imbalance float  -1.0 to +1.0  (+1 = all demand on UP)
        bid_up          float   USDC buying UP
        bid_down        float   USDC buying DOWN
        price_signal    float   0.50 - price_up
        signals         list[str]
    """
    price_up   = market.get("price_up",   0.50)
    price_down = market.get("price_down", 0.50)
    token_up   = market.get("token_id_up")
    token_down = market.get("token_id_down")

    # ── 1. Demand imbalance: bids UP vs bids DOWN ─────────────────────────────
    # Fetch both books in parallel-ish (sequential but cached)
    book_up   = _fetch_orderbook(token_up)
    book_down = _fetch_orderbook(token_down)

    bid_up   = _bid_depth(book_up)    # real money buying UP
    bid_down = _bid_depth(book_down)  # real money buying DOWN
    total_demand = bid_up + bid_down or 1.0

    # +1.0 = all demand on UP (strongly bullish)
    # -1.0 = all demand on DOWN (strongly bearish)
    demand_imbalance = (bid_up - bid_down) / total_demand

    # ── 2. Price deviation signal (diagnostic only — NOT used in prob calc) ──────
    # Removed from combined: price_up > 0.50 in bull markets creates a systematic
    # DOWN bias because price_signal is always negative, asymmetrically lowering
    # the threshold for DOWN bets vs UP bets.
    # Price is still used as a value filter via MAX_PRICE in the bot.
    price_signal = 0.50 - price_up

    # ── 3. Combine demand + price deviation → prob_up ────────────────────────
    # demand_imbalance: full weight 1.0 (bids are symmetric ~0-10%, need sensitivity)
    # price_signal: small weight to capture price deviations from fair value (0.50)
    #   — mean reversion: if price_up=0.44 (UP cheap), small positive push to UP
    #   — weight kept low (0.20) to avoid systematic trend bias
    DEMAND_WEIGHT = 0.80
    PRICE_WEIGHT  = 0.20
    price_signal_norm = price_signal / 0.50  # normalize to [-1, 1]
    combined   = (demand_imbalance * DEMAND_WEIGHT) + (price_signal_norm * PRICE_WEIGHT)
    adjustment = max(-0.20, min(0.20, combined))

    prob_up   = 0.50 + adjustment
    prob_down = 1.0 - prob_up

    edge_up   = round(prob_up   - price_up,   4)
    edge_down = round(prob_down - price_down, 4)

    # ── 4. Signal labels ─────────────────────────────────────────────────────
    # Note: for edge_up/edge_down >= 0.06 (MIN_EDGE) at price ≈ 0.50,
    # need |demand_imbalance| >= 0.24 (symmetric threshold for both UP and DOWN)
    signals: list[str] = []

    if demand_imbalance > 0.15:
        signals.append(f"BID_UP+{demand_imbalance:.0%}")
    elif demand_imbalance < -0.15:
        signals.append(f"BID_DN{demand_imbalance:.0%}")
    else:
        signals.append("BID_NEUTRAL")

    if price_signal > 0.03:
        signals.append(f"PRICE_UP_CHEAP+{price_signal:.0%}")
    elif price_signal < -0.03:
        signals.append(f"PRICE_DN_CHEAP{price_signal:.0%}")

    return {
        "prob_up":          round(prob_up,  4),
        "prob_down":        round(prob_down, 4),
        "edge_up":          edge_up,
        "edge_down":        edge_down,
        "demand_imbalance": round(demand_imbalance, 4),
        "bid_up":           round(bid_up,   2),
        "bid_down":         round(bid_down, 2),
        "price_signal":     round(price_signal, 4),
        "signals":          signals,
    }
