"""
weather_analyzer.py
===================
Three forecast sources (article upgrade):
1. ECMWF via Open-Meteo — global, 7-day, bias-corrected
2. HRRR/GFS Seamless via Open-Meteo — US cities D+0/D+1 (higher resolution)
3. METAR — real-time airport observations from aviationweather.gov

Source selection:
- US cities, horizon <= 48h → HRRR primary
- All others → ECMWF primary
- METAR: fetched for same-day markets as supplementary data
"""

import requests
from datetime import date
from config import OPEN_METEO_URL

# ICAO station codes per city name (lowercase)
CITY_STATIONS: dict[str, str] = {
    "new york":     "KLGA",
    "chicago":      "KORD",
    "miami":        "KMIA",
    "dallas":       "KDAL",
    "seattle":      "KSEA",
    "atlanta":      "KATL",
    "london":       "EGLC",
    "paris":        "LFPG",
    "munich":       "EDDM",
    "ankara":       "LTAC",
    "seoul":        "RKSI",
    "tokyo":        "RJTT",
    "shanghai":     "ZSPD",
    "singapore":    "WSSS",
    "lucknow":      "VILK",
    "tel aviv":     "LLBG",
    "toronto":      "CYYZ",
    "sao paulo":    "SBGR",
    "buenos aires": "SAEZ",
    "wellington":   "NZWN",
    # fallbacks for other cities in market_scraper
    "sydney":       "YSSY",
    "los angeles":  "KLAX",
    "berlin":       "EDDB",
    "madrid":       "LEMD",
    "rome":         "LIRF",
    "istanbul":     "LTBA",
    "dubai":        "OMDB",
    "mumbai":       "VABB",
    "moscow":       "UUEE",
    "amsterdam":    "EHAM",
    "mexico city":  "MMMX",
    "bangkok":      "VTBS",
    "cape town":    "FACT",
    "bogota":       "SKBO",
    "lima":         "SPJC",
    "santiago":     "SCEL",
}

# Timezones for Open-Meteo daily query
CITY_TIMEZONES: dict[str, str] = {
    "new york":     "America/New_York",
    "chicago":      "America/Chicago",
    "miami":        "America/New_York",
    "dallas":       "America/Chicago",
    "seattle":      "America/Los_Angeles",
    "atlanta":      "America/New_York",
    "london":       "Europe/London",
    "paris":        "Europe/Paris",
    "munich":       "Europe/Berlin",
    "ankara":       "Europe/Istanbul",
    "seoul":        "Asia/Seoul",
    "tokyo":        "Asia/Tokyo",
    "shanghai":     "Asia/Shanghai",
    "singapore":    "Asia/Singapore",
    "lucknow":      "Asia/Kolkata",
    "tel aviv":     "Asia/Jerusalem",
    "toronto":      "America/Toronto",
    "sao paulo":    "America/Sao_Paulo",
    "buenos aires": "America/Argentina/Buenos_Aires",
    "wellington":   "Pacific/Auckland",
    "sydney":       "Australia/Sydney",
    "los angeles":  "America/Los_Angeles",
    "berlin":       "Europe/Berlin",
    "madrid":       "Europe/Madrid",
    "rome":         "Europe/Rome",
    "istanbul":     "Europe/Istanbul",
    "dubai":        "Asia/Dubai",
    "mumbai":       "Asia/Kolkata",
    "moscow":       "Europe/Moscow",
    "amsterdam":    "Europe/Amsterdam",
    "mexico city":  "America/Mexico_City",
    "bangkok":      "Asia/Bangkok",
}

# US cities (use HRRR/GFS for D+0/D+1)
US_CITIES: set[str] = {"new york", "chicago", "miami", "dallas", "seattle", "atlanta"}


def _unit_param(unit: str) -> str:
    return "fahrenheit" if unit.upper() == "F" else "celsius"


def get_ecmwf_forecast(lat: float, lon: float, target_date: date, unit: str, city: str = "") -> dict | None:
    """ECMWF IFS 0.25° from Open-Meteo, bias-corrected."""
    tz = CITY_TIMEZONES.get(city.lower(), "UTC")
    try:
        resp = requests.get(
            OPEN_METEO_URL,
            params={
                "latitude":         lat,
                "longitude":        lon,
                "daily":            "temperature_2m_max,temperature_2m_min",
                "temperature_unit": _unit_param(unit),
                "forecast_days":    7,
                "timezone":         tz,
                "start_date":       target_date.isoformat(),
                "end_date":         target_date.isoformat(),
                "models":           "ecmwf_ifs025",
                "bias_correction":  "true",
            },
            timeout=12,
        )
        resp.raise_for_status()
        daily = resp.json().get("daily", {})
        maxes = daily.get("temperature_2m_max", [None])
        mins  = daily.get("temperature_2m_min", [None])
        if not maxes or maxes[0] is None:
            return None
        return {"source": "ecmwf", "temp_max": maxes[0], "temp_min": mins[0] if mins else None}
    except Exception:
        return None


def get_hrrr_forecast(lat: float, lon: float, target_date: date, unit: str, city: str = "") -> dict | None:
    """GFS Seamless (HRRR+GFS blend) from Open-Meteo. US cities only, horizon <= 3 days."""
    tz = CITY_TIMEZONES.get(city.lower(), "America/New_York")
    try:
        resp = requests.get(
            OPEN_METEO_URL,
            params={
                "latitude":         lat,
                "longitude":        lon,
                "daily":            "temperature_2m_max,temperature_2m_min",
                "temperature_unit": _unit_param(unit),
                "forecast_days":    3,
                "timezone":         tz,
                "start_date":       target_date.isoformat(),
                "end_date":         target_date.isoformat(),
                "models":           "gfs_seamless",
            },
            timeout=12,
        )
        resp.raise_for_status()
        daily = resp.json().get("daily", {})
        maxes = daily.get("temperature_2m_max", [None])
        mins  = daily.get("temperature_2m_min", [None])
        if not maxes or maxes[0] is None:
            return None
        return {"source": "hrrr", "temp_max": maxes[0], "temp_min": mins[0] if mins else None}
    except Exception:
        return None


def get_metar_observation(station: str, unit: str) -> dict | None:
    """Real-time METAR from aviationweather.gov — same stations Polymarket resolves against."""
    if not station:
        return None
    try:
        resp = requests.get(
            "https://aviationweather.gov/api/data/metar",
            params={"ids": station, "format": "json"},
            timeout=8,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data:
            return None
        temp_c = data[0].get("temp")
        if temp_c is None:
            return None
        temp = round((temp_c * 9 / 5 + 32), 1) if unit.upper() == "F" else round(float(temp_c), 1)
        return {"source": "metar", "temp_obs": temp, "temp_max": None, "temp_min": None}
    except Exception:
        return None


def analyze_temperature(
    lat: float,
    lon: float,
    temp_threshold: float,
    condition: str,       # "gte" | "lte"
    target_date: date,
    city: str = "",
    unit: str = "C",
    horizon_hours: int = 24,
    calibration: dict | None = None,
) -> dict:
    """
    Fetch 3 sources and compute probability that temp condition is met.

    Source priority:
      - US + horizon <= 48h → HRRR primary
      - All others → ECMWF primary
      - METAR: supplementary (same-day only)

    With calibration sigma, uses normal distribution for probability.
    Without calibration, uses Dirichlet-smoothed model consensus.
    """
    city_lower = city.lower()
    is_us = city_lower in US_CITIES

    # ── Fetch sources ──────────────────────────────────────────────────────────
    ecmwf = get_ecmwf_forecast(lat, lon, target_date, unit, city)
    hrrr  = None
    if is_us and horizon_hours <= 48:
        hrrr = get_hrrr_forecast(lat, lon, target_date, unit, city)

    station = CITY_STATIONS.get(city_lower)
    metar   = None
    if station and horizon_hours <= 24:
        metar = get_metar_observation(station, unit)

    # ── Determine primary source ───────────────────────────────────────────────
    if is_us and hrrr and hrrr.get("temp_max") is not None:
        primary_source = "hrrr"
        primary_temp   = hrrr["temp_max"]
    elif ecmwf and ecmwf.get("temp_max") is not None:
        primary_source = "ecmwf"
        primary_temp   = ecmwf["temp_max"]
    else:
        primary_source = None
        primary_temp   = None

    # ── Collect all valid temp_max predictions ─────────────────────────────────
    forecasts = [s for s in [ecmwf, hrrr] if s and s.get("temp_max") is not None]

    if not forecasts:
        return {
            "prob_real":      0.5,
            "models_agree":   0,
            "models_total":   0,
            "temp_avg_max":   None,
            "temp_avg_min":   None,
            "primary_temp":   None,
            "primary_source": None,
            "ecmwf":          ecmwf,
            "hrrr":           hrrr,
            "metar":          metar,
            "confidence":     "LOW",
            "sigma":          None,
        }

    # ── Count models that agree ────────────────────────────────────────────────
    agree = 0
    valid_maxes = [f["temp_max"] for f in forecasts]
    valid_mins  = [f["temp_min"] for f in [ecmwf, hrrr] if f and f.get("temp_min") is not None]

    for f in forecasts:
        temp_max = f["temp_max"]
        temp_min = f.get("temp_min")
        if condition == "gte":
            if temp_max >= temp_threshold:
                agree += 1
        else:
            if temp_min is not None and temp_min <= temp_threshold:
                agree += 1
            elif temp_max <= temp_threshold:
                agree += 1

    total    = len(forecasts)
    raw_prob = agree / total if total > 0 else 0.5

    # ── Probability: calibrated (normal dist) or smoothed model consensus ─────
    sigma = None
    if calibration and primary_source and primary_temp is not None:
        cal_key = f"{city_lower}_{primary_source}"
        sigma   = calibration.get(cal_key, {}).get("sigma")

    if sigma and primary_temp is not None:
        try:
            from statistics import NormalDist
            nd = NormalDist(mu=0, sigma=1)
            z  = (primary_temp - temp_threshold) / sigma
            prob_cal = nd.cdf(z) if condition == "gte" else (1.0 - nd.cdf(z))
            prob_bayes = round(min(0.99, max(0.01, prob_cal)), 4)
        except Exception:
            alpha      = 0.5
            prob_bayes = (agree + alpha) / (total + 2 * alpha)
    else:
        alpha      = 0.5
        prob_bayes = (agree + alpha) / (total + 2 * alpha)

    # ── Confidence ─────────────────────────────────────────────────────────────
    if total >= 2 and (agree == total or agree == 0):
        confidence = "HIGH"
    elif total >= 1 and abs(raw_prob - 0.5) > 0.3:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"

    return {
        "prob_real":      round(prob_bayes, 4),
        "models_agree":   agree,
        "models_total":   total,
        "temp_avg_max":   round(sum(valid_maxes) / len(valid_maxes), 1),
        "temp_avg_min":   round(sum(valid_mins)  / len(valid_mins),  1) if valid_mins else None,
        "primary_temp":   primary_temp,
        "primary_source": primary_source,
        "ecmwf":          ecmwf,
        "hrrr":           hrrr,
        "metar":          metar,
        "confidence":     confidence,
        "sigma":          sigma,
    }
