"""
trader_tracker.py
=================
Busca y copia las posiciones de los mejores traders de clima en Polymarket.
- Consulta leaderboard de Polymarket Data API
- Filtra traders con win rate alto en mercados de temperatura
- Copia sus posiciones abiertas como señales adicionales
"""

import requests
from config import DATA_API, GAMMA_API, TOP_TRADER_ADDRESSES


def fetch_top_weather_traders(limit: int = 20) -> list[dict]:
    """
    Intenta obtener los mejores traders de mercados de clima via API.
    Retorna lista de {address, username, profit, win_rate, positions}.
    """
    traders = []

    # 1. Intentar leaderboard de Polymarket
    try:
        url = f"{DATA_API}/leaderboard"
        params = {"limit": limit, "window": "all"}
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            entries = data if isinstance(data, list) else data.get("data", [])
            for entry in entries[:limit]:
                addr = entry.get("proxyWallet") or entry.get("address") or ""
                if addr:
                    traders.append({
                        "address":  addr,
                        "username": entry.get("name") or entry.get("username") or addr[:8],
                        "profit":   float(entry.get("pnl") or entry.get("profit") or 0),
                        "rank":     entry.get("rank", 999),
                    })
    except Exception:
        pass

    # 2. Agregar traders manuales configurados en .env
    for addr in TOP_TRADER_ADDRESSES:
        if not any(t["address"].lower() == addr.lower() for t in traders):
            traders.append({
                "address":  addr,
                "username": f"manual_{addr[:6]}",
                "profit":   0,
                "rank":     0,
            })

    return traders


def fetch_trader_positions(address: str) -> list[dict]:
    """
    Obtiene posiciones abiertas de un trader específico.
    Retorna lista de posiciones en mercados de temperatura.
    """
    positions = []

    # Intentar Data API
    endpoints = [
        f"{DATA_API}/positions?user={address}&sizeThreshold=0",
        f"{GAMMA_API}/positions?user={address}&active=true",
    ]

    raw_positions = []
    for url in endpoints:
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                raw_positions = data if isinstance(data, list) else data.get("positions", data.get("data", []))
                if raw_positions:
                    break
        except Exception:
            continue

    for pos in raw_positions:
        market_id   = pos.get("market") or pos.get("conditionId") or pos.get("marketId") or ""
        outcome     = (pos.get("outcome") or pos.get("side") or "YES").upper()
        size        = float(pos.get("size") or pos.get("amount") or 0)
        avg_price   = float(pos.get("avgPrice") or pos.get("price") or 0.5)
        question    = pos.get("title") or pos.get("question") or ""

        # Solo mercados de temperatura
        if not any(kw in question.lower() for kw in ["temperature", "°c", "°f", "degrees"]):
            continue

        positions.append({
            "market_id":  market_id,
            "question":   question,
            "outcome":    outcome,    # "YES" o "NO"
            "size":       size,
            "avg_price":  avg_price,
        })

    return positions


def get_copy_signals(max_traders: int = 10) -> list[dict]:
    """
    Genera señales de copy trading basadas en posiciones de top traders.
    Retorna lista de señales con mercado y dirección recomendada.
    """
    traders = fetch_top_weather_traders(limit=max_traders)
    if not traders:
        return []

    # Agregar por mercado: contar cuantos traders están en YES vs NO
    market_votes: dict[str, dict] = {}

    for trader in traders:
        positions = fetch_trader_positions(trader["address"])
        for pos in positions:
            mid = pos["market_id"]
            if not mid:
                continue
            if mid not in market_votes:
                market_votes[mid] = {
                    "market_id": mid,
                    "question":  pos["question"],
                    "yes_count": 0,
                    "no_count":  0,
                    "yes_size":  0.0,
                    "no_size":   0.0,
                    "traders":   [],
                }
            vote = market_votes[mid]
            if pos["outcome"] == "YES":
                vote["yes_count"] += 1
                vote["yes_size"]  += pos["size"]
            else:
                vote["no_count"]  += 1
                vote["no_size"]   += pos["size"]
            vote["traders"].append(trader["username"])

    signals = []
    for mid, vote in market_votes.items():
        total = vote["yes_count"] + vote["no_count"]
        if total == 0:
            continue

        # Señal si mayoría de traders coincide
        if vote["yes_count"] > vote["no_count"]:
            consensus = "YES"
            consensus_pct = vote["yes_count"] / total
        else:
            consensus = "NO"
            consensus_pct = vote["no_count"] / total

        if consensus_pct >= 0.6:   # al menos 60% de acuerdo
            signals.append({
                "market_id":     mid,
                "question":      vote["question"],
                "copy_side":     consensus,
                "consensus_pct": round(consensus_pct, 2),
                "trader_count":  total,
                "traders":       list(set(vote["traders"])),
            })

    signals.sort(key=lambda x: x["consensus_pct"], reverse=True)
    return signals
