"""Sentinel-2 NDVI retrieval helpers with graceful fallback.

This service tries to fetch real NDVI means from Sentinel Hub Statistical API
when credentials are available. If the call fails, it falls back to cached
historical series and deterministic synthetic values.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import hashlib
import json
import os

import numpy as np
import requests


CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "ndvi_cache"


@dataclass
class NdviInputs:
    ndvi_recent: List[float]
    ndvi_historique: List[List[float]]
    source_recent: str
    source_historique: str


def _extract_polygon(oliveraie: Dict[str, Any]) -> List[Dict[str, float]]:
    return oliveraie.get("polygone") or oliveraie.get("coordinates") or []


def _ensure_closed_ring(points: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    if not points:
        return points
    if points[0] != points[-1]:
        return points + [points[0]]
    return points


def _polygon_to_geojson(oliveraie: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    points = _extract_polygon(oliveraie)
    if len(points) < 3:
        return None

    ring = []
    for point in points:
        lat = point.get("lat")
        lng = point.get("lng")
        if lat is None or lng is None:
            continue
        ring.append((float(lng), float(lat)))

    if len(ring) < 3:
        return None

    ring = _ensure_closed_ring(ring)
    return {
        "type": "Polygon",
        "coordinates": [ring],
    }


def _cache_path(parcel_id: str) -> Path:
    return CACHE_DIR / f"{parcel_id}.json"


def _load_cached_historique(parcel_id: str) -> Optional[List[List[float]]]:
    path = _cache_path(parcel_id)
    if not path.exists():
        return None

    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        historique = payload.get("ndvi_historique")
        if isinstance(historique, list) and historique:
            return historique
    except Exception:
        return None
    return None


def _save_cached_historique(parcel_id: str, historique: List[List[float]], source: str) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "parcel_id": parcel_id,
        "source": source,
        "updated_at": datetime.utcnow().isoformat(),
        "ndvi_historique": historique,
    }
    with open(_cache_path(parcel_id), "w", encoding="utf-8") as handle:
        json.dump(payload, handle)


def _seed_from(parcel_id: str, date_target: datetime, tag: str) -> int:
    raw = f"{parcel_id}|{date_target.date().isoformat()}|{tag}".encode("utf-8")
    return int(hashlib.sha256(raw).hexdigest()[:16], 16) % (2**32)


def _simulate_historique(parcel_id: str, date_target: datetime, systeme: str, years: int = 5) -> List[List[float]]:
    base = {
        "extensif": 0.42,
        "intensif": 0.55,
        "hyper-intensif": 0.68,
    }.get(systeme, 0.45)

    rng = np.random.default_rng(_seed_from(parcel_id, date_target, "historique"))
    historique: List[List[float]] = []
    for _ in range(years):
        season = 0.15 * np.sin(2 * np.pi * np.arange(365) / 365)
        noise = rng.normal(0, 0.02, 365)
        series = np.clip(base + season + noise, 0.0, 1.0)
        historique.append([float(v) for v in series])
    return historique


def _simulate_recent_from_historique(
    parcel_id: str,
    date_target: datetime,
    historique: List[List[float]],
    window_days: int,
) -> List[float]:
    day_idx = max(0, min(364, date_target.timetuple().tm_yday - 1))
    baseline_values = [year[day_idx] for year in historique if isinstance(year, list) and len(year) > day_idx]
    baseline = float(np.mean(baseline_values)) if baseline_values else 0.45

    rng = np.random.default_rng(_seed_from(parcel_id, date_target, "recent"))
    trend = np.linspace(0.0, -0.02, window_days)
    noise = rng.normal(0, 0.015, window_days)
    series = np.clip(baseline + trend + noise, 0.0, 1.0)
    return [float(v) for v in series]


def _get_sentinelhub_token() -> Optional[str]:
    client_id = os.getenv("SH_CLIENT_ID") or os.getenv("COPERNICUS_CLIENT_ID")
    client_secret = os.getenv("SH_CLIENT_SECRET") or os.getenv("COPERNICUS_CLIENT_SECRET")

    if not client_id or not client_secret:
        return None

    try:
        response = requests.post(
            "https://services.sentinel-hub.com/oauth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            },
            timeout=10,
        )
        response.raise_for_status()
        return response.json().get("access_token")
    except Exception:
        return None


def _parse_daily_ndvi(stats_payload: Dict[str, Any]) -> List[float]:
    values: List[float] = []
    for item in stats_payload.get("data", []):
        outputs = item.get("outputs", {})
        ndvi_block = outputs.get("ndvi") or {}
        band_block = ndvi_block.get("bands") or {}
        b0 = band_block.get("B0") or {}
        stats = b0.get("stats") or {}
        mean_value = stats.get("mean")
        if mean_value is None:
            continue
        values.append(float(mean_value))
    return [max(0.0, min(1.0, v)) for v in values]


def _fetch_recent_ndvi_sentinelhub(
    oliveraie: Dict[str, Any],
    date_target: datetime,
    window_days: int,
) -> Optional[List[float]]:
    geometry = _polygon_to_geojson(oliveraie)
    if geometry is None:
        return None

    token = _get_sentinelhub_token()
    if token is None:
        return None

    start_date = (date_target - timedelta(days=window_days - 1)).strftime("%Y-%m-%dT00:00:00Z")
    end_date = date_target.strftime("%Y-%m-%dT23:59:59Z")

    evalscript = """
//VERSION=3
function setup() {
  return {
    input: [{ bands: ["B04", "B08", "dataMask"] }],
    output: [
      { id: "ndvi", bands: 1, sampleType: "FLOAT32" },
      { id: "dataMask", bands: 1 }
    ]
  };
}

function evaluatePixel(sample) {
  let ndvi = (sample.B08 - sample.B04) / (sample.B08 + sample.B04 + 0.0001);
  return {
    ndvi: [ndvi],
    dataMask: [sample.dataMask]
  };
}
""".strip()

    payload = {
        "input": {
            "bounds": {"geometry": geometry},
            "data": [
                {
                    "type": "sentinel-2-l2a",
                    "dataFilter": {
                        "timeRange": {"from": start_date, "to": end_date},
                    },
                }
            ],
        },
        "aggregation": {
            "timeRange": {"from": start_date, "to": end_date},
            "aggregationInterval": {"of": "P1D"},
            "resx": 10,
            "resy": 10,
            "evalscript": evalscript,
        },
        "calculations": {
            "ndvi": {"statistics": {"default": {"percentiles": {"k": [50]}}}}
        },
    }

    try:
        response = requests.post(
            "https://services.sentinel-hub.com/api/v1/statistics",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
        daily = _parse_daily_ndvi(response.json())
        if len(daily) >= 5:
            if len(daily) < window_days:
                last_value = daily[-1]
                daily.extend([last_value] * (window_days - len(daily)))
            return daily[:window_days]
    except Exception:
        return None

    return None


def fetch_ndvi_inputs(
    oliveraie: Dict[str, Any],
    date_target: datetime,
    systeme: str,
    window_days: int = 21,
    historique_years: int = 5,
) -> NdviInputs:
    parcel_id = oliveraie.get("id") or "unknown"

    cached_historique = _load_cached_historique(parcel_id)
    if cached_historique is None:
        cached_historique = _simulate_historique(parcel_id, date_target, systeme, years=historique_years)
        _save_cached_historique(parcel_id, cached_historique, source="simulated-historical")
        historique_source = "cache-generated"
    else:
        historique_source = "cache-file"

    recent_real = _fetch_recent_ndvi_sentinelhub(oliveraie, date_target, window_days=window_days)
    if recent_real is not None:
        recent_source = "sentinelhub-statistics"
        recent_series = recent_real
    else:
        recent_source = "synthetic-fallback"
        recent_series = _simulate_recent_from_historique(
            parcel_id=parcel_id,
            date_target=date_target,
            historique=cached_historique,
            window_days=window_days,
        )

    return NdviInputs(
        ndvi_recent=recent_series,
        ndvi_historique=cached_historique,
        source_recent=recent_source,
        source_historique=historique_source,
    )
