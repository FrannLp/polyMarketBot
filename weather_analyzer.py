"""
weather_analyzer.py
===================
Usa Open-Meteo (gratis, sin API key) con 4 modelos meteorologicos.
Calcula probabilidad real de que se cumpla la condicion de temperatura.
"""

import requests
from datetime import datetime, timezone, date
from config import OPEN_METEO_URL

# 4 modelos meteorologicos de alta precision
WEATHER_MODELS = [
    "best_match",
    "ecmwf_ifs025",
    "gfs_seamless",
    "icon_seamless",
]


def _fetch_forecast(lat: float, lon: float, target_date: date, model: str) -> dict | None:
    """Descarga forecast de Open-Meteo para un modelo dado."""
    params = {
        "latitude":    lat,
        "longitude":   lon,
        "daily":       "temperature_2m_max,temperature_2m_min",
        "timezone":    "auto",
        "start_date":  target_date.isoformat(),
        "end_date":    target_date.isoformat(),
        "models":      model,
    }
    try:
        resp = requests.get(OPEN_METEO_URL, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        daily = data.get("daily", {})
        temps_max = daily.get("temperature_2m_max", [None])
        temps_min = daily.get("temperature_2m_min", [None])
        return {
            "model":    model,
            "temp_max": temps_max[0] if temps_max else None,
            "temp_min": temps_min[0] if temps_min else None,
        }
    except Exception:
        return None


def analyze_temperature(
    lat: float,
    lon: float,
    temp_threshold: float,
    condition: str,       # "gte" o "lte"
    target_date: date,
) -> dict:
    """
    Consulta 4 modelos y calcula probabilidad real de que se cumpla la condicion.

    Returns:
        {
            "prob_real":     float,   # 0.0 - 1.0
            "models_agree":  int,     # cuantos modelos acuerdan
            "models_total":  int,
            "temp_avg_max":  float,
            "temp_avg_min":  float,
            "forecasts":     list,
            "confidence":    str,     # "HIGH" / "MEDIUM" / "LOW"
        }
    """
    forecasts = []
    for model in WEATHER_MODELS:
        result = _fetch_forecast(lat, lon, target_date, model)
        if result:
            forecasts.append(result)

    if not forecasts:
        return {
            "prob_real":    0.5,
            "models_agree": 0,
            "models_total": 0,
            "temp_avg_max": None,
            "temp_avg_min": None,
            "forecasts":    [],
            "confidence":   "LOW",
        }

    # Temperatura relevante según condicion
    agree = 0
    valid_maxes = [f["temp_max"] for f in forecasts if f["temp_max"] is not None]
    valid_mins  = [f["temp_min"] for f in forecasts if f["temp_min"] is not None]

    for f in forecasts:
        temp_max = f["temp_max"]
        temp_min = f["temp_min"]
        if temp_max is None:
            continue

        if condition == "gte":
            # "highest temperature >= threshold"
            if temp_max >= temp_threshold:
                agree += 1
        else:
            # "highest temperature <= threshold" o temperatura min
            if temp_min is not None and temp_min <= temp_threshold:
                agree += 1
            elif temp_max <= temp_threshold:
                agree += 1

    total = len(forecasts)
    raw_prob = agree / total if total > 0 else 0.5

    # Ajuste bayesiano conservador (evita extremos 0 y 1)
    # Dirichlet smoothing con alpha=0.5
    alpha = 0.5
    prob_bayes = (agree + alpha) / (total + 2 * alpha)

    # Clasificar confianza
    if total >= 3 and (agree == total or agree == 0):
        confidence = "HIGH"
    elif total >= 2 and abs(raw_prob - 0.5) > 0.3:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"

    return {
        "prob_real":    round(prob_bayes, 4),
        "models_agree": agree,
        "models_total": total,
        "temp_avg_max": round(sum(valid_maxes) / len(valid_maxes), 1) if valid_maxes else None,
        "temp_avg_min": round(sum(valid_mins)  / len(valid_mins),  1) if valid_mins  else None,
        "forecasts":    forecasts,
        "confidence":   confidence,
    }
