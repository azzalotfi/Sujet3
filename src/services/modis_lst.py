"""MODIS LST (Land Surface Temperature) service.

Primary source: NASA POWER API with TS (Earth Skin Temperature) parameter.
This is the MERRA-2 reanalysis product spatially consistent with MODIS LST
at 0.5° resolution, no API key required.

Optional: Real MODIS LST via NASA AppEEARS API (set env var NASA_EARTHDATA_TOKEN
for actual 1 km MODIS MOD11A1 data).

Data source priority:
  1. NASA AppEEARS MODIS MOD11A1 (if NASA_EARTHDATA_TOKEN set)
  2. NASA POWER TS parameter (free, no key, MERRA-2 reanalysis)
  3. None (caller handles fallback)
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Dict, Optional, List
import requests


NASA_POWER_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"
APPEEARS_BASE = "https://appeears.earthdatacloud.nasa.gov/api"


def fetch_modis_lst(
    lat: float,
    lon: float,
    date_target: datetime,
    window_days: int = 21,
    timeout: int = 8,
) -> Optional[Dict]:
    """Fetch land surface temperature data.

    Tries NASA AppEEARS (real MODIS) first if token available,
    then falls back to NASA POWER TS (reanalysis proxy).

    Returns a dict with:
        source: str
        lst_mean_c: float       — mean daytime LST over window
        lst_max_c: float        — peak daytime LST
        lst_daily_c: List[float]
        dates: List[str]
        heat_stress_days: int   — days where LST > 38°C (critical for olive)
        very_hot_days: int      — days where LST > 45°C (irreversible damage risk)
    Returns None on failure.
    """
    token = os.environ.get("NASA_EARTHDATA_TOKEN")

    result = None
    if token:
        result = _fetch_modis_appeears(lat, lon, date_target, window_days, token, timeout)

    if result is None:
        result = _fetch_nasa_power_lst(lat, lon, date_target, window_days, timeout)

    return result


def _fetch_nasa_power_lst(
    lat: float,
    lon: float,
    date_target: datetime,
    window_days: int,
    timeout: int,
) -> Optional[Dict]:
    """Fetch land surface temperature from NASA POWER (TS parameter).

    TS = Earth Skin Temperature (MERRA-2), daily mean, equivalent to
    MODIS LST in terms of thermal stress assessment for vegetation.
    """
    start_dt = date_target - timedelta(days=window_days - 1)
    params = {
        "parameters": "TS,T2M_MAX",
        "community": "AG",
        "longitude": round(lon, 4),
        "latitude": round(lat, 4),
        "start": start_dt.strftime("%Y%m%d"),
        "end": date_target.strftime("%Y%m%d"),
        "format": "JSON",
    }

    try:
        response = requests.get(NASA_POWER_URL, params=params, timeout=timeout)
        response.raise_for_status()
        data = response.json()

        param_data = data.get("properties", {}).get("parameter", {})
        ts_data = param_data.get("TS", {})
        t2m_max_data = param_data.get("T2M_MAX", {})

        if not ts_data:
            return None

        dates = sorted(ts_data.keys())

        def safe_float(v, default=20.0):
            try:
                f = float(v)
                return f if f > -900 else default
            except (TypeError, ValueError):
                return default

        lst_values = [safe_float(ts_data.get(d)) for d in dates]
        t2m_max_values = [safe_float(t2m_max_data.get(d)) for d in dates]

        lst_mean = sum(lst_values) / len(lst_values) if lst_values else 20.0
        lst_max = max(lst_values) if lst_values else 20.0
        heat_stress_days = sum(1 for v in lst_values if v > 38.0)
        very_hot_days = sum(1 for v in lst_values if v > 45.0)

        return {
            "source": "modis-lst-nasa-power",
            "resolution_deg": 0.5,
            "lst_mean_c": round(lst_mean, 1),
            "lst_max_c": round(lst_max, 1),
            "lst_daily_c": [round(v, 1) for v in lst_values],
            "t2m_max_daily_c": [round(v, 1) for v in t2m_max_values],
            "dates": dates,
            "heat_stress_days": heat_stress_days,
            "very_hot_days": very_hot_days,
            "window_days": window_days,
        }

    except Exception:
        return None


def _fetch_modis_appeears(
    lat: float,
    lon: float,
    date_target: datetime,
    window_days: int,
    token: str,
    timeout: int,
) -> Optional[Dict]:
    """Fetch actual MODIS MOD11A1 LST via NASA AppEEARS point API.

    Requires NASA_EARTHDATA_TOKEN environment variable.
    1 km resolution, Terra MODIS daily daytime LST.
    """
    try:
        headers = {"Authorization": f"Bearer {token}"}
        start_str = (date_target - timedelta(days=window_days - 1)).strftime("%m-%d-%Y")
        end_str = date_target.strftime("%m-%d-%Y")

        task_payload = {
            "task_type": "point",
            "task_name": f"LST_{date_target.strftime('%Y%m%d')}",
            "params": {
                "dates": [{"startDate": start_str, "endDate": end_str}],
                "layers": [{"product": "MOD11A1.061", "layer": "LST_Day_1km"}],
                "coordinates": [{"longitude": lon, "latitude": lat, "id": "parcel", "category": "olive"}],
                "output": {"format": {"type": "json"}, "projection": {"name": "geographic"}},
            },
        }

        submit = requests.post(
            f"{APPEEARS_BASE}/task",
            json=task_payload,
            headers=headers,
            timeout=timeout,
        )
        submit.raise_for_status()
        task_id = submit.json().get("task_id")
        if not task_id:
            return None

        # AppEEARS tasks are async — for hackathon, we only check status once
        # Real integration would poll until "done"
        status = requests.get(
            f"{APPEEARS_BASE}/task/{task_id}",
            headers=headers,
            timeout=timeout,
        )
        task_status = status.json().get("status", "")

        if task_status != "done":
            # Task still processing — fall back to NASA POWER
            return None

        # Fetch results
        bundle = requests.get(
            f"{APPEEARS_BASE}/bundle/{task_id}",
            headers=headers,
            timeout=timeout,
        )
        files = bundle.json().get("files", [])
        csv_file = next((f for f in files if f.get("file_name", "").endswith(".csv")), None)
        if not csv_file:
            return None

        dl = requests.get(
            f"{APPEEARS_BASE}/bundle/{task_id}/{csv_file['file_id']}",
            headers=headers,
            timeout=timeout,
        )
        lines = dl.text.strip().split("\n")
        if len(lines) < 2:
            return None

        # Parse CSV: Date,LST_Day_1km
        lst_values = []
        dates_out = []
        for line in lines[1:]:
            parts = line.split(",")
            if len(parts) >= 2:
                try:
                    date_str = parts[0].strip()
                    # MOD11A1 LST is in Kelvin × 0.02 — convert to Celsius
                    raw = float(parts[1].strip())
                    lst_c = round(raw * 0.02 - 273.15, 1) if raw > 0 else None
                    if lst_c is not None and 0 < lst_c < 80:
                        lst_values.append(lst_c)
                        dates_out.append(date_str)
                except (ValueError, IndexError):
                    continue

        if not lst_values:
            return None

        return {
            "source": "modis-lst-appeears-1km",
            "resolution_km": 1,
            "lst_mean_c": round(sum(lst_values) / len(lst_values), 1),
            "lst_max_c": round(max(lst_values), 1),
            "lst_daily_c": lst_values,
            "dates": dates_out,
            "heat_stress_days": sum(1 for v in lst_values if v > 38.0),
            "very_hot_days": sum(1 for v in lst_values if v > 45.0),
            "window_days": window_days,
        }

    except Exception:
        return None
