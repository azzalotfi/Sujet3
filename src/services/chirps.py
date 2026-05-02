"""CHIRPS precipitation data via NASA POWER API.

NASA POWER uses CHIRPS-corrected precipitation (PRECTOTCORR) at 0.5° resolution,
1981-present, with no API key required. This is the recommended T1 data source
for pluviometry in the hackathon specification.

API docs: https://power.larc.nasa.gov/docs/
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, Optional, List
import requests


NASA_POWER_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"


def fetch_chirps_precipitation(
    lat: float,
    lon: float,
    date_target: datetime,
    window_days: int = 21,
    timeout: int = 8,
) -> Optional[Dict]:
    """Fetch CHIRPS-corrected precipitation from NASA POWER.

    Returns a dict with:
        source: "chirps-nasa-power"
        precipitation_total_mm: float
        precipitation_daily_mm: List[float]
        dates: List[str]
        mean_daily_mm: float
        dry_days: int  (days with < 1mm)
        max_daily_mm: float
    Returns None on failure.
    """
    start_dt = date_target - timedelta(days=window_days - 1)
    start_str = start_dt.strftime("%Y%m%d")
    end_str = date_target.strftime("%Y%m%d")

    params = {
        "parameters": "PRECTOTCORR",
        "community": "AG",
        "longitude": round(lon, 4),
        "latitude": round(lat, 4),
        "start": start_str,
        "end": end_str,
        "format": "JSON",
    }

    try:
        response = requests.get(NASA_POWER_URL, params=params, timeout=timeout)
        response.raise_for_status()
        data = response.json()

        properties = data.get("properties", {})
        parameter_data = properties.get("parameter", {})
        prec_data = parameter_data.get("PRECTOTCORR", {})

        if not prec_data:
            return None

        dates = sorted(prec_data.keys())
        values = [
            max(0.0, float(v)) if v is not None and float(v) >= 0 else 0.0
            for v in [prec_data[d] for d in dates]
        ]

        if not values:
            return None

        total = sum(values)
        dry_days = sum(1 for v in values if v < 1.0)
        mean_daily = total / len(values) if values else 0.0
        max_daily = max(values) if values else 0.0

        return {
            "source": "chirps-nasa-power",
            "resolution_deg": 0.5,
            "precipitation_total_mm": round(total, 2),
            "mean_daily_mm": round(mean_daily, 2),
            "max_daily_mm": round(max_daily, 2),
            "dry_days": dry_days,
            "wet_days": len(values) - dry_days,
            "precipitation_daily_mm": [round(v, 2) for v in values],
            "dates": dates,
            "window_days": window_days,
            "lat": lat,
            "lon": lon,
        }

    except Exception:
        return None
