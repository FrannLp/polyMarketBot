"""
market_scraper.py
=================
Consulta Polymarket Events API (tag_slug=weather) y filtra mercados de
temperatura tipo: "Will the highest temperature in [City] be X°C or higher..."
"""

import re
import requests
from datetime import datetime, timezone
from config import GAMMA_API

# Coordenadas de ciudades
CITY_COORDS = {
    "wellington":     (-41.2865, 174.7762),
    "tokyo":          (35.6895, 139.6917),
    "buenos aires":   (-34.6037, -58.3816),
    "sao paulo":      (-23.5505, -46.6333),
    "new york":       (40.7128, -74.0060),
    "london":         (51.5074, -0.1278),
    "paris":          (48.8566, 2.3522),
    "sydney":         (-33.8688, 151.2093),
    "miami":          (25.7617, -80.1918),
    "chicago":        (41.8781, -87.6298),
    "los angeles":    (34.0522, -118.2437),
    "toronto":        (43.6532, -79.3832),
    "berlin":         (52.5200, 13.4050),
    "madrid":         (40.4168, -3.7038),
    "rome":           (41.9028, 12.4964),
    "seoul":          (37.5665, 126.9780),
    "singapore":      (1.3521, 103.8198),
    "cape town":      (-33.9249, 18.4241),
    "mexico city":    (19.4326, -99.1332),
    "bangkok":        (13.7563, 100.5018),
    "istanbul":       (41.0082, 28.9784),
    "dubai":          (25.2048, 55.2708),
    "mumbai":         (19.0760, 72.8777),
    "moscow":         (55.7558, 37.6176),
    "amsterdam":      (52.3676, 4.9041),
    "santiago":       (-33.4489, -70.6693),
    "lima":           (-12.0464, -77.0428),
    "bogota":         (4.7110, -74.0721),
}

# Fahrenheit a Celsius
def _f_to_c(f: float) -> float:
    return (f - 32) * 5 / 9

# Regex principal: "highest temperature in City be X°C/°F or higher/lower on Date"
RE_CITY_TEMP = re.compile(
    r"highest temperature in (?P<city>[a-z ,]+?) be "
    r"(?P<temp>-?\d+(?:\.\d+)?)\s*[°]?\s*(?P<unit>[CF])?\s*"
    r"(?P<cond>or higher|or lower|or above|or below|on |\?)",
    re.IGNORECASE,
)


def _parse_market(m: dict) -> dict | None:
    question = m.get("question") or ""
    match = RE_CITY_TEMP.search(question)
    if not match:
        return None

    city_raw = match.group("city").strip().lower().rstrip(",")
    coords = None
    matched_city = city_raw
    for known, coord in CITY_COORDS.items():
        if known in city_raw or city_raw in known:
            coords = coord
            matched_city = known
            break
    if not coords:
        return None

    temp = float(match.group("temp"))
    unit = (match.group("unit") or "C").upper()
    if unit == "F":
        temp = _f_to_c(temp)

    cond_raw = (match.group("cond") or "").lower()
    # "or higher/above" → gte,  "or lower/below" → lte, exact → gte por defecto
    if "higher" in cond_raw or "above" in cond_raw:
        condition = "gte"
    elif "lower" in cond_raw or "below" in cond_raw:
        condition = "lte"
    else:
        condition = "gte"   # mercados exactos los tratamos como >=

    # Precio YES: lastTradePrice o mid entre bestBid/bestAsk
    last  = m.get("lastTradePrice")
    bid   = m.get("bestBid")
    ask   = m.get("bestAsk")
    try:
        if last and float(last) > 0:
            price_yes = float(last)
        elif bid and ask and float(bid) > 0 and float(ask) > 0:
            price_yes = (float(bid) + float(ask)) / 2
        else:
            # fallback a outcomePrices
            prices = m.get("outcomePrices") or ["0.5", "0.5"]
            price_yes = float(prices[0]) if float(prices[0]) > 0 else 0.5
    except Exception:
        price_yes = 0.5
    price_no = round(1.0 - price_yes, 4)

    # Fecha de resolución
    end_str = m.get("endDate") or m.get("endDateIso") or ""
    end_date = None
    days_to_resolve = None
    try:
        end_date = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
        days_to_resolve = (end_date - datetime.now(timezone.utc)).days
    except Exception:
        pass

    return {
        "market_id":      m.get("conditionId") or m.get("id") or "",
        "slug":           m.get("slug") or "",
        "question":       question,
        "city":           matched_city,
        "lat":            coords[0],
        "lon":            coords[1],
        "temp_threshold": round(temp, 1),
        "condition":      condition,
        "price_yes":      price_yes,
        "price_no":       price_no,
        "end_date":       end_date,
        "days_to_resolve": days_to_resolve,
        "volume":         float(m.get("volume") or 0),
        "active":         m.get("active", True),
        "closed":         m.get("closed", False),
    }


def fetch_weather_markets(max_pages: int = 5) -> list[dict]:
    """
    Descarga eventos weather de Polymarket y extrae mercados de temperatura de ciudades.
    Retorna solo mercados activos con fecha futura.
    """
    now = datetime.now(timezone.utc)
    parsed = []
    seen = set()

    for page in range(max_pages):
        offset = page * 200
        try:
            resp = requests.get(
                f"{GAMMA_API}/events",
                params={"limit": 200, "offset": offset, "active": "true",
                        "closed": "false", "tag_slug": "weather"},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            break

        events = data if isinstance(data, list) else data.get("events", data.get("data", []))
        if not events:
            break

        for event in events:
            for m in event.get("markets", []):
                if m.get("closed") or not m.get("active", True):
                    continue

                result = _parse_market(m)
                if not result:
                    continue
                if result["market_id"] in seen:
                    continue

                days = result["days_to_resolve"]
                if days is None or days < 0 or days > 7:
                    continue

                seen.add(result["market_id"])
                parsed.append(result)

        # Si ya tenemos suficientes, parar
        if len(parsed) >= 100:
            break

    parsed.sort(key=lambda x: x["volume"], reverse=True)
    return parsed
