"""
crypto_scraper.py
=================
Consulta Polymarket Events API (tag_slug=crypto) y filtra mercados de precio:
- Above/Below un precio  →  "Will Bitcoin be above $70,000 on March 26?"
- Rango de precio        →  "Bitcoin in $70k-$72k range on March 26?"
- Dirección              →  "Will Bitcoin go up on March 26?"
"""

import re
import requests
from datetime import datetime, timezone
from config import GAMMA_API

# Mapeo nombre → CoinGecko ID
ASSET_MAP = {
    "bitcoin":    "bitcoin",
    "btc":        "bitcoin",
    "ethereum":   "ethereum",
    "eth":        "ethereum",
    "solana":     "solana",
    "sol":        "solana",
    "xrp":        "ripple",
    "ripple":     "ripple",
    "dogecoin":   "dogecoin",
    "doge":       "dogecoin",
    "bnb":        "binancecoin",
    "binance":    "binancecoin",
    "cardano":    "cardano",
    "ada":        "cardano",
    "avalanche":  "avalanche-2",
    "avax":       "avalanche-2",
    "polygon":    "matic-network",
    "matic":      "matic-network",
    "chainlink":  "chainlink",
    "link":       "chainlink",
}

# Regex: "above $70,000" / "above $70k" / "below $1.50"
RE_ABOVE_BELOW = re.compile(
    r"(?P<asset>bitcoin|btc|ethereum|eth|solana|sol|xrp|ripple|dogecoin|doge|bnb|cardano|ada|avalanche|avax|polygon|matic|chainlink|link)"
    r".*?(?P<dir>above|below|over|under|exceed)"
    r".*?\$(?P<price>[\d,]+(?:\.\d+)?)\s*(?P<unit>k|m)?",
    re.IGNORECASE,
)

# Regex: rango "$70,000-$72,000" o "$70k-$72k"
RE_RANGE = re.compile(
    r"(?P<asset>bitcoin|btc|ethereum|eth|solana|sol|xrp|ripple|dogecoin|doge|bnb|cardano|ada|avalanche|avax|polygon|matic|chainlink|link)"
    r".*?\$(?P<lo>[\d,]+(?:\.\d+)?)\s*(?P<lo_unit>k|m)?"
    r"\s*[-–to]+\s*"
    r"\$(?P<hi>[\d,]+(?:\.\d+)?)\s*(?P<hi_unit>k|m)?",
    re.IGNORECASE,
)

# Regex: "up or down" / "go up" / "go down"
RE_DIRECTION = re.compile(
    r"(?P<asset>bitcoin|btc|ethereum|eth|solana|sol|xrp|ripple|dogecoin|doge|bnb|cardano|ada|avalanche|avax|polygon|matic|chainlink|link)"
    r".*?(?P<dir>up or down|go up|go down|increase|decrease|pump|dump|higher|lower)",
    re.IGNORECASE,
)


def _parse_price(val: str, unit: str) -> float:
    price = float(val.replace(",", ""))
    unit = (unit or "").lower()
    if unit == "k":
        price *= 1_000
    elif unit == "m":
        price *= 1_000_000
    return price


def _get_prices(m: dict) -> tuple[float, float]:
    """Retorna (price_yes, price_no) del mercado."""
    last = m.get("lastTradePrice")
    bid  = m.get("bestBid")
    ask  = m.get("bestAsk")
    try:
        if last and float(last) > 0:
            price_yes = float(last)
        elif bid and ask and float(bid) > 0 and float(ask) > 0:
            price_yes = (float(bid) + float(ask)) / 2
        else:
            prices = m.get("outcomePrices") or ["0.5", "0.5"]
            price_yes = float(prices[0]) if float(prices[0]) > 0 else 0.5
    except Exception:
        price_yes = 0.5
    return round(price_yes, 4), round(1.0 - price_yes, 4)


def _parse_market(m: dict) -> dict | None:
    question = m.get("question") or ""

    end_str = m.get("endDate") or m.get("endDateIso") or ""
    end_date = None
    days_to_resolve = None
    try:
        end_date = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
        days_to_resolve = (end_date - datetime.now(timezone.utc)).days
    except Exception:
        pass

    price_yes, price_no = _get_prices(m)

    # --- Intentar parsear tipo ABOVE/BELOW ---
    match = RE_ABOVE_BELOW.search(question)
    if match:
        asset_raw = match.group("asset").lower()
        asset_id  = ASSET_MAP.get(asset_raw)
        if not asset_id:
            return None
        direction = match.group("dir").lower()
        condition = "above" if direction in ("above", "over", "exceed") else "below"
        price_target = _parse_price(match.group("price"), match.group("unit"))
        if price_target <= 0:
            return None
        return {
            "market_id":      m.get("conditionId") or m.get("id") or "",
            "slug":           m.get("slug") or "",
            "question":       question,
            "asset":          asset_raw,
            "asset_id":       asset_id,
            "market_type":    "above_below",
            "condition":      condition,
            "price_target":   price_target,
            "price_lo":       None,
            "price_hi":       None,
            "price_yes":      price_yes,
            "price_no":       price_no,
            "end_date":       end_date,
            "days_to_resolve": days_to_resolve,
            "volume":         float(m.get("volume") or 0),
        }

    # --- Intentar parsear tipo RANGO ---
    match = RE_RANGE.search(question)
    if match:
        asset_raw = match.group("asset").lower()
        asset_id  = ASSET_MAP.get(asset_raw)
        if not asset_id:
            return None
        lo = _parse_price(match.group("lo"), match.group("lo_unit"))
        hi = _parse_price(match.group("hi"), match.group("hi_unit"))
        if lo <= 0 or hi <= 0 or lo >= hi:
            return None
        return {
            "market_id":      m.get("conditionId") or m.get("id") or "",
            "slug":           m.get("slug") or "",
            "question":       question,
            "asset":          asset_raw,
            "asset_id":       asset_id,
            "market_type":    "range",
            "condition":      "range",
            "price_target":   (lo + hi) / 2,
            "price_lo":       lo,
            "price_hi":       hi,
            "price_yes":      price_yes,
            "price_no":       price_no,
            "end_date":       end_date,
            "days_to_resolve": days_to_resolve,
            "volume":         float(m.get("volume") or 0),
        }

    # --- Intentar parsear tipo DIRECCIÓN ---
    match = RE_DIRECTION.search(question)
    if match:
        asset_raw = match.group("asset").lower()
        asset_id  = ASSET_MAP.get(asset_raw)
        if not asset_id:
            return None
        dir_raw = match.group("dir").lower()
        condition = "up" if any(w in dir_raw for w in ("up", "higher", "increase", "pump")) else "down"
        return {
            "market_id":      m.get("conditionId") or m.get("id") or "",
            "slug":           m.get("slug") or "",
            "question":       question,
            "asset":          asset_raw,
            "asset_id":       asset_id,
            "market_type":    "direction",
            "condition":      condition,
            "price_target":   None,
            "price_lo":       None,
            "price_hi":       None,
            "price_yes":      price_yes,
            "price_no":       price_no,
            "end_date":       end_date,
            "days_to_resolve": days_to_resolve,
            "volume":         float(m.get("volume") or 0),
        }

    return None


def fetch_crypto_markets(max_pages: int = 5) -> list[dict]:
    """
    Descarga eventos crypto de Polymarket y extrae mercados de precio.
    Retorna solo mercados activos con fecha futura (max 30 días).
    """
    parsed = []
    seen   = set()

    tag_slugs = ["crypto", "bitcoin", "cryptocurrency", "defi"]

    for tag in tag_slugs:
        for page in range(max_pages):
            offset = page * 200
            try:
                resp = requests.get(
                    f"{GAMMA_API}/events",
                    params={"limit": 200, "offset": offset, "active": "true",
                            "closed": "false", "tag_slug": tag},
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
                    if not result or result["market_id"] in seen:
                        continue
                    days = result["days_to_resolve"]
                    if days is None or days < 0 or days > 30:
                        continue
                    seen.add(result["market_id"])
                    parsed.append(result)

            if len(parsed) >= 150:
                break

    parsed.sort(key=lambda x: x["volume"], reverse=True)
    return parsed
