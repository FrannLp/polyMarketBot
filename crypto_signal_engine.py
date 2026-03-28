"""
crypto_signal_engine.py
========================
Combina datos del mercado crypto + análisis de precio real (CoinGecko) + copy trading.
Genera señales de apuesta con edge calculado.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from crypto_scraper  import fetch_crypto_markets
from price_analyzer  import analyze_crypto_market
from trader_tracker  import get_copy_signals
from config import CRYPTO_MIN_EDGE as MIN_EDGE, CRYPTO_BET_SIZE as BET_SIZE

MAX_MARKETS_TO_ANALYZE = 60
MIN_VOLUME = 500  # ignorar mercados con menos de $500 volumen


def generate_crypto_signals(verbose: bool = True) -> list[dict]:
    """
    Flujo completo:
    1. Scrapear mercados crypto de Polymarket
    2. Analizar precio real con CoinGecko + indicadores técnicos
    3. Calcular edge
    4. Enriquecer con copy trading
    5. Filtrar y rankear señales
    """
    if verbose:
        print("[crypto_engine] Buscando mercados crypto en Polymarket...")

    markets = fetch_crypto_markets(max_pages=3)

    # Filtrar por volumen mínimo
    markets = [m for m in markets if m["volume"] >= MIN_VOLUME]

    if verbose:
        print(f"[crypto_engine] {len(markets)} mercados con volumen ≥ ${MIN_VOLUME}, analizando top {MAX_MARKETS_TO_ANALYZE}...")

    if not markets:
        return []

    markets = markets[:MAX_MARKETS_TO_ANALYZE]

    # Analizar precios en paralelo (con cache, no abusa de CoinGecko)
    analysis_results: dict[str, dict] = {}

    def analyze(mkt):
        return mkt, analyze_crypto_market(mkt)

    done = 0
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(analyze, m): m for m in markets}
        for future in as_completed(futures):
            mkt, result = future.result()
            if result:
                analysis_results[mkt["market_id"]] = result
            done += 1
            if verbose and done % 10 == 0:
                print(f"[crypto_engine] Análisis: {done}/{len(markets)}")

    if verbose:
        print(f"[crypto_engine] Análisis completado para {len(analysis_results)} mercados")

    # Copy trading
    copy_signals_map: dict[str, dict] = {}
    try:
        copy_sigs = get_copy_signals(max_traders=10)
        for cs in copy_sigs:
            copy_signals_map[cs["market_id"]] = cs
        if verbose:
            print(f"[crypto_engine] {len(copy_signals_map)} señales de copy trading")
    except Exception as e:
        if verbose:
            print(f"[crypto_engine] Copy trading no disponible: {e}")

    signals = []

    for mkt in markets:
        analysis = analysis_results.get(mkt["market_id"])
        if not analysis:
            continue

        prob_yes   = analysis["prob_yes"]
        prob_no    = 1.0 - prob_yes
        confidence = analysis["confidence"]
        price_yes  = mkt["price_yes"]
        price_no   = mkt["price_no"]

        # Filtrar precios extremos (mercados sin liquidez real)
        if price_yes < 0.05 or price_yes > 0.95:
            continue

        # Edge para cada lado
        edge_yes = prob_yes - price_yes
        edge_no  = prob_no  - price_no

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

        prob_win = prob_yes if best_side == "YES" else prob_no

        if prob_win < 0.60:
            continue

        if confidence == "LOW":
            continue

        # Copy trading bonus
        copy_info    = copy_signals_map.get(mkt["market_id"])
        copy_bonus   = 0.0
        copy_aligned = False
        if copy_info and copy_info["copy_side"] == best_side:
            copy_aligned = True
            copy_bonus   = 0.03 * copy_info["consensus_pct"]

        score = best_edge + copy_bonus + (0.01 if confidence == "HIGH" else 0)

        signals.append({
            # Mercado
            "market_id":      mkt["market_id"],
            "slug":           mkt["slug"],
            "question":       mkt["question"],
            "asset":          mkt["asset"].upper(),
            "market_type":    mkt["market_type"],
            "condition":      mkt["condition"],
            "price_target":   mkt["price_target"],
            "price_lo":       mkt["price_lo"],
            "price_hi":       mkt["price_hi"],
            "days_to_resolve": mkt.get("days_to_resolve", 0),
            "end_date":       mkt.get("end_date"),
            "volume":         mkt["volume"],

            # Precios mercado
            "price_yes":      price_yes,
            "price_no":       price_no,

            # Análisis técnico
            "current_price":  analysis["current_price"],
            "expected_price": analysis["expected_price"],
            "change_24h":     analysis["change_24h"],
            "rsi":            analysis["rsi"],
            "trend_score":    analysis["trend_score"],
            "sma7":           analysis["sma7"],
            "sma20":          analysis["sma20"],
            "prob_yes":       prob_yes,
            "confidence":     confidence,

            # Señal
            "best_side":      best_side,
            "best_edge":      round(best_edge, 4),
            "prob_win":       round(prob_win, 4),
            "bet_size":       BET_SIZE,

            # Copy trading
            "copy_aligned":   copy_aligned,
            "copy_info":      copy_info,

            # Score ranking
            "score":          round(score, 4),
        })

    signals.sort(
        key=lambda x: (
            int(x["copy_aligned"]),
            int(x["confidence"] == "HIGH"),
            x["score"],
        ),
        reverse=True,
    )

    return signals
