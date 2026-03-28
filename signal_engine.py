"""
signal_engine.py
================
Combina datos del mercado + clima real + copy trading.
Genera señales de apuesta con edge calculado.
Filtros estrictos para minimo riesgo.
"""

from datetime import datetime, timezone, date
from concurrent.futures import ThreadPoolExecutor, as_completed
from market_scraper  import fetch_weather_markets
from weather_analyzer import analyze_temperature
from trader_tracker   import get_copy_signals
from config import MIN_EDGE, BET_SIZE

MAX_MARKETS_TO_ANALYZE = 40   # solo top 40 por volumen


def kelly_fraction(prob: float, odds: float, fraction: float = 0.25) -> float:
    """
    Kelly fraccionario (1/4 Kelly por seguridad).
    prob  = probabilidad real de ganar (0-1)
    odds  = pago por $1 apostado (ej: si precio=0.3 -> odds = 1/0.3 - 1 = 2.33)
    """
    if odds <= 0 or prob <= 0:
        return 0.0
    b = odds
    q = 1 - prob
    kelly = (b * prob - q) / b
    return max(0.0, kelly * fraction)


def generate_signals(verbose: bool = True) -> list[dict]:
    """
    Flujo completo:
    1. Scrapear mercados de temperatura
    2. Analizar clima real con Open-Meteo
    3. Calcular edge
    4. Enriquecer con copy trading
    5. Filtrar y rankear señales

    Retorna lista de señales ordenadas por edge.
    """
    if verbose:
        print("[signal_engine] Buscando mercados de temperatura en Polymarket...")

    markets = fetch_weather_markets(max_pages=3)

    if verbose:
        print(f"[signal_engine] {len(markets)} mercados encontrados, analizando top {MAX_MARKETS_TO_ANALYZE}...")

    if not markets:
        return []

    # Solo top N por volumen
    markets = markets[:MAX_MARKETS_TO_ANALYZE]

    # Deduplicar llamadas al clima: misma ciudad+fecha = mismo resultado
    weather_cache: dict[str, dict] = {}

    def get_weather(mkt):
        end_date = mkt.get("end_date")
        if not end_date:
            return mkt, None
        key = f"{mkt['lat']},{mkt['lon']},{end_date.date().isoformat()}"
        if key not in weather_cache:
            weather_cache[key] = analyze_temperature(
                mkt["lat"], mkt["lon"],
                mkt["temp_threshold"], mkt["condition"],
                end_date.date(),
            )
        return mkt, weather_cache[key]

    # Obtener clima en paralelo (max 8 threads)
    weather_results = {}
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(get_weather, m): m for m in markets}
        done = 0
        for future in as_completed(futures):
            mkt, w = future.result()
            weather_results[mkt["market_id"]] = w
            done += 1
            if verbose and done % 10 == 0:
                print(f"[signal_engine] Clima: {done}/{len(markets)}")

    if verbose:
        print(f"[signal_engine] Clima obtenido para {len(weather_results)} mercados")

    # Obtener señales de copy trading
    copy_signals_map: dict[str, dict] = {}
    try:
        copy_sigs = get_copy_signals(max_traders=10)
        for cs in copy_sigs:
            copy_signals_map[cs["market_id"]] = cs
        if verbose:
            print(f"[signal_engine] {len(copy_signals_map)} señales de copy trading")
    except Exception as e:
        if verbose:
            print(f"[signal_engine] Copy trading no disponible: {e}")

    signals = []
    now = datetime.now(timezone.utc)

    for mkt in markets:
        # Solo mercados que resuelven en <= 7 días
        days = mkt.get("days_to_resolve")
        if days is None or days < 0 or days > 7:
            continue

        end_date = mkt.get("end_date")
        if not end_date:
            continue

        target_date = end_date.date()

        # Obtener clima del cache paralelo
        weather = weather_results.get(mkt["market_id"])
        if not weather:
            continue

        prob_real = weather["prob_real"]
        confidence = weather["confidence"]

        # Determinar la mejor apuesta: YES o NO
        price_yes = mkt["price_yes"]
        price_no  = mkt["price_no"]

        # Edge = diferencia entre probabilidad real y precio del mercado
        edge_yes = prob_real - price_yes
        edge_no  = (1 - prob_real) - price_no

        best_side  = None
        best_edge  = 0.0
        best_price = 0.5

        if edge_yes >= edge_no and edge_yes > 0:
            best_side  = "YES"
            best_edge  = edge_yes
            best_price = price_yes
        elif edge_no > edge_yes and edge_no > 0:
            best_side  = "NO"
            best_edge  = edge_no
            best_price = price_no

        if best_side is None or best_edge < MIN_EDGE:
            continue

        # Filtrar mercados con precios extremos (sin liquidez real)
        if best_price < 0.05 or best_price > 0.95:
            continue

        # Probabilidad de ganar según el lado elegido
        prob_win = prob_real if best_side == "YES" else (1 - prob_real)

        # Solo apostar si hay probabilidad alta
        if prob_win < 0.65:
            continue

        # Solo mercados con confianza MEDIUM o HIGH
        if confidence == "LOW":
            continue

        # Calcular Kelly
        odds = (1.0 / best_price) - 1.0
        kelly = kelly_fraction(prob_win, odds)

        # Check copy trading
        copy_info = copy_signals_map.get(mkt["market_id"])
        copy_bonus = 0.0
        copy_aligned = False
        if copy_info:
            if copy_info["copy_side"] == best_side:
                copy_aligned = True
                copy_bonus = 0.03 * copy_info["consensus_pct"]  # bonus hasta +3%

        # Score final
        score = best_edge + copy_bonus + (0.01 if confidence == "HIGH" else 0)

        signals.append({
            # Mercado
            "market_id":     mkt["market_id"],
            "slug":          mkt["slug"],
            "question":      mkt["question"],
            "city":          mkt["city"].title(),
            "temp_threshold": mkt["temp_threshold"],
            "condition":     mkt["condition"],
            "days_to_resolve": days,
            "target_date":   target_date.isoformat(),
            "volume":        mkt["volume"],

            # Precios del mercado
            "price_yes":     price_yes,
            "price_no":      price_no,

            # Analisis clima
            "prob_real":     prob_real,
            "temp_avg_max":  weather["temp_avg_max"],
            "temp_avg_min":  weather["temp_avg_min"],
            "models_agree":  weather["models_agree"],
            "models_total":  weather["models_total"],
            "confidence":    confidence,

            # Señal
            "best_side":     best_side,
            "best_edge":     round(best_edge, 4),
            "prob_win":      round(prob_win, 4),
            "kelly":         round(kelly, 4),
            "bet_size":      BET_SIZE,   # fijo $0.50

            # Copy trading
            "copy_aligned":  copy_aligned,
            "copy_info":     copy_info,

            # Score para ranking
            "score":         round(score, 4),
        })

    # Ordenar: primero copy aligned + HIGH confidence, luego por score
    signals.sort(
        key=lambda x: (
            int(x["copy_aligned"]),
            int(x["confidence"] == "HIGH"),
            x["score"],
        ),
        reverse=True,
    )

    return signals
