"""
cryp_5m_scraper.py
==================
Fetches active 5-minute UP/DOWN markets from Polymarket for BTC, ETH, SOL and XRP.

Slug pattern: {asset}-updown-5m-{unix_timestamp}
where unix_timestamp is divisible by 300 (5-minute window start).

Example slugs:
  btc-updown-5m-1774494300
  eth-updown-5m-1774494300
  sol-updown-5m-1774494300
  xrp-updown-5m-1774494300
"""

import time
import requests
from datetime import datetime, timezone

GAMMA_API   = "https://gamma-api.polymarket.com"
WINDOW_SIZE = 300  # 5 minutes in seconds

# Asset slug prefix → Binance symbol
ASSETS = {
    "BTC":  {"slug_prefix": "btc-updown-5m",      "binance": "BTCUSDT"},
    "ETH":  {"slug_prefix": "eth-updown-5m",      "binance": "ETHUSDT"},
    "SOL":  {"slug_prefix": "sol-updown-5m",      "binance": "SOLUSDT"},
    "XRP":  {"slug_prefix": "xrp-updown-5m",      "binance": "XRPUSDT"},
    "DOGE": {"slug_prefix": "doge-updown-5m",     "binance": "DOGEUSDT"},
    "HYPE": {"slug_prefix": "hype-updown-5m",     "binance": "HYPEUSDT"},
}


def _get_window_timestamps() -> list[int]:
    """Return [current_window_start, next_window_start] as Unix timestamps."""
    now_ts = int(time.time())
    current = (now_ts // WINDOW_SIZE) * WINDOW_SIZE
    return [current, current + WINDOW_SIZE]


def _parse_prices(m: dict) -> tuple[float, float]:
    """
    Extract (price_up, price_down) from a Polymarket market dict.
    outcomePrices[0] = Up/Arriba, outcomePrices[1] = Down/Abajo
    """
    outcomes = m.get("outcomePrices") or []
    if len(outcomes) >= 2:
        try:
            return float(outcomes[0]), float(outcomes[1])
        except (ValueError, TypeError):
            pass
    # Fallback: lastTradePrice
    ltp = m.get("lastTradePrice")
    if ltp:
        try:
            p = float(ltp)
            return p, 1.0 - p
        except (ValueError, TypeError):
            pass
    return 0.5, 0.5


def _parse_end_date(m: dict, event: dict) -> datetime | None:
    for src in (m, event):
        for key in ("endDate", "endDateIso"):
            val = src.get(key)
            if val:
                try:
                    return datetime.fromisoformat(str(val).replace("Z", "+00:00"))
                except Exception:
                    pass
    return None


def _fetch_event_by_slug(slug: str) -> dict | None:
    """Fetch a single event from Gamma API by exact slug."""
    try:
        resp = requests.get(
            f"{GAMMA_API}/events",
            params={"slug": slug, "active": "true"},
            timeout=8,
        )
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list) and data:
            return data[0]
        if isinstance(data, dict) and data:
            return data
    except Exception:
        pass
    return None


def fetch_5m_markets() -> list[dict]:
    """
    Fetch all active 5-minute UP/DOWN markets for BTC, ETH, SOL, XRP.

    Returns a list of market dicts, each with:
        asset       : str  ("BTC", "ETH", "SOL", "XRP")
        binance_sym : str  ("BTCUSDT", ...)
        market_id   : str
        slug        : str
        question    : str
        price_up    : float  (cost to bet UP = YES)
        price_down  : float  (cost to bet DOWN = NO)
        end_date    : datetime | None
        window_start: int   (Unix timestamp)
        volume      : float
    Sorted by end_date ascending (soonest first).
    """
    now      = datetime.now(timezone.utc)
    windows  = _get_window_timestamps()
    results  = []
    seen     = set()

    for asset, cfg in ASSETS.items():
        slug_prefix = cfg["slug_prefix"]
        binance_sym = cfg["binance"]

        for win_ts in windows:
            slug  = f"{slug_prefix}-{win_ts}"
            event = _fetch_event_by_slug(slug)
            if not event:
                continue

            for m in event.get("markets", []):
                mid = m.get("conditionId") or m.get("id", "")
                if not mid or mid in seen:
                    continue

                end_date = _parse_end_date(m, event)
                if end_date:
                    secs_left = (end_date - now).total_seconds()
                    # Skip: already closed or > 10 minutes away
                    if secs_left < 20 or secs_left > 600:
                        continue

                price_up, price_down = _parse_prices(m)
                question = m.get("question") or event.get("title", f"{asset} Up or Down 5m")

                # Parse clobTokenIds: ["token_up", "token_down"]
                raw_ids = m.get("clobTokenIds") or "[]"
                try:
                    import json as _json
                    token_ids = _json.loads(raw_ids) if isinstance(raw_ids, str) else raw_ids
                except Exception:
                    token_ids = []
                token_id_up   = token_ids[0] if len(token_ids) > 0 else None
                token_id_down = token_ids[1] if len(token_ids) > 1 else None

                seen.add(mid)
                results.append({
                    "asset":         asset,
                    "binance_sym":   binance_sym,
                    "market_id":     mid,
                    "slug":          slug,
                    "question":      question,
                    "price_up":      price_up,
                    "price_down":    price_down,
                    "token_id_up":   token_id_up,
                    "token_id_down": token_id_down,
                    "end_date":      end_date,
                    "window_start":  win_ts,
                    "volume":        float(m.get("volume") or 0),
                })

    results.sort(key=lambda x: x["end_date"] or datetime.max.replace(tzinfo=timezone.utc))
    return results
