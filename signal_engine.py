"""
signal_engine.py
================
Generates weather betting signals with:
- 3 forecast sources (ECMWF, HRRR, METAR)
- Expected Value filter (min_ev from config)
- Fractional Kelly Criterion for position sizing
- Calibration-aware probability (sigma per city/source)
- Copy trading alignment
"""

import json
import os
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

from market_scraper  import fetch_weather_markets
from weather_analyzer import analyze_temperature
from trader_tracker   import get_copy_signals
from config import (
    MIN_EDGE,                    # legacy fallback
    WEATHER_MIN_EV,
    WEATHER_MAX_PRICE,
    WEATHER_MIN_VOLUME,
    WEATHER_KELLY_FRACTION,
    WEATHER_BET_SIZE,
    WEATHER_MAX_SLIPPAGE,
    GAMMA_API,
)

MAX_MARKETS_TO_ANALYZE = 50
CALIBRATION_FILE = os.path.join(os.path.dirname(__file__), "data", "calibration.json")


def _load_calibration() -> dict:
    """Load calibration data (MAE sigma per city/source) if available."""
    try:
        if os.path.exists(CALIBRATION_FILE):
            with open(CALIBRATION_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def calc_ev(p: float, price: float) -> float:
    """
    Expected Value per dollar bet.
    EV = p × (1/price − 1) − (1 − p)
    Positive EV = profitable in the long run.
    """
    if price <= 0 or price >= 1 or p <= 0:
        return 0.0
    return round(p * (1.0 / price - 1.0) - (1.0 - p), 4)


def calc_kelly(p: float, price: float, kelly_frac: float = None) -> float:
    """
    Fractional Kelly: what fraction of balance to bet.
    f* = (p × b − (1 − p)) / b  ×  kelly_fraction
    Returns fraction 0-1 (multiply by balance for dollar amount).
    """
    if kelly_frac is None:
        kelly_frac = WEATHER_KELLY_FRACTION
    if price <= 0 or price >= 1 or p <= 0:
        return 0.0
    b = 1.0 / price - 1.0      # net odds per dollar bet
    f = (p * b - (1.0 - p)) / b
    return round(min(max(0.0, f) * kelly_frac, 1.0), 4)


def generate_signals(verbose: bool = True) -> list[dict]:
    """
    Full pipeline:
    1. Fetch weather markets from Polymarket Gamma API
    2. Query 3 forecast sources in parallel (ECMWF, HRRR, METAR)
    3. Calculate EV and Kelly for each market
    4. Filter: EV >= WEATHER_MIN_EV, price <= WEATHER_MAX_PRICE, volume >= WEATHER_MIN_VOLUME
    5. Enrich with copy trading
    6. Sort by EV

    Returns list of signal dicts ordered by EV (best first).
    """
    if verbose:
        print("[signal_engine] Buscando mercados de temperatura en Polymarket...")

    markets = fetch_weather_markets(max_pages=3)

    if not markets:
        return []

    # Filter by volume and take top N
    markets = [m for m in markets if m.get("volume", 0) >= WEATHER_MIN_VOLUME]
    markets = markets[:MAX_MARKETS_TO_ANALYZE]

    if verbose:
        print(f"[signal_engine] {len(markets)} mercados (vol>={WEATHER_MIN_VOLUME:.0f}), analizando...")

    calibration = _load_calibration()

    # ── Weather analysis in parallel ──────────────────────────────────────────
    weather_cache: dict[str, dict] = {}

    def get_weather(mkt):
        end_date = mkt.get("end_date")
        if not end_date:
            return mkt, None
        horizon_h = mkt.get("days_to_resolve", 1) * 24
        key = f"{mkt['lat']},{mkt['lon']},{end_date.date().isoformat()}"
        if key not in weather_cache:
            weather_cache[key] = analyze_temperature(
                lat=mkt["lat"],
                lon=mkt["lon"],
                temp_threshold=mkt["temp_threshold"],
                condition=mkt["condition"],
                target_date=end_date.date(),
                city=mkt["city"],
                unit=mkt.get("unit", "C"),
                horizon_hours=horizon_h,
                calibration=calibration,
            )
        return mkt, weather_cache[key]

    weather_results: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(get_weather, m): m for m in markets}
        done = 0
        for future in as_completed(futures):
            mkt, w = future.result()
            weather_results[mkt["market_id"]] = w
            done += 1
            if verbose and done % 10 == 0:
                print(f"[signal_engine] Clima: {done}/{len(markets)}")

    if verbose:
        print(f"[signal_engine] Clima OK para {len(weather_results)} mercados")

    # ── Copy trading signals ───────────────────────────────────────────────────
    copy_signals_map: dict[str, dict] = {}
    try:
        for cs in get_copy_signals(max_traders=10):
            copy_signals_map[cs["market_id"]] = cs
        if verbose:
            print(f"[signal_engine] {len(copy_signals_map)} señales copy trading")
    except Exception as e:
        if verbose:
            print(f"[signal_engine] Copy trading no disponible: {e}")

    # ── Build signals ──────────────────────────────────────────────────────────
    signals = []
    now = datetime.now(timezone.utc)

    for mkt in markets:
        days = mkt.get("days_to_resolve")
        if days is None or days < 0:
            continue

        end_date = mkt.get("end_date")
        if not end_date:
            continue

        # Hours to resolution check
        hours_left = (end_date - now).total_seconds() / 3600
        from config import WEATHER_MIN_HOURS, WEATHER_MAX_HOURS
        if hours_left < WEATHER_MIN_HOURS or hours_left > WEATHER_MAX_HOURS:
            continue

        weather = weather_results.get(mkt["market_id"])
        if not weather:
            continue

        # Skip LOW confidence (no reliable forecast)
        if weather["confidence"] == "LOW":
            continue

        prob_real = weather["prob_real"]
        price_yes = mkt["price_yes"]
        price_no  = mkt["price_no"]

        # Skip overpriced buckets
        if price_yes > WEATHER_MAX_PRICE and price_no > WEATHER_MAX_PRICE:
            continue

        # ── EV for each side ──────────────────────────────────────────────────
        ev_yes = calc_ev(prob_real,       price_yes)
        ev_no  = calc_ev(1.0 - prob_real, price_no)

        best_side  = None
        best_ev    = 0.0
        best_price = 0.5
        prob_win   = 0.5

        if ev_yes >= ev_no and ev_yes > 0 and price_yes <= WEATHER_MAX_PRICE:
            best_side  = "YES"
            best_ev    = ev_yes
            best_price = price_yes
            prob_win   = prob_real
        elif ev_no > ev_yes and ev_no > 0 and price_no <= WEATHER_MAX_PRICE:
            best_side  = "NO"
            best_ev    = ev_no
            best_price = price_no
            prob_win   = 1.0 - prob_real

        # Filter: must meet min EV threshold
        if best_side is None or best_ev < WEATHER_MIN_EV:
            continue

        # Spread check (skip thin markets)
        spread = mkt.get("spread")
        if spread and spread > WEATHER_MAX_SLIPPAGE:
            continue

        # Must have reasonable win probability
        if prob_win < 0.55:
            continue

        # ── Kelly sizing ──────────────────────────────────────────────────────
        kelly_frac = calc_kelly(prob_win, best_price)

        # ── Edge (legacy compat) ──────────────────────────────────────────────
        best_edge = (prob_real - price_yes) if best_side == "YES" else ((1 - prob_real) - price_no)

        # ── Copy trading ──────────────────────────────────────────────────────
        copy_info    = copy_signals_map.get(mkt["market_id"])
        copy_bonus   = 0.0
        copy_aligned = False
        if copy_info and copy_info.get("copy_side") == best_side:
            copy_aligned = True
            copy_bonus   = 0.03 * copy_info.get("consensus_pct", 0)

        # ── Score ─────────────────────────────────────────────────────────────
        score = best_ev + copy_bonus + (0.01 if weather["confidence"] == "HIGH" else 0)

        signals.append({
            # Market
            "market_id":       mkt["market_id"],
            "slug":            mkt["slug"],
            "question":        mkt["question"],
            "city":            mkt["city"].title(),
            "unit":            mkt.get("unit", "C"),
            "temp_threshold":  mkt["temp_threshold"],
            "condition":       mkt["condition"],
            "days_to_resolve": days,
            "hours_left":      round(hours_left, 1),
            "target_date":     end_date.date().isoformat(),
            "end_date":        end_date,
            "volume":          mkt["volume"],
            "token_id_yes":    mkt.get("token_id_yes"),
            "token_id_no":     mkt.get("token_id_no"),
            "spread":          spread,

            # Market prices
            "price_yes":       price_yes,
            "price_no":        price_no,

            # Forecast analysis
            "prob_real":       prob_real,
            "temp_avg_max":    weather.get("temp_avg_max"),
            "temp_avg_min":    weather.get("temp_avg_min"),
            "primary_temp":    weather.get("primary_temp"),
            "primary_source":  weather.get("primary_source"),
            "models_agree":    weather["models_agree"],
            "models_total":    weather["models_total"],
            "confidence":      weather["confidence"],
            "sigma":           weather.get("sigma"),
            "ecmwf_temp":      (weather.get("ecmwf") or {}).get("temp_max"),
            "hrrr_temp":       (weather.get("hrrr") or {}).get("temp_max"),
            "metar_obs":       (weather.get("metar") or {}).get("temp_obs"),

            # Signal
            "best_side":       best_side,
            "best_edge":       round(best_edge, 4),
            "ev":              round(best_ev, 4),
            "prob_win":        round(prob_win, 4),
            "kelly_frac":      kelly_frac,

            # Copy trading
            "copy_aligned":    copy_aligned,
            "copy_info":       copy_info,

            # Score for ranking
            "score":           round(score, 4),
        })

    # Sort: copy aligned + HIGH first, then by EV
    signals.sort(
        key=lambda x: (int(x["copy_aligned"]), int(x["confidence"] == "HIGH"), x["ev"]),
        reverse=True,
    )

    return signals
