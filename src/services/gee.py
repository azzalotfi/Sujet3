"""Google Earth Engine (GEE) service — accès unifié MODIS LST + Sentinel-2 NDVI.

Ce service combine dans un seul script GEE :
  - Sentinel-2 NDVI (10m résolution, bandes B8/B4)
  - MODIS LST MOD11A1 (1km, température de surface)

Authentification (priorité décroissante) :
  1. Compte de service  : variables env GEE_SERVICE_ACCOUNT + GEE_KEY_FILE
  2. Token OAuth direct  : variable env GEE_OAUTH_TOKEN
  3. Projet GEE          : variable env GEE_PROJECT (+ `earthengine authenticate` préalable)
  4. Fallback gracieux   : retourne None sans planter l'API

Utilisation hackathon :
  - Sans compte GEE : retourne None, les autres sources (CHIRPS, MODIS NASA POWER) prennent le relais
  - Avec compte GEE  : NDVI Sentinel-2 réel 10m + MODIS LST haute résolution
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Dict, Optional, List

# GEE SDK optionnel — l'import ne doit pas planter si non configuré
try:
    import ee
    _GEE_AVAILABLE = True
except ImportError:
    _GEE_AVAILABLE = False


_GEE_INITIALIZED = False


def _init_gee() -> bool:
    """Initialiser GEE selon les variables d'environnement disponibles.

    Returns True si l'initialisation a réussi, False sinon.
    """
    global _GEE_INITIALIZED
    if not _GEE_AVAILABLE:
        return False

    # Si déjà initialisé avec succès, retourner True directement
    if _GEE_INITIALIZED:
        return True

    project = os.environ.get("GEE_PROJECT", "")
    service_account = os.environ.get("GEE_SERVICE_ACCOUNT", "")
    key_file = os.environ.get("GEE_KEY_FILE", "")

    try:
        if service_account and key_file and os.path.exists(key_file):
            credentials = ee.ServiceAccountCredentials(service_account, key_file)
            ee.Initialize(credentials, project=project or None)
        elif project:
            ee.Initialize(project=project)
        else:
            return False
        _GEE_INITIALIZED = True
        return True
    except Exception as exc:
        # GEE déjà initialisé = succès
        if "already been initialized" in str(exc).lower():
            _GEE_INITIALIZED = True
            return True
        return False


def fetch_gee_ndvi_lst(
    lat: float,
    lon: float,
    date_target: datetime,
    window_days: int = 21,
) -> Optional[Dict]:
    """Récupérer NDVI Sentinel-2 + LST MODIS via GEE.

    Combine en un seul appel GEE :
    - Sentinel-2 SR collection (COPERNICUS/S2_SR_HARMONIZED) — NDVI = (B8-B4)/(B8+B4)
    - MODIS MOD11A1.061 — LST_Day_1km

    Returns dict avec:
        source: "gee-sentinel2-modis"
        ndvi_mean: float         — NDVI moyen sur la fenêtre 21j
        ndvi_series: List[float] — série temporelle NDVI
        lst_mean_c: float        — LST MODIS moyenne (°C)
        lst_max_c: float         — LST MODIS max
        heat_stress_days: int    — jours LST > 38°C
        dates: List[str]
    Returns None si GEE non configuré ou en erreur.
    """
    if not _init_gee():
        return None

    try:
        start_dt = date_target - timedelta(days=window_days - 1)
        start_str = start_dt.strftime("%Y-%m-%d")
        end_str = date_target.strftime("%Y-%m-%d")

        point = ee.Geometry.Point([lon, lat])
        # Buffer de 500m autour du centroïde pour couvrir la parcelle
        region = point.buffer(500)

        # --- Sentinel-2 NDVI ---
        s2 = (
            ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
            .filterBounds(region)
            .filterDate(start_str, end_str)
            .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 20))
            .map(lambda img: img.normalizedDifference(["B8", "B4"])
                               .rename("NDVI")
                               .set("system:time_start", img.get("system:time_start")))
        )

        ndvi_list = s2.aggregate_array("NDVI").getInfo()
        ndvi_dates = s2.aggregate_array("system:time_start").getInfo()

        if ndvi_list and len(ndvi_list) > 0:
            # Extraire valeur moyenne par image sur la région
            def get_mean_ndvi(img):
                val = img.reduceRegion(
                    reducer=ee.Reducer.mean(),
                    geometry=region,
                    scale=10,
                    maxPixels=1e6,
                ).get("NDVI")
                return ee.Feature(None, {"ndvi": val, "date": img.date().format("YYYY-MM-dd")})

            ndvi_features = s2.map(get_mean_ndvi).getInfo()
            features = ndvi_features.get("features", [])
            ndvi_values = [
                f["properties"]["ndvi"]
                for f in features
                if f["properties"].get("ndvi") is not None
            ]
            ndvi_date_strs = [f["properties"]["date"] for f in features if f["properties"].get("date")]
        else:
            ndvi_values = []
            ndvi_date_strs = []

        # --- MODIS LST ---
        modis = (
            ee.ImageCollection("MODIS/061/MOD11A1")
            .filterBounds(region)
            .filterDate(start_str, end_str)
            .select("LST_Day_1km")
        )

        def get_lst(img):
            # Conversion Kelvin × 0.02 → Celsius
            lst_c = img.multiply(0.02).subtract(273.15)
            val = lst_c.reduceRegion(
                reducer=ee.Reducer.mean(),
                geometry=region,
                scale=1000,
                maxPixels=1e6,
            ).get("LST_Day_1km")
            return ee.Feature(None, {"lst_c": val, "date": img.date().format("YYYY-MM-dd")})

        lst_features = modis.map(get_lst).getInfo()
        lst_values = [
            f["properties"]["lst_c"]
            for f in lst_features.get("features", [])
            if f["properties"].get("lst_c") is not None and float(f["properties"]["lst_c"]) > 0
        ]
        lst_date_strs = [
            f["properties"]["date"]
            for f in lst_features.get("features", [])
            if f["properties"].get("date")
        ]

        # Résumé
        ndvi_mean = sum(ndvi_values) / len(ndvi_values) if ndvi_values else None
        lst_mean = sum(lst_values) / len(lst_values) if lst_values else None
        lst_max = max(lst_values) if lst_values else None
        heat_stress_days = sum(1 for v in lst_values if v > 38.0)
        very_hot_days = sum(1 for v in lst_values if v > 45.0)

        return {
            "source": "gee-sentinel2-modis",
            "resolution_sentinel2_m": 10,
            "resolution_modis_km": 1,
            # NDVI Sentinel-2
            "ndvi_mean": round(ndvi_mean, 4) if ndvi_mean is not None else None,
            "ndvi_series": [round(v, 4) for v in ndvi_values],
            "ndvi_dates": ndvi_date_strs,
            "ndvi_count": len(ndvi_values),
            # MODIS LST
            "lst_mean_c": round(lst_mean, 1) if lst_mean is not None else None,
            "lst_max_c": round(lst_max, 1) if lst_max is not None else None,
            "lst_daily_c": [round(v, 1) for v in lst_values],
            "lst_dates": lst_date_strs,
            "heat_stress_days": heat_stress_days,
            "very_hot_days": very_hot_days,
            "window_days": window_days,
        }

    except Exception as exc:
        return {"source": "gee-error", "error": str(exc)}


def gee_status() -> Dict:
    """Retourner le statut de configuration GEE (pour l'endpoint /health)."""
    if not _GEE_AVAILABLE:
        return {"available": False, "reason": "earthengine-api non installe"}

    project = os.environ.get("GEE_PROJECT", "")
    service_account = os.environ.get("GEE_SERVICE_ACCOUNT", "")
    key_file = os.environ.get("GEE_KEY_FILE", "")

    if service_account and key_file:
        auth_mode = "service-account"
    elif project:
        auth_mode = "oauth-local"
    else:
        auth_mode = "non-configure"

    initialized = _init_gee()
    return {
        "available": _GEE_AVAILABLE,
        "initialized": initialized,
        "auth_mode": auth_mode,
        "project": project or None,
        "sdk_version": getattr(ee, "__version__", "unknown") if _GEE_AVAILABLE else None,
    }
