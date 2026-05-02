"""Weather context helpers for olive parcel diagnostics.

Data source priority:
  1. Open-Meteo archive API (temperature + precipitation, free, no key)
  2. CHIRPS via NASA POWER API (PRECTOTCORR, 0.5°, 1981-present, free, no key)
  3. MODIS LST via NASA POWER TS or AppEEARS (land surface temperature)
  4. Deterministic seasonal fallback (offline)

CHIRPS and MODIS LST are fetched in parallel and merged into the weather context.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple
import math
import concurrent.futures

import requests

from services.chirps import fetch_chirps_precipitation
from services.modis_lst import fetch_modis_lst
from services.gee import fetch_gee_ndvi_lst, gee_status


def _extract_coordinates(oliveraie: Any) -> Optional[Tuple[float, float]]:
    """Return a (lat, lon) centroid from a parcel model or mapping."""
    if oliveraie is None:
        return None

    polygone = None
    if isinstance(oliveraie, dict):
        polygone = oliveraie.get("polygone")
    else:
        polygone = getattr(oliveraie, "polygone", None)

    if not polygone:
        return None

    latitudes = []
    longitudes = []
    for point in polygone:
        if isinstance(point, dict):
            latitudes.append(float(point.get("lat", 0.0)))
            longitudes.append(float(point.get("lng", 0.0)))
        else:
            latitudes.append(float(getattr(point, "lat", 0.0)))
            longitudes.append(float(getattr(point, "lng", 0.0)))

    if not latitudes or not longitudes:
        return None

    return sum(latitudes) / len(latitudes), sum(longitudes) / len(longitudes)


def _seasonal_fallback(date_target: datetime, latitude: float, longitude: float, window_days: int) -> Dict[str, Any]:
    """Offline weather approximation used when the live API is unavailable."""
    day_of_year = date_target.timetuple().tm_yday
    phase = (2 * math.pi * day_of_year) / 365.0

    base_precip = 18.0 + 10.0 * math.cos(phase + latitude / 18.0)
    temperature = 20.0 + 9.0 * math.sin(phase - 0.7) + (latitude - 34.0) * 0.35
    wind_proxy = 8.0 + abs(math.sin(phase + longitude / 24.0)) * 6.0

    precipitation_21d = max(0.0, base_precip * (window_days / 7.0))
    water_stress_index = max(
        0.0,
        min(1.0, ((26.0 - precipitation_21d) / 26.0) + max(0.0, temperature - 31.0) / 18.0),
    )

    return {
        "source": "open-meteo-fallback",
        "latitude": latitude,
        "longitude": longitude,
        "window_days": window_days,
        "temperature_mean_c": round(temperature, 1),
        "temperature_max_c": round(temperature + 4.0, 1),
        "precipitation_21d_mm": round(precipitation_21d, 1),
        "precipitation_daily_mm": round(base_precip, 1),
        "wind_proxy_kph": round(wind_proxy, 1),
        "water_stress_index": round(water_stress_index, 3),
        "daily": [],
    }


def fetch_weather_context(
    date_target: datetime,
    oliveraie: Any = None,
    window_days: int = 21,
) -> Dict[str, Any]:
    """Return enriched weather context for the diagnostic window.

    Sources fetched in parallel:
    - Open-Meteo archive (temperature + precipitation baseline)
    - CHIRPS via NASA POWER (PRECTOTCORR — precise satellite precipitation)
    - MODIS LST via NASA POWER (land surface temperature for thermal stress)

    CHIRPS precipitation is preferred over Open-Meteo when available.
    MODIS LST enriches the thermal stress assessment.
    """
    coordinates = _extract_coordinates(oliveraie)
    if coordinates is None:
        return _seasonal_fallback(date_target, 34.0, 9.0, window_days)

    lat, lon = coordinates

    # Fetch all sources in parallel (max 8s each, non-blocking)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        fut_openmeteo = executor.submit(_fetch_open_meteo, lat, lon, date_target, window_days)
        fut_chirps = executor.submit(fetch_chirps_precipitation, lat, lon, date_target, window_days)
        fut_modis = executor.submit(fetch_modis_lst, lat, lon, date_target, window_days)
        fut_gee = executor.submit(fetch_gee_ndvi_lst, lat, lon, date_target, window_days)

        openmeteo = fut_openmeteo.result()
        chirps = fut_chirps.result()
        modis = fut_modis.result()
        gee = fut_gee.result()

    # Base context from Open-Meteo or fallback
    if openmeteo:
        ctx = openmeteo
    else:
        ctx = _seasonal_fallback(date_target, lat, lon, window_days)

    # Override precipitation with CHIRPS when available (more accurate)
    if chirps:
        ctx["precipitation_21d_mm"] = chirps["precipitation_total_mm"]
        ctx["precipitation_daily_mm"] = chirps["mean_daily_mm"]
        ctx["chirps"] = {
            "source": chirps["source"],
            "resolution_deg": chirps["resolution_deg"],
            "total_mm": chirps["precipitation_total_mm"],
            "mean_daily_mm": chirps["mean_daily_mm"],
            "max_daily_mm": chirps["max_daily_mm"],
            "dry_days": chirps["dry_days"],
            "wet_days": chirps["wet_days"],
        }
        # Recompute water stress index with CHIRPS precipitation
        temp_mean = ctx.get("temperature_mean_c", 25.0)
        ctx["water_stress_index"] = round(
            max(0.0, min(1.0,
                ((28.0 - chirps["precipitation_total_mm"]) / 28.0)
                + max(0.0, temp_mean - 30.0) / 20.0
            )),
            3,
        )
        # Update sources label
        ctx["source"] = ctx["source"].replace("open-meteo", "open-meteo+chirps")

    # Enrich with MODIS LST when available
    if modis:
        ctx["modis_lst"] = {
            "source": modis["source"],
            "lst_mean_c": modis["lst_mean_c"],
            "lst_max_c": modis["lst_max_c"],
            "heat_stress_days": modis["heat_stress_days"],
            "very_hot_days": modis["very_hot_days"],
        }
        # Override temperature_max_c with LST max (more accurate for vegetation stress)
        if modis["lst_max_c"] > ctx.get("temperature_max_c", 0):
            ctx["temperature_max_c"] = modis["lst_max_c"]
        ctx["source"] = ctx["source"] + "+modis-lst"

    # Recompute water stress index if we have both CHIRPS and MODIS LST
    if chirps and modis:
        lst_penalty = max(0.0, (modis["lst_mean_c"] - 32.0) / 18.0)
        prec_penalty = max(0.0, (28.0 - chirps["precipitation_total_mm"]) / 28.0)
        ctx["water_stress_index"] = round(min(1.0, prec_penalty + lst_penalty), 3)

    # Enrich with GEE (Sentinel-2 NDVI 10m + MODIS LST 1km) when available
    if gee and gee.get("source") != "gee-error" and gee.get("ndvi_mean") is not None:
        ctx["gee"] = {
            "source": gee["source"],
            "resolution_sentinel2_m": gee["resolution_sentinel2_m"],
            "ndvi_mean": gee["ndvi_mean"],
            "ndvi_count": gee["ndvi_count"],
            "lst_mean_c": gee.get("lst_mean_c"),
            "lst_max_c": gee.get("lst_max_c"),
            "heat_stress_days": gee.get("heat_stress_days", 0),
        }
        ctx["source"] = ctx["source"] + "+gee"
        # GEE LST 1km prioritaire sur NASA POWER 0.5° si disponible
        if gee.get("lst_max_c") and gee["lst_max_c"] > ctx.get("temperature_max_c", 0):
            ctx["temperature_max_c"] = gee["lst_max_c"]

    return ctx


def _fetch_open_meteo(lat: float, lon: float, date_target: datetime, window_days: int) -> Optional[Dict]:
    """Fetch temperature and precipitation from Open-Meteo archive."""
    start_date = (date_target - timedelta(days=window_days - 1)).date().isoformat()
    end_date = date_target.date().isoformat()

    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start_date,
        "end_date": end_date,
        "daily": "temperature_2m_mean,temperature_2m_max,precipitation_sum",
        "timezone": "Africa/Tunis",
    }

    try:
        response = requests.get(
            "https://archive-api.open-meteo.com/v1/archive",
            params=params,
            timeout=5,
        )
        response.raise_for_status()
        payload = response.json()
        daily = payload.get("daily", {})

        temperatures = daily.get("temperature_2m_mean", []) or []
        temperatures_max = daily.get("temperature_2m_max", []) or []
        precipitation = daily.get("precipitation_sum", []) or []

        precipitation_21d = float(sum(float(v) for v in precipitation))
        temperature_mean = float(sum(float(v) for v in temperatures) / max(len(temperatures), 1))
        temperature_max = float(max((float(v) for v in temperatures_max), default=temperature_mean))

        water_stress_index = max(
            0.0,
            min(1.0, ((28.0 - precipitation_21d) / 28.0) + max(0.0, temperature_mean - 30.0) / 20.0),
        )

        return {
            "source": "open-meteo-archive",
            "latitude": lat,
            "longitude": lon,
            "window_days": window_days,
            "temperature_mean_c": round(temperature_mean, 1),
            "temperature_max_c": round(temperature_max, 1),
            "precipitation_21d_mm": round(precipitation_21d, 1),
            "precipitation_daily_mm": round(precipitation_21d / max(len(precipitation), 1), 1),
            "wind_proxy_kph": None,
            "water_stress_index": round(water_stress_index, 3),
            "daily": [
                {"date": day, "temperature_mean_c": temp, "temperature_max_c": tmax, "precipitation_mm": rain}
                for day, temp, tmax, rain in zip(
                    daily.get("time", []), temperatures, temperatures_max, precipitation
                )
            ],
        }
    except Exception:
        return None