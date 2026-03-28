"""
cryp_5m_analyzer.py
====================
Short-term momentum analysis for 5-minute UP/DOWN prediction.
Uses Binance public API for 1-minute OHLCV data (no API key required).

Signals:
  - RSI(14)            mean-reversion at extremes
  - MACD(3/15/3)       momentum crossover  — backtested 60% win rate on Poly 5m
  - VWAP               price location vs volume-weighted fair value
  - CVD divergence     buy/sell pressure via taker volume delta — 63% win rate
  - Window momentum    % move since current 5-min window opened (strongest signal)
  - Pre-window mom     5m and 1m context

Endpoint: GET https://api.binance.com/api/v3/klines
          ?symbol=BTCUSDT&interval=1m&limit=30
"""

import requests
import time as _time_module

BINANCE_API      = "https://api.binance.com"
BINANCE_FUTURES  = "https://fapi.binance.com"
_CACHE: dict[str, dict] = {}   # {symbol: {"ts": epoch, "result": dict}}
CACHE_TTL        = 15           # seconds — fresher data for tighter 5-min windows
_FUNDING_CACHE: dict[str, dict] = {}
_FUNDING_TTL     = 300          # funding rates change every 8h, cache 5min is fine


def fetch_funding_rate(symbol: str) -> float | None:
    """
    Fetch the current perpetual futures funding rate from Binance.
    Positive  → longs paying shorts → market overextended UP → mild bearish signal
    Negative  → shorts paying longs → market overextended DOWN → mild bullish signal

    Typical range: -0.05% to +0.05%. Extremes (>0.1%) = strong contrarian signal.
    Free endpoint, no API key required.
    """
    cached = _FUNDING_CACHE.get(symbol)
    if cached and (_time_module.time() - cached["ts"]) < _FUNDING_TTL:
        return cached["rate"]
    try:
        resp = requests.get(
            f"{BINANCE_FUTURES}/fapi/v1/premiumIndex",
            params={"symbol": symbol},
            timeout=6,
        )
        resp.raise_for_status()
        rate = float(resp.json().get("lastFundingRate", 0))
        _FUNDING_CACHE[symbol] = {"ts": _time_module.time(), "rate": rate}
        return rate
    except Exception:
        return None


def _fetch_klines(symbol: str, limit: int = 30) -> list[dict]:
    """Fetch 1-minute candles from Binance spot; falls back to futures for tokens
    not listed on spot (e.g. HYPE). Includes taker buy volume for CVD."""
    def _parse(rows: list) -> list[dict]:
        return [
            {
                "ts":            int(row[0]),
                "open":          float(row[1]),
                "high":          float(row[2]),
                "low":           float(row[3]),
                "close":         float(row[4]),
                "volume":        float(row[5]),
                "taker_buy_vol": float(row[9]),
            }
            for row in rows
        ]
    # Try spot first
    try:
        resp = requests.get(
            f"{BINANCE_API}/api/v3/klines",
            params={"symbol": symbol, "interval": "1m", "limit": limit},
            timeout=8,
        )
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list) and data:
            return _parse(data)
    except Exception:
        pass
    # Fallback: perpetual futures (covers HYPE and other futures-only tokens)
    try:
        resp = requests.get(
            f"{BINANCE_FUTURES}/fapi/v1/klines",
            params={"symbol": symbol, "interval": "1m", "limit": limit},
            timeout=8,
        )
        resp.raise_for_status()
        return _parse(resp.json())
    except Exception:
        return []


def _rsi(closes: list[float], period: int = 14) -> float | None:
    """RSI(period) from list of close prices."""
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_g = sum(gains[-period:]) / period
    avg_l = sum(losses[-period:]) / period
    if avg_l == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + avg_g / avg_l))


def _ema(values: list[float], period: int) -> list[float]:
    """Exponential Moving Average. Returns aligned list same length as output."""
    if len(values) < period:
        return []
    k = 2.0 / (period + 1)
    result = [sum(values[:period]) / period]   # seed with SMA
    for v in values[period:]:
        result.append(v * k + result[-1] * (1.0 - k))
    return result


def _macd(closes: list[float], fast: int = 3, slow: int = 15, signal: int = 3
          ) -> tuple[float | None, float | None, float | None, float | None]:
    """
    MACD(fast, slow, signal) — default (3, 15, 3) as documented.

    Returns (macd_line, signal_line, histogram_now, histogram_prev)
    or (None, None, None, None) if not enough data.

    histogram_prev allows crossover detection: sign change = strong signal.
    """
    if len(closes) < slow + signal + 1:
        return None, None, None, None

    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)

    # Align: ema_fast has more values; trim front to match ema_slow length
    diff = len(ema_fast) - len(ema_slow)
    macd_line = [f - s for f, s in zip(ema_fast[diff:], ema_slow)]

    if len(macd_line) < signal + 1:
        return None, None, None, None

    sig_line = _ema(macd_line, signal)
    if len(sig_line) < 2:
        return None, None, None, None

    hist_now  = macd_line[-1] - sig_line[-1]
    hist_prev = macd_line[-2] - sig_line[-2]
    return macd_line[-1], sig_line[-1], hist_now, hist_prev


def _vwap(candles: list[dict]) -> float | None:
    """VWAP over the provided candles (typical price × volume / total volume)."""
    if not candles:
        return None
    cum_pv = sum(((c["high"] + c["low"] + c["close"]) / 3.0) * c["volume"]
                 for c in candles)
    cum_v = sum(c["volume"] for c in candles)
    return cum_pv / cum_v if cum_v > 0 else None


def _cvd(candles: list[dict]) -> tuple[float, float]:
    """
    Cumulative Volume Delta using Binance taker buy volume.

    CVD = sum(buy_vol - sell_vol) over candles.
    Returns (cvd_total, cvd_last5) — last5 shows recent pressure shift.
    """
    def delta(c: dict) -> float:
        buy  = c.get("taker_buy_vol", 0.0)
        sell = c.get("volume", 0.0) - buy
        return buy - sell

    cvd_total = sum(delta(c) for c in candles)
    cvd_last5 = sum(delta(c) for c in candles[-5:])
    return cvd_total, cvd_last5


def _fetch_window_open_price(symbol: str, window_start_ts: int) -> float | None:
    """
    Fetch the Binance open price at the exact start of the 5-minute window.
    window_start_ts is a Unix timestamp (seconds).
    Returns the open price of the 1m candle that opened at that timestamp, or None.
    """
    try:
        resp = requests.get(
            f"{BINANCE_API}/api/v3/klines",
            params={
                "symbol":    symbol,
                "interval":  "1m",
                "startTime": window_start_ts * 1000,  # Binance expects ms
                "limit":     1,
            },
            timeout=6,
        )
        resp.raise_for_status()
        rows = resp.json()
        if rows:
            return float(rows[0][1])  # open price
    except Exception:
        pass
    return None


def analyze_asset_5m(binance_symbol: str, window_start_ts: int = 0) -> dict:
    """
    Analyze short-term BTC/ETH/SOL/XRP momentum for a 5-minute prediction.

    Parameters
    ----------
    binance_symbol  : str  e.g. "BTCUSDT"
    window_start_ts : int  Unix timestamp (seconds) when THIS market window opened.

    Returns
    -------
    dict with keys:
        prob_up          : float   estimated probability price ends HIGHER
        prob_down        : float   1 - prob_up
        current_price    : float | None
        rsi              : float | None   RSI(14) on 1m closes
        macd_hist        : float | None   MACD(3/15/3) histogram — positive = bullish
        macd_cross       : str | None     "BULL" | "BEAR" | None  (fresh crossover)
        vwap             : float | None   volume-weighted average price
        vwap_pos         : str            "ABOVE" | "BELOW" | "AT"
        cvd              : float          cumulative volume delta (buy - sell pressure)
        cvd_last5        : float          CVD of last 5 candles (recent pressure shift)
        cvd_divergence   : str | None     "BULL" | "BEAR" | None  (price vs CVD split)
        momentum_5m      : float   % change over last 5 candles (pre-window context)
        momentum_1m      : float   % change of last candle
        window_momentum  : float   % change since THIS window opened (key signal)
        volume_ratio     : float   current candle volume / 10-period average
        greens           : int     # of green candles in last 5
        funding_rate     : float | None  perpetual futures funding rate (contrarian sentiment)
        trend            : str     "UP" | "DOWN" | "NEUTRAL"
        candles          : int     number of candles used
    """
    cache_key = f"{binance_symbol}:{window_start_ts}"
    cached = _CACHE.get(cache_key)
    if cached and (_time_module.time() - cached["ts"]) < CACHE_TTL:
        return cached["result"]

    candles = _fetch_klines(binance_symbol, limit=30)   # 30 for MACD headroom

    _empty = {
        "prob_up": 0.50, "prob_down": 0.50,
        "current_price": None, "rsi": None,
        "macd_hist": None, "macd_cross": None,
        "vwap": None, "vwap_pos": "AT",
        "cvd": 0.0, "cvd_last5": 0.0, "cvd_divergence": None,
        "momentum_5m": 0.0, "momentum_1m": 0.0, "window_momentum": 0.0,
        "volume_ratio": 1.0, "greens": 0, "trend": "NEUTRAL", "candles": 0,
    }
    if len(candles) < 20:
        _CACHE[cache_key] = {"ts": _time_module.time(), "result": _empty}
        return _empty

    closes  = [c["close"]  for c in candles]
    volumes = [c["volume"] for c in candles]
    last5   = candles[-5:]

    current_price = closes[-1]

    # ── Momentum ───────────────────────────────────────────────────────────────
    mom_5m = (closes[-1] - closes[-6]) / closes[-6] if closes[-6] != 0 else 0.0
    mom_1m = (closes[-1] - closes[-2]) / closes[-2] if closes[-2] != 0 else 0.0

    # ── Funding rate (perpetual futures sentiment) ─────────────────────────────
    # Uses the USDT-margined futures symbol (same base, add USDT suffix)
    funding_rate = fetch_funding_rate(binance_symbol)

    window_momentum = 0.0
    if window_start_ts > 0:
        win_open = _fetch_window_open_price(binance_symbol, window_start_ts)
        if win_open and win_open > 0:
            window_momentum = (current_price - win_open) / win_open

    # ── RSI(14) ────────────────────────────────────────────────────────────────
    rsi = _rsi(closes)

    # ── MACD(3, 15, 3) ─────────────────────────────────────────────────────────
    _, _, macd_hist, macd_hist_prev = _macd(closes, fast=3, slow=15, signal=3)

    # Crossover: histogram just flipped sign this candle
    macd_cross = None
    if macd_hist is not None and macd_hist_prev is not None:
        if macd_hist > 0 and macd_hist_prev <= 0:
            macd_cross = "BULL"   # crossed above zero — strong buy signal
        elif macd_hist < 0 and macd_hist_prev >= 0:
            macd_cross = "BEAR"   # crossed below zero — strong sell signal

    # ── VWAP ──────────────────────────────────────────────────────────────────
    vwap = _vwap(candles[-20:])   # rolling 20-min VWAP
    vwap_pos = "AT"
    if vwap and current_price:
        diff_pct = (current_price - vwap) / vwap
        if   diff_pct >  0.001: vwap_pos = "ABOVE"
        elif diff_pct < -0.001: vwap_pos = "BELOW"

    # ── CVD divergence ─────────────────────────────────────────────────────────
    # CVD divergence (strongest signal per article — 63% win rate):
    #   price falling but CVD rising  → buyers absorbing, expect reversal UP
    #   price rising but CVD falling  → sellers absorbing, expect reversal DOWN
    cvd_total, cvd_last5 = _cvd(candles)

    cvd_divergence = None
    price_move = mom_5m   # use 5m price change as reference
    if len(candles) >= 10:
        cvd_prev = sum(
            (c.get("taker_buy_vol", 0) - (c["volume"] - c.get("taker_buy_vol", 0)))
            for c in candles[-10:-5]
        )
        cvd_recent = cvd_last5
        cvd_rising = cvd_recent > cvd_prev
        cvd_falling = cvd_recent < cvd_prev

        if price_move < -0.001 and cvd_rising:
            cvd_divergence = "BULL"   # selling price but buyers dominating
        elif price_move > 0.001 and cvd_falling:
            cvd_divergence = "BEAR"   # rising price but sellers dominating

    # ── Volume ratio ──────────────────────────────────────────────────────────
    avg_vol   = sum(volumes[-10:]) / 10
    vol_ratio = volumes[-1] / avg_vol if avg_vol > 0 else 1.0

    greens = sum(1 for c in last5 if c["close"] >= c["open"])

    # ── Probability model ──────────────────────────────────────────────────────
    prob_up = 0.50

    # 1. Within-window momentum (strongest — token price tracks it in real time)
    if   window_momentum >  0.003:  prob_up += 0.14
    elif window_momentum >  0.002:  prob_up += 0.10
    elif window_momentum >  0.001:  prob_up += 0.05
    elif window_momentum < -0.003:  prob_up -= 0.14
    elif window_momentum < -0.002:  prob_up -= 0.10
    elif window_momentum < -0.001:  prob_up -= 0.05

    # 2. MACD(3/15/3) — crossover is the key signal (60% win rate per article)
    if macd_cross == "BULL":
        prob_up += 0.08
    elif macd_cross == "BEAR":
        prob_up -= 0.08
    elif macd_hist is not None:
        # No crossover, but histogram direction still adds mild weight
        if   macd_hist >  0.0005: prob_up += 0.04
        elif macd_hist >  0:      prob_up += 0.02
        elif macd_hist < -0.0005: prob_up -= 0.04
        elif macd_hist <  0:      prob_up -= 0.02

    # 3. CVD divergence — strongest reversal signal (63% win rate per article)
    if cvd_divergence == "BULL":
        prob_up += 0.07
    elif cvd_divergence == "BEAR":
        prob_up -= 0.07
    elif cvd_last5 > 0:
        prob_up += 0.03   # net buying in last 5 candles
    elif cvd_last5 < 0:
        prob_up -= 0.03

    # 4. VWAP position — price location relative to fair value (59% win rate)
    if   vwap_pos == "ABOVE": prob_up += 0.03
    elif vwap_pos == "BELOW": prob_up -= 0.03

    # 5. Pre-window 5-minute momentum (trend context)
    if   mom_5m >  0.003:  prob_up += 0.06
    elif mom_5m >  0.0015: prob_up += 0.04
    elif mom_5m >  0.0005: prob_up += 0.02
    elif mom_5m < -0.003:  prob_up -= 0.06
    elif mom_5m < -0.0015: prob_up -= 0.04
    elif mom_5m < -0.0005: prob_up -= 0.02

    # 6. Last-candle momentum
    if   mom_1m >  0.002:  prob_up += 0.04
    elif mom_1m >  0.0008: prob_up += 0.02
    elif mom_1m < -0.002:  prob_up -= 0.04
    elif mom_1m < -0.0008: prob_up -= 0.02

    # 7. RSI mean-reversion (blocks over-extension)
    if rsi is not None:
        if   rsi >= 78: prob_up -= 0.10
        elif rsi >= 65: prob_up -= 0.04
        elif rsi <= 22: prob_up += 0.10
        elif rsi <= 35: prob_up += 0.04

    # 8. Green/red candle pattern
    if   greens >= 5: prob_up += 0.04
    elif greens >= 4: prob_up += 0.02
    elif greens <= 0: prob_up -= 0.04
    elif greens <= 1: prob_up -= 0.02

    # 9. Volume confirmation of direction
    if vol_ratio > 1.8:
        if   mom_5m > 0: prob_up += 0.02
        elif mom_5m < 0: prob_up -= 0.02

    # 10. Funding rate — contrarian sentiment signal
    # Positive funding = longs overextended, correction likely → bearish
    # Negative funding = shorts overextended, squeeze likely   → bullish
    if funding_rate is not None:
        if   funding_rate >  0.0010: prob_up -= 0.06   # very overextended longs
        elif funding_rate >  0.0005: prob_up -= 0.03
        elif funding_rate < -0.0010: prob_up += 0.06   # very overextended shorts
        elif funding_rate < -0.0005: prob_up += 0.03

    # Clamp to [0.20, 0.80]
    prob_up   = max(0.20, min(0.80, prob_up))
    prob_down = round(1.0 - prob_up, 4)
    prob_up   = round(prob_up, 4)

    if   prob_up > 0.56: trend = "UP"
    elif prob_up < 0.44: trend = "DOWN"
    else:                trend = "NEUTRAL"

    result = {
        "prob_up":         prob_up,
        "prob_down":       prob_down,
        "current_price":   current_price,
        "rsi":             round(rsi, 1) if rsi is not None else None,
        "macd_hist":       round(macd_hist, 6) if macd_hist is not None else None,
        "macd_cross":      macd_cross,
        "vwap":            round(vwap, 4) if vwap else None,
        "vwap_pos":        vwap_pos,
        "cvd":             round(cvd_total, 2),
        "cvd_last5":       round(cvd_last5, 2),
        "cvd_divergence":  cvd_divergence,
        "momentum_5m":     round(mom_5m * 100, 4),
        "momentum_1m":     round(mom_1m * 100, 4),
        "window_momentum": round(window_momentum * 100, 4),
        "volume_ratio":    round(vol_ratio, 2),
        "greens":          greens,
        "funding_rate":    round(funding_rate * 100, 4) if funding_rate is not None else None,
        "trend":           trend,
        "candles":         len(candles),
    }
    _CACHE[cache_key] = {"ts": _time_module.time(), "result": result}
    return result
