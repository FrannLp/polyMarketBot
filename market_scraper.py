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

# Airport coordinates (ICAO station) — Polymarket resolves against these exact stations
# Source: article by AlterEgo_eth + extended for other Polymarket cities
CITY_COORDS = {
    # US — °F (airport coords)
    "new york":     (40.7772,  -73.8726),   # KLGA LaGuardia
    "chicago":      (41.9742,  -87.9073),   # KORD O'Hare
    "miami":        (25.7959,  -80.2870),   # KMIA Miami Intl
    "dallas":       (32.8471,  -96.8518),   # KDAL Dallas Love
    "seattle":      (47.4502, -122.3088),   # KSEA Seattle-Tacoma
    "atlanta":      (33.6407,  -84.4277),   # KATL Hartsfield-Jackson
    "los angeles":  (33.9425, -118.4081),   # KLAX LAX
    # EU — °C (airport coords)
    "london":       (51.5048,    0.0495),   # EGLC London City
    "paris":        (48.9962,    2.5979),   # LFPG CDG
    "munich":       (48.3537,   11.7750),   # EDDM Munich
    "berlin":       (52.3667,   13.5033),   # EDDB Brandenburg
    "madrid":       (40.4722,   -3.5608),   # LEMD Barajas
    "rome":         (41.8003,   12.2388),   # LIRF Fiumicino
    "amsterdam":    (52.3105,    4.7683),   # EHAM Schiphol
    "ankara":       (40.1281,   32.9951),   # LTAC Esenboga
    "istanbul":     (41.2753,   28.7519),   # LTBA Ataturk
    "moscow":       (55.9726,   37.4146),   # UUEE Sheremetyevo
    # Asia — °C (airport coords)
    "seoul":        (37.4691,  126.4505),   # RKSI Incheon
    "tokyo":        (35.7647,  140.3864),   # RJTT Haneda
    "shanghai":     (31.1443,  121.8083),   # ZSPD Pudong
    "singapore":    ( 1.3502,  103.9940),   # WSSS Changi
    "lucknow":      (26.7606,   80.8893),   # VILK Chaudhary Charan Singh
    "tel aviv":     (32.0114,   34.8867),   # LLBG Ben Gurion
    "dubai":        (25.2532,   55.3657),   # OMDB Dubai Intl
    "mumbai":       (19.0887,   72.8679),   # VABB Chhatrapati Shivaji
    "bangkok":      (13.9125,  100.6068),   # VTBS Suvarnabhumi
    # Americas — °C (airport coords)
    "toronto":      (43.6772,  -79.6306),   # CYYZ Pearson
    "sao paulo":    (-23.4356, -46.4731),   # SBGR Guarulhos
    "buenos aires": (-34.8222, -58.5358),   # SAEZ Ezeiza
    "mexico city":  (19.4363,  -99.0721),   # MMMX Benito Juarez
    "bogota":       ( 4.7016,  -74.1469),   # SKBO El Dorado
    "lima":         (-12.0219, -77.1143),   # SPJC Jorge Chavez
    "santiago":     (-33.3930, -70.7858),   # SCEL Arturo Merino Benitez
    # Oceania — °C (airport coords)
    "wellington":   (-41.3272,  174.8052),  # NZWN Wellington
    "sydney":       (-33.9461,  151.1772),  # YSSY Kingsford Smith
    "cape town":    (-33.9648,   18.6017),  # FACT Cape Town Intl
}

# Unit per city: "F" for US, "C" for rest
CITY_UNITS: dict[str, str] = {
    "new york": "F", "chicago": "F", "miami": "F",
    "dallas": "F", "seattle": "F", "atlanta": "F", "los angeles": "F",
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

    # Extract CLOB token IDs (needed for live order execution)
    import json as _json
    raw_ids = m.get("clobTokenIds") or "[]"
    try:
        token_ids = _json.loads(raw_ids) if isinstance(raw_ids, str) else (raw_ids or [])
    except Exception:
        token_ids = []
    token_id_yes = token_ids[0] if len(token_ids) > 0 else None
    token_id_no  = token_ids[1] if len(token_ids) > 1 else None

    # Bid/ask spread
    try:
        bid = float(m.get("bestBid") or 0)
        ask = float(m.get("bestAsk") or 0)
        spread = round(ask - bid, 4) if bid > 0 and ask > 0 else None
    except Exception:
        bid = ask = 0
        spread = None

    unit = CITY_UNITS.get(matched_city, "C")

    return {
        "market_id":      m.get("conditionId") or m.get("id") or "",
        "slug":           m.get("slug") or "",
        "question":       question,
        "city":           matched_city,
        "lat":            coords[0],
        "lon":            coords[1],
        "unit":           unit,
        "temp_threshold": round(temp, 1),
        "condition":      condition,
        "price_yes":      price_yes,
        "price_no":       price_no,
        "bid":            bid,
        "ask":            ask,
        "spread":         spread,
        "token_id_yes":   token_id_yes,
        "token_id_no":    token_id_no,
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
