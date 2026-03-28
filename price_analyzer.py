"""
price_analyzer.py
=================
Obtiene precio real + indicadores técnicos de CoinGecko (sin API key).
Calcula SMA, RSI, tendencia y genera probabilidad estimada para cada mercado.
Solo usa: requests + math (sin numpy, pandas ni librerías extra).
"""

import math
import time
import requests
from datetime import datetime, timezone

COINGECKO_BASE = "https://api.coingecko.com/api/v3"

# Cache simple en memoria para no spamear CoinGecko
_cache: dict[str, tuple[float, dict]] = {}
CACHE_TTL = 300  # 5 minutos


def _cached(key: str) -> dict | None:
    if key in _cache:
        ts, data = _cache[key]
        if time.time() - ts < CACHE_TTL:
            return data
    return None


def _store(key: str, data: dict):
    _cache[key] = (time.time(), data)


# ─── Indicadores técnicos en Python puro ─────────────────────────────────────

def _sma(prices: list[float], n: int) -> float:
    if len(prices) < n:
        return prices[-1] if prices else 0.0
    return sum(prices[-n:]) / n


def _rsi(prices: list[float], n: int = 14) -> float:
    if len(prices) < n + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(prices)):
        diff = prices[i] - prices[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[-n:]) / n
    avg_loss = sum(losses[-n:]) / n
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def _volatility(prices: list[float], n: int = 14) -> float:
    """Desviación estándar de retornos logarítmicos."""
    if len(prices) < 2:
        return 0.02
    returns = [math.log(prices[i] / prices[i - 1]) for i in range(1, len(prices)) if prices[i - 1] > 0]
    if not returns:
        return 0.02
    mu = sum(returns) / len(returns)
    variance = sum((r - mu) ** 2 for r in returns) / len(returns)
    return math.sqrt(variance)


def _normal_cdf(x: float) -> float:
    """Aproximación de la CDF normal estándar (sin scipy)."""
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


# ─── CoinGecko API ───────────────────────────────────────────────────────────

def _get_ohlc(asset_id: str, days: int = 14) -> list[float]:
    """Retorna lista de precios de cierre (OHLC → close)."""
    key = f"ohlc_{asset_id}_{days}"
    cached = _cached(key)
    if cached:
        return cached

    try:
        resp = requests.get(
            f"{COINGECKO_BASE}/coins/{asset_id}/ohlc",
            params={"vs_currency": "usd", "days": days},
            timeout=10,
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
        # OHLC: [timestamp, open, high, low, close]
        closes = [candle[4] for candle in data if len(candle) >= 5]
        _store(key, closes)
        return closes
    except Exception:
        return []


def _get_current_price(asset_id: str) -> float | None:
    key = f"price_{asset_id}"
    cached = _cached(key)
    if cached:
        return cached.get("price")

    try:
        resp = requests.get(
            f"{COINGECKO_BASE}/simple/price",
            params={"ids": asset_id, "vs_currencies": "usd",
                    "include_24hr_change": "true"},
            timeout=10,
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json().get(asset_id, {})
        result = {
            "price":      data.get("usd", 0),
            "change_24h": data.get("usd_24h_change", 0),
        }
        _store(key, result)
        return result["price"]
    except Exception:
        return None


def _get_price_and_change(asset_id: str) -> tuple[float, float]:
    key = f"price_{asset_id}"
    cached = _cached(key)
    if cached:
        return cached.get("price", 0), cached.get("change_24h", 0)

    try:
        resp = requests.get(
            f"{COINGECKO_BASE}/simple/price",
            params={"ids": asset_id, "vs_currencies": "usd",
                    "include_24hr_change": "true"},
            timeout=10,
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json().get(asset_id, {})
        result = {
            "price":      data.get("usd", 0),
            "change_24h": data.get("usd_24h_change", 0),
        }
        _store(key, result)
        return result["price"], result["change_24h"]
    except Exception:
        return 0.0, 0.0


# ─── Análisis principal ───────────────────────────────────────────────────────

def analyze_crypto_market(market: dict) -> dict | None:
    """
    Analiza un mercado crypto y devuelve probabilidad estimada + indicadores.

    market_type:
      "above_below" → condition = "above" | "below", price_target = float
      "range"       → condition = "range",  price_lo + price_hi
      "direction"   → condition = "up" | "down"
    """
    asset_id     = market["asset_id"]
    market_type  = market["market_type"]
    days         = market.get("days_to_resolve") or 1

    current_price, change_24h = _get_price_and_change(asset_id)
    if not current_price:
        return None

    closes = _get_ohlc(asset_id, days=14)
    if not closes:
        closes = [current_price]

    # Añadir precio actual como último punto
    closes = closes + [current_price]

    sma7  = _sma(closes, 7)
    sma20 = _sma(closes, 20)
    rsi   = _rsi(closes, 14)
    vol   = _volatility(closes, 14)

    # Tendencia: -1 (bajista) a +1 (alcista)
    trend_score = 0.0
    if current_price > sma7:
        trend_score += 0.3
    if current_price > sma20:
        trend_score += 0.3
    if sma7 > sma20:
        trend_score += 0.2
    if change_24h > 0:
        trend_score += 0.1
    if rsi > 50:
        trend_score += 0.1
    # trend_score: 0.0 = muy bajista, 1.0 = muy alcista

    # Proyección de precio para el día de resolución
    # Usamos drift simple: precio_esperado ≈ actual * (1 + daily_return * days)
    daily_return = change_24h / 100 / max(days, 1) if days > 0 else 0
    expected_price = current_price * (1 + daily_return * days)

    # Volatilidad proyectada al horizonte (vol diaria * sqrt(days))
    vol_horizon = vol * math.sqrt(max(days, 1))

    # ── Calcular probabilidad según tipo de mercado ──

    if market_type == "above_below":
        target = market["price_target"]
        if vol_horizon < 0.001:
            vol_horizon = 0.05  # fallback mínimo

        # Z-score: cuántas desviaciones estándar está target del expected
        z = (math.log(target / expected_price)) / vol_horizon
        prob_above = 1 - _normal_cdf(z)
        prob_below = _normal_cdf(z)

        if market["condition"] == "above":
            prob_yes = prob_above
        else:
            prob_yes = prob_below

        confidence = _confidence_level(prob_yes, rsi, trend_score, vol_horizon)

        return {
            "asset_id":       asset_id,
            "current_price":  round(current_price, 4),
            "expected_price": round(expected_price, 4),
            "change_24h":     round(change_24h, 2),
            "sma7":           round(sma7, 4),
            "sma20":          round(sma20, 4),
            "rsi":            round(rsi, 1),
            "trend_score":    round(trend_score, 2),
            "vol_horizon":    round(vol_horizon, 4),
            "prob_yes":       round(prob_yes, 4),
            "confidence":     confidence,
        }

    elif market_type == "range":
        lo = market["price_lo"]
        hi = market["price_hi"]
        if vol_horizon < 0.001:
            vol_horizon = 0.05

        z_lo = math.log(lo / expected_price) / vol_horizon
        z_hi = math.log(hi / expected_price) / vol_horizon
        prob_in_range = _normal_cdf(z_hi) - _normal_cdf(z_lo)
        prob_in_range = max(0.0, min(1.0, prob_in_range))

        confidence = _confidence_level(prob_in_range, rsi, trend_score, vol_horizon)

        return {
            "asset_id":       asset_id,
            "current_price":  round(current_price, 4),
            "expected_price": round(expected_price, 4),
            "change_24h":     round(change_24h, 2),
            "sma7":           round(sma7, 4),
            "sma20":          round(sma20, 4),
            "rsi":            round(rsi, 1),
            "trend_score":    round(trend_score, 2),
            "vol_horizon":    round(vol_horizon, 4),
            "prob_yes":       round(prob_in_range, 4),
            "confidence":     confidence,
        }

    elif market_type == "direction":
        # Dirección: usamos trend_score como base de probabilidad de subida
        prob_up = 0.3 + trend_score * 0.4  # rango 0.3 - 0.7
        # Ajuste por RSI
        if rsi > 70:
            prob_up -= 0.05  # sobrecomprado, más probable corrección
        elif rsi < 30:
            prob_up += 0.05  # sobrevendido, posible rebote
        prob_up = max(0.1, min(0.9, prob_up))

        prob_yes = prob_up if market["condition"] == "up" else (1 - prob_up)
        confidence = _confidence_level(prob_yes, rsi, trend_score, vol_horizon)

        return {
            "asset_id":       asset_id,
            "current_price":  round(current_price, 4),
            "expected_price": round(expected_price, 4),
            "change_24h":     round(change_24h, 2),
            "sma7":           round(sma7, 4),
            "sma20":          round(sma20, 4),
            "rsi":            round(rsi, 1),
            "trend_score":    round(trend_score, 2),
            "vol_horizon":    round(vol_horizon, 4),
            "prob_yes":       round(prob_yes, 4),
            "confidence":     confidence,
        }

    return None


def _confidence_level(prob: float, rsi: float, trend: float, vol: float) -> str:
    """
    HIGH:   señal clara (prob > 0.70 o < 0.30) + indicadores alineados
    MEDIUM: señal moderada
    LOW:    señal débil o contradictoria
    """
    extreme_prob = prob > 0.68 or prob < 0.32
    indicators_agree = (rsi > 55 and trend > 0.6) or (rsi < 45 and trend < 0.4)
    low_vol = vol < 0.15  # mercados muy volátiles = menos confianza

    if extreme_prob and indicators_agree and low_vol:
        return "HIGH"
    elif extreme_prob or indicators_agree:
        return "MEDIUM"
    else:
        return "LOW"
